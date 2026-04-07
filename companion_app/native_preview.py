from __future__ import annotations

import ctypes
import os
import tkinter as tk
from pathlib import Path

from . import paths


DEFAULT_UP_AXIS_MODE = 2


class _NativePreviewBridge:
    def __init__(self) -> None:
        runtime_dir = paths.repo_root() / "sandbox" / "runtime"
        candidates = [
            runtime_dir / "GaussianSplatRenderer_preview.dll",
            runtime_dir / "GaussianSplatRenderer.dll",
            paths.repo_root() / "sandbox" / "GaussianSplatRenderer.dll",
        ]
        renderer_path = next((path for path in candidates if path.exists()), None)
        if renderer_path is None:
            raise FileNotFoundError("Gaussian preview runtime DLL was not found.")

        self._dll_dirs = []
        if hasattr(os, "add_dll_directory"):
            for directory in {renderer_path.parent, runtime_dir}:
                if directory.exists():
                    self._dll_dirs.append(os.add_dll_directory(str(directory)))

        self.dll = ctypes.WinDLL(str(renderer_path))
        self.renderer_path = renderer_path

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
        self.dll.SetSHRenderDegree.argtypes = [ctypes.c_int]
        self.dll.SetSHRenderDegree.restype = None
        self.dll.SetFastApproximateSortingEnabled.argtypes = [ctypes.c_int]
        self.dll.SetFastApproximateSortingEnabled.restype = None

    def create_window(self, parent_hwnd: int, width: int, height: int) -> bool:
        return bool(self.dll.CreateStandalonePreviewWindow(ctypes.c_void_p(parent_hwnd), 0, 0, int(width), int(height)))

    def destroy_window(self) -> None:
        self.dll.DestroyStandalonePreviewWindow()

    def resize_window(self, width: int, height: int) -> None:
        self.dll.ResizeStandalonePreviewWindow(0, 0, int(width), int(height))

    def request_redraw(self) -> None:
        self.dll.RequestStandalonePreviewRedraw()

    def clear_splats(self) -> None:
        self.dll.ClearSplats()

    def load_ply(self, path: str | Path, up_axis_mode: int = DEFAULT_UP_AXIS_MODE) -> None:
        payload = str(path).encode("utf-8")
        self.dll.LoadSplatsFromPLYWithUpAxis(payload, int(up_axis_mode))

    def reset_camera(self) -> None:
        self.dll.ResetStandalonePreviewCamera()

    def fit_camera(self) -> None:
        self.dll.FitStandalonePreviewCamera()

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
        self.bridge.load_ply(scene_path, self._pending_up_axis)
        self.bridge.request_redraw()
        self._current_path = str(scene_path)
