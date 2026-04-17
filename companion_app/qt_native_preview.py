from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .native_preview import DEFAULT_UP_AXIS_MODE, preview_bridge
from .splat_transform import (
    clone_snapshot,
    normalize_snapshot,
    snapshot_from_payload,
    snapshot_to_payload,
    snapshots_equal,
)


class NativePreviewHost(QtWidgets.QWidget):
    OBJECT_ID = "companion_preview_scene"
    GIZMO_NONE = 0
    GIZMO_MOVE = 1
    GIZMO_TRANSFORM = 2

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#07070A"))
        self.setPalette(palette)

        self._bridge = preview_bridge()
        self._window_created = False
        self._pending_path: str | None = None
        self._current_path: str | None = None
        self._up_axis_mode = DEFAULT_UP_AXIS_MODE
        self._last_load_succeeded = False
        self._pending_transform_payload: dict[str, object] | None = None
        self._current_snapshot: dict[str, object] | None = None
        self._active_tool = "projects"
        self._redraw_timer = QtCore.QTimer(self)
        self._redraw_timer.setInterval(120)
        self._redraw_timer.timeout.connect(self._heartbeat_redraw)
        self._redraw_timer.start()

    @property
    def available(self) -> bool:
        return self._bridge is not None

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._ensure_window)
        QtCore.QTimer.singleShot(0, self._sync_native_window)
        QtCore.QTimer.singleShot(50, self._sync_native_window)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._sync_native_window()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            self._destroy_window()
        finally:
            super().closeEvent(event)

    def clear_scene(self) -> None:
        self._pending_path = None
        self._current_path = None
        self._last_load_succeeded = False
        self._pending_transform_payload = None
        self._current_snapshot = None
        if not self.available:
            return
        self._ensure_window()
        if self._window_created:
            self._bridge.clear_splat_objects()
            self._bridge.request_redraw()

    def load_scene(
        self,
        path: str | Path,
        *,
        up_axis_mode: int = DEFAULT_UP_AXIS_MODE,
        force_reload: bool = False,
        transform_payload: dict[str, object] | None = None,
    ) -> None:
        self._pending_path = str(path)
        self._up_axis_mode = int(up_axis_mode)
        self._pending_transform_payload = dict(transform_payload or {}) if transform_payload else None
        if not self.available:
            return
        self._ensure_window()
        if not self._window_created:
            return
        self._apply_scene(force_reload=force_reload, sync_transform=True)
        self._sync_native_window()

    def fit_view(self) -> None:
        if self.available and self._window_created:
            self._bridge.fit_camera()

    def reset_view(self) -> None:
        if self.available and self._window_created:
            self._bridge.reset_camera()

    def preview_fps(self) -> float:
        if not self.available or not self._window_created:
            return 0.0
        try:
            return float(self._bridge.get_preview_fps())
        except Exception:
            return 0.0

    @property
    def last_load_succeeded(self) -> bool:
        return self._last_load_succeeded

    @property
    def current_snapshot(self) -> dict[str, object] | None:
        return clone_snapshot(self._current_snapshot) if self._current_snapshot else None

    def set_active_tool(self, tool_name: str) -> None:
        next_tool = (tool_name or "").strip().lower() or "projects"
        if next_tool == self._active_tool:
            return
        self._active_tool = next_tool
        if self.available and self._window_created:
            self._bridge.set_gizmo_mode(self._tool_mode())
            self._bridge.request_redraw()

    def camera_state(self) -> dict[str, object] | None:
        if not self.available or not self._window_created:
            return None
        try:
            return self._bridge.standalone_camera_state()
        except Exception:
            return None

    def set_transform(self, snapshot: dict[str, object], *, persist_pending: bool = True) -> bool:
        if not self.available or not self._window_created or not self._current_path:
            return False
        normalized = normalize_snapshot(snapshot)
        if not self._bridge.set_splat_object_transform(self.OBJECT_ID, normalized, visible=True):
            return False
        self._current_snapshot = normalized
        if persist_pending:
            self._pending_transform_payload = snapshot_to_payload(normalized)
        self._bridge.request_redraw()
        return True

    def orbit_camera(self, delta_x_pixels: float, delta_y_pixels: float) -> None:
        if self.available and self._window_created:
            self._bridge.orbit_camera(delta_x_pixels, delta_y_pixels)

    def pan_camera(self, delta_x_pixels: float, delta_y_pixels: float) -> None:
        if self.available and self._window_created:
            self._bridge.pan_camera(delta_x_pixels, delta_y_pixels)

    def zoom_camera(self, steps: float) -> None:
        if self.available and self._window_created:
            self._bridge.zoom_camera(steps)

    def sync_transform_from_native(self) -> dict[str, object] | None:
        if not self.available or not self._window_created or not self._current_path:
            return None
        snapshot = self._bridge.get_splat_object_transform(self.OBJECT_ID)
        if snapshot is None:
            return None
        self._current_snapshot = snapshot
        self._pending_transform_payload = snapshot_to_payload(snapshot)
        return clone_snapshot(snapshot)

    def reset_transform(self) -> dict[str, object] | None:
        return self._run_transform_command(self._bridge.reset_splat_object_transform)

    def undo_transform(self) -> dict[str, object] | None:
        return self._run_transform_command(self._bridge.undo_splat_object_transform)

    def redo_transform(self) -> dict[str, object] | None:
        return self._run_transform_command(self._bridge.redo_splat_object_transform)

    def is_transform_dragging(self) -> bool:
        return bool(self.available and self._window_created and self._bridge.is_standalone_preview_dragging())

    def _ensure_window(self) -> None:
        if not self.available or self._window_created or not self.isVisible():
            return
        native_width, native_height = self._native_size()
        created = self._bridge.create_window(int(self.winId()), native_width, native_height)
        self._window_created = bool(created)
        if self._window_created:
            self._bridge.resize_window(native_width, native_height)
            self._bridge.set_gizmo_mode(self._tool_mode())
            self._bridge.request_redraw()
        if self._window_created and self._pending_path:
            self._apply_scene(force_reload=True, sync_transform=True)

    def _sync_native_window(self) -> None:
        if self.available and self._window_created:
            native_width, native_height = self._native_size()
            self._bridge.resize_window(native_width, native_height)
            self._bridge.set_gizmo_mode(self._tool_mode())
            self._bridge.request_redraw()

    def _heartbeat_redraw(self) -> None:
        if not self.available or not self._window_created or not self.isVisible():
            return
        if not self._current_path and not self._pending_path:
            return
        try:
            self._bridge.request_redraw()
        except Exception:
            pass

    def _native_size(self) -> tuple[int, int]:
        dpr = max(float(self.devicePixelRatioF()), 1.0)
        return max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr))

    def _apply_scene(self, *, force_reload: bool, sync_transform: bool) -> None:
        if not self.available or not self._window_created:
            return
        if not self._pending_path:
            self.clear_scene()
            return
        desired_transform = snapshot_from_payload(self._pending_transform_payload)
        if not force_reload and self._current_path == self._pending_path:
            if sync_transform and desired_transform and (
                self._current_snapshot is None or not snapshots_equal(self._current_snapshot, desired_transform)
            ):
                if self._bridge.set_splat_object_transform(self.OBJECT_ID, desired_transform, visible=True):
                    self._current_snapshot = desired_transform
            self._bridge.request_redraw()
            return
        scene_path = Path(self._pending_path)
        if not scene_path.exists():
            self.clear_scene()
            return
        loaded_snapshot = self._bridge.load_scene_as_object(self.OBJECT_ID, scene_path, self._up_axis_mode)
        loaded = loaded_snapshot is not None
        if loaded:
            self._current_snapshot = loaded_snapshot
            if desired_transform:
                if self._bridge.set_splat_object_transform(self.OBJECT_ID, desired_transform, visible=True):
                    self._current_snapshot = desired_transform
            self._bridge.fit_camera()
            self._current_path = str(scene_path)
        else:
            self._bridge.clear_splat_objects()
            self._current_path = None
            self._current_snapshot = None
        self._last_load_succeeded = loaded
        self._bridge.request_redraw()

    def _destroy_window(self) -> None:
        if self.available and self._window_created:
            try:
                self._bridge.destroy_window()
            except Exception:
                pass
            self._window_created = False

    def _tool_mode(self) -> int:
        if self._active_tool == "move":
            return self.GIZMO_MOVE
        if self._active_tool == "transform":
            return self.GIZMO_TRANSFORM
        return self.GIZMO_NONE

    def _run_transform_command(self, command: object) -> dict[str, object] | None:
        if not self.available or not self._window_created or not self._current_path:
            return None
        if not callable(command):
            return None
        if not bool(command(self.OBJECT_ID)):
            return None
        snapshot = self.sync_transform_from_native()
        self._bridge.request_redraw()
        return snapshot
