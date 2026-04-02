#include "GaussianSplatRenderer.h"
#include <windows.h>
#include <GL/glew.h>
#include <GL/gl.h>
#include <cstdio>
#include <cstdarg>
#include <cstring>
#include <vector>
#include <cmath>
#include <algorithm>
#include <map>
#include <numeric> // Для std::iota

#define NOMINMAX
#undef min
#undef max
#define M_PI 3.14159265358979323846

// --- Типы функций хука ---
typedef bool (*PFN_GET_MATRIX_BY_LOC)(int location, float* matrix);
static PFN_GET_MATRIX_BY_LOC GetMatrixByLocation = nullptr;

// --- Глобальные переменные ---
static std::vector<float> g_points;     // Для хранения данных из SetPointCloud
static bool g_dataReady = false;        // Флаг для g_points
static GLuint g_gaussTexture = 0;
static bool g_textureInitialized = false;

struct GaussSplat {
    float position[3]; // X, Y, Z координаты (мировые)
    float color[4];    // R, G, B, A
    float scale[2];    // X, Y масштаб
    float rotation[4]; // Кватернион вращения (w, x, y, z)
};
static std::vector<GaussSplat> g_splats;

enum SplatSortingMode {
    SORT_Z_COORD = 0, SORT_HEMISPHERE = 1, SORT_WEIGHTED_DEPTH = 2,
    SORT_RADIAL_SECTORS = 3, SORT_ADAPTIVE_ANGLE = 4, SORT_FORWARD_PLUS = 5
};
static SplatSortingMode g_sortingMode = SORT_FORWARD_PLUS;

struct SplatVBOData {
    struct VertexData {
        float position[3]; float texCoord[2]; float color[4];
    };
    GLuint vbo = 0; GLuint vao = 0; GLuint ebo = 0;
    std::vector<VertexData> vertices; std::vector<GLuint> indices;
    bool initialized = false; bool needsUpdate = true;
};
static SplatVBOData g_splatVBO; // Инициализация по умолчанию

static std::vector<GLuint> g_splatSortIndices; // Индексы сплэтов для сортировки

// Структура для оптимизации сортировки
struct SplatSortData {
    GLuint index;         // Исходный индекс сплата
    float projValue;      // Проекция на направление взгляда
    float distanceSquared; // Квадрат расстояния до камеры
    float sortKey;        // Финальный ключ сортировки
    bool isBackfacing;    // Обращён ли назад
};
static std::vector<SplatSortData> g_splatSortCache;

// Переменные для отслеживания изменений камеры
static float g_lastCamPos[3] = { 0, 0, 0 };
static float g_lastViewDir[3] = { 0, 0, 0 };
static int g_framesSinceLastSort = 0;
static const int SORT_EVERY_N_FRAMES = 2; // Сортировать каждые 2 кадра

static GLuint g_splatShader = 0; // Шейдерная программа

// --- Утилиты ---
static void LogRenderer(const char* format, ...) {
    char buffer[1024]; va_list args; va_start(args, format);
    vsnprintf(buffer, sizeof(buffer), format, args); va_end(args);
    OutputDebugStringA("[RendDBG] "); OutputDebugStringA(buffer); OutputDebugStringA("\n");
}

static void PrintRendererMatrix(const char* name, const float* matrix) {
    LogRenderer("Matrix '%s':", name);
    if (!matrix) { LogRenderer("  (null)"); return; }
    for (int i = 0; i < 4; i++) {
        char rowBuf[128];
        snprintf(rowBuf, sizeof(rowBuf), "[%.4f %.4f %.4f %.4f]",
            matrix[i * 4 + 0], matrix[i * 4 + 1], matrix[i * 4 + 2], matrix[i * 4 + 3]);
        LogRenderer("  %s", rowBuf);
    }
}

// --- Математика и OpenGL подготовка ---
void QuaternionToMatrix(float q0, float q1, float q2, float q3, float matrix[16]) {
    float norm = sqrt(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3);
    if (norm < 1e-5f) { memset(matrix, 0, 16 * sizeof(float)); matrix[0] = matrix[5] = matrix[10] = matrix[15] = 1.0f; return; }
    q0 /= norm; q1 /= norm; q2 /= norm; q3 /= norm;
    float q1q1 = q1 * q1, q2q2 = q2 * q2, q3q3 = q3 * q3;
    float q1q2 = q1 * q2, q1q3 = q1 * q3, q2q3 = q2 * q3;
    float q0q1 = q0 * q1, q0q2 = q0 * q2, q0q3 = q0 * q3;
    matrix[0] = 1.0f - 2.0f * (q2q2 + q3q3); matrix[1] = 2.0f * (q1q2 - q0q3);       matrix[2] = 2.0f * (q1q3 + q0q2);       matrix[3] = 0.0f;
    matrix[4] = 2.0f * (q1q2 + q0q3);       matrix[5] = 1.0f - 2.0f * (q1q1 + q3q3); matrix[6] = 2.0f * (q2q3 - q0q1);       matrix[7] = 0.0f;
    matrix[8] = 2.0f * (q1q3 - q0q2);       matrix[9] = 2.0f * (q2q3 + q0q1);       matrix[10] = 1.0f - 2.0f * (q1q1 + q2q2); matrix[11] = 0.0f;
    matrix[12] = 0.0f;                 matrix[13] = 0.0f;                  matrix[14] = 0.0f;                  matrix[15] = 1.0f;
}

static GLuint CreateSplatShader() {
    LogRenderer("Creating splat shader...");
    const char* vertexShaderSource = R"(#version 130
        attribute vec3 aPos; attribute vec2 aTexCoord; attribute vec4 aColor;
        uniform mat4 uMVP;
        varying vec2 vTexCoord; varying vec4 vColor;
        void main() { gl_Position = uMVP * vec4(aPos, 1.0); vTexCoord = aTexCoord; vColor = aColor; })";
    const char* fragmentShaderSource = R"(#version 130
        varying vec2 vTexCoord; varying vec4 vColor; uniform sampler2D uTexture;
        void main() { vec4 texColor = texture2D(uTexture, vTexCoord); gl_FragColor = vec4(vColor.rgb, texColor.a * vColor.a); })";

    GLuint vertexShader = glCreateShader(GL_VERTEX_SHADER);
    glShaderSource(vertexShader, 1, &vertexShaderSource, NULL);
    glCompileShader(vertexShader);
    GLint success; char infoLog[512]; glGetShaderiv(vertexShader, GL_COMPILE_STATUS, &success);
    if (!success) { glGetShaderInfoLog(vertexShader, 512, NULL, infoLog); LogRenderer("ERROR: VS compile failed: %s", infoLog); glDeleteShader(vertexShader); return 0; } // Добавил удаление шейдера при ошибке

    GLuint fragmentShader = glCreateShader(GL_FRAGMENT_SHADER);
    glShaderSource(fragmentShader, 1, &fragmentShaderSource, NULL);
    glCompileShader(fragmentShader);
    glGetShaderiv(fragmentShader, GL_COMPILE_STATUS, &success);
    if (!success) { glGetShaderInfoLog(fragmentShader, 512, NULL, infoLog); LogRenderer("ERROR: FS compile failed: %s", infoLog); glDeleteShader(vertexShader); glDeleteShader(fragmentShader); return 0; } // Добавил удаление шейдеров

    GLuint shaderProgram = glCreateProgram();
    glAttachShader(shaderProgram, vertexShader);
    glAttachShader(shaderProgram, fragmentShader);
    glBindAttribLocation(shaderProgram, 0, "aPos");
    glBindAttribLocation(shaderProgram, 1, "aTexCoord");
    glBindAttribLocation(shaderProgram, 2, "aColor");
    glLinkProgram(shaderProgram);
    glGetProgramiv(shaderProgram, GL_LINK_STATUS, &success);
    if (!success) { glGetProgramInfoLog(shaderProgram, 512, NULL, infoLog); LogRenderer("ERROR: Shader link failed: %s", infoLog); }

    glDeleteShader(vertexShader); glDeleteShader(fragmentShader); // Удаляем в любом случае после линковки
    if (!success) { glDeleteProgram(shaderProgram); return 0; } // Если линковка не удалась, удаляем программу
    LogRenderer("Shader program created successfully (ID: %u).", shaderProgram);
    return shaderProgram;
}

static GLuint CreateGaussianTexture(int size, float sigma) {
    LogRenderer("Creating gaussian texture %dx%d sigma=%.2f", size, size, sigma);
    unsigned char* data = new unsigned char[size * size * 4]; float center = size / 2.0f;
    for (int y = 0; y < size; y++) for (int x = 0; x < size; x++) {
        float dx = (x - center + 0.5f) / center; float dy = (y - center + 0.5f) / center;
        float distSq = dx * dx + dy * dy; float alpha = exp(-distSq / (2.0f * sigma * sigma));
        data[(y * size + x) * 4 + 0] = 255; data[(y * size + x) * 4 + 1] = 255; data[(y * size + x) * 4 + 2] = 255; data[(y * size + x) * 4 + 3] = (unsigned char)(alpha * 255.0f);
    }
    GLuint texture = 0; glGenTextures(1, &texture); if (texture == 0) { LogRenderer("ERROR glGenTextures failed"); delete[] data; return 0; }
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, size, size, 0, GL_RGBA, GL_UNSIGNED_BYTE, data);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE); glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glBindTexture(GL_TEXTURE_2D, 0); delete[] data;
    LogRenderer("Gaussian texture created (ID: %u)", texture);
    return texture;
}

// --- VBO Инициализация и Обновление ---
static void InitializeSplatVBO() {
    if (g_splatVBO.initialized) return;
    LogRenderer("Initializing Splat VBO...");
    GLenum err = glewInit(); if (err != GLEW_OK) { LogRenderer("ERROR: GLEW init failed: %s", glewGetErrorString(err)); return; }
    if (!GLEW_VERSION_2_0 || !GLEW_ARB_vertex_buffer_object) { LogRenderer("WARN: OpenGL VBO/Shader support not available."); return; }

    glGenVertexArrays(1, &g_splatVBO.vao); glBindVertexArray(g_splatVBO.vao);
    glGenBuffers(1, &g_splatVBO.vbo); glBindBuffer(GL_ARRAY_BUFFER, g_splatVBO.vbo);
    glGenBuffers(1, &g_splatVBO.ebo); glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g_splatVBO.ebo);
    // Атрибуты
    glVertexAttribPointer(0, 3, GL_FLOAT, GL_FALSE, sizeof(SplatVBOData::VertexData), (void*)offsetof(SplatVBOData::VertexData, position)); glEnableVertexAttribArray(0);
    glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, sizeof(SplatVBOData::VertexData), (void*)offsetof(SplatVBOData::VertexData, texCoord)); glEnableVertexAttribArray(1);
    glVertexAttribPointer(2, 4, GL_FLOAT, GL_FALSE, sizeof(SplatVBOData::VertexData), (void*)offsetof(SplatVBOData::VertexData, color)); glEnableVertexAttribArray(2);
    glBindBuffer(GL_ARRAY_BUFFER, 0); glBindVertexArray(0); // Отвязка

    if (g_splatShader == 0) { g_splatShader = CreateSplatShader(); }
    if (g_splatShader == 0) { LogRenderer("ERROR: Shader creation failed during VBO init. VBO unusable."); return; }

    g_splatVBO.initialized = true; g_splatVBO.needsUpdate = true;
    LogRenderer("Splat VBO initialized successfully (VAO:%u, VBO:%u, EBO:%u).", g_splatVBO.vao, g_splatVBO.vbo, g_splatVBO.ebo);
}

static void UpdateSplatVBOVertices() {
    if (!g_splatVBO.initialized) { LogRenderer("DEBUG: UpdateVBOVertices skip - not inited"); return; }
    if (!g_splatVBO.needsUpdate) { return; }
    if (g_splats.empty() && g_splatVBO.vertices.empty()) { g_splatVBO.needsUpdate = false; return; }

    LogRenderer("DEBUG: Entering UpdateSplatVBOVertices for %zu splats...", g_splats.size());

    if (g_splats.empty()) {
        if (g_splatVBO.vao != 0) {
            glBindVertexArray(g_splatVBO.vao);
            glBindBuffer(GL_ARRAY_BUFFER, g_splatVBO.vbo); glBufferData(GL_ARRAY_BUFFER, 0, nullptr, GL_STATIC_DRAW);
            glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g_splatVBO.ebo); glBufferData(GL_ELEMENT_ARRAY_BUFFER, 0, nullptr, GL_DYNAMIC_DRAW);
            glBindVertexArray(0);
            g_splatVBO.vertices.clear(); g_splatVBO.indices.clear(); g_splatSortIndices.clear();
            LogRenderer("DEBUG: Cleared VBO/EBO/Indices as splats are empty.");
        }
    }
    else {
        g_splatVBO.vertices.clear();
        g_splatVBO.vertices.reserve(g_splats.size() * 4);
        float quadSize = 1.0f;
        for (size_t i = 0; i < g_splats.size(); ++i) {
            const GaussSplat& splat = g_splats[i];
            float rotMatrix[16]; QuaternionToMatrix(splat.rotation[0], splat.rotation[1], splat.rotation[2], splat.rotation[3], rotMatrix);
            float localXVec[3] = { quadSize * splat.scale[0],0,0 }; float localYVec[3] = { 0,quadSize * splat.scale[1],0 };
            float rotatedXVec[3], rotatedYVec[3];
            rotatedXVec[0] = localXVec[0] * rotMatrix[0] + localYVec[0] * rotMatrix[4]; rotatedXVec[1] = localXVec[0] * rotMatrix[1] + localYVec[0] * rotMatrix[5]; rotatedXVec[2] = localXVec[0] * rotMatrix[2] + localYVec[0] * rotMatrix[6];
            rotatedYVec[0] = localXVec[1] * rotMatrix[0] + localYVec[1] * rotMatrix[4]; rotatedYVec[1] = localXVec[1] * rotMatrix[1] + localYVec[1] * rotMatrix[5]; rotatedYVec[2] = localXVec[1] * rotMatrix[2] + localYVec[1] * rotMatrix[6];
            SplatVBOData::VertexData v0, v1, v2, v3;
            v0.position[0] = splat.position[0] - rotatedXVec[0] - rotatedYVec[0]; v0.position[1] = splat.position[1] - rotatedXVec[1] - rotatedYVec[1]; v0.position[2] = splat.position[2] - rotatedXVec[2] - rotatedYVec[2]; v0.texCoord[0] = 0.0f; v0.texCoord[1] = 0.0f;
            v1.position[0] = splat.position[0] + rotatedXVec[0] - rotatedYVec[0]; v1.position[1] = splat.position[1] + rotatedXVec[1] - rotatedYVec[1]; v1.position[2] = splat.position[2] + rotatedXVec[2] - rotatedYVec[2]; v1.texCoord[0] = 1.0f; v1.texCoord[1] = 0.0f;
            v2.position[0] = splat.position[0] + rotatedXVec[0] + rotatedYVec[0]; v2.position[1] = splat.position[1] + rotatedXVec[1] + rotatedYVec[1]; v2.position[2] = splat.position[2] + rotatedXVec[2] + rotatedYVec[2]; v2.texCoord[0] = 1.0f; v2.texCoord[1] = 1.0f;
            v3.position[0] = splat.position[0] - rotatedXVec[0] + rotatedYVec[0]; v3.position[1] = splat.position[1] - rotatedXVec[1] + rotatedYVec[1]; v3.position[2] = splat.position[2] - rotatedXVec[2] + rotatedYVec[2]; v3.texCoord[0] = 0.0f; v3.texCoord[1] = 1.0f;
            v0.color[0] = v1.color[0] = v2.color[0] = v3.color[0] = splat.color[0]; v0.color[1] = v1.color[1] = v2.color[1] = v3.color[1] = splat.color[1]; v0.color[2] = v1.color[2] = v2.color[2] = v3.color[2] = splat.color[2]; v0.color[3] = v1.color[3] = v2.color[3] = v3.color[3] = splat.color[3];
            g_splatVBO.vertices.push_back(v0); g_splatVBO.vertices.push_back(v1); g_splatVBO.vertices.push_back(v2); g_splatVBO.vertices.push_back(v3);
        }
        glBindVertexArray(g_splatVBO.vao);
        glBindBuffer(GL_ARRAY_BUFFER, g_splatVBO.vbo);
        glBufferData(GL_ARRAY_BUFFER, g_splatVBO.vertices.size() * sizeof(SplatVBOData::VertexData), g_splatVBO.vertices.data(), GL_STATIC_DRAW);
        glBindVertexArray(0);

        g_splatSortIndices.resize(g_splats.size());
        std::iota(g_splatSortIndices.begin(), g_splatSortIndices.end(), 0);
        LogRenderer("DEBUG: Initialized g_splatSortIndices with size %zu after VBO update", g_splatSortIndices.size());
    }
    g_splatVBO.needsUpdate = false;
    LogRenderer("DEBUG: Exiting UpdateSplatVBOVertices. Vertex count: %zu", g_splatVBO.vertices.size());
}

static void UpdateSplatEBO() {
    if (!g_splatVBO.initialized) {
        // LogRenderer("DEBUG: UpdateSplatEBO skip - VBO not initialized."); // Раскомментируй для отладки
        return;
    }
    if (g_splatVBO.ebo == 0) {
        LogRenderer("ERROR: UpdateSplatEBO - EBO ID is 0!");
        return;
    }

    if (g_splatSortIndices.empty()) {
        // LogRenderer("DEBUG: UpdateSplatEBO skip - g_splatSortIndices is empty."); // Раскомментируй для отладки
        // Если сортировочные индексы пусты, но EBO не пуст, очистим его
        if (!g_splatVBO.indices.empty()) {
            LogRenderer("DEBUG: Clearing EBO as sort indices are empty.");
            glBindVertexArray(g_splatVBO.vao); // Нужен VAO для привязки EBO
            glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g_splatVBO.ebo);
            glBufferData(GL_ELEMENT_ARRAY_BUFFER, 0, nullptr, GL_DYNAMIC_DRAW); // Очищаем буфер на GPU
            glBindVertexArray(0);
            g_splatVBO.indices.clear(); // Очищаем и CPU-копию индексов
        }
        return;
    }

    // 1. Генерируем индексы в векторе g_splatVBO.indices на CPU
    // LogRenderer("DEBUG: Generating %zu indices for EBO...", g_splatSortIndices.size() * 6); // Слишком часто

    g_splatVBO.indices.clear(); // Очищаем старые CPU-индексы
    g_splatVBO.indices.reserve(g_splatSortIndices.size() * 6); // Резервируем память для производительности

    for (GLuint splatIndex : g_splatSortIndices) {
        // Проверка на валидность индекса сплэта
        if (splatIndex >= g_splats.size()) {
            // LogRenderer("ERROR UpdateSplatEBO: Invalid splatIndex %u (max: %zu)", splatIndex, g_splats.size() - 1); // Раскомментируй для отладки
            continue; // Пропускаем невалидный индекс
        }

        GLuint baseVertexIndex = splatIndex * 4; // Индекс первой вершины для этого сплэта в VBO

        // Проверка на валидность индекса вершины
        if (baseVertexIndex + 3 >= g_splatVBO.vertices.size()) {
            // LogRenderer("ERROR UpdateSplatEBO: Invalid baseVertexIndex %u (max: %zu)", baseVertexIndex, g_splatVBO.vertices.size() - 1); // Раскомментируй для отладки
            continue; // Пропускаем, если вершины для этого сплэта отсутствуют
        }

        // Добавляем 6 индексов для двух треугольников квада
        g_splatVBO.indices.push_back(baseVertexIndex + 0);
        g_splatVBO.indices.push_back(baseVertexIndex + 1);
        g_splatVBO.indices.push_back(baseVertexIndex + 2);

        g_splatVBO.indices.push_back(baseVertexIndex + 0);
        g_splatVBO.indices.push_back(baseVertexIndex + 2);
        g_splatVBO.indices.push_back(baseVertexIndex + 3);
    }

    // 2. Загружаем сгенерированные индексы в GPU одним вызовом glBufferData
    // LogRenderer("DEBUG: Uploading %zu indices (%zu bytes) to EBO (ID: %u)...", g_splatVBO.indices.size(), g_splatVBO.indices.size() * sizeof(GLuint), g_splatVBO.ebo); // Слишком часто

    glBindVertexArray(g_splatVBO.vao); // Привязываем VAO
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g_splatVBO.ebo); // Привязываем наш EBO

    // Используем glBufferData для загрузки данных. GL_DYNAMIC_DRAW - подсказка драйверу, что данные будут часто меняться.
    glBufferData(GL_ELEMENT_ARRAY_BUFFER,           // Цель - индексный буфер
        g_splatVBO.indices.size() * sizeof(GLuint), // Размер данных в байтах
        g_splatVBO.indices.data(),          // Указатель на данные (из вектора)
        GL_DYNAMIC_DRAW);                   // Подсказка использования

    glBindVertexArray(0); // Отвязываем VAO (EBO останется привязанным к VAO)

    // Проверка на ошибки OpenGL после загрузки
    GLenum err = glGetError();
    if (err != GL_NO_ERROR) {
        LogRenderer("ERROR: OpenGL Error 0x%x after EBO update (glBufferData)! Check buffer size and memory.", err);
    }
    // LogRenderer("DEBUG: UpdateSplatEBO finished."); // Слишком часто
}

// --- Инициализация и Вспомогательные Функции ---
static void InitializeDefaultSplats() { LogRenderer("Init default splat."); g_splats.clear(); GaussSplat s; s.position[0] = 0; s.position[1] = 0; s.position[2] = 0; s.color[0] = 0; s.color[1] = 0; s.color[2] = 1; s.color[3] = 0.8f; s.scale[0] = 50; s.scale[1] = 50; s.rotation[0] = 1; s.rotation[1] = 0; s.rotation[2] = 0; s.rotation[3] = 0; g_splats.push_back(s); g_splatVBO.needsUpdate = true; }
static void EnsureTextureInitialized() { if (!g_textureInitialized) { g_gaussTexture = CreateGaussianTexture(64, 0.3f); g_textureInitialized = true; if (g_gaussTexture == 0) LogRenderer("ERR: Gauss texture failed."); } }
static void LoadHookFunctions() { if (GetMatrixByLocation) return; HMODULE dll = GetModuleHandleA("SketchUpOverlayBridge.dll"); if (dll) { GetMatrixByLocation = (PFN_GET_MATRIX_BY_LOC)GetProcAddress(dll, "GetMatrixByLocation"); if (GetMatrixByLocation) LogRenderer("Found GetMatrixByLocation."); else LogRenderer("ERR: GetMatrixByLocation not found."); } else LogRenderer("ERR: Overlay bridge DLL not found."); }
static void ExtractCameraPosition(const float* view, float* pos) { float inv[16]; inv[0] = view[0]; inv[1] = view[4]; inv[2] = view[8]; inv[4] = view[1]; inv[5] = view[5]; inv[6] = view[9]; inv[8] = view[2]; inv[9] = view[6]; inv[10] = view[10]; inv[12] = -(inv[0] * view[12] + inv[4] * view[13] + inv[8] * view[14]); inv[13] = -(inv[1] * view[12] + inv[5] * view[13] + inv[9] * view[14]); inv[14] = -(inv[2] * view[12] + inv[6] * view[13] + inv[10] * view[14]); pos[0] = inv[12]; pos[1] = inv[13]; pos[2] = inv[14]; }
static void ExtractViewDirection(const float* view, float* dir) { dir[0] = -view[2]; dir[1] = -view[6]; dir[2] = -view[10]; float l = sqrt(dir[0] * dir[0] + dir[1] * dir[1] + dir[2] * dir[2]); if (l > 1e-5f) { dir[0] /= l;dir[1] /= l;dir[2] /= l; } else { dir[0] = 0;dir[1] = 0;dir[2] = -1; } }
static void ExtractCameraUp(const float* view, float* up) { up[0] = view[1]; up[1] = view[5]; up[2] = view[9]; float l = sqrt(up[0] * up[0] + up[1] * up[1] + up[2] * up[2]); if (l > 1e-5f) { up[0] /= l;up[1] /= l;up[2] /= l; } else { up[0] = 0;up[1] = 1;up[2] = 0; } }
static void CrossProduct(const float* v1, const float* v2, float* r) { r[0] = v1[1] * v2[2] - v1[2] * v2[1]; r[1] = v1[2] * v2[0] - v1[0] * v2[2]; r[2] = v1[0] * v2[1] - v1[1] * v2[0]; }

// --- Экспортируемые функции управления данными ---
extern "C" EXPORT void SetPointCloud(const double* points_in, int count) { LogRenderer("SetPointCloud called."); if (!points_in || count <= 0)return; LoadHookFunctions(); g_points.clear(); g_points.reserve(count * 6); for (int i = 0;i < count;++i) { g_points.push_back((float)points_in[i * 6 + 0]); g_points.push_back((float)points_in[i * 6 + 1]); g_points.push_back((float)points_in[i * 6 + 2]); g_points.push_back((float)points_in[i * 6 + 3] / 255.f); g_points.push_back((float)points_in[i * 6 + 4] / 255.f); g_points.push_back((float)points_in[i * 6 + 5] / 255.f); } g_dataReady = true; }
extern "C" EXPORT void AddSplat(float x, float y, float z, float r, float g, float b, float a, float scaleX, float scaleY, float rotation, bool rotateVertical) { GaussSplat s; s.position[0] = x;s.position[1] = y;s.position[2] = z; s.color[0] = r;s.color[1] = g;s.color[2] = b;s.color[3] = a; s.scale[0] = scaleX;s.scale[1] = scaleY; float an = rotation * M_PI / 180.f, ha = an * .5f, sn = sin(ha), cn = cos(ha); s.rotation[0] = cn; if (rotateVertical) { s.rotation[1] = sn;s.rotation[2] = 0;s.rotation[3] = 0; } else { s.rotation[1] = 0;s.rotation[2] = 0;s.rotation[3] = sn; } g_splats.push_back(s); g_splatVBO.needsUpdate = true; }
extern "C" EXPORT void AddSplatWithQuaternion(float x, float y, float z, float r, float g, float b, float a, float scaleX, float scaleY, float qw, float qx, float qy, float qz) { GaussSplat s; s.position[0] = x;s.position[1] = y;s.position[2] = z; s.color[0] = r;s.color[1] = g;s.color[2] = b;s.color[3] = std::max(0.01f, std::min(a, 1.f)); s.scale[0] = scaleX;s.scale[1] = scaleY; float n = sqrt(qw * qw + qx * qx + qy * qy + qz * qz); if (n > 1e-5f) { s.rotation[0] = qw / n;s.rotation[1] = qx / n;s.rotation[2] = qy / n;s.rotation[3] = qz / n; } else { s.rotation[0] = 1;s.rotation[1] = 0;s.rotation[2] = 0;s.rotation[3] = 0; } g_splats.push_back(s); g_splatVBO.needsUpdate = true; }
extern "C" EXPORT void ClearSplats() { LogRenderer("ClearSplats called."); g_splats.clear(); g_splatSortIndices.clear(); g_splatVBO.needsUpdate = true; }
extern "C" EXPORT void SetSplatSortingMode(SplatSortingMode mode) { if (mode >= 0 && mode <= 5) { g_sortingMode = mode; LogRenderer("Sort mode set %d.", mode); } else LogRenderer("Invalid sort mode %d.", mode); }

// --- Рендеринг ---
static void RenderSingleSplatIM(const GaussSplat& splat, const float* viewMatrix, const float* projectionMatrix) { glPushMatrix(); glTranslatef(splat.position[0], splat.position[1], splat.position[2]); float rm[16]; QuaternionToMatrix(splat.rotation[0], splat.rotation[1], splat.rotation[2], splat.rotation[3], rm); glMultMatrixf(rm); glScalef(splat.scale[0], splat.scale[1], 1.0f); glColor4f(splat.color[0], splat.color[1], splat.color[2], splat.color[3]); float qs = 1.0f; glBegin(GL_QUADS); glTexCoord2f(0, 0);glVertex3f(-qs, -qs, 0); glTexCoord2f(1, 0);glVertex3f(qs, -qs, 0); glTexCoord2f(1, 1);glVertex3f(qs, qs, 0); glTexCoord2f(0, 1);glVertex3f(-qs, qs, 0); glEnd(); glPopMatrix(); }
static bool CheckGLCapabilities() { GLenum e = glewInit(); if (e != GLEW_OK) { LogRenderer("GLEW failed:%s", glewGetErrorString(e));return false; } if (!GLEW_VERSION_2_0 || !GLEW_ARB_vertex_buffer_object) { LogRenderer("WARN No VBO/Shader support");return false; } LogRenderer("OpenGL VBO/Shader support detected."); return true; }

// --- Основная функция рендеринга ---
extern "C" EXPORT void renderPointCloud() {
    // LogRenderer("DEBUG: renderPointCloud ENTER"); // Слишком часто
    LoadHookFunctions(); if (!GetMatrixByLocation) { LogRenderer("ERROR: GetMatrixByLocation is NULL, cannot proceed."); return; }

    static bool firstCall = true; static bool useVBO = false;
    if (firstCall) {
        LogRenderer("DEBUG: First call to renderPointCloud.");
        EnsureTextureInitialized();
        useVBO = CheckGLCapabilities();
        if (useVBO) InitializeSplatVBO();
        if (g_splats.empty()) InitializeDefaultSplats();
        firstCall = false;
    }
    if (useVBO && !g_splatVBO.initialized) {
        LogRenderer("DEBUG: Attempting VBO re-initialization...");
        InitializeSplatVBO();
        if (!g_splatVBO.initialized) { LogRenderer("WARN: VBO re-init failed. Disabling VBO path."); useVBO = false; }
    }

    if (g_splats.empty()) { return; } // Нечего рендерить

    if (g_splatVBO.needsUpdate) {
        LogRenderer("DEBUG: renderPointCloud - needsUpdate=true, calling UpdateSplatVBOVertices()");
        UpdateSplatVBOVertices();
        if (!g_splats.empty()) { LogRenderer("DEBUG: renderPointCloud - VBO updated, calling UpdateSplatEBO() for initial order."); UpdateSplatEBO(); }
    }

    float viewMatrix[16]; float mvpMatrix[16]; float projectionMatrix[16] = { 0 }; // Инициализируем нулями
    int viewLoc = 14, mvpLoc = 25, projLoc = -1; // !!! Установить правильный projLoc, если найдешь !!!
    bool hasView = GetMatrixByLocation(viewLoc, viewMatrix);
    bool hasMVP = GetMatrixByLocation(mvpLoc, mvpMatrix);
    bool hasProj = (projLoc >= 0) && GetMatrixByLocation(projLoc, projectionMatrix);

    if (!hasView || !hasMVP) { LogRenderer("WARN: Missing View(loc%d)=%d or MVP(loc%d)=%d matrix. Skipping render.", viewLoc, hasView, mvpLoc, hasMVP); return; }

    bool sorting_done = false;
    if (hasView) {
        if (g_splatSortIndices.size() != g_splats.size()) {
            g_splatSortIndices.resize(g_splats.size()); std::iota(g_splatSortIndices.begin(), g_splatSortIndices.end(), 0);
            LogRenderer("DEBUG: Resized sort indices to %zu in sort block.", g_splatSortIndices.size());
            sorting_done = true; // Нужно обновить EBO после изменения размера
        }

        // Получаем данные камеры
        float camPos[3], viewDir[3], camUp[3];
        ExtractCameraPosition(viewMatrix, camPos);
        ExtractViewDirection(viewMatrix, viewDir);
        ExtractCameraUp(viewMatrix, camUp);

        // Проверяем, нужно ли выполнять сортировку
        bool needSorting = false;

        // Проверяем, значительно ли изменилась камера
        float posDistSq =
            (camPos[0] - g_lastCamPos[0]) * (camPos[0] - g_lastCamPos[0]) +
            (camPos[1] - g_lastCamPos[1]) * (camPos[1] - g_lastCamPos[1]) +
            (camPos[2] - g_lastCamPos[2]) * (camPos[2] - g_lastCamPos[2]);

        float dirDiff =
            (viewDir[0] - g_lastViewDir[0]) * (viewDir[0] - g_lastViewDir[0]) +
            (viewDir[1] - g_lastViewDir[1]) * (viewDir[1] - g_lastViewDir[1]) +
            (viewDir[2] - g_lastViewDir[2]) * (viewDir[2] - g_lastViewDir[2]);

        // Увеличиваем счетчик кадров
        g_framesSinceLastSort++;

        // Если камера сдвинулась более чем на 0.01 единиц или поворот более 0.001 рад
        // или прошло достаточно кадров - выполняем сортировку
        if (posDistSq > 0.0001f || dirDiff > 0.000001f || g_framesSinceLastSort >= SORT_EVERY_N_FRAMES) {
            needSorting = true;

            // Обновляем последнюю позицию камеры
            memcpy(g_lastCamPos, camPos, 3 * sizeof(float));
            memcpy(g_lastViewDir, viewDir, 3 * sizeof(float));

            // Сбрасываем счетчик кадров
            g_framesSinceLastSort = 0;
        }

        // Если нужна сортировка, выполняем её
        if (needSorting) {
            LogRenderer("DEBUG: Sorting %zu indices for view changes or frame limit...", g_splats.size());

            // Предварительное вычисление данных для сортировки
            g_splatSortCache.resize(g_splats.size());

            // Заполняем кэш данными для сортировки
            for (size_t i = 0; i < g_splats.size(); ++i) {
                const GaussSplat& splat = g_splats[i];
                SplatSortData& sortData = g_splatSortCache[i];

                sortData.index = i;

                // Вычисление вектора от камеры к сплату
                float vec[3] = {
                    splat.position[0] - camPos[0],
                    splat.position[1] - camPos[1],
                    splat.position[2] - camPos[2]
                };

                // Проекция вектора на направление взгляда
                sortData.projValue = vec[0] * viewDir[0] + vec[1] * viewDir[1] + vec[2] * viewDir[2];

                // Квадрат расстояния
                sortData.distanceSquared = vec[0] * vec[0] + vec[1] * vec[1] + vec[2] * vec[2];

                // Направлен ли сплат от нас
                sortData.isBackfacing = (sortData.projValue <= 0);

                // Вычисление ключа сортировки
                if (!sortData.isBackfacing) {
                    // Для передних граней: проекция + небольшая доля расстояния
                    float distanceFactor = sortData.distanceSquared * 0.001f;
                    // Защита от деления на очень маленькие числа
                    if (sortData.projValue > 0.001f) {
                        distanceFactor /= sortData.projValue;
                    }
                    sortData.sortKey = sortData.projValue + distanceFactor;
                }
                else {
                    // Для задних граней: только по расстоянию
                    sortData.sortKey = sortData.distanceSquared;
                }
            }

            // Быстрая сортировка по предварительно вычисленным значениям
            std::sort(g_splatSortCache.begin(), g_splatSortCache.end(),
                [](const SplatSortData& a, const SplatSortData& b) -> bool {
                    // Сначала разделяем по направлению (forward/backward)
                    if (a.isBackfacing != b.isBackfacing) return a.isBackfacing;

                    // Если оба направлены от нас, сортируем по квадрату расстояния (дальние первыми)
                    if (a.isBackfacing) return a.distanceSquared > b.distanceSquared;

                    // Если оба направлены к нам, используем предварительно вычисленный ключ
                    return a.sortKey > b.sortKey;
                });

            // Обновляем индексы сортировки
            for (size_t i = 0; i < g_splatSortCache.size(); ++i) {
                g_splatSortIndices[i] = g_splatSortCache[i].index;
            }

            sorting_done = true;
        }
    }
    else {
        LogRenderer("DEBUG: Skipping sorting (no view matrix).");
        if (g_splatVBO.indices.size() != g_splatSortIndices.size() * 6) { LogRenderer("DEBUG: EBO needs update (default order)."); UpdateSplatEBO(); }
    }

    if (sorting_done) { /* LogRenderer("DEBUG: Calling UpdateSplatEBO() after sort.");*/ UpdateSplatEBO(); } // Обновляем EBO *после* сортировки

    // --- Рендеринг ---
    GLint oldProg = 0; glGetIntegerv(GL_CURRENT_PROGRAM, &oldProg); GLboolean blendEn = glIsEnabled(GL_BLEND); GLint oldBlendSrcRGB, oldBlendDstRGB, oldBlendSrcAlpha, oldBlendDstAlpha; glGetIntegerv(GL_BLEND_SRC_RGB, &oldBlendSrcRGB); glGetIntegerv(GL_BLEND_DST_RGB, &oldBlendDstRGB); glGetIntegerv(GL_BLEND_SRC_ALPHA, &oldBlendSrcAlpha); glGetIntegerv(GL_BLEND_DST_ALPHA, &oldBlendDstAlpha); GLboolean depthEn = glIsEnabled(GL_DEPTH_TEST); GLboolean depthMask; glGetBooleanv(GL_DEPTH_WRITEMASK, &depthMask); GLint oldDepthFunc; glGetIntegerv(GL_DEPTH_FUNC, &oldDepthFunc); GLboolean cullEn = glIsEnabled(GL_CULL_FACE); GLint oldCullMode; glGetIntegerv(GL_CULL_FACE_MODE, &oldCullMode); GLboolean texEn = glIsEnabled(GL_TEXTURE_2D); GLint oldActiveTex = 0; glGetIntegerv(GL_ACTIVE_TEXTURE, &oldActiveTex); GLint oldTexBind = 0; glGetIntegerv(GL_TEXTURE_BINDING_2D, &oldTexBind);

    glEnable(GL_DEPTH_TEST); glDepthFunc(GL_LEQUAL); glDepthMask(GL_FALSE);
    glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
    glDisable(GL_CULL_FACE);

    if (useVBO && g_splatVBO.initialized && g_splatShader != 0) {
        if (g_splatVBO.ebo == 0) { LogRenderer("ERROR: EBO is 0, cannot draw!"); }
        else {
            GLint eboSizeCheck = 0; glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, g_splatVBO.ebo); glGetBufferParameteriv(GL_ELEMENT_ARRAY_BUFFER, GL_BUFFER_SIZE, &eboSizeCheck); glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0);
            if (eboSizeCheck == 0 && g_splatSortIndices.size() > 0) { LogRenderer("WARN: EBO size is 0 but we have sort indices! Skipping draw."); }
            else if (eboSizeCheck > 0) {
                // LogRenderer("DEBUG: Rendering %d indices using VBO...", eboSizeCheck / sizeof(GLuint)); // Слишком часто
                glUseProgram(g_splatShader);
                GLint mvpLoc = glGetUniformLocation(g_splatShader, "uMVP"); if (mvpLoc != -1) glUniformMatrix4fv(mvpLoc, 1, GL_FALSE, mvpMatrix); else LogRenderer("ERR uMVP loc");
                GLint texLoc = glGetUniformLocation(g_splatShader, "uTexture"); if (texLoc != -1 && g_gaussTexture != 0) { glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, g_gaussTexture); glUniform1i(texLoc, 0); }
                else LogRenderer("WARN uTex loc/tex (ID:%u)", g_gaussTexture);
                glBindVertexArray(g_splatVBO.vao);
                glDrawElements(GL_TRIANGLES, (GLsizei)(eboSizeCheck / sizeof(GLuint)), GL_UNSIGNED_INT, 0);
                glBindVertexArray(0);
                if (texLoc != -1) { glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, 0); }
            }
        }
    }
    else if (!useVBO && hasView && hasProj) { // Проверяем hasProj для IM
        LogRenderer("DEBUG: Using Immediate Mode fallback for %zu splats.", g_splatSortIndices.size());
        glUseProgram(0);
        glEnable(GL_TEXTURE_2D); glActiveTexture(GL_TEXTURE0); glBindTexture(GL_TEXTURE_2D, g_gaussTexture);
        glMatrixMode(GL_PROJECTION); glLoadMatrixf(projectionMatrix);
        glMatrixMode(GL_MODELVIEW); glLoadMatrixf(viewMatrix);
        for (GLuint splatIndex : g_splatSortIndices) { if (splatIndex < g_splats.size()) RenderSingleSplatIM(g_splats[splatIndex], viewMatrix, projectionMatrix); } // Добавил проверку индекса
        glBindTexture(GL_TEXTURE_2D, 0);
        if (!texEn) glDisable(GL_TEXTURE_2D);
    }
    else {
        if (!useVBO) LogRenderer("DEBUG: Skipping render (VBO disabled or IM requirements not met [view:%d, proj:%d]).", hasView, hasProj);
        else if (!g_splatVBO.initialized) LogRenderer("DEBUG: Skipping render (VBO not initialized).");
        else if (g_splatShader == 0) LogRenderer("DEBUG: Skipping render (Shader not loaded).");
        else LogRenderer("DEBUG: Skipping render (Unknown reason).");
    }

    glUseProgram(oldProg); if (blendEn) glBlendFuncSeparate(oldBlendSrcRGB, oldBlendDstRGB, oldBlendSrcAlpha, oldBlendDstAlpha); else glDisable(GL_BLEND); if (depthEn) glDepthFunc(oldDepthFunc); else glDisable(GL_DEPTH_TEST); glDepthMask(depthMask); if (cullEn) { glEnable(GL_CULL_FACE); glCullFace(oldCullMode); }
    else glDisable(GL_CULL_FACE); glActiveTexture(oldActiveTex); glBindTexture(GL_TEXTURE_2D, oldTexBind); if (!texEn && oldTexBind == 0) glDisable(GL_TEXTURE_2D); else if (texEn) glEnable(GL_TEXTURE_2D);

    GLenum renderErr = glGetError(); if (renderErr != GL_NO_ERROR) LogRenderer("GL Error after render: 0x%x", renderErr);
    // LogRenderer("DEBUG: renderPointCloud EXIT"); // Слишком часто
}


// --- Функции загрузки PLY ---
void ConvertColor(float dc0, float dc1, float dc2, float& r, float& g, float& b) { auto sig = [](float x) {return 1.f / (1.f + exp(-x));}; r = sig(dc0); g = sig(dc1); b = sig(dc2); }
void ConvertScale(float s0, float s1, float& sx, float& sy) { float rx = exp(s0); float ry = exp(s1); float sf = 45.f; sx = rx * sf; sy = ry * sf; }
extern "C" EXPORT void LoadSplatsFromPLY(const char* filename) { LogRenderer("Loading PLY: %s", filename); HMODULE plyDLL = LoadLibraryA("PlyImporter.dll"); if (!plyDLL) { LogRenderer("ERR Load PlyImporter %d", GetLastError()); return; } typedef int(*LPD)(const char*, PLYGaussianPoint**); typedef void(*FPD)(PLYGaussianPoint*); LPD load = (LPD)GetProcAddress(plyDLL, "LoadPLYData"); FPD free = (FPD)GetProcAddress(plyDLL, "FreePLYData"); if (!load || !free) { LogRenderer("ERR Find funcs PlyImporter"); FreeLibrary(plyDLL); return; } PLYGaussianPoint* pts = nullptr; int cnt = load(filename, &pts); if (cnt <= 0 || !pts) { LogRenderer("ERR: PLY load failed (count=%d).", cnt); } else { LogRenderer("Loaded %d pts.", cnt); AddSplatsFromPLYData(pts, cnt); } if (pts) free(pts); if (plyDLL) FreeLibrary(plyDLL); LogRenderer("PLY load finished."); }
extern "C" EXPORT void AddSplatsFromPLYData(PLYGaussianPoint* points, int count) {
    LogRenderer("Adding %d splats from PLY data...", count); ClearSplats(); g_splats.reserve(count);
    float scaleDistance = 20.0f; float opacityMultiplier = 3.0f; int addedCount = 0;
    for (int i = 0; i < count; ++i) {
        float x = points[i].position[0] * scaleDistance; float y = -points[i].position[1] * scaleDistance; float z = points[i].position[2] * scaleDistance;
        float r, g, b; ConvertColor(points[i].color[0], points[i].color[1], points[i].color[2], r, g, b);
        float scaleX, scaleY; ConvertScale(points[i].scale[0], points[i].scale[1], scaleX, scaleY);
        if (scaleX <= 0 || scaleY <= 0 || !std::isfinite(scaleX) || !std::isfinite(scaleY)) continue;
        float opacity = 1.0f / (1.0f + exp(-points[i].opacity)); opacity *= opacityMultiplier; opacity = std::max(0.01f, std::min(opacity, 1.0f));
        float qw = points[i].rotation[0]; float qx = points[i].rotation[1]; float qy = points[i].rotation[2]; float qz = points[i].rotation[3];
        AddSplatWithQuaternion(x, y, z, r, g, b, opacity, scaleX, scaleY, qw, qx, qy, qz); addedCount++;
    } g_splatVBO.needsUpdate = true; LogRenderer("Added %d splats. Total: %zu.", addedCount, g_splats.size());
}

// --- Точка входа DLL и очистка ---
BOOL APIENTRY DllMain(HMODULE hModule, DWORD ul_reason_for_call, LPVOID lpReserved) {
    switch (ul_reason_for_call) {
    case DLL_PROCESS_ATTACH: LogRenderer("DLL_PROCESS_ATTACH"); LoadHookFunctions(); break;
    case DLL_PROCESS_DETACH: LogRenderer("DLL_PROCESS_DETACH"); /* Очистка OpenGL закомментирована */ break;
    case DLL_THREAD_ATTACH: break;
    case DLL_THREAD_DETACH: break;
    }
    return TRUE;
}
