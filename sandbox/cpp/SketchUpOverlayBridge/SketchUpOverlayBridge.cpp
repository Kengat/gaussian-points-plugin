// SketchUpOverlayBridge.cpp
// Registers a minimal Live C API overlay from Ruby and reuses its frame callback
// to capture SketchUp camera matrices for GaussianSplatRenderer.dll.

#include "SketchUpOverlayBridge.h"

#include <windows.h>

#include <cstdio>
#include <cstring>
#include <fstream>
#include <string>

#include <GL/gl.h>
#include <MinHook.h>

#include <SketchUpAPI/application/application.h>
#include <SketchUpAPI/application/model.h>
#include <SketchUpAPI/application/overlay.h>

extern "C" IMAGE_DOS_HEADER __ImageBase;

namespace {

float g_view[16] = { 0.0f };
float g_proj[16] = { 0.0f };
float g_camera_position[3] = { 0.0f, 0.0f, 0.0f };
float g_camera_target[3] = { 0.0f, 0.0f, 0.0f };
float g_camera_up[3] = { 0.0f, 1.0f, 0.0f };
bool g_camera_is_perspective = true;
bool g_overlay_registered = false;
SUOverlayRef g_overlay = SU_INVALID;
bool g_frame_pending = false;

using PFN_RENDER_POINTCLOUD = void (*)();
using PFN_SET_POINTCLOUD = void (*)(const double*, int);
PFN_RENDER_POINTCLOUD g_gaussian_render = nullptr;
PFN_RENDER_POINTCLOUD g_pointcloud_render = nullptr;
PFN_SET_POINTCLOUD g_pointcloud_set_data = nullptr;
using PFN_SWAPBUFFERS = BOOL(WINAPI*)(HDC);
PFN_SWAPBUFFERS g_orig_swap_buffers = nullptr;
bool g_swap_hook_installed = false;
thread_local bool g_inside_swap_hook = false;

constexpr const char* kOverlayId = "kengat.gaussian_points.overlay";
constexpr const char* kOverlayName = "Gaussian Points Overlay";
constexpr const char* kOverlayDesc = "Feeds SketchUp camera matrices to Gaussian splat renderer";
constexpr const char* kOverlaySource = "Gaussian Points Sandbox";

void LogBridge(const char* message) {
  OutputDebugStringA("[OverlayBridge] ");
  OutputDebugStringA(message);
  OutputDebugStringA("\n");

  char tempPath[MAX_PATH] = {};
  DWORD length = GetTempPathA(MAX_PATH, tempPath);
  if (length == 0 || length > MAX_PATH) {
    return;
  }

  std::ofstream logFile(std::string(tempPath) + "gaussian_splats_native.log", std::ios::app);
  if (logFile.is_open()) {
    logFile << "[OverlayBridge] " << message << "\n";
  }
}

HMODULE LoadSiblingModule(const char* file_name) {
  HMODULE module = GetModuleHandleA(file_name);
  if (module != nullptr) {
    return module;
  }

  char path[MAX_PATH] = {};
  if (GetModuleFileNameA(reinterpret_cast<HMODULE>(&__ImageBase), path, MAX_PATH) != 0) {
    char* slash = strrchr(path, '\\');
    if (slash != nullptr) {
      *(slash + 1) = '\0';

      const std::string bridge_dir(path);
      const std::string runtime_path = bridge_dir + "runtime\\" + file_name;
      module = LoadLibraryA(runtime_path.c_str());
      if (module != nullptr) {
        return module;
      }

      const std::string sibling_path = bridge_dir + file_name;
      module = LoadLibraryA(sibling_path.c_str());
      if (module != nullptr) {
        return module;
      }
    }
  }

  if (module == nullptr) {
    module = LoadLibraryA(file_name);
  }

  return module;
}

void LoadRenderers() {
  if (g_gaussian_render == nullptr) {
    HMODULE module = LoadSiblingModule("GaussianSplatRenderer.dll");
    if (module == nullptr) {
      LogBridge("GaussianSplatRenderer.dll not found.");
    } else {
      g_gaussian_render = reinterpret_cast<PFN_RENDER_POINTCLOUD>(
          GetProcAddress(module, "renderPointCloud"));
      if (g_gaussian_render == nullptr) {
        LogBridge("Gaussian renderPointCloud export not found.");
      }
    }
  }

  if (g_pointcloud_render == nullptr || g_pointcloud_set_data == nullptr) {
    HMODULE module = LoadSiblingModule("PointCloudRendererDLL.dll");
    if (module == nullptr) {
      LogBridge("PointCloudRendererDLL.dll not found.");
    } else {
      g_pointcloud_render = reinterpret_cast<PFN_RENDER_POINTCLOUD>(
          GetProcAddress(module, "renderPointCloud"));
      g_pointcloud_set_data = reinterpret_cast<PFN_SET_POINTCLOUD>(
          GetProcAddress(module, "SetPointCloud"));
      if (g_pointcloud_render == nullptr || g_pointcloud_set_data == nullptr) {
        LogBridge("Point cloud renderer exports not found.");
      }
    }
  }
}

void LoadRenderer() {
  LoadRenderers();
}

extern "C" __declspec(dllexport) bool GetCurrentMatrices(float* modelview, float* projection) {
  if (modelview == nullptr || projection == nullptr) {
    return false;
  }

  memcpy(modelview, g_view, sizeof(g_view));
  memcpy(projection, g_proj, sizeof(g_proj));
  return true;
}

BOOL WINAPI HookedSwapBuffers(HDC hdc) {
  if (g_orig_swap_buffers == nullptr) {
    return FALSE;
  }

  if (g_inside_swap_hook || !g_frame_pending || wglGetCurrentContext() == nullptr) {
    return g_orig_swap_buffers(hdc);
  }

  g_inside_swap_hook = true;
  LoadRenderers();
  if (g_pointcloud_render != nullptr) {
    g_pointcloud_render();
  }
  if (g_gaussian_render != nullptr) {
    g_gaussian_render();
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
    LogBridge("gdi32.dll not loaded.");
    return;
  }

  FARPROC target = GetProcAddress(gdi32, "SwapBuffers");
  if (target == nullptr) {
    LogBridge("SwapBuffers export not found.");
    return;
  }

  MH_STATUS status = MH_Initialize();
  if (status != MH_OK && status != MH_ERROR_ALREADY_INITIALIZED) {
    LogBridge("MH_Initialize failed.");
    return;
  }

  status = MH_CreateHook(target, &HookedSwapBuffers,
      reinterpret_cast<LPVOID*>(&g_orig_swap_buffers));
  if (status != MH_OK && status != MH_ERROR_ALREADY_CREATED) {
    LogBridge("MH_CreateHook(SwapBuffers) failed.");
    return;
  }

  status = MH_EnableHook(target);
  if (status != MH_OK && status != MH_ERROR_ENABLED) {
    LogBridge("MH_EnableHook(SwapBuffers) failed.");
    return;
  }

  g_swap_hook_installed = true;
  LogBridge("SwapBuffers hook installed.");
}

void BeginFrame(SUOverlayRef, const SUBeginFrameInfo* info, void*) {
  static bool loggedFirstFrame = false;
  if (info == nullptr) {
    return;
  }

  for (int i = 0; i < 16; ++i) {
    g_view[i] = static_cast<float>(info->view_matrix[i]);
    g_proj[i] = static_cast<float>(info->projection_matrix[i]);
  }

  g_camera_position[0] = static_cast<float>(info->position.x);
  g_camera_position[1] = static_cast<float>(info->position.y);
  g_camera_position[2] = static_cast<float>(info->position.z);
  g_camera_target[0] = static_cast<float>(info->target.x);
  g_camera_target[1] = static_cast<float>(info->target.y);
  g_camera_target[2] = static_cast<float>(info->target.z);
  g_camera_up[0] = static_cast<float>(info->up.x);
  g_camera_up[1] = static_cast<float>(info->up.y);
  g_camera_up[2] = static_cast<float>(info->up.z);
  g_camera_is_perspective = info->is_perspective;

  LoadRenderers();
  if (!loggedFirstFrame) {
    LogBridge("BeginFrame received.");
    loggedFirstFrame = true;
  }
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

extern "C" __declspec(dllexport) bool GetCameraState(
    float* position, float* target, float* up, int* is_perspective) {
  if (position == nullptr || target == nullptr || up == nullptr) {
    return false;
  }

  memcpy(position, g_camera_position, sizeof(g_camera_position));
  memcpy(target, g_camera_target, sizeof(g_camera_target));
  memcpy(up, g_camera_up, sizeof(g_camera_up));
  if (is_perspective != nullptr) {
    *is_perspective = g_camera_is_perspective ? 1 : 0;
  }
  return true;
}

extern "C" __declspec(dllexport) void InstallAllHooks() {
  if (g_overlay_registered) {
    return;
  }

  InstallSwapBuffersHook();

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

extern "C" __declspec(dllexport) void SetPointCloudData(const double* points, int count) {
  LoadRenderers();
  if (g_pointcloud_set_data == nullptr) {
    LogBridge("Point cloud SetPointCloud export not found.");
    return;
  }

  g_pointcloud_set_data(points, count);
}

BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID) {
  if (reason == DLL_PROCESS_ATTACH) {
    // Avoid LoadLibrary work under the loader lock. The renderer is loaded lazily
    // from BeginFrame after SketchUp has fully initialized the overlay callback.
  }
  if (reason == DLL_PROCESS_DETACH) {
    g_frame_pending = false;
    if (g_swap_hook_installed) {
      MH_DisableHook(MH_ALL_HOOKS);
      MH_Uninitialize();
    }
  }
  return TRUE;
}
