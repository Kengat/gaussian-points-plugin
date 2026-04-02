#include "PointCloudRenderer.h"
#include <windows.h>
#include <GL/glew.h>
#include <GL/gl.h>
#include <cstdio>
#include <cstdarg>
#include <cstring>
#include <vector>

// Тип функции получения матриц
typedef bool (*PFN_GET_MATRICES)(float* modelview, float* projection);
static PFN_GET_MATRICES GetCurrentMatrices = nullptr;

// Глобальные переменные для хранения данных точек и состояния OpenGL
static std::vector<float> g_points;
static bool g_dataReady = false;
static GLuint g_program = 0;
static GLuint g_vao = 0;
static GLuint g_vbo = 0;

// Функция логирования
static void LogMessage(const char* format, ...) {
    char buffer[1024];
    va_list args;
    va_start(args, format);
    vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);
    OutputDebugStringA(buffer);
}

// Вспомогательная функция для компиляции шейдера
static GLuint CompileShader(GLenum type, const char* source) {
    GLuint shader = glCreateShader(type);
    if (!shader) {
        LogMessage("[Renderer] Failed to create shader\n");
        return 0;
    }

    glShaderSource(shader, 1, &source, nullptr);
    glCompileShader(shader);

    GLint success;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &success);
    if (!success) {
        char infoLog[512];
        glGetShaderInfoLog(shader, sizeof(infoLog), nullptr, infoLog);
        LogMessage("[Renderer] Shader compilation error:\n%s\n", infoLog);
        glDeleteShader(shader);
        return 0;
    }

    LogMessage("[Renderer] Shader compiled successfully\n");
    return shader;
}

// Создание шейдерной программы один раз
static bool InitializeRenderer() {
    if (g_program != 0) {
        return true;  // Уже инициализировано
    }

    LogMessage("[Renderer] Starting renderer initialization...\n");

    // Шейдер для точек с использованием projection-матрицы
    const char* vertexShaderSource = R"(
        #version 150
        uniform mat4 projection;
        
        in vec3 position;
        in vec3 color;
        in float size;
        
        flat out vec4 pointColor;
        
        void main() {
            // Позиционируем точку с использованием только projection-матрицы
            gl_Position = projection * vec4(position, 1.0);
            
            // Передаем цвет и устанавливаем полупрозрачность
            pointColor = vec4(color, 0.7);
            
            // Устанавливаем размер точки
            gl_PointSize = size;
        }
    )";

    const char* fragmentShaderSource = R"(
        #version 150
        flat in vec4 pointColor;
        out vec4 outColor;
        
        void main() {
            outColor = pointColor;
        }
    )";

    // Компилируем шейдеры
    GLuint vertexShader = CompileShader(GL_VERTEX_SHADER, vertexShaderSource);
    if (!vertexShader) {
        LogMessage("[Renderer] Failed to compile vertex shader\n");
        return false;
    }

    GLuint fragmentShader = CompileShader(GL_FRAGMENT_SHADER, fragmentShaderSource);
    if (!fragmentShader) {
        glDeleteShader(vertexShader);
        LogMessage("[Renderer] Failed to compile fragment shader\n");
        return false;
    }

    // Создаем и линкуем программу
    g_program = glCreateProgram();
    glAttachShader(g_program, vertexShader);
    glAttachShader(g_program, fragmentShader);

    // Привязываем атрибуты к конкретным локациям
    glBindAttribLocation(g_program, 0, "position");
    glBindAttribLocation(g_program, 1, "color");
    glBindAttribLocation(g_program, 2, "size");

    glLinkProgram(g_program);

    GLint success;
    glGetProgramiv(g_program, GL_LINK_STATUS, &success);
    if (!success) {
        char infoLog[512];
        glGetProgramInfoLog(g_program, sizeof(infoLog), nullptr, infoLog);
        LogMessage("[Renderer] Program linking error:\n%s\n", infoLog);
        glDeleteShader(vertexShader);
        glDeleteShader(fragmentShader);
        glDeleteProgram(g_program);
        g_program = 0;
        return false;
    }

    glDeleteShader(vertexShader);
    glDeleteShader(fragmentShader);

    // Создаем VAO и VBO для точек
    glGenVertexArrays(1, &g_vao);
    glGenBuffers(1, &g_vbo);

    LogMessage("[Renderer] Renderer initialized successfully\n");
    return true;
}

// Освобождение ресурсов
static void CleanupRenderer() {
    if (g_vao) glDeleteVertexArrays(1, &g_vao);
    if (g_vbo) glDeleteBuffers(1, &g_vbo);
    if (g_program) glDeleteProgram(g_program);
    g_vao = 0;
    g_vbo = 0;
    g_program = 0;
    LogMessage("[Renderer] Resources cleaned up\n");
}

// Функция для сохранения данных точек
extern "C" EXPORT void SetPointCloud(const double* points_in, int count) {
    LogMessage("[Renderer] SetPointCloud called with %d points\n", count);

    if (!points_in || count <= 0) {
        LogMessage("[Renderer] Invalid input data\n");
        return;
    }

    // Пытаемся загрузить функцию получения матриц
    if (!GetCurrentMatrices) {
        HMODULE hookDLL = GetModuleHandleA("PointCloudHookDLL.dll");
        if (hookDLL) {
            GetCurrentMatrices = (PFN_GET_MATRICES)GetProcAddress(hookDLL, "GetCurrentMatrices");
            if (GetCurrentMatrices) {
                LogMessage("[Renderer] Found GetCurrentMatrices function\n");
            }
            else {
                LogMessage("[Renderer] GetCurrentMatrices function not found\n");
            }
        }
        else {
            LogMessage("[Renderer] Hook DLL not found\n");
        }
    }

    // Инициализируем рендерер при первой загрузке
    if (!InitializeRenderer()) {
        LogMessage("[Renderer] Failed to initialize renderer\n");
        return;
    }

    // Преобразуем данные - для каждой точки сохраняем:
    // - координаты (x, y, z)
    // - цвет (r, g, b)
    // - размер (константа, 5.0f)
    g_points.clear();
    g_points.reserve(count * 7); // xyz + rgb + size

    for (int i = 0; i < count; ++i) {
        // Координаты XYZ (не изменяем, берем как есть)
        g_points.push_back(static_cast<float>(points_in[i * 6 + 0]));
        g_points.push_back(static_cast<float>(points_in[i * 6 + 1]));
        g_points.push_back(static_cast<float>(points_in[i * 6 + 2]));

        // Цвет RGB (нормализуем до 0-1)
        g_points.push_back(static_cast<float>(points_in[i * 6 + 3]) / 255.0f);
        g_points.push_back(static_cast<float>(points_in[i * 6 + 4]) / 255.0f);
        g_points.push_back(static_cast<float>(points_in[i * 6 + 5]) / 255.0f);

        // Размер точки (фиксированный)
        g_points.push_back(5.0f);
    }

    // Загружаем данные в VBO
    glBindVertexArray(g_vao);
    glBindBuffer(GL_ARRAY_BUFFER, g_vbo);
    glBufferData(GL_ARRAY_BUFFER, g_points.size() * sizeof(float), g_points.data(), GL_STATIC_DRAW);

    // Настраиваем атрибуты
    // Позиция (xyz)
    glEnableVertexAttribArray(0);
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, 7 * sizeof(float), (void*)0);

    // Цвет (rgb)
    glEnableVertexAttribArray(1);
    glVertexAttribPointer(1, 3, GL_FLOAT, GL_FALSE, 7 * sizeof(float), (void*)(3 * sizeof(float)));

    // Размер точки
    glEnableVertexAttribArray(2);
    glVertexAttribPointer(2, 1, GL_FLOAT, GL_FALSE, 7 * sizeof(float), (void*)(6 * sizeof(float)));

    // Освобождаем привязку
    glBindVertexArray(0);
    glBindBuffer(GL_ARRAY_BUFFER, 0);

    g_dataReady = true;
    LogMessage("[Renderer] Data processed and uploaded to GPU, %zu bytes\n", g_points.size() * sizeof(float));
}

// Функция для рендеринга облака точек
extern "C" EXPORT void renderPointCloud() {
    LogMessage("[Renderer] renderPointCloud called\n");

    if (!g_dataReady || g_points.empty() || !g_program || !g_vao) {
        LogMessage("[Renderer] Not ready for rendering\n");
        return;
    }

    // Пытаемся найти функцию получения матриц, если еще не нашли
    if (!GetCurrentMatrices) {
        HMODULE hookDLL = GetModuleHandleA("PointCloudHookDLL.dll");
        if (hookDLL) {
            GetCurrentMatrices = (PFN_GET_MATRICES)GetProcAddress(hookDLL, "GetCurrentMatrices");
            if (!GetCurrentMatrices) {
                LogMessage("[Renderer] GetCurrentMatrices function not found\n");
                return;
            }
        }
        else {
            LogMessage("[Renderer] Hook DLL not found\n");
            return;
        }
    }

    // Получаем текущие матрицы
    float modelview[16];
    float projection[16];
    if (!GetCurrentMatrices(modelview, projection)) {
        LogMessage("[Renderer] Failed to get matrices\n");
        return;
    }

    // Сохраняем текущее состояние OpenGL
    GLint oldProgram;
    glGetIntegerv(GL_CURRENT_PROGRAM, &oldProgram);

    GLboolean depthTest, blend, depthMask;
    glGetBooleanv(GL_DEPTH_TEST, &depthTest);
    glGetBooleanv(GL_BLEND, &blend);
    glGetBooleanv(GL_DEPTH_WRITEMASK, &depthMask);

    // Устанавливаем наши параметры рендеринга
    glUseProgram(g_program);
    glBindVertexArray(g_vao);

    // Включаем depth test для корректного перекрытия объектами
    glEnable(GL_DEPTH_TEST);
    glDepthMask(GL_TRUE);

    // Включаем blending для прозрачности
    glEnable(GL_BLEND);
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);

    // Включаем рендеринг точек с переменным размером
    glEnable(GL_PROGRAM_POINT_SIZE);

    // Устанавливаем projection-матрицу в шейдер
    GLint projectionLoc = glGetUniformLocation(g_program, "projection");
    if (projectionLoc >= 0) {
        glUniformMatrix4fv(projectionLoc, 1, GL_FALSE, projection);
    }
    else {
        LogMessage("[Renderer] Warning: projection uniform not found\n");
    }

    // Рисуем все точки одним вызовом
    int pointCount = g_points.size() / 7; // 7 float на точку (xyz + rgb + size)
    LogMessage("[Renderer] Drawing %d points\n", pointCount);
    glDrawArrays(GL_POINTS, 0, pointCount);

    // Проверяем ошибки
    GLenum err = glGetError();
    if (err != GL_NO_ERROR) {
        LogMessage("[Renderer] OpenGL error: 0x%x\n", err);
    }

    // Восстанавливаем предыдущее состояние OpenGL
    glBindVertexArray(0);
    glUseProgram(oldProgram);

    if (!depthTest) glDisable(GL_DEPTH_TEST);
    if (!blend) glDisable(GL_BLEND);
    if (!depthMask) glDepthMask(GL_FALSE);

    glDisable(GL_PROGRAM_POINT_SIZE);

    LogMessage("[Renderer] Rendering completed\n");
}

// Обработка загрузки/выгрузки DLL
BOOL APIENTRY DllMain(HMODULE hModule, DWORD ul_reason_for_call, LPVOID lpReserved) {
    switch (ul_reason_for_call) {
    case DLL_PROCESS_ATTACH:
    {
        LogMessage("[Renderer] DLL_PROCESS_ATTACH\n");
        // Пытаемся найти функцию получения матриц
        HMODULE hookDLL = GetModuleHandleA("PointCloudHookDLL.dll");
        if (hookDLL) {
            GetCurrentMatrices = (PFN_GET_MATRICES)GetProcAddress(hookDLL, "GetCurrentMatrices");
            if (GetCurrentMatrices) {
                LogMessage("[Renderer] Found GetCurrentMatrices function\n");
            }
            else {
                LogMessage("[Renderer] GetCurrentMatrices function not found\n");
            }
        }
        else {
            LogMessage("[Renderer] Hook DLL not found\n");
        }
        break;
    }
    case DLL_PROCESS_DETACH:
        LogMessage("[Renderer] DLL_PROCESS_DETACH\n");
        CleanupRenderer();
        break;
    }
    return TRUE;
}