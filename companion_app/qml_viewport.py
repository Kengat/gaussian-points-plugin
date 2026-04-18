from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .qt_native_preview import NativePreviewHost
from .splat_transform import snapshot_from_payload, snapshot_to_payload, snapshots_equal


def _rounded_region(width: int, height: int, radius: float) -> QtGui.QRegion:
    if width <= 0 or height <= 0:
        return QtGui.QRegion()
    bitmap = QtGui.QBitmap(width, height)
    bitmap.fill(QtCore.Qt.GlobalColor.color0)
    painter = QtGui.QPainter(bitmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QtCore.Qt.PenStyle.NoPen)
    painter.setBrush(QtCore.Qt.GlobalColor.color1)
    painter.drawRoundedRect(QtCore.QRectF(0.5, 0.5, width - 1.0, height - 1.0), radius, radius)
    painter.end()
    return QtGui.QRegion(bitmap)


class ViewportWidget(QtWidgets.QWidget):
    def __init__(self, controller: QtCore.QObject, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._last_project_id: str | None = None
        self._current_project_id: str | None = None
        self._current_scene_path: str | None = None
        self._last_saved_snapshot: dict[str, object] | None = None
        self.setMouseTracking(True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background:#050505;")

        self._host = NativePreviewHost(self)
        self._host.setStyleSheet("background:#07070A; border-radius:28px;")

        self._placeholder = QtWidgets.QLabel("Scene preview will appear here", self)
        self._placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color:#71717A; font-size:16px; font-weight:500;")
        self._placeholder.setWordWrap(True)

        self._hud = self._make_panel()
        hud_layout = QtWidgets.QHBoxLayout(self._hud)
        hud_layout.setContentsMargins(14, 10, 14, 10)
        hud_layout.setSpacing(20)
        self._splat_value = self._make_value("#00F0FF")
        self._perf_value = self._make_value("#F4F4F5")
        hud_layout.addLayout(self._metric("Splats", self._splat_value))
        hud_layout.addSpacing(12)
        hud_layout.addLayout(self._metric("Performance", self._perf_value))
        self._last_fps = 0.0
        self._fps_timer = QtCore.QTimer(self)
        self._fps_timer.setInterval(350)
        self._fps_timer.timeout.connect(self._refresh_fps)
        self._fps_timer.start()
        self._transform_timer = QtCore.QTimer(self)
        self._transform_timer.setInterval(150)
        self._transform_timer.timeout.connect(self._sync_native_transform)
        self._transform_timer.start()

        self._fit_button = QtWidgets.QPushButton(self)
        self._fit_button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self._fit_button.setFixedSize(40, 40)
        self._fit_button.setStyleSheet(self._button_css())
        self._fit_button.setIcon(QtGui.QIcon(str(self._asset("maximize-white.png"))))
        self._fit_button.setIconSize(QtCore.QSize(16, 16))
        self._fit_button.clicked.connect(self._host.fit_view)

        self._hint = QtWidgets.QLabel("LMB Orbit   Shift+LMB Pan   Scroll Zoom", self)
        self._hint.setStyleSheet(
            "background:rgba(16,16,22,0.60); border:1px solid rgba(255,255,255,0.08);"
            "border-radius:18px; color:#A1A1AA; padding:10px 20px; font-size:12px; font-weight:500; font-family:'DM Sans 36pt';"
        )
        self._hint.hide()

        self._footer = QtWidgets.QLabel("")
        self._footer.setWordWrap(True)
        self._footer.setStyleSheet("color:#A1A1AA; background:transparent;")
        self._footer.setFont(self._mono_font(12, 400))

        if hasattr(self._controller, "activeToolChanged"):
            self._controller.activeToolChanged.connect(
                lambda: self._host.set_active_tool(str(getattr(self._controller, "activeTool", "projects")))
            )
        self._host.set_active_tool(str(getattr(self._controller, "activeTool", "projects")))

    def apply_detail(self, detail: dict[str, Any] | None) -> None:
        preview = (detail or {}).get("preview") or {}
        footer = preview.get("footer") or ""
        self._footer.setText(footer)
        self._splat_value.setText(f"{int(preview.get('pointCount') or 0):,}")
        self._host.set_active_tool(str(getattr(self._controller, "activeTool", "projects")))
        if preview.get("hasScene") and preview.get("path"):
            project_id = str(preview.get("projectId") or "")
            force_reload = bool(project_id and project_id != self._last_project_id)
            transform_payload = preview.get("transform")
            self._current_project_id = project_id or None
            self._current_scene_path = str(preview.get("path") or "") or None
            self._last_saved_snapshot = snapshot_from_payload(transform_payload)
            self._host.show()
            self._placeholder.hide()
            self._host.load_scene(
                preview["path"],
                force_reload=force_reload,
                transform_payload=transform_payload,
            )
            if self._host.last_load_succeeded:
                self._last_project_id = project_id or self._last_project_id
            else:
                self._placeholder.setText("Preview load failed")
                self._placeholder.show()
        else:
            self._host.clear_scene()
            self._host.hide()
            self._placeholder.setText(preview.get("emptyTitle") or "Scene preview will appear here")
            self._placeholder.show()
            self._last_project_id = None
            self._current_project_id = None
            self._current_scene_path = None
            self._last_saved_snapshot = None
        hud = (detail or {}).get("hud") or {}
        self._refresh_fps(fallback_text=hud.get("performance"))
        self._reposition()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._reposition()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._hint.show()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._hint.hide()
        super().leaveEvent(event)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#050505"))
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, False)
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 25)))
        for x in range(0, self.width(), 20):
            for y in range(0, self.height(), 20):
                painter.drawPoint(x, y)
        vignette = QtGui.QRadialGradient(self.rect().center(), max(self.width(), self.height()) * 0.6)
        vignette.setColorAt(0.0, QtGui.QColor(0, 0, 0, 0))
        vignette.setColorAt(1.0, QtGui.QColor(5, 5, 5, 210))
        painter.fillRect(self.rect(), vignette)
        super().paintEvent(event)

    def _reposition(self) -> None:
        preview_rect = self.rect()
        self._host.setGeometry(preview_rect)
        self._placeholder.setGeometry(preview_rect)
        self._hud.adjustSize()
        self._apply_overlay_masks()
        self._hud.move(28, 28)
        self._fit_button.move(self.width() - self._fit_button.width() - 28, 28)
        footer_width = max(160, self.width() - 56)
        footer_height = max(22, self.fontMetrics().lineSpacing() * 2)
        self._footer.setGeometry(28, self.height() - footer_height - 18, footer_width, footer_height)
        self._hint.adjustSize()
        hint_bottom = self._footer.y() - 14
        self._hint.move((self.width() - self._hint.width()) // 2, hint_bottom - self._hint.height())
        self._host.lower()
        self._placeholder.raise_()
        self._hud.raise_()
        self._fit_button.raise_()
        self._footer.raise_()
        self._hint.raise_()

    def _sync_native_transform(self) -> None:
        if not self._current_project_id or not self._current_scene_path:
            return
        if self._host.is_transform_dragging():
            return
        snapshot = self._host.sync_transform_from_native()
        self._persist_snapshot(snapshot)

    def reset_transform(self) -> bool:
        return self._persist_snapshot(self._host.reset_transform())

    def undo_transform(self) -> bool:
        return self._persist_snapshot(self._host.undo_transform())

    def redo_transform(self) -> bool:
        return self._persist_snapshot(self._host.redo_transform())

    def _make_panel(self) -> QtWidgets.QFrame:
        panel = QtWidgets.QFrame(self)
        panel.setStyleSheet(
            "background:rgba(16,16,22,0.60); border:1px solid rgba(255,255,255,0.08);"
            "border-radius:12px;"
        )
        return panel

    def _make_value(self, color: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel("0.0 FPS" if color == "#F4F4F5" else "0")
        label.setStyleSheet(f"color:{color}; background:transparent; border:none;")
        font = self._mono_font(14, 600)
        label.setFont(font)
        return label

    def _refresh_fps(self, fallback_text: str | None = None) -> None:
        fps = self._host.preview_fps()
        if fps > 0.0:
            self._last_fps = fps
            self._perf_value.setText(f"{fps:0.1f} FPS")
            return
        if self._last_fps > 0.0:
            self._perf_value.setText(f"{self._last_fps:0.1f} FPS")
            return
        self._perf_value.setText(fallback_text or "0.0 FPS")

    def _metric(self, title: str, value: QtWidgets.QLabel) -> QtWidgets.QVBoxLayout:
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        title_label = QtWidgets.QLabel(title.upper())
        title_label.setStyleSheet("color:#71717A; letter-spacing:1.5px; background:transparent; border:none;")
        font = self._mono_font(9, 800)
        title_label.setFont(font)
        layout.addWidget(title_label)
        layout.addWidget(value)
        return layout

    def _mono_font(self, pixel_size: int, weight: int) -> QtGui.QFont:
        font = QtGui.QFont("Consolas")
        font.setPixelSize(pixel_size)
        font.setWeight(QtGui.QFont.Weight(weight))
        return font

    def _button_css(self) -> str:
        return (
            "QPushButton{background:rgba(16,16,22,0.60); border:1px solid rgba(255,255,255,0.08);"
            "border-radius:12px; color:#E4E4E7; font-size:15px; font-weight:700;}"
            "QPushButton:hover{background:rgba(255,255,255,0.10);}"
        )

    def _asset(self, name: str) -> str:
        return str(Path(__file__).resolve().parent / "assets" / "icons_png" / name)

    def _apply_overlay_masks(self) -> None:
        self._hud.setMask(_rounded_region(self._hud.width(), self._hud.height(), 12.0))
        self._fit_button.setMask(_rounded_region(self._fit_button.width(), self._fit_button.height(), 12.0))

    def _persist_snapshot(self, snapshot: dict[str, object] | None) -> bool:
        if snapshot is None or not self._current_project_id or not self._current_scene_path:
            return False
        if self._last_saved_snapshot and snapshots_equal(self._last_saved_snapshot, snapshot):
            return True
        self._controller.save_preview_transform(
            self._current_project_id,
            self._current_scene_path,
            snapshot_to_payload(snapshot),
        )
        self._last_saved_snapshot = snapshot
        return True
