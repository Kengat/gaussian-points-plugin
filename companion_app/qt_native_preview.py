from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from .native_preview import DEFAULT_UP_AXIS_MODE, preview_bridge


class NativePreviewHost(QtWidgets.QWidget):
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
        if not self.available:
            return
        self._ensure_window()
        if self._window_created:
            self._bridge.clear_splats()
            self._bridge.request_redraw()

    def load_scene(
        self,
        path: str | Path,
        *,
        up_axis_mode: int = DEFAULT_UP_AXIS_MODE,
        force_reload: bool = False,
    ) -> None:
        self._pending_path = str(path)
        self._up_axis_mode = int(up_axis_mode)
        if not self.available:
            return
        self._ensure_window()
        if not self._window_created:
            return
        self._apply_scene(force_reload=force_reload)
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

    def _ensure_window(self) -> None:
        if not self.available or self._window_created or not self.isVisible():
            return
        native_width, native_height = self._native_size()
        created = self._bridge.create_window(int(self.winId()), native_width, native_height)
        self._window_created = bool(created)
        if self._window_created:
            self._bridge.resize_window(native_width, native_height)
            self._bridge.request_redraw()
        if self._window_created and self._pending_path:
            self._apply_scene(force_reload=True)

    def _sync_native_window(self) -> None:
        if self.available and self._window_created:
            native_width, native_height = self._native_size()
            self._bridge.resize_window(native_width, native_height)
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

    def _apply_scene(self, *, force_reload: bool) -> None:
        if not self.available or not self._window_created:
            return
        if not self._pending_path:
            self.clear_scene()
            return
        if not force_reload and self._current_path == self._pending_path:
            self._bridge.request_redraw()
            return
        scene_path = Path(self._pending_path)
        if not scene_path.exists():
            self.clear_scene()
            return
        self._bridge.load_ply(scene_path, self._up_axis_mode)
        self._bridge.request_redraw()
        self._current_path = str(scene_path)

    def _destroy_window(self) -> None:
        if self.available and self._window_created:
            try:
                self._bridge.destroy_window()
            except Exception:
                pass
            self._window_created = False
