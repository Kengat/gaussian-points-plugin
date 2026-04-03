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
	SKETCHUP_OVERLAY_BRIDGE_API void SetClipBoxState(
		int enabled,
		int visible,
		int gizmo_visible,
		int center_scale_mode,
		const double* center_xyz,
		const double* half_extents_xyz,
		const double* axes_xyz,
		int hovered_handle,
		int active_handle);
	SKETCHUP_OVERLAY_BRIDGE_API void SetMoveToolBoxState(
		int enabled,
		int visible,
		int gizmo_visible,
		int center_scale_mode,
		const double* center_xyz,
		const double* half_extents_xyz,
		const double* axes_xyz,
		int hovered_handle,
		int active_handle);
	SKETCHUP_OVERLAY_BRIDGE_API bool GetClipBoxState(
		int* enabled,
		double* center_xyz,
		double* half_extents_xyz,
		double* axes_xyz);
}

#endif // SKETCHUP_OVERLAY_BRIDGE_H
