from __future__ import annotations

import html
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PIL import Image
from PySide6 import QtCore, QtGui, QtSvg, QtWidgets

from . import paths, store
from .gaussian_gasp import read_gaussian_gasp_metadata
from .native_preview import preview_runtime_available, preview_runtime_error
from .pipeline import ensure_project_camera_manifests, ingest_media_sources, list_project_images
from .ply import read_preview_points
from .preview_scene import preview_scene_path
from .scene_import import IMPORT_MODE_CONVERT, IMPORT_MODE_DIRECT, import_gaussian_scene_file


LIVE_STALE_SECONDS = 120
LIVE_ASK_STOP_SECONDS = 20 * 60


class ThemedDialog(QtWidgets.QDialog):
    def __init__(self, title: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlags(
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(False)
        self.setMinimumWidth(500)
        self._initial_focus: QtWidgets.QWidget | None = None
        self._loop: QtCore.QEventLoop | None = None

        self.setStyleSheet(
            "QDialog{background:transparent;}"
            "QFrame#card{background:#1B1B20; border:1px solid rgba(255,255,255,0.10); border-radius:18px;}"
            "QLabel{color:#FAFAFA;}"
            "QLineEdit,QSpinBox,QComboBox{background:#111116; color:#FAFAFA; border:1px solid rgba(255,255,255,0.12); border-radius:10px; padding:10px 12px; min-height:20px;}"
            "QLineEdit:focus,QSpinBox:focus,QComboBox:focus{border:1px solid #FF5400;}"
            "QComboBox::drop-down{border:none; width:24px;}"
            "QComboBox::down-arrow{image:none; width:0; height:0;}"
            "QComboBox QAbstractItemView{background:#111116; color:#FAFAFA; border:1px solid rgba(255,255,255,0.12); selection-background-color:#FF5400; selection-color:#FFFFFF; outline:none;}"
            "QAbstractSpinBox::up-button,QAbstractSpinBox::down-button{width:0px; border:none;}"
            "QCheckBox{color:#E4E4E7; font-size:13px; font-weight:500; spacing:10px;}"
            "QCheckBox::indicator{width:18px; height:18px; border-radius:6px; border:1px solid rgba(255,255,255,0.16); background:#111116;}"
            "QCheckBox::indicator:checked{background:#FF5400; border:1px solid #FF5400;}"
            "QPushButton{min-width:88px; min-height:34px; border-radius:8px; font-size:12px; font-weight:700; padding:0 14px;}"
        )

        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(0)

        self.card = QtWidgets.QFrame()
        self.card.setObjectName("card")
        shadow = QtWidgets.QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(38)
        shadow.setOffset(0, 10)
        shadow.setColor(QtGui.QColor(0, 0, 0, 180))
        self.card.setGraphicsEffect(shadow)
        root_layout.addWidget(self.card)

        self._layout = QtWidgets.QVBoxLayout(self.card)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self.header = QtWidgets.QWidget()
        header_layout = QtWidgets.QHBoxLayout(self.header)
        header_layout.setContentsMargins(24, 18, 24, 16)
        header_layout.setSpacing(10)

        self.header_badge = QtWidgets.QFrame()
        self.header_badge.setFixedSize(28, 28)
        self.header_badge.setStyleSheet("QFrame{background:rgba(244,63,94,0.12); border-radius:8px;}")
        badge_layout = QtWidgets.QVBoxLayout(self.header_badge)
        badge_layout.setContentsMargins(0, 0, 0, 0)
        badge_layout.setSpacing(0)
        self.header_icon = QtWidgets.QLabel()
        self.header_icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        badge_layout.addWidget(self.header_icon)
        header_layout.addWidget(self.header_badge, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)

        self._title = QtWidgets.QLabel(title)
        self._title.setStyleSheet("font-size:15px; font-weight:700; color:#FFFFFF;")
        header_layout.addWidget(self._title, 1, QtCore.Qt.AlignmentFlag.AlignVCenter)
        self._layout.addWidget(self.header)

        divider = QtWidgets.QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("QFrame{background:rgba(255,255,255,0.08); border:none;}")
        self._layout.addWidget(divider)

        self.content = QtWidgets.QWidget()
        self.body = QtWidgets.QVBoxLayout(self.content)
        self.body.setContentsMargins(24, 20, 24, 24)
        self.body.setSpacing(16)
        self._layout.addWidget(self.content)

        self.buttons = QtWidgets.QHBoxLayout()
        self.buttons.setSpacing(10)
        self.buttons.addStretch(1)
        self.body.addLayout(self.buttons)

    def run(self) -> int:
        self.show()
        loop = QtCore.QEventLoop(self)
        self._loop = loop
        self.finished.connect(loop.quit, QtCore.Qt.ConnectionType.SingleShotConnection)
        loop.exec()
        self._loop = None
        return self.result()

    def set_initial_focus(self, widget: QtWidgets.QWidget) -> None:
        self._initial_focus = widget

    def set_header_icon(self, icon_name: str, tone: str) -> None:
        icon_path = Path(__file__).resolve().parent / "assets" / "icons_png" / f"{icon_name}-{tone}.png"
        if icon_path.exists():
            pixmap = QtGui.QPixmap(str(icon_path)).scaled(
                14,
                14,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            self.header_icon.setPixmap(pixmap)
        else:
            self.header_icon.clear()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self.adjustSize()
        parent = self.parentWidget()
        if not parent:
            return
        parent_rect = parent.rect()
        center = parent.mapToGlobal(parent_rect.center())
        x = center.x() - self.width() // 2
        y = center.y() - self.height() // 2
        self.move(x, y)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.raise_()
        self.activateWindow()
        if self._initial_focus is not None:
            QtCore.QTimer.singleShot(0, self._focus_initial_widget)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if self.isVisible() and event.type() == QtCore.QEvent.Type.MouseButtonPress:
            widget = obj if isinstance(obj, QtWidgets.QWidget) else None
            if widget is None or not (widget is self or self.isAncestorOf(widget)):
                mouse_event = event if isinstance(event, QtGui.QMouseEvent) else None
                if mouse_event is not None and not self.frameGeometry().contains(mouse_event.globalPosition().toPoint()):
                    self.reject()
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def _focus_initial_widget(self) -> None:
        if self._initial_focus is None:
            return
        self._initial_focus.setFocus(QtCore.Qt.FocusReason.ActiveWindowFocusReason)
        if isinstance(self._initial_focus, QtWidgets.QLineEdit):
            self._initial_focus.selectAll()

    def add_button(self, text: str, *, accent: str | None = None) -> QtWidgets.QPushButton:
        button = QtWidgets.QPushButton(text)
        if accent == "danger":
            button.setStyleSheet(
                "QPushButton{background:#F43F5E; color:#FFFFFF; border:1px solid transparent;}"
                "QPushButton:hover{background:#FF5572;}"
            )
        elif accent == "primary":
            button.setStyleSheet(
                "QPushButton{background:#FF5400; color:#FFFFFF; border:1px solid transparent;}"
                "QPushButton:hover{background:#FF6A1E;}"
            )
        else:
            button.setStyleSheet(
                "QPushButton{background:#101014; color:#E4E4E7; border:1px solid rgba(255,255,255,0.10);}"
                "QPushButton:hover{background:#18181F;}"
            )
        self.buttons.addWidget(button)
        return button


class RenameProjectDialog(ThemedDialog):
    def __init__(self, project_name: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Rename Project", parent)
        self.header_badge.setStyleSheet("QFrame{background:rgba(255,84,0,0.12); border-radius:8px;}")
        self.set_header_icon("pen-square", "accent")
        self.name_label = QtWidgets.QLabel("Project name")
        self.name_label.setStyleSheet("font-size:12px; font-weight:500; color:#A1A1AA;")
        self.body.insertWidget(0, self.name_label)
        self.name_input = QtWidgets.QLineEdit(project_name)
        self.name_input.setPlaceholderText("Project name")
        self.body.insertWidget(1, self.name_input)
        self.cancel_button = self.add_button("Cancel")
        self.rename_button = self.add_button("Rename", accent="primary")
        self.cancel_button.clicked.connect(self.reject)
        self.rename_button.clicked.connect(self.accept)
        self.name_input.returnPressed.connect(self.accept)
        self.set_initial_focus(self.name_input)

    @property
    def project_name(self) -> str:
        return self.name_input.text().strip()


class DeleteProjectDialog(ThemedDialog):
    def __init__(self, project_name: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Delete Project", parent)
        self.header_badge.setStyleSheet("QFrame{background:rgba(244,63,94,0.12); border-radius:8px;}")
        self.set_header_icon("trash-2", "rose")
        message = QtWidgets.QLabel(
            f'Are you sure you want to delete "{project_name}"? This removes the project entry but keeps files on disk.'
        )
        message.setWordWrap(True)
        message.setStyleSheet("font-size:13px; color:#A1A1AA;")
        self.body.insertWidget(0, message)
        self.cancel_button = self.add_button("Cancel")
        self.delete_button = self.add_button("Delete", accent="danger")
        self.cancel_button.clicked.connect(self.reject)
        self.delete_button.clicked.connect(self.accept)


class SplatImportModeDialog(ThemedDialog):
    def __init__(self, filename: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Import Gaussian Scene", parent)
        self.setMinimumWidth(560)
        self.header_badge.setStyleSheet("QFrame{background:rgba(255,84,0,0.12); border-radius:8px;}")
        self.set_header_icon("box", "accent")
        self.mode: str | None = None

        file_label = QtWidgets.QLabel(Path(filename).name)
        file_label.setWordWrap(True)
        file_label.setStyleSheet("font-size:14px; font-weight:700; color:#FFFFFF;")
        self.body.insertWidget(0, file_label)

        message = QtWidgets.QLabel(
            "Choose how to load this splat file. GASP creates a fast cache for instant reloads; direct load keeps the selected file as the preview source."
        )
        message.setWordWrap(True)
        message.setStyleSheet("font-size:13px; color:#A1A1AA; line-height:1.35;")
        self.body.insertWidget(1, message)

        self.cancel_button = self.add_button("Cancel")
        self.direct_button = self.add_button("Load Directly")
        self.convert_button = self.add_button("Create / Use GASP", accent="primary")
        self.cancel_button.clicked.connect(self.reject)
        self.direct_button.clicked.connect(lambda: self._accept_mode(IMPORT_MODE_DIRECT))
        self.convert_button.clicked.connect(lambda: self._accept_mode(IMPORT_MODE_CONVERT))

    def _accept_mode(self, mode: str) -> None:
        self.mode = mode
        self.accept()


class PopupMenuItemFrame(QtWidgets.QFrame):
    hovered = QtCore.Signal(object, object)
    activated = QtCore.Signal(object)

    def __init__(self, controller: "QtStateController", spec: dict[str, Any], parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self.spec = spec
        self._hovered = False
        self.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.setMouseTracking(True)
        self.setObjectName("menuItemFrame")
        self.setFixedHeight(28)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 5, 12, 5)
        layout.setSpacing(10)

        self.icon_label = QtWidgets.QLabel()
        self.icon_label.setFixedSize(14, 14)
        self.icon_label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.icon_label, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)

        self.text_label = QtWidgets.QLabel(str(spec.get("label") or ""))
        self.text_label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.text_label.setStyleSheet("background:transparent;")
        text_font = QtGui.QFont("Outfit")
        text_font.setPixelSize(12)
        text_font.setWeight(QtGui.QFont.Weight.Medium)
        self.text_label.setFont(text_font)
        layout.addWidget(self.text_label, 1, QtCore.Qt.AlignmentFlag.AlignVCenter)

        self.shortcut_label = QtWidgets.QLabel(str(spec.get("shortcut") or ""))
        self.shortcut_label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        shortcut_font = QtGui.QFont("JetBrains Mono")
        shortcut_font.setPixelSize(9)
        shortcut_font.setWeight(QtGui.QFont.Weight.Medium)
        self.shortcut_label.setFont(shortcut_font)
        layout.addWidget(self.shortcut_label, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)

        self.arrow_label = QtWidgets.QLabel()
        self.arrow_label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.arrow_label.setFixedSize(14, 14)
        self.arrow_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.arrow_label, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)

        self._update_visual()

    def set_hovered(self, hovered: bool) -> None:
        hovered = bool(hovered)
        if self._hovered == hovered:
            return
        self._hovered = hovered
        self._update_visual()

    def _update_visual(self) -> None:
        bg = "rgba(255,255,255,0.08)" if self._hovered else "transparent"
        self.setStyleSheet(f"QFrame#menuItemFrame{{background:{bg}; border:none; border-radius:6px;}}")
        self.text_label.setStyleSheet(
            f"background:transparent; color:{'#FFFFFF' if self._hovered else '#D4D4D8'};"
        )
        self.shortcut_label.setStyleSheet(
            f"background:transparent; color:{'#71717A' if self.spec.get('shortcut') else 'transparent'};"
        )
        icon_name = str(self.spec.get("icon") or "")
        if icon_name:
            pixmap = self._controller._menu_icon_pixmap(
                icon_name,
                "#00F0FF" if self._hovered else "#A1A1AA",
                14,
            )
            self.icon_label.setPixmap(pixmap)
        else:
            self.icon_label.clear()
        if self.spec.get("submenu"):
            arrow_pixmap = self._controller._menu_icon_pixmap(
                "chevron-right",
                "#FFFFFF" if self._hovered else "#71717A",
                14,
            )
            self.arrow_label.setPixmap(arrow_pixmap)
        else:
            self.arrow_label.clear()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self.set_hovered(True)
        self.hovered.emit(self, self.spec)
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self.set_hovered(False)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.activated.emit(self.spec)
        super().mouseReleaseEvent(event)


class PopupMenuWindow(QtWidgets.QWidget):
    def __init__(
        self,
        controller: "QtStateController",
        menu_name: str,
        items: list[dict[str, Any]],
        *,
        root_menu_name: str,
        parent_popup: "PopupMenuWindow | None" = None,
    ) -> None:
        window_parent = controller._app_window()
        super().__init__(window_parent)
        self._controller = controller
        self._menu_name = menu_name
        self._root_menu_name = root_menu_name
        self._parent_popup = parent_popup
        self._child_popup: PopupMenuWindow | None = None
        self._item_widgets: list[PopupMenuItemFrame] = []

        self.setWindowFlags(
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.card = QtWidgets.QFrame()
        self.card.setObjectName("menuCard")
        self.card.setStyleSheet(
            "QFrame#menuCard{"
            "background:rgba(14,14,18,242);"
            "border:1px solid rgba(255,255,255,0.10);"
            "border-radius:12px;"
            "}"
        )
        shadow = QtWidgets.QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(50)
        shadow.setOffset(0, 20)
        shadow.setColor(QtGui.QColor(0, 0, 0, 204))
        self.card.setGraphicsEffect(shadow)
        root_layout.addWidget(self.card)

        card_layout = QtWidgets.QVBoxLayout(self.card)
        card_layout.setContentsMargins(6, 6, 6, 6)
        card_layout.setSpacing(0)

        self.setMinimumWidth(268 if parent_popup is None else 190)

        for spec in items:
            if spec.get("type") == "separator":
                separator = QtWidgets.QFrame()
                separator.setFixedHeight(14)
                sep_layout = QtWidgets.QVBoxLayout(separator)
                sep_layout.setContentsMargins(12, 6, 12, 6)
                sep_layout.setSpacing(0)
                line = QtWidgets.QFrame()
                line.setFixedHeight(1)
                line.setStyleSheet("background:rgba(255,255,255,0.10); border:none;")
                sep_layout.addWidget(line)
                card_layout.addWidget(separator)
                continue

            item = PopupMenuItemFrame(controller, spec, self.card)
            item.hovered.connect(self._on_item_hovered)
            item.activated.connect(self._on_item_activated)
            self._item_widgets.append(item)
            card_layout.addWidget(item)

        self.adjustSize()

    def open_at(self, global_pos: QtCore.QPoint) -> None:
        self._controller.cancelMenuClose()
        self.adjustSize()
        self.move(global_pos)
        self.show()
        self.raise_()

    def contains_global_pos(self, global_pos: QtCore.QPoint) -> bool:
        if self.isVisible() and self.frameGeometry().contains(global_pos):
            return True
        if self._child_popup and self._child_popup.contains_global_pos(global_pos):
            return True
        return False

    def close_chain(self) -> None:
        if self._child_popup is not None:
            self._child_popup.close_chain()
            self._child_popup.deleteLater()
            self._child_popup = None
        self.hide()

    def enterEvent(self, event: QtCore.QEvent) -> None:
        self._controller.cancelMenuClose()
        super().enterEvent(event)

    def leaveEvent(self, event: QtCore.QEvent) -> None:
        self._controller.scheduleMenuClose()
        super().leaveEvent(event)

    def _open_submenu(self, spec: dict[str, Any], source_item: PopupMenuItemFrame) -> None:
        submenu = spec.get("submenu")
        if not submenu:
            self._close_submenu()
            return
        submenu_name = str(spec.get("label") or "submenu")
        if self._child_popup is not None and self._child_popup._menu_name == submenu_name:
            point = source_item.mapToGlobal(QtCore.QPoint(source_item.width() - 4, 0))
            self._child_popup.open_at(point)
            return
        self._close_submenu()
        child = PopupMenuWindow(
            self._controller,
            submenu_name,
            list(submenu),
            root_menu_name=self._root_menu_name,
            parent_popup=self,
        )
        self._child_popup = child
        point = source_item.mapToGlobal(QtCore.QPoint(source_item.width() - 4, 0))
        child.open_at(point)

    def _close_submenu(self) -> None:
        if self._child_popup is None:
            return
        self._child_popup.close_chain()
        self._child_popup.deleteLater()
        self._child_popup = None

    def _on_item_hovered(self, source_item: PopupMenuItemFrame, spec: dict[str, Any]) -> None:
        submenu = spec.get("submenu")
        if submenu:
            self._open_submenu(spec, source_item)
        else:
            self._close_submenu()

    def _on_item_activated(self, spec: dict[str, Any]) -> None:
        submenu = spec.get("submenu")
        if submenu:
            return
        self._controller._execute_menu_action(str(spec.get("action") or ""))


class TrainModelDialog(ThemedDialog):
    PRESET_OPTIONS = [
        ("compact", "Compact"),
        ("balanced", "Balanced"),
        ("high", "High"),
    ]
    STRATEGY_OPTIONS = [
        ("auto", "Auto"),
        ("mcmc", "MCMC"),
        ("default", "Default"),
    ]
    RASTERIZE_OPTIONS = [
        ("auto", "Auto"),
        ("antialiased", "Antialiased"),
        ("classic", "Classic"),
    ]
    SFM_MATCH_OPTIONS = [
        ("auto", "Auto"),
        ("exhaustive", "Exhaustive"),
        ("sequential", "Sequential"),
        ("spatial", "Spatial"),
    ]

    def __init__(
        self,
        project_name: str,
        settings: dict[str, Any],
        *,
        restart: bool,
        can_preserve_sfm_cache: bool = False,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__("Restart Training" if restart else "Train Model", parent)
        self.setMinimumWidth(620)
        self.header_badge.setStyleSheet("QFrame{background:rgba(255,84,0,0.12); border-radius:8px;}")
        self.set_header_icon("play", "accent")

        subtitle = QtWidgets.QLabel(
            f'Configure training for "{project_name}". These settings are saved for this project and reused next time.'
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("font-size:13px; color:#A1A1AA; line-height:1.35;")
        self.body.insertWidget(0, subtitle)

        grid_host = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)

        self.steps_input = QtWidgets.QSpinBox()
        self.steps_input.setRange(200, 20_000)
        self.steps_input.setSingleStep(100)
        self.steps_input.setValue(int(settings.get("train_steps", 3000)))

        self.preset_combo = QtWidgets.QComboBox()
        for value, label in self.PRESET_OPTIONS:
            self.preset_combo.addItem(label, value)
        self._set_combo_value(self.preset_combo, str(settings.get("quality_preset", "balanced")))

        self.strategy_combo = QtWidgets.QComboBox()
        for value, label in self.STRATEGY_OPTIONS:
            self.strategy_combo.addItem(label, value)
        self._set_combo_value(self.strategy_combo, str(settings.get("strategy_name", "auto")))

        self.rasterize_combo = QtWidgets.QComboBox()
        for value, label in self.RASTERIZE_OPTIONS:
            self.rasterize_combo.addItem(label, value)
        self._set_combo_value(self.rasterize_combo, str(settings.get("rasterize_mode", "auto")))

        self.sfm_match_combo = QtWidgets.QComboBox()
        for value, label in self.SFM_MATCH_OPTIONS:
            self.sfm_match_combo.addItem(label, value)
        self._set_combo_value(self.sfm_match_combo, str(settings.get("sfm_match_mode", "auto")))

        self.resolution_input = QtWidgets.QSpinBox()
        self.resolution_input.setRange(256, 2048)
        self.resolution_input.setSingleStep(64)
        self.resolution_input.setValue(int(settings.get("train_resolution", 640)))

        self.sfm_image_size_input = QtWidgets.QSpinBox()
        self.sfm_image_size_input.setRange(640, 4096)
        self.sfm_image_size_input.setSingleStep(160)
        self.sfm_image_size_input.setValue(int(settings.get("sfm_max_image_size", 1600)))

        self.sh_degree_input = QtWidgets.QSpinBox()
        self.sh_degree_input.setRange(0, 4)
        self.sh_degree_input.setValue(int(settings.get("sh_degree", 3)))

        self.max_gaussians_input = QtWidgets.QSpinBox()
        self.max_gaussians_input.setRange(0, 5_000_000)
        self.max_gaussians_input.setSingleStep(10_000)
        self.max_gaussians_input.setSpecialValueText("Auto (0)")
        self.max_gaussians_input.setToolTip("0 keeps the automatic splat budget. Type a number like 800000 or 1200000 to cap growth manually.")
        self.max_gaussians_input.setValue(int(settings.get("max_gaussians", 0)))

        self.validation_fraction_input = QtWidgets.QDoubleSpinBox()
        self.validation_fraction_input.setRange(0.0, 0.45)
        self.validation_fraction_input.setSingleStep(0.05)
        self.validation_fraction_input.setDecimals(2)
        self.validation_fraction_input.setValue(float(settings.get("validation_fraction", 0.18)))

        self.random_background_checkbox = QtWidgets.QCheckBox("Random background compositing")
        self.random_background_checkbox.setChecked(bool(settings.get("random_background", True)))

        self.revised_opacity_checkbox = QtWidgets.QCheckBox("Use revised opacity handling")
        self.revised_opacity_checkbox.setChecked(bool(settings.get("revised_opacity", True)))

        self.exposure_compensation_checkbox = QtWidgets.QCheckBox("Exposure compensation")
        self.exposure_compensation_checkbox.setChecked(bool(settings.get("enable_exposure_compensation", True)))

        self.preserve_sfm_cache_checkbox = QtWidgets.QCheckBox("Reuse cached COLMAP cameras")
        self.preserve_sfm_cache_checkbox.setChecked(bool(settings.get("preserve_sfm_cache")) and can_preserve_sfm_cache)
        self.preserve_sfm_cache_checkbox.setEnabled(can_preserve_sfm_cache)
        self.preserve_sfm_cache_checkbox.setToolTip(
            "Keeps the recovered camera poses and sparse points, then restarts only Gaussian training. "
            "Turn this off for a full SfM rebuild."
        )

        self._add_field(grid, 0, 0, "Training steps", self.steps_input)
        self._add_field(grid, 0, 1, "Quality preset", self.preset_combo)
        self._add_field(grid, 1, 0, "Strategy", self.strategy_combo)
        self._add_field(grid, 1, 1, "Max splats (0 = Auto)", self.max_gaussians_input)
        self._add_field(grid, 2, 0, "Train resolution", self.resolution_input)
        self._add_field(grid, 2, 1, "SfM image size", self.sfm_image_size_input)
        self._add_field(grid, 3, 0, "SH degree", self.sh_degree_input)
        self._add_field(grid, 3, 1, "Validation split", self.validation_fraction_input)
        self._add_field(grid, 4, 0, "Rasterization", self.rasterize_combo)
        self._add_field(grid, 4, 1, "SfM matching", self.sfm_match_combo)

        options_box = QtWidgets.QFrame()
        options_box.setStyleSheet("QFrame{background:#111116; border:1px solid rgba(255,255,255,0.10); border-radius:12px;}")
        options_layout = QtWidgets.QVBoxLayout(options_box)
        options_layout.setContentsMargins(14, 12, 14, 12)
        options_layout.setSpacing(10)
        options_layout.addWidget(self.exposure_compensation_checkbox)
        options_layout.addWidget(self.random_background_checkbox)
        options_layout.addWidget(self.revised_opacity_checkbox)
        if restart:
            options_layout.addWidget(self.preserve_sfm_cache_checkbox)
            cache_note = QtWidgets.QLabel(
                "On: skip feature extraction/matching and reuse recovered cameras. Off: full restart, including COLMAP."
                if can_preserve_sfm_cache
                else "No cached COLMAP reconstruction was found, so this restart will rebuild SfM."
            )
            cache_note.setWordWrap(True)
            cache_note.setStyleSheet("font-size:11px; color:#71717A;")
            options_layout.addWidget(cache_note)
        grid.addWidget(options_box, 5, 0, 1, 2)

        note = QtWidgets.QLabel(
            "Tip: use Auto + Balanced + Antialiased for real captures. For long videos, keep SfM image size near 1280 and type a Max splats cap such as 800000 or 1200000; 0 means Auto."
        )
        note.setWordWrap(True)
        note.setStyleSheet("font-size:12px; color:#71717A;")
        grid.addWidget(note, 6, 0, 1, 2)

        self.body.insertWidget(1, grid_host)

        self.cancel_button = self.add_button("Cancel")
        self.confirm_button = self.add_button("Restart" if restart else "Train", accent="primary")
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self.accept)
        self.set_initial_focus(self.steps_input.lineEdit())

    @staticmethod
    def _set_combo_value(combo: QtWidgets.QComboBox, value: str) -> None:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    @staticmethod
    def _field_label(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setStyleSheet("font-size:12px; font-weight:500; color:#A1A1AA;")
        return label

    def _add_field(
        self,
        layout: QtWidgets.QGridLayout,
        row: int,
        column: int,
        label_text: str,
        widget: QtWidgets.QWidget,
    ) -> None:
        holder = QtWidgets.QWidget()
        holder_layout = QtWidgets.QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setSpacing(8)
        holder_layout.addWidget(self._field_label(label_text))
        holder_layout.addWidget(widget)
        layout.addWidget(holder, row, column)

    @property
    def training_settings(self) -> dict[str, Any]:
        settings = store.default_job_settings(force_restart=False)
        settings.update(
            {
                "train_steps": int(self.steps_input.value()),
                "quality_preset": str(self.preset_combo.currentData()),
                "strategy_name": str(self.strategy_combo.currentData()),
                "rasterize_mode": str(self.rasterize_combo.currentData()),
                "sfm_match_mode": str(self.sfm_match_combo.currentData()),
                "train_resolution": int(self.resolution_input.value()),
                "sfm_max_image_size": int(self.sfm_image_size_input.value()),
                "sh_degree": int(self.sh_degree_input.value()),
                "max_gaussians": int(self.max_gaussians_input.value()),
                "validation_fraction": float(self.validation_fraction_input.value()),
                "random_background": bool(self.random_background_checkbox.isChecked()),
                "revised_opacity": bool(self.revised_opacity_checkbox.isChecked()),
                "enable_exposure_compensation": bool(self.exposure_compensation_checkbox.isChecked()),
                "preserve_sfm_cache": bool(self.preserve_sfm_cache_checkbox.isChecked()),
            }
        )
        return settings


class QtStateController(QtCore.QObject):
    stateChanged = QtCore.Signal()
    menuPopupVisibleChanged = QtCore.Signal()
    activeMenuNameChanged = QtCore.Signal()
    activeToolChanged = QtCore.Signal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        paths.ensure_runtime_dirs()
        store.init_db()
        self._state: dict[str, Any] = {}
        self._active_project_id: str | None = None
        self._preview_path: str | None = None
        self._preview_stamp: int | None = None
        self._preview_stats: dict[str, Any] | None = None
        self._menu_popup_visible = False
        self._active_menu_name = ""
        self._active_tool = "projects"
        self._menu_definitions: dict[str, list[dict[str, Any]]] = {}
        self._menu_root_popup: PopupMenuWindow | None = None
        self._menu_icon_cache: dict[tuple[str, str, int, float], QtGui.QPixmap] = {}
        self._menu_hot_zone = QtCore.QRect()
        self._menu_close_timer = QtCore.QTimer(self)
        self._menu_close_timer.setSingleShot(True)
        self._menu_close_timer.setInterval(160)
        self._menu_close_timer.timeout.connect(self._close_menus_if_pointer_outside)
        self.refresh()

    @QtCore.Property("QVariantMap", notify=stateChanged)
    def state(self) -> dict[str, Any]:
        return self._state

    @QtCore.Property(bool, notify=menuPopupVisibleChanged)
    def menuPopupVisible(self) -> bool:
        return self._menu_popup_visible

    @QtCore.Property(str, notify=activeMenuNameChanged)
    def activeMenuName(self) -> str:
        return self._active_menu_name

    @QtCore.Property(str, notify=activeToolChanged)
    def activeTool(self) -> str:
        return self._active_tool

    @QtCore.Slot()
    def minimizeWindow(self) -> None:
        w = self._app_window()
        if w:
            w.showMinimized()

    @QtCore.Slot()
    def maximizeWindow(self) -> None:
        w = self._app_window()
        if w:
            if w.isMaximized():
                w.showNormal()
            else:
                w.showMaximized()

    @QtCore.Slot()
    def closeWindow(self) -> None:
        w = self._app_window()
        if w:
            w.close()

    @QtCore.Slot(float, float)
    def startDrag(self, gx: float, gy: float) -> None:
        w = self._app_window()
        if w:
            w._drag_pos = QtCore.QPoint(int(gx), int(gy))

    @QtCore.Slot(float, float)
    def updateDrag(self, gx: float, gy: float) -> None:
        w = self._app_window()
        if w and getattr(w, "_drag_pos", None) is not None:
            delta = QtCore.QPoint(int(gx), int(gy)) - w._drag_pos
            w.move(w.pos() + delta)
            w._drag_pos = QtCore.QPoint(int(gx), int(gy))

    @QtCore.Slot()
    def endDrag(self) -> None:
        w = self._app_window()
        if w:
            w._drag_pos = None

    def _app_window(self) -> QtWidgets.QWidget | None:
        p = self.parent()
        while p and not isinstance(p, QtWidgets.QMainWindow):
            p = p.parent()
        return p

    def _set_menu_popup_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if self._menu_popup_visible == visible:
            return
        self._menu_popup_visible = visible
        app = QtWidgets.QApplication.instance()
        if app is not None:
            if visible:
                app.installEventFilter(self)
            else:
                app.removeEventFilter(self)
        self.menuPopupVisibleChanged.emit()

    def _set_active_menu_name(self, menu_name: str) -> None:
        menu_name = menu_name or ""
        if self._active_menu_name == menu_name:
            return
        self._active_menu_name = menu_name
        self.activeMenuNameChanged.emit()

    def _icon_path(self, name: str) -> Path | None:
        if not name:
            return None
        path = Path(__file__).resolve().parent / "assets" / "icons" / f"{name}.svg"
        return path if path.exists() else None

    def _menu_icon_pixmap(self, name: str, color_hex: str, size: int) -> QtGui.QPixmap:
        app = QtWidgets.QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        dpr = max(1.0, float(screen.devicePixelRatio() if screen is not None else 1.0))
        cache_key = (name, color_hex, int(size), round(dpr, 2))
        cached = self._menu_icon_cache.get(cache_key)
        if cached is not None:
            return cached

        icon_path = self._icon_path(name)
        if icon_path is None:
            return QtGui.QPixmap()

        pixel_size = max(1, int(round(int(size) * dpr)))
        image = QtGui.QImage(pixel_size, pixel_size, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QtCore.Qt.GlobalColor.transparent)
        renderer = QtSvg.QSvgRenderer(str(icon_path))
        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.scale(dpr, dpr)
        logical_rect = QtCore.QRectF(0, 0, int(size), int(size))
        renderer.render(painter, logical_rect)
        painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(logical_rect, QtGui.QColor(color_hex))
        painter.end()
        pixmap = QtGui.QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(dpr)
        self._menu_icon_cache[cache_key] = pixmap
        return pixmap

    def _menu_separator(self) -> dict[str, Any]:
        return {"type": "separator"}

    def _menu_item(
        self,
        label: str,
        *,
        icon: str = "",
        shortcut: str = "",
        action: str = "",
        submenu: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "item",
            "label": label,
            "icon": icon,
            "shortcut": shortcut,
            "action": action,
            "submenu": list(submenu or []),
        }

    def _ensure_menus(self) -> None:
        if self._menu_definitions:
            return

        export_menu = [
            self._menu_item("Export as .ply", icon="download"),
            self._menu_item("Export to SketchUp", icon="arrow-up-right"),
            self._menu_separator(),
            self._menu_item("Export Sequence", icon="film"),
        ]
        appearance_menu = [
            self._menu_item("Dark Mode"),
            self._menu_item("Light Mode"),
            self._menu_separator(),
            self._menu_item("High Contrast"),
        ]
        camera_menu = [
            self._menu_item("Fly Mode"),
            self._menu_item("Orbit Mode"),
            self._menu_item("Walk Mode"),
        ]
        transform_menu = [
            self._menu_item("Translate"),
            self._menu_item("Rotate"),
            self._menu_item("Scale"),
        ]
        workspace_layout_menu = [
            self._menu_item("Default"),
            self._menu_item("Training Focused"),
            self._menu_item("Inspection Focused"),
        ]

        self._menu_definitions = {
            "file": [
                self._menu_item("New Project...", icon="folder", shortcut="CTRL+N"),
                self._menu_item("Open Project...", icon="folder", shortcut="CTRL+O", action="importScene"),
                self._menu_separator(),
                self._menu_item("Save", icon="copy", shortcut="CTRL+S"),
                self._menu_item("Save As...", shortcut="CTRL+SHIFT+S"),
                self._menu_separator(),
                self._menu_item("Export", icon="arrow-up-right", submenu=export_menu),
                self._menu_separator(),
                self._menu_item("Exit", shortcut="ALT+F4"),
            ],
            "edit": [
                self._menu_item("Undo", icon="rotate-ccw", shortcut="CTRL+Z"),
                self._menu_item("Redo", icon="rotate-ccw", shortcut="CTRL+Y"),
                self._menu_separator(),
                self._menu_item("Cut", shortcut="CTRL+X"),
                self._menu_item("Copy", icon="copy", shortcut="CTRL+C"),
                self._menu_item("Paste", shortcut="CTRL+V"),
                self._menu_separator(),
                self._menu_item("Preferences", icon="settings", shortcut="CTRL+,"),
            ],
            "view": [
                self._menu_item("Reset Viewport", icon="maximize", shortcut="SPACE"),
                self._menu_separator(),
                self._menu_item("Appearance", submenu=appearance_menu),
                self._menu_item("Show grid", icon="activity"),
                self._menu_item("Toggle Bounding Box", icon="box-select"),
            ],
            "tools": [
                self._menu_item("Camera Tools", icon="camera", submenu=camera_menu),
                self._menu_item("Transform", icon="move", submenu=transform_menu),
                self._menu_separator(),
                self._menu_item("Point Selection", icon="mouse-pointer-2"),
                self._menu_item("Color Picker", icon="pipette"),
            ],
            "window": [
                self._menu_item("Project Explorer"),
                self._menu_item("Properties"),
                self._menu_item("Console", icon="terminal"),
                self._menu_separator(),
                self._menu_item("Workspace Layout", submenu=workspace_layout_menu),
            ],
            "help": [
                self._menu_item("Documentation"),
                self._menu_item("Tutorials"),
                self._menu_separator(),
                self._menu_item("About Gaussian Studio", icon="box"),
            ],
        }

    def _execute_menu_action(self, action_key: str) -> None:
        if action_key == "importScene":
            self.closeAllMenus()
            self.importGaussianSceneDialog()
            return
        self.closeAllMenus()

    @QtCore.Slot()
    def closeAllMenus(self) -> None:
        self._menu_close_timer.stop()
        if self._menu_root_popup is not None:
            self._menu_root_popup.close_chain()
            self._menu_root_popup.deleteLater()
            self._menu_root_popup = None
        self._set_menu_popup_visible(False)
        self._set_active_menu_name("")

    @QtCore.Slot(float, float, float, float)
    def setMenuHotZone(self, gx: float, gy: float, width: float, height: float) -> None:
        self._menu_hot_zone = QtCore.QRect(
            int(gx),
            int(gy),
            max(1, int(width)),
            max(1, int(height)),
        )

    @QtCore.Slot()
    def cancelMenuClose(self) -> None:
        self._menu_close_timer.stop()

    @QtCore.Slot()
    def scheduleMenuClose(self) -> None:
        if self._menu_popup_visible:
            self._menu_close_timer.start()

    def _close_menus_if_pointer_outside(self) -> None:
        if not self._menu_popup_visible:
            return
        global_pos = QtGui.QCursor.pos()
        if self._menu_hot_zone.contains(global_pos):
            return
        if self._menu_root_popup is not None and self._menu_root_popup.contains_global_pos(global_pos):
            return
        self.closeAllMenus()

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if not self._menu_popup_visible or self._menu_root_popup is None:
            return super().eventFilter(obj, event)
        if event.type() == QtCore.QEvent.Type.KeyPress:
            key_event = event if isinstance(event, QtGui.QKeyEvent) else None
            if key_event is not None and key_event.key() == QtCore.Qt.Key.Key_Escape:
                self.closeAllMenus()
                return True
        if event.type() == QtCore.QEvent.Type.MouseButtonPress:
            mouse_event = event if isinstance(event, QtGui.QMouseEvent) else None
            if mouse_event is not None:
                global_pos = mouse_event.globalPosition().toPoint()
                if not self._menu_root_popup.contains_global_pos(global_pos):
                    self.closeAllMenus()
        return super().eventFilter(obj, event)

    @QtCore.Slot(str, float, float)
    def showMenu(self, menu_name: str, gx: float, gy: float) -> None:
        self._ensure_menus()
        self.cancelMenuClose()
        menu_key = (menu_name or "").lower()
        menu_items = self._menu_definitions.get(menu_key)
        if menu_items is None:
            return
        if self._menu_root_popup is not None:
            self._menu_root_popup.close_chain()
            self._menu_root_popup.deleteLater()
            self._menu_root_popup = None
        self._menu_root_popup = PopupMenuWindow(
            self,
            menu_key,
            list(menu_items),
            root_menu_name=menu_key,
            parent_popup=None,
        )
        self._set_active_menu_name(menu_key)
        self._set_menu_popup_visible(True)
        self._menu_root_popup.open_at(QtCore.QPoint(int(gx), int(gy)))

    @QtCore.Slot(str)
    def selectProject(self, project_id: str) -> None:
        self._active_project_id = project_id or None
        self.refresh()

    @QtCore.Slot(str)
    def setActiveTool(self, tool_name: str) -> None:
        tool = (tool_name or "").strip().lower()
        allowed = {"projects", "select", "move", "transform", "clip", "color"}
        next_tool = tool if tool in allowed else "projects"
        if next_tool == self._active_tool:
            return
        self._active_tool = next_tool
        self.activeToolChanged.emit()

    @QtCore.Slot()
    def newProjectDialog(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(None, "New Project", "Project name:")
        if not ok:
            return
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            None,
            "Choose project media",
            "",
            "Supported Media (*.bmp *.jpg *.jpeg *.png *.tif *.tiff *.webp *.mp4 *.mov *.m4v *.avi *.mkv *.webm *.zip)",
        )
        if not files:
            return
        project = store.create_project(name=name)
        ingest_media_sources(project["id"], list(files))
        self._active_project_id = project["id"]
        self.refresh()

    @QtCore.Slot(str, str)
    def showRenameDialog(self, project_id: str, project_name: str) -> None:
        dialog = RenameProjectDialog(project_name, self._app_window())
        if dialog.run() == QtWidgets.QDialog.DialogCode.Accepted:
            self.renameProject(project_id, dialog.project_name)

    @QtCore.Slot(str, str)
    def renameProject(self, project_id: str, new_name: str) -> None:
        if not new_name.strip():
            return
        store.update_project(project_id, name=new_name.strip())
        self.closeDialog()

    @QtCore.Slot(str, str)
    def showDeleteDialog(self, project_id: str, project_name: str) -> None:
        dialog = DeleteProjectDialog(project_name, self._app_window())
        if dialog.run() == QtWidgets.QDialog.DialogCode.Accepted:
            self.deleteProject(project_id)

    @QtCore.Slot(str)
    def deleteProject(self, project_id: str) -> None:
        store.delete_project(project_id)
        if self._active_project_id == project_id:
            self._active_project_id = None
        self.closeDialog()

    @QtCore.Slot()
    def closeDialog(self) -> None:
        self._state["dialog"] = {"kind": "none", "projectId": "", "projectName": ""}
        self.refresh()

    @QtCore.Slot()
    def addPhotosDialog(self) -> None:
        if not self._active_project_id:
            return
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            None,
            "Add media to project",
            "",
            "Supported Media (*.bmp *.jpg *.jpeg *.png *.tif *.tiff *.webp *.mp4 *.mov *.m4v *.avi *.mkv *.webm *.zip)",
        )
        if not files:
            return
        ingest_media_sources(self._active_project_id, list(files))
        self.refresh()

    @QtCore.Slot()
    def importGaussianSceneDialog(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self._app_window(),
            "Import Gaussian scene",
            "",
            "Gaussian Scenes (*.gasp *.ply);;Gaussian GASP (*.gasp);;Gaussian PLY (*.ply)",
        )
        if not filename:
            return
        mode = IMPORT_MODE_DIRECT if Path(filename).suffix.lower() == ".gasp" else None
        if mode is None:
            dialog = SplatImportModeDialog(filename, self._app_window())
            if dialog.run() != QtWidgets.QDialog.DialogCode.Accepted or not dialog.mode:
                return
            mode = dialog.mode
        try:
            result = import_gaussian_scene_file(filename, mode=mode)
        except Exception as error:
            traceback.print_exc()
            QtWidgets.QMessageBox.warning(
                self._app_window(),
                "Import failed",
                f"{type(error).__name__}: {error}",
            )
            return
        self._active_project_id = result["project"]["id"]
        self.refresh()

    @QtCore.Slot()
    def createSampleProject(self) -> None:
        sample_dir = paths.repo_root() / "sample_datasets" / "nerf_synthetic_lego_12" / "images"
        if not sample_dir.exists():
            return
        files = [str(path) for path in sorted(sample_dir.glob("*.png"))]
        project = store.create_project("Sample Lego 12 Views", note="bundled_sample:nerf_synthetic_lego_12")
        ingest_media_sources(project["id"], files)
        self._active_project_id = project["id"]
        self.refresh()

    @QtCore.Slot(bool)
    def startTrainingDialog(self, restart: bool = False) -> None:
        try:
            self._start_training_dialog(bool(restart))
        except Exception as error:
            traceback.print_exc()
            self._show_training_start_error(error)

    @QtCore.Slot()
    def restartTrainingDialog(self) -> None:
        QtCore.QTimer.singleShot(0, lambda: self.startTrainingDialog(True))

    def _start_training_dialog(self, restart: bool = False) -> None:
        if not self._active_project_id:
            return
        project = store.get_project(self._active_project_id)
        if not project:
            return
        images = list_project_images(self._active_project_id)
        if not images:
            return
        latest = store.latest_job(self._active_project_id)
        if latest and latest["status"] == "running":
            return
        settings = store.project_training_settings(self._active_project_id, force_restart=restart)
        preserve_sfm_cache = bool(
            restart
            and latest
            and str(latest.get("status") or "").lower() in {"failed", "stopped"}
            and self._has_cached_colmap_reconstruction(self._active_project_id)
        )
        if preserve_sfm_cache:
            settings["preserve_sfm_cache"] = True
        if self._project_is_video_derived(project) and len(images) >= 96:
            settings["sfm_max_image_size"] = min(int(settings.get("sfm_max_image_size", 1600)), 1280)
            settings["sfm_num_threads"] = min(int(settings.get("sfm_num_threads", 6)), 4)
            settings["sfm_sequential_overlap"] = min(int(settings.get("sfm_sequential_overlap", 5)), 5)
            settings["sfm_quadratic_overlap"] = False
        dialog = TrainModelDialog(
            project["name"],
            settings,
            restart=restart,
            can_preserve_sfm_cache=preserve_sfm_cache,
            parent=self._app_window(),
        )
        if dialog.run() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        settings = dialog.training_settings
        settings["force_restart"] = bool(restart)
        settings["preserve_sfm_cache"] = bool(restart and settings.get("preserve_sfm_cache"))
        settings["densify_stop_iter"] = min(
            int(settings.get("densify_stop_iter", 0)),
            max(int(settings["train_steps"]) - 1, 1),
        )
        settings["refine_scale2d_stop_iter"] = int(settings["train_steps"])
        store.save_project_training_settings(self._active_project_id, settings)
        ensure_project_camera_manifests(self._active_project_id)
        job = store.create_job(self._active_project_id, settings)
        self._launch_worker(job["id"])
        self.refresh()

    def _show_training_start_error(self, error: Exception) -> None:
        parent = self._app_window()
        QtWidgets.QMessageBox.warning(
            parent,
            "Training could not start",
            f"{type(error).__name__}: {error}",
        )

    @staticmethod
    def _has_cached_colmap_reconstruction(project_id: str) -> bool:
        sparse_dir = paths.project_colmap_scratch_dir(project_id) / "sparse"
        if not sparse_dir.exists():
            return False
        for reconstruction_dir in sparse_dir.iterdir():
            if not reconstruction_dir.is_dir():
                continue
            if all((reconstruction_dir / name).exists() for name in ("cameras.bin", "images.bin", "points3D.bin")):
                return True
        return False

    @staticmethod
    def _project_is_video_derived(project: dict[str, Any] | None) -> bool:
        import_summary = (project or {}).get("last_import_summary") if project else None
        aggregate = (import_summary or {}).get("aggregate") if isinstance(import_summary, dict) else {}
        return int((aggregate or {}).get("source_videos") or 0) > 0

    @QtCore.Slot()
    def stopTraining(self) -> None:
        if not self._active_project_id:
            return
        latest = store.latest_job(self._active_project_id)
        if latest:
            store.request_job_stop(latest["id"])
            self.refresh()

    @QtCore.Slot()
    def openExportFolder(self) -> None:
        if not self._active_project_id:
            return
        project = store.get_project(self._active_project_id)
        if project and project.get("last_manifest_path"):
            os.startfile(Path(project["last_manifest_path"]).parent)  # type: ignore[attr-defined]

    @QtCore.Slot()
    def openDataFolder(self) -> None:
        os.startfile(paths.data_root())  # type: ignore[attr-defined]

    @QtCore.Slot(str)
    def copyText(self, text: str) -> None:
        QtGui.QGuiApplication.clipboard().setText(text or "")

    def refresh(self) -> None:
        self._state = self._build_state()
        self.stateChanged.emit()

    def _build_state(self) -> dict[str, Any]:
        projects = store.list_projects()
        ids = {project["id"] for project in projects}
        if self._active_project_id not in ids:
            self._active_project_id = projects[0]["id"] if projects else None
        detail = self._build_detail(self._active_project_id) if self._active_project_id else None
        dialog = self._state.get("dialog", {"kind": "none", "projectId": "", "projectName": ""})
        return {
            "projects": [{"id": p["id"], "name": p["name"], "status": p["status"]} for p in projects],
            "activeProjectId": self._active_project_id,
            "activeDetail": detail,
            "sampleAvailable": (paths.repo_root() / "sample_datasets" / "nerf_synthetic_lego_12" / "images").exists(),
            "previewRuntimeAvailable": preview_runtime_available(),
            "previewRuntimeMessage": preview_runtime_error() or "",
            "dialog": dialog,
        }

    def _build_detail(self, project_id: str) -> dict[str, Any] | None:
        project = store.get_project(project_id)
        if not project:
            return None
        latest = store.latest_job(project_id)
        images = list_project_images(project_id)
        manifest = ensure_project_camera_manifests(project_id) if images else {"mode": "sfm", "usable_views": 0}
        logs = self._read_logs(latest)
        live_monitor = self._live_monitor_payload(latest, logs)
        preview = self._preview_payload(project, latest, images)
        resolution = self._detect_resolution(images) or "Unknown"
        percent = int(round(float(latest["progress"]) * 100)) if latest else 0
        last_loss = self._extract_last_loss(logs) or "--"
        camera = f"camera manifest: {int(manifest['usable_views'])} views" if manifest["mode"] == "manifest" else "SfM-only cameras"
        import_summary = project.get("last_import_summary") or {}
        import_aggregate = import_summary.get("aggregate") or {}
        import_videos = import_summary.get("videos") or []
        training_summary = project.get("last_training_summary") or {}
        train_metrics = training_summary.get("metrics") or {}
        validation_metrics = training_summary.get("validation_metrics") or {}
        diagnostics = training_summary.get("dataset_diagnostics") or {}
        properties = [
            {"label": "Source Directory", "value": project["workspace_dir"], "copyable": True},
            {"label": "Image Count", "value": f"{len(images)} Frames"},
            {"label": "Resolution", "value": resolution},
        ]
        if int(import_aggregate.get("source_videos") or 0) > 0:
            properties.append({"label": "Video Sources", "value": str(int(import_aggregate["source_videos"]))})
            properties.append(
                {
                    "label": "Video Keyframes",
                    "value": (
                        f"{int(import_aggregate.get('video_selected_frames') or 0)} kept / "
                        f"{int(import_aggregate.get('video_candidate_frames') or 0)} candidates"
                    ),
                }
            )
            if import_aggregate.get("selected_overlap_mean") is not None:
                properties.append(
                    {
                        "label": "Keyframe Overlap",
                        "value": (
                            f"{float(import_aggregate['selected_overlap_mean']) * 100.0:.1f}% avg, "
                            f"{float(import_aggregate.get('selected_overlap_min') or 0.0) * 100.0:.1f}% min"
                        ),
                    }
                )
        if train_metrics.get("psnr") is not None:
            properties.append({"label": "Train PSNR", "value": f"{float(train_metrics['psnr']):.3f}"})
        if validation_metrics.get("psnr") is not None:
            properties.append({"label": "Val PSNR", "value": f"{float(validation_metrics['psnr']):.3f}"})
        if validation_metrics.get("ssim") is not None:
            properties.append({"label": "Val SSIM", "value": f"{float(validation_metrics['ssim']):.4f}"})
        if diagnostics.get("quality_score") is not None:
            properties.append({"label": "Dataset Score", "value": f"{float(diagnostics['quality_score']):.1f}/100"})
        if diagnostics.get("selected_overlap_mean") is not None:
            properties.append(
                {
                    "label": "Dataset Overlap",
                    "value": (
                        f"{float(diagnostics['selected_overlap_mean']) * 100.0:.1f}% avg, "
                        f"{float(diagnostics.get('selected_overlap_min') or 0.0) * 100.0:.1f}% min"
                    ),
                }
            )
        if diagnostics.get("registered_view_ratio") is not None:
            properties.append({"label": "Registered Views", "value": f"{float(diagnostics['registered_view_ratio']) * 100.0:.1f}%"})
        first_video = import_videos[0] if import_videos else {}
        first_video_frame = (first_video.get("selected_frames") or [{}])[0] if first_video else {}
        video_tile_url = ""
        if first_video_frame.get("image_path"):
            video_tile_url = QtCore.QUrl.fromLocalFile(str(first_video_frame["image_path"])).toString()
        return {
            "header": {"title": project["name"], "status": project["status"], "subtitle": f"{len(images)} frames | {camera} | {project['workspace_dir']}"},
            "toolbar": {"canTrain": bool(images) and not (latest and latest["status"] == "running"), "canStop": bool(latest and latest["status"] == "running"), "canExport": bool(project.get("last_manifest_path"))},
            "preview": preview,
            "statusPanel": {"progress": percent, "progressLabel": f"{percent}%", "statusText": latest["status"].capitalize() if latest else "Ready", "stage": latest["stage"] if latest else "Ready", "timeTotal": self._job_duration(latest), "finalLoss": last_loss},
            "propertiesPanel": {"items": properties},
            "exportPanel": {"body": "Trained splats are compiled and ready. Choose a destination format to export the geometry." if project.get("last_manifest_path") else "Run a project first to generate the exported splat package."},
            "logs": logs,
            "liveMonitor": live_monitor,
            "consoleRows": self._console_rows(logs),
            "consoleRunning": bool(latest and latest["status"] == "running"),
            "consoleHtml": self._console_html(logs, running=bool(latest and latest["status"] == "running")),
            "photos": [{"name": image.name, "path": str(image), "url": QtCore.QUrl.fromLocalFile(str(image)).toString()} for image in images],
            "videoTile": {
                "name": str(first_video.get("video_name") or ""),
                "fps": (
                    f"{float(first_video.get('fps') or 0.0):.1f} FPS · "
                    f"{int(first_video.get('selected_count') or 0)} kept"
                    if first_video
                    else ""
                ),
                "url": video_tile_url,
            },
        }

    def _preview_payload(self, project: dict[str, Any], latest: dict[str, Any] | None, images: list[Path]) -> dict[str, Any]:
        preview_path = self._project_preview_path(project)
        if not preview_runtime_available():
            return {"hasScene": False, "path": "", "pointCount": 0, "emptyTitle": "Native preview unavailable", "footer": preview_runtime_error() or "Build the preview runtime to enable the embedded renderer."}
        if not preview_path or not Path(preview_path).exists():
            title = "Add a dataset to this project" if not images else "Training in progress" if latest and latest["status"] == "running" else "Ready to train"
            footer = "No photos have been added yet." if not images else "Training is running. Logs update live in the right panel." if latest and latest["status"] == "running" else f"{len(images)} photos loaded. Press Train Model to build the scene."
            return {"hasScene": False, "path": "", "pointCount": 0, "emptyTitle": title, "footer": footer}
        stats = self._preview_scene_stats(preview_path)
        return {"hasScene": True, "path": preview_path, "projectId": project["id"], "pointCount": int(stats["point_count"]), "emptyTitle": "", "footer": f"Bounds: {stats['bounds']['min']} -> {stats['bounds']['max']}"}

    def _project_preview_path(self, project: dict[str, Any]) -> str | None:
        return preview_scene_path(project)

    def _preview_scene_stats(self, preview_path: str) -> dict[str, Any]:
        stamp = Path(preview_path).stat().st_mtime_ns
        if preview_path != self._preview_path or stamp != self._preview_stamp:
            if Path(preview_path).suffix.lower() == ".gasp":
                metadata = read_gaussian_gasp_metadata(preview_path)
                self._preview_stats = {
                    "point_count": int(metadata.get("vertex_count") or metadata.get("point_count") or 0),
                    "bounds": metadata.get("bounds") or {"min": "unknown", "max": "unknown"},
                }
            else:
                _points, self._preview_stats = read_preview_points(preview_path, sample_limit=64)
            self._preview_path = preview_path
            self._preview_stamp = stamp
        return dict(self._preview_stats or {})

    def _read_logs(self, latest: dict[str, Any] | None) -> str:
        path = Path(latest["log_path"]) if latest and latest.get("log_path") else None
        return path.read_text(encoding="utf-8") if path and path.exists() else ""

    def _detect_resolution(self, images: list[Path]) -> str | None:
        if not images:
            return None
        with Image.open(images[0]) as image:
            return f"{image.width}x{image.height}"

    def _extract_last_loss(self, logs: str) -> str | None:
        matches = [line.split("loss=", 1)[1].split(",", 1)[0] for line in logs.splitlines() if "loss=" in line]
        return matches[-1] if matches else None

    def _log_line_datetime(self, line: str) -> datetime | None:
        if "Worker heartbeat:" in line:
            return None

        colmap_match = re.match(r"^[IWEF](\d{8})\s+(\d{2}:\d{2}:\d{2})\.", line)
        if colmap_match:
            try:
                parsed = datetime.strptime(
                    f"{colmap_match.group(1)} {colmap_match.group(2)}",
                    "%Y%m%d %H:%M:%S",
                )
                return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
            except ValueError:
                return None

        bracket_match = re.match(r"^\[(\d{2}:\d{2}:\d{2})\]", line)
        if not bracket_match:
            return None
        try:
            current = datetime.now().astimezone()
            hour, minute, second = [int(part) for part in bracket_match.group(1).split(":")]
            parsed = current.replace(hour=hour, minute=minute, second=second, microsecond=0)
            if (parsed - current).total_seconds() > 60:
                parsed = parsed - timedelta(days=1)
            return parsed
        except ValueError:
            return None

    def _latest_non_heartbeat_log_datetime(self, log_text: str) -> datetime | None:
        for line in reversed(log_text.splitlines()):
            stripped = line.strip()
            if not stripped or "Worker heartbeat:" in stripped:
                continue
            parsed = self._log_line_datetime(stripped)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _format_monitor_elapsed(seconds: int | None) -> str:
        if seconds is None:
            return "unknown"
        seconds = max(0, int(seconds))
        minutes, seconds_part = divmod(seconds, 60)
        hours, minutes_part = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}h {minutes_part}m ago"
        if minutes > 0:
            return f"{minutes}m {seconds_part}s ago"
        return f"{seconds_part}s ago"

    @staticmethod
    def _seconds_since_iso(value: str | None) -> int | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))

    def _live_monitor_payload(self, latest: dict[str, Any] | None, logs: str) -> dict[str, Any]:
        if not latest:
            return {
                "state": "idle",
                "label": "REST",
                "detail": "No active worker.",
                "ageSeconds": None,
                "showStopPrompt": False,
            }

        job_status = str(latest.get("status") or "idle").lower()
        if job_status not in {"queued", "running"}:
            idle_statuses = {"completed", "stopped", "idle"}
            label = "REST" if job_status in idle_statuses else job_status.upper()
            return {
                "state": "idle" if job_status in idle_statuses else job_status,
                "label": label,
                "detail": f"Job status: {job_status}.",
                "ageSeconds": None,
                "showStopPrompt": False,
            }

        activity_age = self._seconds_since_iso(str(latest.get("monitor_last_activity_at") or ""))
        if activity_age is None:
            log_activity = self._latest_non_heartbeat_log_datetime(logs)
            if log_activity is not None:
                activity_age = max(0, int((datetime.now().astimezone() - log_activity).total_seconds()))
        if activity_age is None:
            activity_age = self._seconds_since_iso(str(latest.get("updated_at") or ""))

        if activity_age is not None and activity_age >= LIVE_ASK_STOP_SECONDS:
            state = "silent"
            label = "CHECK"
            show_stop_prompt = True
        elif activity_age is not None and activity_age >= LIVE_STALE_SECONDS:
            state = "stale"
            label = "QUIET"
            show_stop_prompt = False
        else:
            state = "live"
            label = "LIVE"
            show_stop_prompt = False

        activity_kind = str(latest.get("monitor_last_activity_kind") or "activity")
        return {
            "state": state,
            "label": label,
            "detail": f"Last worker {activity_kind}: {self._format_monitor_elapsed(activity_age)}.",
            "ageSeconds": activity_age,
            "showStopPrompt": show_stop_prompt,
            "staleAfterSeconds": LIVE_STALE_SECONDS,
            "askStopAfterSeconds": LIVE_ASK_STOP_SECONDS,
        }

    def _job_duration(self, latest: dict[str, Any] | None) -> str:
        if not latest or not latest.get("started_at"):
            return "--"
        start = QtCore.QDateTime.fromString(latest["started_at"], QtCore.Qt.DateFormat.ISODate)
        end = QtCore.QDateTime.fromString(latest.get("finished_at") or "", QtCore.Qt.DateFormat.ISODate)
        if not end.isValid():
            end = QtCore.QDateTime.currentDateTimeUtc()
        seconds = max(0, start.secsTo(end))
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours:02d}h {minutes:02d}m"

    def _console_html(self, logs: str, *, running: bool) -> str:
        rows: list[str] = []
        for raw_line in logs.splitlines():
            line = html.escape(raw_line)
            if "[system]" in raw_line:
                rows.append(f"<div style='color:#71717A;font-style:italic;margin:0 0 10px 0;'>&gt; {html.escape(raw_line[8:].strip())}</div>")
                continue
            if raw_line.startswith("[") and "]" in raw_line:
                timestamp, rest = raw_line.split("]", 1)
                rest = rest.lstrip()
                color = "#D4D4D8"
                weight = "400"
                if "[success]" in raw_line:
                    color = "#34D399"
                    rest = rest.replace("[success]", "").strip()
                    weight = "500"
                elif "loss=" in raw_line:
                    color = "#00F0FF"
                    weight = "500"
                rows.append(
                    "<div style='margin:0 0 12px 0;'>"
                    f"<span style='color:#52525B;'>{html.escape(timestamp + ']')}</span> "
                    f"<span style='color:{color};font-weight:{weight};'>{html.escape(rest)}</span>"
                    "</div>"
                )
                continue
            rows.append(f"<div style='color:#D4D4D8;margin:0 0 12px 0;'>{line}</div>")
        prompt = "..." if running else "<span style='display:inline-block;width:6px;height:12px;background:#A1A1AA;vertical-align:middle;'></span>"
        rows.append(f"<div style='color:#52525B;margin-top:14px;'>C:\\Users\\illia\\Companion&gt; {prompt}</div>")
        body = "".join(rows)
        return (
            "<div style=\"font-family:'JetBrains Mono';font-size:11px;line-height:1.9;"
            "color:#D4D4D8;white-space:normal;\">"
            f"{body}</div>"
        )

    def _console_rows(self, logs: str) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for raw_line in logs.splitlines():
            if "[system]" in raw_line:
                rows.append({"kind": "system", "timestamp": "", "message": raw_line[8:].strip()})
                continue
            if raw_line.startswith("[") and "]" in raw_line:
                timestamp, rest = raw_line.split("]", 1)
                rest = rest.lstrip()
                kind = "plain"
                timestamp_value = timestamp + "]"
                if "[success]" in raw_line:
                    kind = "success"
                    timestamp_value = ""
                    rest = rest.replace("[success]", "").strip()
                elif "loss=" in raw_line:
                    kind = "metric"
                rows.append({"kind": kind, "timestamp": timestamp_value, "message": rest})
                continue
            rows.append({"kind": "plain", "timestamp": "", "message": raw_line})
        return rows

    def _launch_worker(self, job_id: str) -> None:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        worker_python = paths.preferred_worker_python()
        python_executable = str(worker_python or Path(sys.executable))
        repo_root = str(paths.repo_root())
        env = os.environ.copy()
        pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = repo_root if not pythonpath else f"{repo_root}{os.pathsep}{pythonpath}"
        subprocess.Popen([python_executable, "-m", "companion_app.worker_entry", job_id], cwd=repo_root, env=env, creationflags=creationflags)
