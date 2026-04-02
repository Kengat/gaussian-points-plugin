#ifndef SKETCHUP_OVERLAY_BRIDGE_H
#define SKETCHUP_OVERLAY_BRIDGE_H

#ifdef _WIN32
#define SKETCHUP_OVERLAY_BRIDGE_API __declspec(dllexport)
#else
#define SKETCHUP_OVERLAY_BRIDGE_API
#endif

extern "C" {
	SKETCHUP_OVERLAY_BRIDGE_API void InstallAllHooks();
	SKETCHUP_OVERLAY_BRIDGE_API void SetPointCloudData(const double* points, int count);
}

#endif // SKETCHUP_OVERLAY_BRIDGE_H
