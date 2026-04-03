#include "PointCloudHook.h"

#include <windows.h>

#include <GL/gl.h>
#include <MinHook.h>

#include <SketchUpAPI/application/application.h>
#include <SketchUpAPI/application/model.h>
#include <SketchUpAPI/application/overlay.h>

#include <cstdarg>
#include <cstdio>
#include <cstring>

extern "C" IMAGE_DOS_HEADER __ImageBase;

namespace {

float g_view[16] = {0.0f};
float g_proj[16] = {0.0f};
bool g_overlay_registered = false;
bool g_frame_pending = false;
bool g_swap_hook_installed = false;
thread_local bool g_inside_swap_hook = false;

using PFN_RENDER_POINTCLOUD = void (*)();
using PFN_SETPOINTCLOUD = void (*)(const double*, int);
using PFN_SWAPBUFFERS = BOOL(WINAPI*)(HDC);

PFN_RENDER_POINTCLOUD g_render_point_cloud = nullptr;
PFN_SETPOINTCLOUD g_set_point_cloud = nullptr;
PFN_SWAPBUFFERS g_orig_swap_buffers = nullptr;

constexpr const char* kOverlayId = "kengat.gaussian_points.point_cloud_overlay";
constexpr const char* kOverlayName = "Gaussian Points Point Cloud Overlay";
constexpr const char* kOverlayDesc = "Feeds true SketchUp camera matrices to the point cloud renderer";
constexpr const char* kOverlaySource = "Gaussian Points";

void LogMessage(const char* format, ...) {
  char buffer[1024];
  va_list args;
  va_start(args, format);
  vsnprintf(buffer, sizeof(buffer), format, args);
  va_end(args);
  OutputDebugStringA("[PointCloudHook] ");
  OutputDebugStringA(buffer);
  OutputDebugStringA("\n");
}

void LoadRendererDLL() {
  if (g_render_point_cloud != nullptr && g_set_point_cloud != nullptr) {
    return;
  }

  HMODULE renderer = GetModuleHandleA("PointCloudRendererDLL.dll");
  if (renderer == nullptr) {
    renderer = LoadLibraryA("PointCloudRendererDLL.dll");
  }

  if (renderer == nullptr) {
    char path[MAX_PATH] = {};
    if (GetModuleFileNameA(reinterpret_cast<HMODULE>(&__ImageBase), path, MAX_PATH) != 0) {
      char* slash = strrchr(path, '\\');
      if (slash != nullptr) {
        *(slash + 1) = '\0';
        strcat_s(path, MAX_PATH, "PointCloudRendererDLL.dll");
        renderer = LoadLibraryA(path);
      }
    }
  }

  if (renderer == nullptr) {
    LogMessage("PointCloudRendererDLL.dll not found.");
    return;
  }

  g_render_point_cloud = reinterpret_cast<PFN_RENDER_POINTCLOUD>(
      GetProcAddress(renderer, "renderPointCloud"));
  g_set_point_cloud = reinterpret_cast<PFN_SETPOINTCLOUD>(
      GetProcAddress(renderer, "SetPointCloud"));

  if (g_render_point_cloud == nullptr || g_set_point_cloud == nullptr) {
    LogMessage("Point cloud renderer exports are missing.");
  }
}

BOOL WINAPI HookedSwapBuffers(HDC hdc) {
  if (g_orig_swap_buffers == nullptr) {
    return FALSE;
  }

  if (g_inside_swap_hook || !g_frame_pending || wglGetCurrentContext() == nullptr) {
    return g_orig_swap_buffers(hdc);
  }

  g_inside_swap_hook = true;
  LoadRendererDLL();
  if (g_render_point_cloud != nullptr) {
    g_render_point_cloud();
  }
  g_frame_pending = false;
  g_inside_swap_hook = false;
  return g_orig_swap_buffers(hdc);
}

void InstallSwapBuffersHook() {
  if (g_swap_hook_installed) {
    return;
  }

  HMODULE gdi32 = GetModuleHandleA("gdi32.dll");
  if (gdi32 == nullptr) {
    LogMessage("gdi32.dll is not loaded.");
    return;
  }

  FARPROC target = GetProcAddress(gdi32, "SwapBuffers");
  if (target == nullptr) {
    LogMessage("SwapBuffers export not found.");
    return;
  }

  MH_STATUS status = MH_Initialize();
  if (status != MH_OK && status != MH_ERROR_ALREADY_INITIALIZED) {
    LogMessage("MH_Initialize failed with status=%d", status);
    return;
  }

  status = MH_CreateHook(target, &HookedSwapBuffers,
      reinterpret_cast<LPVOID*>(&g_orig_swap_buffers));
  if (status != MH_OK && status != MH_ERROR_ALREADY_CREATED) {
    LogMessage("MH_CreateHook(SwapBuffers) failed with status=%d", status);
    return;
  }

  status = MH_EnableHook(target);
  if (status != MH_OK && status != MH_ERROR_ENABLED) {
    LogMessage("MH_EnableHook(SwapBuffers) failed with status=%d", status);
    return;
  }

  g_swap_hook_installed = true;
  LogMessage("SwapBuffers hook installed.");
}

void BeginFrame(SUOverlayRef, const SUBeginFrameInfo* info, void*) {
  if (info == nullptr) {
    return;
  }

  for (int i = 0; i < 16; ++i) {
    g_view[i] = static_cast<float>(info->view_matrix[i]);
    g_proj[i] = static_cast<float>(info->projection_matrix[i]);
  }

  LoadRendererDLL();
  g_frame_pending = true;
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
}

}  // namespace

extern "C" PCH_API void InstallAllHooks() {
  if (g_overlay_registered) {
    return;
  }

  InstallSwapBuffersHook();

  SUModelRef model = SU_INVALID;
  if (SUApplicationGetActiveModel(&model) != SU_ERROR_NONE) {
    LogMessage("SUApplicationGetActiveModel failed.");
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
    LogMessage("SUModelCreateOverlay failed.");
    return;
  }

  if (SUOverlayEnable(overlay, true) != SU_ERROR_NONE) {
    LogMessage("SUOverlayEnable failed.");
    SUModelReleaseOverlay(model, &overlay);
    return;
  }

  g_overlay_registered = true;
  LogMessage("Overlay registered.");
}

extern "C" PCH_API void SetPointCloudData(const double* points, int count) {
  LoadRendererDLL();
  if (g_set_point_cloud == nullptr) {
    LogMessage("SetPointCloud export is unavailable.");
    return;
  }

  g_set_point_cloud(points, count);
}

extern "C" PCH_API bool GetCurrentMatrices(float* modelview, float* projection) {
  if (modelview == nullptr || projection == nullptr) {
    return false;
  }

  memcpy(modelview, g_view, sizeof(g_view));
  memcpy(projection, g_proj, sizeof(g_proj));
  return true;
}

BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID) {
  if (reason == DLL_PROCESS_DETACH) {
    g_frame_pending = false;
    if (g_swap_hook_installed) {
      MH_DisableHook(MH_ALL_HOOKS);
      MH_Uninitialize();
    }
  }
  return TRUE;
}
