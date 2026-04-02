#include "PointCloudHook.h"
#include <windows.h>
#include <GL/glew.h>
#include <GL/gl.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cmath>
#include "MinHook.h"

// Типы оригинальных функций OpenGL
typedef void (APIENTRY* PFNGLDRAWARRAYS)(GLenum mode, GLint first, GLsizei count);
static PFNGLDRAWARRAYS g_origDrawArrays = nullptr;

typedef void (APIENTRY* PFNGLUNIFORMMATRIX4FV)(GLint location, GLsizei count, GLboolean transpose, const GLfloat* value);
static PFNGLUNIFORMMATRIX4FV g_origUniformMatrix4fv = nullptr;

typedef void (APIENTRY* PFNGLUSEPROGRAM)(GLuint program);
static PFNGLUSEPROGRAM g_origUseProgram = nullptr;

// Указатели на функции рендерера
typedef void (*PFN_RENDER_POINTCLOUD)(void);
typedef void (*PFN_SETPOINTCLOUD)(const double*, int);
static PFN_RENDER_POINTCLOUD g_renderPointCloud = nullptr;
static PFN_SETPOINTCLOUD g_setPointCloud = nullptr;

// Глобальные переменные состояния
static GLuint g_currentProgram = 0;
static bool g_matrixValid = false;
static float g_currentModelview[16] = { 1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1 };
static float g_currentProjection[16] = { 1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1 };

// Функция логирования
static void LogMessage(const char* format, ...) {
    char buffer[1024];
    va_list args;
    va_start(args, format);
    vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);
    OutputDebugStringA(buffer);
}

// Печать матрицы
static void printMatrix(const char* label, const float* matrix) {
    LogMessage("[Hook] %s matrix:\n", label);
    for (int i = 0; i < 4; i++) {
        LogMessage("[%.2f %.2f %.2f %.2f]\n",
            matrix[i * 4 + 0], matrix[i * 4 + 1],
            matrix[i * 4 + 2], matrix[i * 4 + 3]);
    }
}

// Хук для glUseProgram
void APIENTRY My_glUseProgram(GLuint program) {
    g_currentProgram = program;
    LogMessage("[Hook] glUseProgram called with program=%u\n", program);
    if (g_origUseProgram)
        g_origUseProgram(program);
}

// Хук для glUniformMatrix4fv
void APIENTRY My_glUniformMatrix4fv(GLint location, GLsizei count,
    GLboolean transpose, const GLfloat* value) {
    if (g_currentProgram > 0) {
        char name[256];
        GLsizei length;
        GLint size;
        GLenum type;

        glGetActiveUniform(g_currentProgram, location,
            sizeof(name), &length, &size, &type, name);

        LogMessage("[Hook] glUniformMatrix4fv for uniform '%s' at location %d\n",
            name, location);
        printMatrix(name, value);

        // Проверяем имена и локации
        if ((location == 14 && strcmp(name, "u_ModelViewMatrix") == 0) ||
            (location == 24 && strcmp(name, "@") == 0)) {
            memcpy(g_currentModelview, value, 16 * sizeof(float));
            LogMessage("[Hook] Captured ModelView matrix!\n");
            g_matrixValid = true;
        }
        else if ((location == 15 && strcmp(name, "u_ModelViewProjectionMatrix") == 0) ||
            (location == 25 && strcmp(name, "@") == 0)) {
            memcpy(g_currentProjection, value, 16 * sizeof(float));
            LogMessage("[Hook] Captured Projection matrix!\n");
            g_matrixValid = true;
        }

        if (g_matrixValid) {
            LogMessage("[Hook] Current matrices state:\n");
            printMatrix("ModelView", g_currentModelview);
            printMatrix("Projection", g_currentProjection);
        }
    }

    if (g_origUniformMatrix4fv)
        g_origUniformMatrix4fv(location, count, transpose, value);
}

// Хук для glDrawArrays
void APIENTRY My_glDrawArrays(GLenum mode, GLint first, GLsizei count) {
    static bool inHook = false;
    if (inHook) {
        if (g_origDrawArrays)
            g_origDrawArrays(mode, first, count);
        return;
    }

    inHook = true;
    LogMessage("[Hook] My_glDrawArrays called: mode=0x%X, first=%d, count=%d\n",
        mode, first, count);

    if (mode == 0x6 && g_matrixValid) {
        // Сохраняем состояние OpenGL
        GLint oldProgram;
        glGetIntegerv(GL_CURRENT_PROGRAM, &oldProgram);

        LogMessage("[Hook] GL_POINTS detected and matrices are valid!\n");
        LogMessage("[Hook] Calling custom renderer...\n");
        if (g_renderPointCloud) {
            g_renderPointCloud();
            LogMessage("[Hook] Custom renderer finished\n");
        }
        else {
            LogMessage("[Hook] WARNING: g_renderPointCloud is NULL!\n");
        }

        // Восстанавливаем состояние
        glUseProgram(oldProgram);
    }

    if (g_origDrawArrays)
        g_origDrawArrays(mode, first, count);

    inHook = false;
}

// Функция для установки хука через MinHook
static bool HookOpenGLFunction(const char* funcName, LPVOID detourFunc, LPVOID* origFunc) {
    HMODULE hOpengl = GetModuleHandleA("opengl32.dll");
    if (!hOpengl) {
        LogMessage("[Hook] GetModuleHandleA(opengl32.dll) failed.\n");
        return false;
    }

    FARPROC pTarget = GetProcAddress(hOpengl, funcName);
    if (!pTarget) {
        LogMessage("[Hook] GetProcAddress(%s) failed, trying wglGetProcAddress...\n", funcName);
        pTarget = (FARPROC)wglGetProcAddress(funcName);
        if (!pTarget) {
            LogMessage("[Hook] wglGetProcAddress(%s) also failed.\n", funcName);
            return false;
        }
    }

    LogMessage("[Hook] Found %s at %p\n", funcName, pTarget);

    MH_STATUS st = MH_CreateHook(pTarget, detourFunc, origFunc);
    if (st != MH_OK) {
        LogMessage("[Hook] MH_CreateHook(%s) failed with status=%d\n", funcName, st);
        return false;
    }

    st = MH_EnableHook(pTarget);
    if (st != MH_OK) {
        LogMessage("[Hook] MH_EnableHook(%s) failed with status=%d\n", funcName, st);
        return false;
    }

    LogMessage("[Hook] Successfully hooked %s\n", funcName);
    return true;
}

// Загрузка DLL рендерера
static void LoadRendererDLL() {
    char path[MAX_PATH] = { 0 };
    HMODULE hThis = GetModuleHandleA("PointCloudHookDLL.dll");
    if (!hThis) {
        LogMessage("[Hook] GetModuleHandleA(PointCloudHookDLL.dll) failed.\n");
        return;
    }

    if (!GetModuleFileNameA(hThis, path, MAX_PATH)) {
        LogMessage("[Hook] GetModuleFileNameA failed.\n");
        return;
    }

    char* slash = strrchr(path, '\\');
    if (slash)
        *(slash + 1) = '\0';
    strcat_s(path, MAX_PATH, "PointCloudRendererDLL.dll");

    LogMessage("[Hook] Loading renderer from: %s\n", path);

    HMODULE hRenderer = LoadLibraryA(path);
    if (!hRenderer) {
        LogMessage("[Hook] Failed to load renderer DLL. Error: %d\n", GetLastError());
        return;
    }

    g_renderPointCloud = (PFN_RENDER_POINTCLOUD)GetProcAddress(hRenderer, "renderPointCloud");
    g_setPointCloud = (PFN_SETPOINTCLOUD)GetProcAddress(hRenderer, "SetPointCloud");

    if (g_renderPointCloud && g_setPointCloud) {
        LogMessage("[Hook] Renderer functions loaded successfully.\n");
    }
    else {
        LogMessage("[Hook] WARNING: Some renderer functions not found.\n");
        if (!g_renderPointCloud) LogMessage("[Hook] renderPointCloud not found\n");
        if (!g_setPointCloud) LogMessage("[Hook] SetPointCloud not found\n");
    }
}

// Установка всех хуков
static bool SetupAllHooks() {
    // Инициализация GLEW
    GLenum err = glewInit();
    if (err != GLEW_OK) {
        LogMessage("[Hook] GLEW initialization failed: %s\n", glewGetErrorString(err));
        return false;
    }
    LogMessage("[Hook] GLEW initialized successfully.\n");

    MH_STATUS st = MH_Initialize();
    if (st != MH_OK && st != MH_ERROR_ALREADY_INITIALIZED) {
        LogMessage("[Hook] MH_Initialize failed with status=%d\n", st);
        return false;
    }

    bool ok = true;
    ok &= HookOpenGLFunction("glDrawArrays", (LPVOID)My_glDrawArrays, (LPVOID*)&g_origDrawArrays);
    ok &= HookOpenGLFunction("glUniformMatrix4fv", (LPVOID)My_glUniformMatrix4fv, (LPVOID*)&g_origUniformMatrix4fv);
    ok &= HookOpenGLFunction("glUseProgram", (LPVOID)My_glUseProgram, (LPVOID*)&g_origUseProgram);

    if (ok)
        LogMessage("[Hook] All hooks installed successfully.\n");
    else
        LogMessage("[Hook] Some hooks failed to install.\n");

    return ok;
}

// Экспортированные функции
extern "C" PCH_API void InstallAllHooks() {
    LogMessage("[Hook] InstallAllHooks called.\n");
    LoadRendererDLL();
    SetupAllHooks();
}

extern "C" PCH_API void SetPointCloudData(const double* points, int count) {
    LogMessage("[Hook] SetPointCloudData called with %d points.\n", count);
    if (g_setPointCloud) {
        g_setPointCloud(points, count);
        LogMessage("[Hook] Data sent to renderer.\n");
    }
    else {
        LogMessage("[Hook] ERROR: g_setPointCloud is NULL!\n");
    }
}

// Функция для передачи матриц в рендерер
extern "C" PCH_API bool GetCurrentMatrices(float* modelview, float* projection) {
    if (!g_matrixValid || !modelview || !projection) {
        return false;
    }
    memcpy(modelview, g_currentModelview, 16 * sizeof(float));
    memcpy(projection, g_currentProjection, 16 * sizeof(float));
    return true;
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD ul_reason_for_call, LPVOID lpReserved) {
    switch (ul_reason_for_call) {
    case DLL_PROCESS_ATTACH:
        DisableThreadLibraryCalls(hModule);
        LogMessage("[Hook] DLL_PROCESS_ATTACH\n");
        LoadRendererDLL();
        SetupAllHooks();
        break;

    case DLL_PROCESS_DETACH:
        LogMessage("[Hook] DLL_PROCESS_DETACH\n");
        MH_DisableHook(MH_ALL_HOOKS);
        MH_Uninitialize();
        break;
    }
    return TRUE;
}