from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6 import QtCore, QtGui, QtQuickWidgets, QtWidgets

from . import APP_VERSION
from .qml_viewport import ViewportWidget
from .qt_state import QtStateController


class ChromeWidget(QtQuickWidgets.QQuickWidget):
    def __init__(self, source: Path, controller: QtCore.QObject, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setResizeMode(QtQuickWidgets.QQuickWidget.ResizeMode.SizeRootObjectToView)
        self.setClearColor(QtGui.QColor("#050505"))
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_AlwaysStackOnTop, False)
        self.rootContext().setContextProperty("controller", controller)
        self.setSource(QtCore.QUrl.fromLocalFile(str(source)))


class QmlCompanionWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.controller = QtStateController(self)

        self.setWindowTitle("Gaussian Points Studio")
        self.resize(1600, 980)
        self.setMinimumSize(1440, 860)
        self.setStyleSheet("QMainWindow{background:#050505; color:#FAFAFA;}")

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        root_layout = QtWidgets.QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        qml_dir = Path(__file__).resolve().parent / "qml"
        self.menu_bar_view = ChromeWidget(qml_dir / "MainMenu.qml", self.controller)
        self.menu_bar_view.setFixedHeight(32)
        root_layout.addWidget(self.menu_bar_view)

        body = QtWidgets.QWidget()
        body_layout = QtWidgets.QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        root_layout.addWidget(body, 1)

        self.tool_rail = ChromeWidget(qml_dir / "ToolRail.qml", self.controller)
        self.tool_rail.setFixedWidth(52)
        body_layout.addWidget(self.tool_rail)

        workspace = QtWidgets.QWidget()
        workspace_layout = QtWidgets.QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        body_layout.addWidget(workspace, 1)

        self.left_sidebar = ChromeWidget(qml_dir / "ProjectSidebar.qml", self.controller)
        self.left_sidebar.setFixedWidth(280)
        workspace_layout.addWidget(self.left_sidebar)

        center = QtWidgets.QWidget()
        center_layout = QtWidgets.QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        workspace_layout.addWidget(center, 1)

        self.header_view = ChromeWidget(qml_dir / "CenterHeader.qml", self.controller)
        self.header_view.setFixedHeight(64)
        center_layout.addWidget(self.header_view)

        self.viewport = ViewportWidget(self.controller)
        center_layout.addWidget(self.viewport, 1)

        self.right_sidebar = ChromeWidget(qml_dir / "InspectorSidebar.qml", self.controller)
        self.right_sidebar.setFixedWidth(360)
        workspace_layout.addWidget(self.right_sidebar)

        self.controller.stateChanged.connect(self._sync_widgets)
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.timeout.connect(self.controller.refresh)
        self._refresh_timer.start(1200)
        self._sync_widgets()

    def _sync_widgets(self) -> None:
        detail = (self.controller.state or {}).get("activeDetail")
        self.viewport.apply_detail(detail)


def launch(plugin_root: str | None = None) -> int:
    _ = plugin_root
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    font_dir = Path(__file__).resolve().parent / "assets" / "fonts"
    loaded_families: set[str] = set()
    for font_path in font_dir.glob("*.ttf"):
        fid = QtGui.QFontDatabase.addApplicationFont(str(font_path))
        if fid >= 0:
            for fam in QtGui.QFontDatabase.applicationFontFamilies(fid):
                loaded_families.add(fam)
    if loaded_families:
        print(f"[fonts] loaded: {', '.join(sorted(loaded_families))}")
    qt_ver = tuple(int(x) for x in QtCore.__version__.split(".")[:2])
    if qt_ver < (6, 6):
        print(f"[fonts] Qt {QtCore.__version__} — font.features requires Qt 6.6+, slashed zeros may not render")
    app.setFont(QtGui.QFont("Outfit", 10))
    window = QmlCompanionWindow()
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
