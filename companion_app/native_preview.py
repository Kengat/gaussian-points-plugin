from __future__ import annotations

import ctypes
import os
import tkinter as tk
from pathlib import Path

from . import paths
from .gaussian_gasp import export_ply_from_gaussian_gasp
from .splat_transform import normalize_snapshot, snapshot_from_bounds


DEFAULT_UP_AXIS_MODE = 2


_REQUIRED_PREVIEW_EXPORTS = (
    "CreateStandalonePreviewWindow",
    "DestroyStandalonePreviewWindow",
    "ResizeStandalonePreviewWindow",
    "RequestStandalonePreviewRedraw",
    "ResetStandalonePreviewCamera",
    "FitStandalonePreviewCamera",
    "ClearSplats",
    "LoadSplatsFromPLYWithUpAxis",
    "SetSHRenderDegree",
    "SetFastApproximateSortingEnabled",
)


class _NativePreviewBridge:
    def __init__(self) -> None:
        runtime_dir = paths.repo_root() / "sandbox" / "runtime"
        candidates = [
            paths.repo_root() / "build_out" / "native" / "preview" / "bin" / "GaussianSplatRenderer_preview.dll",
            runtime_dir / "GaussianSplatRenderer_preview.dll",
            paths.repo_root() / "build_out" / "native" / "gaussian" / "bin" / "GaussianSplatRenderer.dll",
            runtime_dir / "GaussianSplatRenderer.dll",
            paths.repo_root() / "sandbox" / "GaussianSplatRenderer.dll",
        ]
        existing_candidates = [path for path in candidates if path.exists()]
        if not existing_candidates:
            raise FileNotFoundError("Gaussian preview runtime DLL was not found.")

        self._dll_dirs = []
        if hasattr(os, "add_dll_directory"):
            for directory in {runtime_dir, *(path.parent for path in existing_candidates)}:
                if directory.exists():
                    self._dll_dirs.append(os.add_dll_directory(str(directory)))

        load_errors: list[str] = []
        self.dll = None
        self.renderer_path = None
        for renderer_path in existing_candidates:
            try:
                dll = ctypes.WinDLL(str(renderer_path))
                missing_exports = [name for name in _REQUIRED_PREVIEW_EXPORTS if not hasattr(dll, name)]
            except Exception as error:
                load_errors.append(f"{renderer_path}: {error}")
                continue
            if missing_exports:
                load_errors.append(f"{renderer_path}: missing exports {', '.join(missing_exports)}")
                continue
            self.dll = dll
            self.renderer_path = renderer_path
            break

        if self.dll is None or self.renderer_path is None:
            details = "; ".join(load_errors) if load_errors else "No usable preview DLL candidates."
            raise RuntimeError(f"Gaussian preview runtime DLL could not be loaded. {details}")

        self.dll.CreateStandalonePreviewWindow.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.dll.CreateStandalonePreviewWindow.restype = ctypes.c_int
        self.dll.DestroyStandalonePreviewWindow.argtypes = []
        self.dll.DestroyStandalonePreviewWindow.restype = None
        self.dll.ResizeStandalonePreviewWindow.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.dll.ResizeStandalonePreviewWindow.restype = None
        self.dll.RequestStandalonePreviewRedraw.argtypes = []
        self.dll.RequestStandalonePreviewRedraw.restype = None
        self.dll.ResetStandalonePreviewCamera.argtypes = []
        self.dll.ResetStandalonePreviewCamera.restype = None
        self.dll.FitStandalonePreviewCamera.argtypes = []
        self.dll.FitStandalonePreviewCamera.restype = None
        self.dll.ClearSplats.argtypes = []
        self.dll.ClearSplats.restype = None
        self.dll.LoadSplatsFromPLYWithUpAxis.argtypes = [ctypes.c_char_p, ctypes.c_int]
        self.dll.LoadSplatsFromPLYWithUpAxis.restype = None
        self._load_gasp_with_up_axis = getattr(self.dll, "LoadSplatsFromGASPWithUpAxis", None)
        if self._load_gasp_with_up_axis is not None:
            self._load_gasp_with_up_axis.argtypes = [ctypes.c_char_p, ctypes.c_int]
            self._load_gasp_with_up_axis.restype = None
        self.dll.SetSHRenderDegree.argtypes = [ctypes.c_int]
        self.dll.SetSHRenderDegree.restype = None
        self.dll.SetFastApproximateSortingEnabled.argtypes = [ctypes.c_int]
        self.dll.SetFastApproximateSortingEnabled.restype = None
        self._clear_splat_objects = getattr(self.dll, "ClearSplatObjects", None)
        if self._clear_splat_objects is not None:
            self._clear_splat_objects.argtypes = []
            self._clear_splat_objects.restype = None
        self._load_splat_object_from_ply_with_up_axis = getattr(self.dll, "LoadSplatObjectFromPLYWithUpAxis", None)
        if self._load_splat_object_from_ply_with_up_axis is not None:
            self._load_splat_object_from_ply_with_up_axis.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_int,
            ]
            self._load_splat_object_from_ply_with_up_axis.restype = ctypes.c_int
        self._load_splat_object_from_gasp_with_up_axis = getattr(self.dll, "LoadSplatObjectFromGASPWithUpAxis", None)
        if self._load_splat_object_from_gasp_with_up_axis is not None:
            self._load_splat_object_from_gasp_with_up_axis.argtypes = [
                ctypes.c_char_p,
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_int,
            ]
            self._load_splat_object_from_gasp_with_up_axis.restype = ctypes.c_int
        self._set_splat_object_transform = getattr(self.dll, "SetSplatObjectTransform", None)
        if self._set_splat_object_transform is not None:
            self._set_splat_object_transform.argtypes = [
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.c_int,
            ]
            self._set_splat_object_transform.restype = ctypes.c_int
        self._get_standalone_preview_camera_state = getattr(self.dll, "GetStandalonePreviewCameraState", None)
        if self._get_standalone_preview_camera_state is not None:
            self._get_standalone_preview_camera_state.argtypes = [
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
            ]
            self._get_standalone_preview_camera_state.restype = ctypes.c_int
        self._orbit_standalone_preview_camera = getattr(self.dll, "OrbitStandalonePreviewCamera", None)
        if self._orbit_standalone_preview_camera is not None:
            self._orbit_standalone_preview_camera.argtypes = [ctypes.c_float, ctypes.c_float]
            self._orbit_standalone_preview_camera.restype = None
        self._pan_standalone_preview_camera = getattr(self.dll, "PanStandalonePreviewCamera", None)
        if self._pan_standalone_preview_camera is not None:
            self._pan_standalone_preview_camera.argtypes = [ctypes.c_float, ctypes.c_float]
            self._pan_standalone_preview_camera.restype = None
        self._zoom_standalone_preview_camera = getattr(self.dll, "ZoomStandalonePreviewCamera", None)
        if self._zoom_standalone_preview_camera is not None:
            self._zoom_standalone_preview_camera.argtypes = [ctypes.c_float]
            self._zoom_standalone_preview_camera.restype = None
        self._set_standalone_preview_gizmo_mode = getattr(self.dll, "SetStandalonePreviewGizmoMode", None)
        if self._set_standalone_preview_gizmo_mode is not None:
            self._set_standalone_preview_gizmo_mode.argtypes = [ctypes.c_int]
            self._set_standalone_preview_gizmo_mode.restype = None
        self._reset_standalone_preview_object_transform = getattr(self.dll, "ResetStandalonePreviewObjectTransform", None)
        if self._reset_standalone_preview_object_transform is not None:
            self._reset_standalone_preview_object_transform.argtypes = [ctypes.c_char_p]
            self._reset_standalone_preview_object_transform.restype = ctypes.c_int
        self._undo_standalone_preview_object_transform = getattr(self.dll, "UndoStandalonePreviewObjectTransform", None)
        if self._undo_standalone_preview_object_transform is not None:
            self._undo_standalone_preview_object_transform.argtypes = [ctypes.c_char_p]
            self._undo_standalone_preview_object_transform.restype = ctypes.c_int
        self._redo_standalone_preview_object_transform = getattr(self.dll, "RedoStandalonePreviewObjectTransform", None)
        if self._redo_standalone_preview_object_transform is not None:
            self._redo_standalone_preview_object_transform.argtypes = [ctypes.c_char_p]
            self._redo_standalone_preview_object_transform.restype = ctypes.c_int
        self._is_standalone_preview_dragging = getattr(self.dll, "IsStandalonePreviewDragging", None)
        if self._is_standalone_preview_dragging is not None:
            self._is_standalone_preview_dragging.argtypes = []
            self._is_standalone_preview_dragging.restype = ctypes.c_int
        self._get_standalone_preview_object_transform = getattr(self.dll, "GetStandalonePreviewObjectTransform", None)
        if self._get_standalone_preview_object_transform is not None:
            self._get_standalone_preview_object_transform.argtypes = [
                ctypes.c_char_p,
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
            ]
            self._get_standalone_preview_object_transform.restype = ctypes.c_int
        self._get_splat_bounds = getattr(self.dll, "GetSplatBounds", None)
        if self._get_splat_bounds is not None:
            self._get_splat_bounds.argtypes = [
                ctypes.POINTER(ctypes.c_double),
                ctypes.POINTER(ctypes.c_double),
            ]
            self._get_splat_bounds.restype = ctypes.c_int
        self._get_preview_fps = getattr(self.dll, "GetStandalonePreviewFPS", None)
        if self._get_preview_fps is not None:
            self._get_preview_fps.argtypes = []
            self._get_preview_fps.restype = ctypes.c_double

    def create_window(self, parent_hwnd: int, width: int, height: int, *, x: int = 0, y: int = 0) -> bool:
        return bool(self.dll.CreateStandalonePreviewWindow(ctypes.c_void_p(parent_hwnd), int(x), int(y), int(width), int(height)))

    def destroy_window(self) -> None:
        self.dll.DestroyStandalonePreviewWindow()

    def resize_window(self, width: int, height: int, *, x: int = 0, y: int = 0) -> None:
        self.dll.ResizeStandalonePreviewWindow(int(x), int(y), int(width), int(height))

    def request_redraw(self) -> None:
        self.dll.RequestStandalonePreviewRedraw()

    def clear_splats(self) -> None:
        self.dll.ClearSplats()

    @property
    def supports_object_api(self) -> bool:
        return (
            self._load_splat_object_from_ply_with_up_axis is not None
            and self._load_splat_object_from_gasp_with_up_axis is not None
            and self._set_splat_object_transform is not None
        )

    def load_ply(self, path: str | Path, up_axis_mode: int = DEFAULT_UP_AXIS_MODE) -> bool:
        payload = str(path).encode("utf-8")
        self.dll.LoadSplatsFromPLYWithUpAxis(payload, int(up_axis_mode))
        return self.has_loaded_scene()

    def load_scene(self, path: str | Path, up_axis_mode: int = DEFAULT_UP_AXIS_MODE) -> bool:
        scene_path = Path(path)
        self.clear_splats()
        if scene_path.suffix.lower() == ".gasp":
            if self._load_gasp_with_up_axis is not None:
                self._load_gasp_with_up_axis(str(scene_path).encode("utf-8"), int(up_axis_mode))
                return self.has_loaded_scene()
            cache_dir = paths.scratch_root() / "preview_gasp_cache"
            cache_name = f"{scene_path.stem}_{scene_path.stat().st_mtime_ns}.ply"
            scene_path = export_ply_from_gaussian_gasp(scene_path, cache_dir / cache_name)
        return self.load_ply(scene_path, up_axis_mode)

    def clear_splat_objects(self) -> None:
        if self._clear_splat_objects is not None:
            self._clear_splat_objects()
        else:
            self.clear_splats()

    def load_scene_as_object(
        self,
        object_id: str,
        path: str | Path,
        up_axis_mode: int = DEFAULT_UP_AXIS_MODE,
    ) -> dict[str, object] | None:
        if not self.supports_object_api:
            return None
        scene_path = Path(path)
        loader = (
            self._load_splat_object_from_gasp_with_up_axis
            if scene_path.suffix.lower() == ".gasp"
            else self._load_splat_object_from_ply_with_up_axis
        )
        if loader is None:
            return None
        center = (ctypes.c_double * 3)(0.0, 0.0, 0.0)
        half_extents = (ctypes.c_double * 3)(0.0, 0.0, 0.0)
        self.clear_splat_objects()
        loaded = int(
            loader(
                str(object_id).encode("utf-8"),
                str(scene_path).encode("utf-8"),
                center,
                half_extents,
                int(up_axis_mode),
            )
        )
        if loaded == 0:
            return None
        return snapshot_from_bounds(tuple(center), tuple(half_extents))

    def set_splat_object_transform(self, object_id: str, snapshot: dict[str, object], *, visible: bool = True) -> bool:
        if not self.supports_object_api:
            return False
        current = normalize_snapshot(snapshot)
        center = (ctypes.c_double * 3)(*tuple(current["center"]))
        half_extents = (ctypes.c_double * 3)(
            float(current["half_extents"]["x"]),
            float(current["half_extents"]["y"]),
            float(current["half_extents"]["z"]),
        )
        axes = (ctypes.c_double * 9)(
            *tuple(current["axes"]["x"]),
            *tuple(current["axes"]["y"]),
            *tuple(current["axes"]["z"]),
        )
        return bool(
            self._set_splat_object_transform(
                str(object_id).encode("utf-8"),
                center,
                half_extents,
                axes,
                1 if visible else 0,
            )
        )

    def has_loaded_scene(self) -> bool:
        if self._get_splat_bounds is None:
            return True
        mins = (ctypes.c_double * 3)()
        maxs = (ctypes.c_double * 3)()
        return bool(self._get_splat_bounds(mins, maxs))

    def standalone_camera_state(self) -> dict[str, object] | None:
        if self._get_standalone_preview_camera_state is None:
            return None
        view = (ctypes.c_float * 16)()
        projection = (ctypes.c_float * 16)()
        position = (ctypes.c_float * 3)()
        target = (ctypes.c_float * 3)()
        up = (ctypes.c_float * 3)()
        viewport_width = ctypes.c_int()
        viewport_height = ctypes.c_int()
        available = int(
            self._get_standalone_preview_camera_state(
                view,
                projection,
                position,
                target,
                up,
                ctypes.byref(viewport_width),
                ctypes.byref(viewport_height),
            )
        )
        if available == 0:
            return None
        return {
            "view_matrix": [float(value) for value in view],
            "projection_matrix": [float(value) for value in projection],
            "camera_position": [float(value) for value in position],
            "camera_target": [float(value) for value in target],
            "camera_up": [float(value) for value in up],
            "viewport_width": int(viewport_width.value),
            "viewport_height": int(viewport_height.value),
        }

    def orbit_camera(self, delta_x_pixels: float, delta_y_pixels: float) -> None:
        if self._orbit_standalone_preview_camera is not None:
            self._orbit_standalone_preview_camera(float(delta_x_pixels), float(delta_y_pixels))

    def pan_camera(self, delta_x_pixels: float, delta_y_pixels: float) -> None:
        if self._pan_standalone_preview_camera is not None:
            self._pan_standalone_preview_camera(float(delta_x_pixels), float(delta_y_pixels))

    def zoom_camera(self, steps: float) -> None:
        if self._zoom_standalone_preview_camera is not None:
            self._zoom_standalone_preview_camera(float(steps))

    def set_gizmo_mode(self, tool_mode: int) -> None:
        if self._set_standalone_preview_gizmo_mode is not None:
            self._set_standalone_preview_gizmo_mode(int(tool_mode))

    def reset_splat_object_transform(self, object_id: str) -> bool:
        if self._reset_standalone_preview_object_transform is None:
            return False
        return bool(self._reset_standalone_preview_object_transform(str(object_id).encode("utf-8")))

    def undo_splat_object_transform(self, object_id: str) -> bool:
        if self._undo_standalone_preview_object_transform is None:
            return False
        return bool(self._undo_standalone_preview_object_transform(str(object_id).encode("utf-8")))

    def redo_splat_object_transform(self, object_id: str) -> bool:
        if self._redo_standalone_preview_object_transform is None:
            return False
        return bool(self._redo_standalone_preview_object_transform(str(object_id).encode("utf-8")))

    def is_standalone_preview_dragging(self) -> bool:
        if self._is_standalone_preview_dragging is None:
            return False
        return bool(self._is_standalone_preview_dragging())

    def get_splat_object_transform(self, object_id: str) -> dict[str, object] | None:
        if self._get_standalone_preview_object_transform is None:
            return None
        center = (ctypes.c_double * 3)()
        half_extents = (ctypes.c_double * 3)()
        axes = (ctypes.c_double * 9)()
        available = int(
            self._get_standalone_preview_object_transform(
                str(object_id).encode("utf-8"),
                center,
                half_extents,
                axes,
            )
        )
        if available == 0:
            return None
        return normalize_snapshot(
            {
                "center": tuple(float(value) for value in center),
                "half_extents": {
                    "x": float(half_extents[0]),
                    "y": float(half_extents[1]),
                    "z": float(half_extents[2]),
                },
                "axes": {
                    "x": tuple(float(value) for value in axes[0:3]),
                    "y": tuple(float(value) for value in axes[3:6]),
                    "z": tuple(float(value) for value in axes[6:9]),
                },
            }
        )

    def reset_camera(self) -> None:
        self.dll.ResetStandalonePreviewCamera()

    def fit_camera(self) -> None:
        self.dll.FitStandalonePreviewCamera()

    def get_preview_fps(self) -> float:
        if self._get_preview_fps is None:
            return 0.0
        return float(self._get_preview_fps())

    def set_sh_degree(self, degree: int) -> None:
        self.dll.SetSHRenderDegree(int(degree))

    def set_fast_sorting(self, enabled: bool) -> None:
        self.dll.SetFastApproximateSortingEnabled(1 if enabled else 0)


_BRIDGE: _NativePreviewBridge | None = None
_BRIDGE_ERROR: str | None = None


def preview_runtime_error() -> str | None:
    global _BRIDGE, _BRIDGE_ERROR
    if _BRIDGE is not None:
        return None
    if _BRIDGE_ERROR is not None:
        return _BRIDGE_ERROR
    try:
        _BRIDGE = _NativePreviewBridge()
    except Exception as error:  # pragma: no cover - runtime-dependent
        _BRIDGE_ERROR = str(error)
        return _BRIDGE_ERROR
    return None


def preview_runtime_available() -> bool:
    return preview_runtime_error() is None


def preview_bridge() -> _NativePreviewBridge | None:
    if preview_runtime_error() is not None:
        return None
    return _BRIDGE


class NativeSplatPreview(tk.Frame):
    def __init__(self, master: tk.Misc, **kwargs) -> None:
        super().__init__(master, **kwargs)
        self.bridge = _BRIDGE if preview_runtime_available() else None
        self._window_created = False
        self._pending_path: str | None = None
        self._pending_up_axis = DEFAULT_UP_AXIS_MODE
        self._current_path: str | None = None
        self.bind("<Configure>", self._on_configure, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.after_idle(self._ensure_window)

    @property
    def available(self) -> bool:
        return self.bridge is not None

    def _ensure_window(self) -> None:
        if not self.available or self._window_created or not self.winfo_exists():
            return
        self.update_idletasks()
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        created = self.bridge.create_window(self.winfo_id(), width, height)
        self._window_created = bool(created)
        if self._window_created and self._pending_path is not None:
            self._apply_pending_scene()

    def _on_configure(self, _event: tk.Event) -> None:
        if not self.available:
            return
        if not self._window_created:
            self.after_idle(self._ensure_window)
            return
        self.bridge.resize_window(max(1, self.winfo_width()), max(1, self.winfo_height()))

    def _on_destroy(self, _event: tk.Event) -> None:
        if self.available and self._window_created:
            try:
                self.bridge.destroy_window()
            except Exception:
                pass
            self._window_created = False

    def clear_scene(self) -> None:
        self._pending_path = None
        self._current_path = None
        if not self.available:
            return
        self._ensure_window()
        if self._window_created:
            self.bridge.clear_splats()
            self.bridge.request_redraw()

    def load_scene(self, path: str | Path, up_axis_mode: int = DEFAULT_UP_AXIS_MODE, *, force_reload: bool = False) -> None:
        self._pending_path = str(path)
        self._pending_up_axis = int(up_axis_mode)
        if not self.available:
            return
        if not force_reload and self._current_path == self._pending_path:
            self.bridge.request_redraw()
            return
        self.after_idle(self._apply_pending_scene)

    def reset_view(self) -> None:
        if self.available and self._window_created:
            self.bridge.reset_camera()

    def fit_view(self) -> None:
        if self.available and self._window_created:
            self.bridge.fit_camera()

    def preview_fps(self) -> float:
        if not self.available or not self._window_created:
            return 0.0
        return self.bridge.get_preview_fps()

    def _apply_pending_scene(self) -> None:
        if not self.available:
            return
        self._ensure_window()
        if not self._window_created:
            return
        if not self._pending_path:
            self.bridge.clear_splats()
            self.bridge.request_redraw()
            self._current_path = None
            return
        scene_path = Path(self._pending_path)
        if not scene_path.exists():
            self.bridge.clear_splats()
            self.bridge.request_redraw()
            self._current_path = None
            return
        loaded = self.bridge.load_scene(scene_path, self._pending_up_axis)
        self.bridge.request_redraw()
        self._current_path = str(scene_path) if loaded else None
