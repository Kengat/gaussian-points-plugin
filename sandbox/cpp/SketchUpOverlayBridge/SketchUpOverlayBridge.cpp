// SketchUpOverlayBridge.cpp
// Registers a minimal Live C API overlay from Ruby and reuses its frame callback
// to capture SketchUp camera matrices for GaussianSplatRenderer.dll.

#include "SketchUpOverlayBridge.h"

#include <windows.h>

#include <cstdio>
#include <cstring>

#include <SketchUpAPI/application/application.h>
#include <SketchUpAPI/application/model.h>
#include <SketchUpAPI/application/overlay.h>

extern "C" IMAGE_DOS_HEADER __ImageBase;

namespace {

float g_view[16] = { 0.0f };
float g_proj[16] = { 0.0f };
bool g_overlay_registered = false;
SUOverlayRef g_overlay = SU_INVALID;

using PFN_RENDER_POINTCLOUD = void (*)();
PFN_RENDER_POINTCLOUD g_render = nullptr;

constexpr const char* kOverlayId = "kengat.gaussian_points.overlay";
constexpr const char* kOverlayName = "Gaussian Points Overlay";
constexpr const char* kOverlayDesc = "Feeds SketchUp camera matrices to Gaussian splat renderer";
constexpr const char* kOverlaySource = "Gaussian Points Sandbox";

void LogBridge(const char* message) {
  OutputDebugStringA("[OverlayBridge] ");
  OutputDebugStringA(message);
  OutputDebugStringA("\n");
}

void LoadRenderer() {
  if (g_render != nullptr) {
    return;
  }

  HMODULE module = GetModuleHandleA("GaussianSplatRenderer.dll");
  if (module == nullptr) {
    char path[MAX_PATH] = {};
    if (GetModuleFileNameA(reinterpret_cast<HMODULE>(&__ImageBase), path, MAX_PATH) != 0) {
      char* slash = strrchr(path, '\\');
      if (slash != nullptr) {
        *(slash + 1) = '\0';
        strcat_s(path, MAX_PATH, "GaussianSplatRenderer.dll");
        module = LoadLibraryA(path);
      }
    }
  }

  if (module == nullptr) {
    module = LoadLibraryA("GaussianSplatRenderer.dll");
  }

  if (module == nullptr) {
    LogBridge("GaussianSplatRenderer.dll not found.");
    return;
  }

  g_render = reinterpret_cast<PFN_RENDER_POINTCLOUD>(
      GetProcAddress(module, "renderPointCloud"));
  if (g_render == nullptr) {
    LogBridge("renderPointCloud export not found.");
  }
}

void BeginFrame(SUOverlayRef, const SUBeginFrameInfo* info, void*) {
  if (info == nullptr) {
    return;
  }

  for (int i = 0; i < 16; ++i) {
    g_view[i] = static_cast<float>(info->view_matrix[i]);
    g_proj[i] = static_cast<float>(info->projection_matrix[i]);
  }

  LoadRenderer();
  if (g_render != nullptr) {
    g_render();
  }
}

void DrawFrame(SUOverlayRef, SUOverlayDrawFrameInfo* info, void*) {
  if (info == nullptr) {
    return;
  }

  info->blending_factor = 0.0;
  info->color.ptr = nullptr;
  info->color.row_pitch = 0;
  info->color.size = 0;
  info->depth.ptr = nullptr;
  info->depth.row_pitch = 0;
  info->depth.size = 0;
  info->reserved = nullptr;
}

void EndFrame(SUOverlayRef, void*) {
  // Nothing to release here. The renderer uses the captured matrices immediately.
}

}  // namespace

extern "C" __declspec(dllexport) bool GetMatrixByLocation(int loc, float* out) {
  if (out == nullptr) {
    return false;
  }

  if (loc == 14) {
    memcpy(out, g_view, sizeof(g_view));
    return true;
  }

  if (loc == 15) {
    memcpy(out, g_proj, sizeof(g_proj));
    return true;
  }

  return false;
}

extern "C" __declspec(dllexport) void InstallAllHooks() {
  if (g_overlay_registered) {
    return;
  }

  SUModelRef model = SU_INVALID;
  if (SUApplicationGetActiveModel(&model) != SU_ERROR_NONE) {
    LogBridge("SUApplicationGetActiveModel failed.");
    return;
  }

  SUOverlayCreateInfo info = {};
  info.version = SUOVERLAY_CREATE_INFO_VERSION;
  info.id = kOverlayId;
  info.name = kOverlayName;
  info.desc = kOverlayDesc;
  info.source = kOverlaySource;
  info.image_format = SUOVERLAY_IMAGE_FORMAT_RGBA;
  info.image_orientation = SUOVERLAY_IMAGE_ORIENTATION_TOP_DOWN;
  info.begin_frame = BeginFrame;
  info.draw_frame = DrawFrame;
  info.end_frame = EndFrame;

  SUOverlayRef overlay = SU_INVALID;
  if (SUModelCreateOverlay(model, &info, &overlay) != SU_ERROR_NONE) {
    LogBridge("SUModelCreateOverlay failed.");
    return;
  }

  if (SUOverlayEnable(overlay, true) != SU_ERROR_NONE) {
    LogBridge("SUOverlayEnable failed.");
    SUModelReleaseOverlay(model, &overlay);
    return;
  }

  g_overlay = overlay;
  g_overlay_registered = true;
  LogBridge("Overlay registered.");
}

extern "C" __declspec(dllexport) void SetPointCloudData(const double*, int) {
  // Kept only for ABI compatibility with earlier experiments.
}

BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID) {
  if (reason == DLL_PROCESS_ATTACH) {
    // Avoid LoadLibrary work under the loader lock. The renderer is loaded lazily
    // from BeginFrame after SketchUp has fully initialized the overlay callback.
  }
  return TRUE;
}
