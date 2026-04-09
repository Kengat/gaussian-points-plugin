from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from . import APP_VERSION, store
from .native_preview import preview_bridge, preview_runtime_available
from .web_desktop_app import CompanionApi


APP_BG = "#050505"
SURFACE = "#0A0A0D"
SURFACE_ALT = "#111116"
TEXT = "#FAFAFA"
TEXT_MUTED = "#A1A1AA"
ACCENT = "#FF5400"
ACCENT_CYAN = "#00F0FF"
ACCENT_PINK = "#FF2E93"
READY = "#16C784"
FAILED = "#F43F5E"
IDLE = "#71717A"


def status_color(status: str | None) -> str:
    return {
        "ready": READY,
        "running": ACCENT,
        "queued": ACCENT,
        "training": ACCENT,
        "failed": FAILED,
        "idle": IDLE,
    }.get((status or "").lower(), IDLE)


class QtStateApi(CompanionApi):
    def _sync_preview_overlay(self, preview: dict[str, Any]) -> None:
        return

    def _pick_media_files(self) -> tuple[str, ...] | None:
        parent = self._window if isinstance(self._window, QtWidgets.QWidget) else None
        files, _filter = QtWidgets.QFileDialog.getOpenFileNames(
            parent,
            "Select input media",
            "",
            "Supported Media (*.bmp *.jpg *.jpeg *.png *.tif *.tiff *.webp *.mp4 *.mov *.m4v *.avi *.mkv *.webm *.zip);;All files (*.*)",
        )
        return tuple(files) if files else None


class NativePreviewWidget(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#07070A"))
        self.setPalette(palette)
        self.bridge = preview_bridge() if preview_runtime_available() else None
        self._window_created = False
        self._current_path: str | None = None
        self._pending_path: str | None = None

    @property
    def available(self) -> bool:
        return self.bridge is not None

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._ensure_window)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.available and self._window_created:
            self.bridge.resize_window(max(1, self.width()), max(1, self.height()), x=0, y=0)
            self.bridge.request_redraw()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            self._destroy_window()
        finally:
            super().closeEvent(event)

    def _ensure_window(self) -> None:
        if not self.available or self._window_created or not self.isVisible():
            return
        created = self.bridge.create_window(int(self.winId()), max(1, self.width()), max(1, self.height()), x=0, y=0)
        self._window_created = bool(created)
        if self._window_created:
            self.bridge.resize_window(max(1, self.width()), max(1, self.height()), x=0, y=0)
            self.bridge.request_redraw()
        if self._window_created and self._pending_path:
            self._apply_scene(self._pending_path, force_reload=True)

    def _destroy_window(self) -> None:
        if self.available and self._window_created:
            try:
                self.bridge.destroy_window()
            except Exception:
                pass
            self._window_created = False

    def clear_scene(self) -> None:
        self._pending_path = None
        self._current_path = None
        if self.available:
            self._ensure_window()
            if self._window_created:
                self.bridge.clear_splats()
                self.bridge.request_redraw()

    def load_scene(self, path: str, *, force_reload: bool = False) -> None:
        self._pending_path = path
        if not self.available:
            return
        self._ensure_window()
        if not self._window_created:
            return
        self._apply_scene(path, force_reload=force_reload)

    def _apply_scene(self, path: str, *, force_reload: bool) -> None:
        if not self.available or not self._window_created:
            return
        if not force_reload and self._current_path == path:
            self.bridge.request_redraw()
            return
        scene_path = Path(path)
        if not scene_path.exists():
            self.clear_scene()
            return
        self.bridge.load_ply(scene_path)
        self.bridge.request_redraw()
        self._current_path = str(scene_path)

    def preview_fps(self) -> float:
        if not self.available or not self._window_created:
            return 0.0
        try:
            return float(self.bridge.get_preview_fps())
        except Exception:
            return 0.0


class ClickableFrame(QtWidgets.QFrame):
    clicked = QtCore.Signal()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ProjectCard(ClickableFrame):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_id: str | None = None
        self.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.setFixedHeight(56)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(12)

        self.name_label = QtWidgets.QLabel("Project")
        self.name_label.setStyleSheet("font-size: 15px; font-weight: 600; color: #EAEAF0;")

        self.status_dot = QtWidgets.QLabel()
        self.status_dot.setFixedSize(10, 10)

        self.status_text = QtWidgets.QLabel("READY")
        self.status_text.setStyleSheet("font-size: 11px; font-weight: 700; letter-spacing: 1px;")

        left = QtWidgets.QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(4)
        left.addWidget(self.name_label)
        layout.addLayout(left, 1)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_text)
        layout.addLayout(status_row)
        self.set_active(False)

    def set_summary(self, summary: dict[str, Any], active: bool) -> None:
        self.project_id = summary["id"]
        self.name_label.setText(summary["name"])
        color = status_color(summary.get("status"))
        self.status_dot.setStyleSheet(f"background:{color}; border-radius:5px;")
        self.status_text.setText((summary.get("status") or "idle").upper())
        self.status_text.setStyleSheet(
            f"font-size: 11px; font-weight: 700; letter-spacing: 1px; color: {color};"
        )
        self.set_active(active)

    def set_active(self, active: bool) -> None:
        border = ACCENT if active else "rgba(255,255,255,0.08)"
        bg = "rgba(255,84,0,0.12)" if active else "rgba(17,17,22,0.88)"
        self.setStyleSheet(f"QFrame{{background:{bg}; border:1px solid {border}; border-radius:16px;}}")


class TabButton(QtWidgets.QPushButton):
    def __init__(self, text: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.setMinimumHeight(42)
        self.setStyleSheet(
            """
            QPushButton {
                color: #70707A;
                background: transparent;
                border: none;
                border-bottom: 2px solid transparent;
                font-size: 12px;
                font-weight: 700;
                padding: 0 12px 10px 12px;
            }
            QPushButton:checked {
                color: #FFFFFF;
                border-bottom: 2px solid #FF5400;
            }
            """
        )


class DotViewport(QtWidgets.QWidget):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.preview = NativePreviewWidget(self)
        self._last_fps = 0.0
        self.preview.setStyleSheet("background:#07070A; border-radius:28px;")
        self.hud = QtWidgets.QFrame(self)
        self.hud.setStyleSheet(
            "QFrame{background:rgba(16,16,22,0.72); border:1px solid rgba(255,255,255,0.08); border-radius:18px;}"
        )
        hud_layout = QtWidgets.QHBoxLayout(self.hud)
        hud_layout.setContentsMargins(18, 14, 18, 14)
        hud_layout.setSpacing(18)
        splat_label = QtWidgets.QLabel("SPLATS")
        splat_label.setStyleSheet("color:#6B7280; font-size:10px; font-weight:700; letter-spacing:2px;")
        self.splats_value = QtWidgets.QLabel("0")
        self.splats_value.setStyleSheet("color:#00F0FF; font-size:22px; font-weight:700;")
        perf_label = QtWidgets.QLabel("PERFORMANCE")
        perf_label.setStyleSheet("color:#6B7280; font-size:10px; font-weight:700; letter-spacing:2px;")
        self.performance_value = QtWidgets.QLabel("0.0 FPS")
        self.performance_value.setStyleSheet("color:#F4F4F5; font-size:18px; font-weight:600;")
        splat_col = QtWidgets.QVBoxLayout()
        splat_col.addWidget(splat_label)
        splat_col.addWidget(self.splats_value)
        perf_col = QtWidgets.QVBoxLayout()
        perf_col.addWidget(perf_label)
        perf_col.addWidget(self.performance_value)
        hud_layout.addLayout(splat_col)
        hud_layout.addSpacing(12)
        hud_layout.addLayout(perf_col)
        self.fullscreen_button = QtWidgets.QPushButton("[]", self)
        self.fullscreen_button.setFixedSize(44, 44)
        self.fullscreen_button.setStyleSheet(
            "QPushButton{background:rgba(16,16,22,0.72); border:1px solid rgba(255,255,255,0.08); border-radius:14px; color:#E4E4E7; font-size:16px; font-weight:700;}"
        )
        self.bottom_hint = QtWidgets.QLabel("LMB  Orbit     Shift+LMB  Pan     Scroll  Zoom", self)
        self.bottom_hint.setStyleSheet(
            "background:rgba(16,16,22,0.72); border:1px solid rgba(255,255,255,0.08); border-radius:22px; color:#A1A1AA; padding:10px 18px; font-size:12px; font-weight:600;"
        )
        self.placeholder = QtWidgets.QLabel("Scene preview will appear here", self)
        self.placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet("color:#71717A; font-size:16px; font-weight:500;")
        self.fps_timer = QtCore.QTimer(self)
        self.fps_timer.setInterval(350)
        self.fps_timer.timeout.connect(self._refresh_fps)
        self.fps_timer.start()

    def set_preview_state(self, preview: dict[str, Any], performance_text: str) -> None:
        splats = preview.get("pointCount")
        self.splats_value.setText(f"{int(splats):,}" if isinstance(splats, int) else "0")
        if preview.get("hasScene") and preview.get("path"):
            self.placeholder.hide()
            self.preview.load_scene(preview["path"], force_reload=False)
        else:
            self.preview.clear_scene()
            self.placeholder.setText(preview.get("emptyTitle") or "Scene preview will appear here")
            self.placeholder.show()
        self._refresh_fps(fallback_text=performance_text)

    def _refresh_fps(self, fallback_text: str | None = None) -> None:
        fps = self.preview.preview_fps()
        if fps > 0.0:
            self._last_fps = fps
            self.performance_value.setText(f"{fps:0.1f} FPS")
            return
        if self._last_fps > 0.0:
            self.performance_value.setText(f"{self._last_fps:0.1f} FPS")
            return
        self.performance_value.setText(fallback_text or "0.0 FPS")

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        panel_rect = self.rect().adjusted(28, 28, -28, -28)
        self.preview.setGeometry(panel_rect)
        self.placeholder.setGeometry(panel_rect)
        self.hud.adjustSize()
        self.hud.move(28, 28)
        self.fullscreen_button.move(self.width() - self.fullscreen_button.width() - 28, 28)
        self.bottom_hint.adjustSize()
        self.bottom_hint.move((self.width() - self.bottom_hint.width()) // 2, self.height() - self.bottom_hint.height() - 28)
        self.preview.lower()
        self.placeholder.raise_()
        self.hud.raise_()
        self.fullscreen_button.raise_()
        self.bottom_hint.raise_()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor(APP_BG))
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 38)))
        for x in range(0, self.width(), 26):
            for y in range(0, self.height(), 26):
                painter.drawPoint(x, y)


class ModalDialog(QtWidgets.QDialog):
    def __init__(self, title: str, subtitle: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self.setStyleSheet(
            "QDialog{background:#0A0A0D; color:#FAFAFA; border:1px solid rgba(255,255,255,0.08);}"
            "QLabel{color:#FAFAFA;} QLineEdit,QSpinBox{background:#050505; color:#FAFAFA; border:1px solid rgba(255,255,255,0.12); border-radius:12px; padding:10px 12px;}"
            "QPushButton{min-height:42px; border-radius:14px; font-size:14px; font-weight:700;}"
        )
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(18)
        title_label = QtWidgets.QLabel(title)
        title_label.setStyleSheet("font-size:24px; font-weight:700;")
        subtitle_label = QtWidgets.QLabel(subtitle)
        subtitle_label.setStyleSheet("color:#A1A1AA; font-size:13px;")
        subtitle_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(subtitle_label)
        self.body = QtWidgets.QVBoxLayout()
        self.body.setSpacing(14)
        layout.addLayout(self.body)
        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QtWidgets.QPushButton("Cancel")
        self.cancel_button.setStyleSheet("QPushButton{background:#16161D; color:#FAFAFA; border:1px solid rgba(255,255,255,0.08);}")
        self.ok_button = QtWidgets.QPushButton("Confirm")
        self.ok_button.setStyleSheet("QPushButton{background:#FF5400; color:white; border:1px solid transparent;}")
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self.accept)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)


class CreateProjectDialog(ModalDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("New Project", "Create a project from local photos and keep the same studio layout.", parent)
        self.name_input = QtWidgets.QLineEdit()
        self.name_input.setPlaceholderText("Project name")
        self.body.addWidget(self.name_input)
        self.name_input.setFocus()

    @property
    def project_name(self) -> str:
        return self.name_input.text().strip()


class TrainStepsDialog(ModalDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Training Setup", "Choose how many optimization steps to run for this model.", parent)
        self.steps_input = QtWidgets.QSpinBox()
        self.steps_input.setRange(200, 20000)
        self.steps_input.setSingleStep(100)
        self.steps_input.setValue(3000)
        self.steps_input.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.PlusMinus)
        self.body.addWidget(self.steps_input)

    @property
    def steps(self) -> int:
        return int(self.steps_input.value())


class QtCompanionWindow(QtWidgets.QMainWindow):
    def __init__(self, plugin_root: str | None = None) -> None:
        super().__init__()
        self.api = QtStateApi(plugin_root=plugin_root, webview_module=None)
        self.active_project_id: str | None = None
        self.state: dict[str, Any] | None = None
        self.current_tab = "inspect"
        self.project_cards: list[ProjectCard] = []

        self.setWindowTitle("Gaussian Points Studio")
        self.resize(1600, 980)
        self.setMinimumSize(1440, 860)
        self.setStyleSheet(f"QMainWindow{{background:{APP_BG}; color:{TEXT};}}")

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        self.root_layout = QtWidgets.QVBoxLayout(root)
        self.root_layout.setContentsMargins(0, 0, 0, 0)
        self.root_layout.setSpacing(0)
        self._build_shell()

        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_state)
        self.refresh_timer.start(self.api.POLL_MS)
        QtCore.QTimer.singleShot(0, lambda: self.refresh_state(initial=True))

    def _build_shell(self) -> None:
        self._build_menu_bar()
        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        self.root_layout.addWidget(body, 1)
        body_layout.addWidget(self._build_tool_rail())
        body_layout.addWidget(self._build_workspace(), 1)

    def _build_menu_bar(self) -> None:
        bar = QtWidgets.QFrame()
        bar.setFixedHeight(32)
        bar.setStyleSheet("QFrame{background:#050505; border-bottom:1px solid rgba(255,255,255,0.08);}")
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(14, 0, 12, 0)
        layout.setSpacing(14)
        title = QtWidgets.QLabel("Gaussian Points Studio")
        title.setStyleSheet("font-size:13px; font-weight:700; color:#E4E4E7;")
        layout.addWidget(title)
        for name in ["File", "Edit", "View", "Tools", "Window", "Help"]:
            button = QtWidgets.QPushButton(name)
            button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            button.setStyleSheet(
                "QPushButton{background:transparent; border:none; color:#A1A1AA; font-size:12px; font-weight:600;}"
                "QPushButton:hover{color:#FFFFFF;}"
            )
            layout.addWidget(button)
        layout.addStretch(1)
        community = QtWidgets.QPushButton("Community Gallery")
        community.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        community.setStyleSheet(
            "QPushButton{background:transparent; border:none; color:#A1A1AA; font-size:12px; font-weight:600;}"
            "QPushButton:hover{color:#FFFFFF;}"
        )
        signin = QtWidgets.QPushButton("Sign In")
        signin.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        signin.setStyleSheet(
            "QPushButton{background:rgba(0,240,255,0.08); color:#00F0FF; border:1px solid rgba(0,240,255,0.25); border-radius:8px; padding:7px 12px; font-size:12px; font-weight:700;}"
        )
        layout.addWidget(community)
        layout.addWidget(signin)
        self.root_layout.addWidget(bar)

    def _build_tool_rail(self) -> QtWidgets.QWidget:
        rail = QtWidgets.QFrame()
        rail.setFixedWidth(52)
        rail.setStyleSheet("QFrame{background:#050505; border-right:1px solid #000000;}")
        layout = QtWidgets.QVBoxLayout(rail)
        layout.setContentsMargins(0, 16, 0, 16)
        layout.setSpacing(9)
        for text, active in [(">", True), ("+", False), ("o", False), ("[]", False), ("~", False)]:
            button = QtWidgets.QPushButton(text)
            button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            button.setFixedSize(34, 34)
            style = (
                "QPushButton{background:rgba(255,84,0,0.12); color:#FF5400; border:1px solid rgba(255,84,0,0.32); border-radius:12px; font-size:16px; font-weight:700;}"
                if active
                else "QPushButton{background:transparent; color:#71717A; border:1px solid transparent; border-radius:12px; font-size:15px; font-weight:700;} QPushButton:hover{background:rgba(255,255,255,0.05); color:#FFFFFF;}"
            )
            button.setStyleSheet(style)
            layout.addWidget(button, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        settings = QtWidgets.QPushButton("*")
        settings.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        settings.setFixedSize(34, 34)
        settings.setStyleSheet(
            "QPushButton{background:transparent; color:#71717A; border:1px solid transparent; border-radius:12px; font-size:18px; font-weight:700;} QPushButton:hover{background:rgba(255,255,255,0.05); color:#FFFFFF;}"
        )
        layout.addWidget(settings, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)
        return rail

    def _build_workspace(self) -> QtWidgets.QWidget:
        frame = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.left_sidebar = self._build_left_sidebar()
        self.center_panel = self._build_center_panel()
        self.right_sidebar = self._build_right_sidebar()
        layout.addWidget(self.left_sidebar)
        layout.addWidget(self.center_panel, 1)
        layout.addWidget(self.right_sidebar)
        return frame

    def _build_left_sidebar(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setFixedWidth(280)
        panel.setStyleSheet("QFrame{background:rgba(10,10,13,0.92); border-right:1px solid rgba(255,255,255,0.08);}")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(16, 0, 16, 16)
        layout.setSpacing(14)

        header = QtWidgets.QFrame()
        header.setFixedHeight(64)
        header.setStyleSheet("QFrame{border-bottom:1px solid rgba(255,255,255,0.08);}")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        label = QtWidgets.QLabel("PROJECT EXPLORER")
        label.setStyleSheet("font-size:12px; font-weight:800; letter-spacing:1px; color:#E4E4E7;")
        header_layout.addWidget(label, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(header)

        self.new_project_button = QtWidgets.QPushButton("New Project")
        self.new_project_button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.new_project_button.setFixedHeight(40)
        self.new_project_button.setStyleSheet(
            "QPushButton{background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.10); border-radius:10px; color:#FFFFFF; font-size:14px; font-weight:700;}"
            "QPushButton:hover{background:rgba(255,255,255,0.06);}"
        )
        self.new_project_button.clicked.connect(self._create_project)
        layout.addWidget(self.new_project_button)

        section = QtWidgets.QLabel("RECENT LOCAL PROJECTS")
        section.setStyleSheet("font-size:10px; font-weight:800; letter-spacing:2px; color:#71717A; margin-top:10px;")
        layout.addWidget(section)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea{background:transparent; border:none;} QScrollBar:vertical{width:6px; background:transparent;} QScrollBar::handle:vertical{background:rgba(255,255,255,0.12); border-radius:3px;}")
        holder = QtWidgets.QWidget()
        self.projects_layout = QtWidgets.QVBoxLayout(holder)
        self.projects_layout.setContentsMargins(0, 0, 4, 0)
        self.projects_layout.setSpacing(12)
        self.projects_layout.addStretch(1)
        scroll.setWidget(holder)
        layout.addWidget(scroll, 1)
        return panel

    def _build_center_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setStyleSheet("QFrame{background:#050505;}")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QtWidgets.QFrame()
        header.setFixedHeight(64)
        header.setStyleSheet("QFrame{background:rgba(10,10,13,0.55); border-bottom:1px solid rgba(255,255,255,0.08);}")
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(22, 0, 16, 0)
        header_layout.setSpacing(12)

        self.project_title = QtWidgets.QLabel("No project")
        self.project_title.setStyleSheet("font-size:18px; font-weight:700; color:#FFFFFF;")
        self.status_badge = QtWidgets.QLabel("IDLE")
        self.status_badge.setStyleSheet("padding:7px 14px; border-radius:12px; font-size:12px; font-weight:700;")
        header_layout.addWidget(self.project_title)
        header_layout.addWidget(self.status_badge, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        header_layout.addStretch(1)

        self.add_dataset_button = self._action_button("Add Dataset", False)
        self.train_button = self._action_button("Train Model", True)
        self.restart_button = self._icon_button("R")
        self.stop_button = self._icon_button("[]")
        self.export_button = self._action_button("Open Export Folder", False)
        self.add_dataset_button.clicked.connect(self._add_photos)
        self.train_button.clicked.connect(self._start_training)
        self.restart_button.clicked.connect(self._restart_training)
        self.stop_button.clicked.connect(self._stop_training)
        self.export_button.clicked.connect(self._open_export_folder)
        for widget in [self.add_dataset_button, self.train_button, self.restart_button, self.stop_button, self.export_button]:
            header_layout.addWidget(widget)
        layout.addWidget(header)

        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        self.subtitle_label = QtWidgets.QLabel("")
        self.subtitle_label.hide()
        self.viewport = DotViewport()
        body_layout.addWidget(self.viewport, 1)
        layout.addWidget(body, 1)
        return panel

    def _action_button(self, text: str, accent: bool) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        button.setFixedHeight(40)
        button.setMinimumWidth(126)
        style = (
            "QPushButton{background:#FF5400; color:white; border:1px solid transparent; border-radius:12px; padding:0 18px; font-size:14px; font-weight:700;}"
            "QPushButton:hover{background:#FF6A1E;}"
            if accent
            else "QPushButton{background:transparent; color:#F4F4F5; border:1px solid rgba(255,255,255,0.10); border-radius:12px; padding:0 18px; font-size:14px; font-weight:700;}"
            "QPushButton:hover{background:rgba(255,255,255,0.06);}"
        )
        button.setStyleSheet(style)
        return button

    def _icon_button(self, text: str) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        button.setFixedSize(40, 40)
        button.setStyleSheet(
            "QPushButton{background:transparent; color:#E4E4E7; border:1px solid rgba(255,255,255,0.10); border-radius:12px; font-size:15px; font-weight:700;}"
            "QPushButton:hover{background:rgba(255,255,255,0.06);}"
        )
        return button

    def _build_right_sidebar(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setFixedWidth(360)
        panel.setStyleSheet("QFrame{background:rgba(10,10,13,0.92); border-left:1px solid rgba(255,255,255,0.08);}")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        tabs_bar = QtWidgets.QFrame()
        tabs_bar.setFixedHeight(64)
        tabs_bar.setStyleSheet("QFrame{border-bottom:1px solid rgba(255,255,255,0.08);}")
        tabs_layout = QtWidgets.QHBoxLayout(tabs_bar)
        tabs_layout.setContentsMargins(10, 12, 10, 0)
        tabs_layout.setSpacing(6)
        self.inspect_tab = TabButton("Inspect")
        self.console_tab = TabButton("Console")
        self.dataset_tab = TabButton("Dataset")
        self.inspect_tab.clicked.connect(lambda: self._set_tab("inspect"))
        self.console_tab.clicked.connect(lambda: self._set_tab("console"))
        self.dataset_tab.clicked.connect(lambda: self._set_tab("dataset"))
        tabs_layout.addWidget(self.inspect_tab)
        tabs_layout.addWidget(self.console_tab)
        tabs_layout.addWidget(self.dataset_tab)
        layout.addWidget(tabs_bar)

        self.sidebar_stack = QtWidgets.QStackedWidget()
        self.inspect_page = self._build_inspect_page()
        self.console_page = self._build_console_page()
        self.dataset_page = self._build_dataset_page()
        self.sidebar_stack.addWidget(self.inspect_page)
        self.sidebar_stack.addWidget(self.console_page)
        self.sidebar_stack.addWidget(self.dataset_page)
        layout.addWidget(self.sidebar_stack, 1)
        self._set_tab("inspect")
        return panel

    def _build_inspect_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QScrollArea()
        page.setWidgetResizable(True)
        page.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        page.setStyleSheet("QScrollArea{background:transparent; border:none;} QScrollBar:vertical{width:6px; background:transparent;} QScrollBar::handle:vertical{background:rgba(255,255,255,0.12); border-radius:3px;}")
        inner = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(inner)
        layout.setContentsMargins(20, 22, 20, 22)
        layout.setSpacing(22)

        self.progress_card = QtWidgets.QFrame()
        self.progress_card.setStyleSheet("QFrame{background:#111116; border:1px solid rgba(255,255,255,0.08); border-radius:22px;}")
        card_layout = QtWidgets.QVBoxLayout(self.progress_card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        self.progress_percent = QtWidgets.QLabel("0%")
        self.progress_percent.setStyleSheet("font-size:56px; font-weight:700; color:#FFFFFF;")
        self.progress_status = QtWidgets.QLabel("Ready")
        self.progress_status.setStyleSheet("font-size:15px; color:#A1A1AA;")
        self.progress_meta = QtWidgets.QLabel("--")
        self.progress_meta.setStyleSheet("font-size:14px; color:#E4E4E7;")
        self.progress_loss = QtWidgets.QLabel("--")
        self.progress_loss.setStyleSheet("font-size:18px; font-weight:700; color:#00F0FF;")
        card_layout.addWidget(self.progress_percent)
        card_layout.addWidget(self.progress_status)
        card_layout.addSpacing(12)
        card_layout.addWidget(self.progress_meta)
        card_layout.addWidget(self.progress_loss)
        layout.addWidget(self.progress_card)

        props_title = QtWidgets.QLabel("MODEL PROPERTIES")
        props_title.setStyleSheet("font-size:12px; font-weight:800; letter-spacing:1px; color:#71717A;")
        layout.addWidget(props_title)
        self.properties_box = QtWidgets.QFrame()
        self.properties_box.setStyleSheet("QFrame{background:transparent;}")
        self.properties_layout = QtWidgets.QVBoxLayout(self.properties_box)
        self.properties_layout.setContentsMargins(0, 0, 0, 0)
        self.properties_layout.setSpacing(12)
        layout.addWidget(self.properties_box)

        self.export_card = QtWidgets.QFrame()
        self.export_card.setStyleSheet("QFrame{background:rgba(30,22,24,0.46); border:1px solid rgba(255,255,255,0.08); border-radius:22px;}")
        export_layout = QtWidgets.QVBoxLayout(self.export_card)
        export_layout.setContentsMargins(20, 20, 20, 20)
        self.export_title = QtWidgets.QLabel("Export Options")
        self.export_title.setStyleSheet("font-size:16px; font-weight:700;")
        self.export_body = QtWidgets.QLabel("")
        self.export_body.setWordWrap(True)
        self.export_body.setStyleSheet("font-size:14px; color:#A1A1AA;")
        self.export_ply_button = self._action_button("Export to .ply file", False)
        self.export_sketchup_button = self._action_button("Export directly to SketchUp", True)
        self.export_ply_button.clicked.connect(self._open_export_folder)
        self.export_sketchup_button.clicked.connect(self._open_export_folder)
        export_layout.addWidget(self.export_title)
        export_layout.addWidget(self.export_body)
        export_layout.addSpacing(10)
        export_layout.addWidget(self.export_ply_button)
        export_layout.addWidget(self.export_sketchup_button)
        layout.addWidget(self.export_card)
        layout.addStretch(1)
        page.setWidget(inner)
        return page

    def _build_console_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QTextEdit()
        page.setReadOnly(True)
        page.setStyleSheet("QTextEdit{background:#020202; border:none; color:#D4D4D8; font-family:Consolas; font-size:12px; padding:18px;}")
        return page

    def _build_dataset_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(16)
        self.dataset_grid = QtWidgets.QListWidget()
        self.dataset_grid.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.dataset_grid.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.dataset_grid.setMovement(QtWidgets.QListView.Movement.Static)
        self.dataset_grid.setSpacing(10)
        self.dataset_grid.setIconSize(QtCore.QSize(92, 92))
        self.dataset_grid.setGridSize(QtCore.QSize(108, 126))
        self.dataset_grid.setStyleSheet(
            "QListWidget{background:#0A0A0D; border:none; color:#E4E4E7;}"
            "QListWidget::item{background:#050505; border:1px solid rgba(255,255,255,0.08); border-radius:10px; padding:8px;}"
            "QListWidget::item:selected{border:1px solid rgba(0,240,255,0.35);}"
        )
        self.dataset_add_button = self._action_button("Add Photos or Video", False)
        self.dataset_add_button.clicked.connect(self._add_photos)
        layout.addWidget(self.dataset_grid, 1)
        layout.addWidget(self.dataset_add_button)
        return page

    def _set_tab(self, name: str) -> None:
        self.current_tab = name
        mapping = {"inspect": 0, "console": 1, "dataset": 2}
        self.sidebar_stack.setCurrentIndex(mapping[name])
        self.inspect_tab.setChecked(name == "inspect")
        self.console_tab.setChecked(name == "console")
        self.dataset_tab.setChecked(name == "dataset")

    def refresh_state(self, initial: bool = False) -> None:
        result = self.api.boot() if initial else self.api.refresh(self.active_project_id)
        state = result.get("state") or {}
        self.state = state
        self.active_project_id = state.get("activeProjectId")
        self._render_state(state)

    def _render_state(self, state: dict[str, Any]) -> None:
        self._render_projects(state.get("projects") or [], state.get("activeProjectId"))
        detail = state.get("activeDetail") or {}
        project = detail.get("project") or {}
        header = detail.get("header") or {}
        toolbar = detail.get("toolbar") or {}
        status_panel = detail.get("statusPanel") or {}
        export_panel = detail.get("exportPanel") or {}
        preview = detail.get("preview") or {}

        self.project_title.setText(header.get("title") or "No project")
        self.subtitle_label.setText(header.get("subtitle") or "")
        status_name = (project.get("status") or "idle").upper()
        badge_color = status_color(project.get("status"))
        self.status_badge.setText(status_name)
        self.status_badge.setStyleSheet(
            f"background:rgba(0,0,0,0.0); color:{badge_color}; border:1px solid {badge_color}; border-radius:12px; padding:7px 14px; font-size:12px; font-weight:700;"
        )
        self.add_dataset_button.setEnabled(bool(toolbar.get("canAddPhotos")))
        self.train_button.setEnabled(bool(toolbar.get("canTrain")))
        self.stop_button.setEnabled(bool(toolbar.get("canStop")))
        self.export_button.setEnabled(bool(toolbar.get("canOpenExport")))
        self.progress_percent.setText(status_panel.get("progressLabel") or "0%")
        self.progress_status.setText(status_panel.get("statusText") or "Ready")
        self.progress_meta.setText((status_panel.get("timeTotal") or "--") + "   |   " + (status_panel.get("stage") or "--"))
        self.progress_loss.setText(status_panel.get("finalLoss") or "--")
        self.export_body.setText(export_panel.get("body") or "")
        self.export_ply_button.setEnabled(bool(export_panel.get("canExport")))
        self.export_sketchup_button.setEnabled(bool(export_panel.get("canExport")))
        self.viewport.set_preview_state(preview, (detail.get("hud") or {}).get("performance") or "0.0 FPS")
        self._render_properties(detail.get("propertiesPanel") or {})
        self._render_logs(detail.get("logs") or "")
        self._render_photos(detail.get("photos") or [])

    def _render_projects(self, projects: list[dict[str, Any]], active_id: str | None) -> None:
        while self.project_cards:
            card = self.project_cards.pop()
            card.setParent(None)
            card.deleteLater()
        stretch = self.projects_layout.takeAt(self.projects_layout.count() - 1)
        for summary in projects:
            card = ProjectCard()
            card.set_summary(summary, summary["id"] == active_id)
            card.clicked.connect(lambda project_id=summary["id"]: self._select_project(project_id))
            self.projects_layout.addWidget(card)
            self.project_cards.append(card)
        if stretch is not None:
            self.projects_layout.addItem(stretch)

    def _render_properties(self, panel: dict[str, Any]) -> None:
        while self.properties_layout.count():
            item = self.properties_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for item in panel.get("items") or []:
            row = QtWidgets.QFrame()
            layout = QtWidgets.QVBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)
            top = QtWidgets.QHBoxLayout()
            top.setContentsMargins(0, 0, 0, 0)
            top.setSpacing(8)
            label = QtWidgets.QLabel(item.get("label") or "")
            label.setStyleSheet("font-size:13px; font-weight:700; color:#A1A1AA;")
            top.addWidget(label)
            top.addStretch(1)
            if item.get("copyable"):
                copy_button = QtWidgets.QPushButton("Copy Path")
                copy_button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
                copy_button.setStyleSheet(
                    "QPushButton{background:rgba(255,255,255,0.05); color:#E4E4E7; border:1px solid rgba(255,255,255,0.08); border-radius:10px; padding:6px 10px; font-size:11px; font-weight:700;}"
                    "QPushButton:hover{background:rgba(255,255,255,0.09);}"
                )
                copy_button.clicked.connect(lambda _checked=False, text=item.get("value") or "": self._copy_text(text))
                top.addWidget(copy_button)
            value = QtWidgets.QLabel(item.get("value") or "")
            value.setWordWrap(True)
            value.setStyleSheet("font-size:14px; color:#F4F4F5;")
            layout.addLayout(top)
            layout.addWidget(value)
            self.properties_layout.addWidget(row)
        self.properties_layout.addStretch(1)

    def _render_logs(self, text: str) -> None:
        self.console_page.setPlainText(text or "")
        cursor = self.console_page.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        self.console_page.setTextCursor(cursor)

    def _render_photos(self, photos: list[dict[str, Any]]) -> None:
        self.dataset_grid.clear()
        for photo in photos:
            item = QtWidgets.QListWidgetItem(photo.get("name") or "")
            photo_path = photo.get("path") or ""
            pixmap = QtGui.QPixmap(photo_path)
            if not pixmap.isNull():
                item.setIcon(QtGui.QIcon(pixmap.scaled(92, 92, QtCore.Qt.AspectRatioMode.KeepAspectRatioByExpanding, QtCore.Qt.TransformationMode.SmoothTransformation)))
            self.dataset_grid.addItem(item)

    def _consume_result(self, result: dict[str, Any]) -> None:
        if not result.get("ok"):
            QtWidgets.QMessageBox.warning(self, "Companion", result.get("error") or "Action failed.")
        state = result.get("state")
        if state:
            self.state = state
            self.active_project_id = state.get("activeProjectId")
            self._render_state(state)

    def _select_project(self, project_id: str) -> None:
        self.active_project_id = project_id
        self._consume_result(self.api.refresh(project_id))

    def _create_project(self) -> None:
        dialog = CreateProjectDialog(self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        if not dialog.project_name:
            QtWidgets.QMessageBox.information(self, "Companion", "Enter a project name first.")
            return
        self._consume_result(self.api.create_project(dialog.project_name))

    def _create_sample_project(self) -> None:
        self._consume_result(self.api.create_sample_project())

    def _add_photos(self) -> None:
        self._consume_result(self.api.add_photos(self.active_project_id))

    def _start_training(self) -> None:
        dialog = TrainStepsDialog(self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._consume_result(self.api.start_job(self.active_project_id, dialog.steps))

    def _restart_training(self) -> None:
        dialog = TrainStepsDialog(self)
        if dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._consume_result(self.api.restart_job(self.active_project_id, dialog.steps))

    def _stop_training(self) -> None:
        self._consume_result(self.api.stop_job(self.active_project_id))

    def _open_export_folder(self) -> None:
        self._consume_result(self.api.open_export_folder(self.active_project_id))

    def _copy_text(self, text: str) -> None:
        QtWidgets.QApplication.clipboard().setText(text or "")


def launch(plugin_root: str | None = None) -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    font = QtGui.QFont("Segoe UI", 10)
    app.setFont(font)
    window = QtCompanionWindow(plugin_root=plugin_root)
    window.api.attach_window(window)
    window.show()
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-root", default=None)
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(APP_VERSION)
        return 0
    return launch(plugin_root=args.plugin_root)
