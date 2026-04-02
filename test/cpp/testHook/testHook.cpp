// SketchUpOverlayBridge.cpp – Overlay‑плагин, замена старого OpenGL‑хука
// ---------------------------------------------------------------------------
// • Компилируйте как DLL x64; скопируйте в
//   %ProgramData%\SketchUp\SketchUp 2024\SketchUp\Plugins\Overlays\
// • Берёт View/Projection через Overlay‑API (Classic и New Engine).
// • Экспортирует GetMatrixByLocation(14/15) — testRendererDLL.dll менять не надо.
// • Загружает testRendererDLL.dll из той же папки и вызывает renderPointCloud().
// ---------------------------------------------------------------------------

#include <windows.h>
#include <cstdio>
#include <cstring>

// Порядок важен: сначала базовый заголовок SDK, потом Overlay API.
#include <SketchUpAPI/sketchup.h>
#include <SketchUpAPI/application/overlay.h>

// ────────────────────────────────────
// Глобальные буферы для матриц (column‑major, float)
static float g_view[16] = { 0 };
static float g_proj[16] = { 0 };

// ────────────────────────────────────
// Функция из renderer‑DLL
using PFN_RENDER_POINTCLOUD = void (*)();
static PFN_RENDER_POINTCLOUD g_render = nullptr;

// ────────────────────────────────────
// beginFrame – главный коллбек Overlay‑API
static SUResult beginFrame(void*, /*user*/
    SUOverlayInputHandlerRef,
    const SUBeginFrameInfo* info)
{
    // Копируем double → float
    for (int i = 0; i < 16; ++i) {
        g_view[i] = static_cast<float>(info->view_matrix[i]);
        g_proj[i] = static_cast<float>(info->projection_matrix[i]);
    }

    if (g_render) g_render();
    return SU_ERROR_NONE;
}

// ────────────────────────────────────
// API для rendererDLL — совместимо со старым OpenGL‑хуком
extern "C" __declspec(dllexport)
bool GetMatrixByLocation(int loc, float* out)
{
    if (!out) return false;
    if (loc == 14) { memcpy(out, g_view, 16 * sizeof(float)); return true; }
    if (loc == 15) { memcpy(out, g_proj, 16 * sizeof(float)); return true; }
    return false;
}

// ────────────────────────────────────
// Bootstrap для SketchUp: регистрация Overlay‑интерфейса
extern "C" __declspec(dllexport)
SUResult SUGetPluginOverlayInterface(SUOverlayInterface* iface)
{
    if (!iface) return SU_ERROR_NULL_POINTER_OUTPUT;
    memset(iface, 0, sizeof(*iface));
    iface->version = 1;
    iface->begin_frame = beginFrame;
    return SU_ERROR_NONE;
}

// ────────────────────────────────────
// dllMain: при загрузке ищем testRendererDLL.dll и его функцию
BOOL APIENTRY DllMain(HMODULE hMod, DWORD reason, LPVOID)
{
    if (reason == DLL_PROCESS_ATTACH) {
        char path[MAX_PATH] = {};
        GetModuleFileNameA(hMod, path, MAX_PATH);
        char* p = strrchr(path, '\\');
        if (p) *(p + 1) = '\0';           // оставляем только папку
        strcat_s(path, MAX_PATH, "testRendererDLL.dll");

        HMODULE hR = LoadLibraryA(path);
        if (!hR) hR = LoadLibraryA("testRendererDLL.dll");

        if (hR) {
            g_render = reinterpret_cast<PFN_RENDER_POINTCLOUD>(
                GetProcAddress(hR, "renderPointCloud"));
        }
    }
    return TRUE;
}
