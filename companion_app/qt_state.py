from __future__ import annotations

import html
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from PIL import Image
from PySide6 import QtCore, QtGui, QtWidgets

from . import paths, store
from .native_preview import preview_runtime_available, preview_runtime_error
from .pipeline import copy_input_images, ensure_project_camera_manifests, list_project_images
from .ply import read_preview_points


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
            "QLineEdit{background:#111116; color:#FAFAFA; border:1px solid rgba(255,255,255,0.12); border-radius:10px; padding:10px 12px;}"
            "QLineEdit:focus{border:1px solid #FF5400;}"
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


class QtStateController(QtCore.QObject):
    stateChanged = QtCore.Signal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        paths.ensure_runtime_dirs()
        store.init_db()
        self._state: dict[str, Any] = {}
        self._active_project_id: str | None = None
        self._preview_path: str | None = None
        self._preview_stamp: int | None = None
        self._preview_stats: dict[str, Any] | None = None
        self.refresh()

    @QtCore.Property("QVariantMap", notify=stateChanged)
    def state(self) -> dict[str, Any]:
        return self._state

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

    @QtCore.Slot(str)
    def selectProject(self, project_id: str) -> None:
        self._active_project_id = project_id or None
        self.refresh()

    @QtCore.Slot()
    def newProjectDialog(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(None, "New Project", "Project name:")
        if not ok:
            return
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            None,
            "Choose project photos",
            "",
            "Images (*.bmp *.jpg *.jpeg *.png *.tif *.tiff *.webp)",
        )
        if not files:
            return
        project = store.create_project(name=name)
        copy_input_images(project["id"], list(files))
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
            "Add photos to project",
            "",
            "Images (*.bmp *.jpg *.jpeg *.png *.tif *.tiff *.webp)",
        )
        if not files:
            return
        copy_input_images(self._active_project_id, list(files))
        self.refresh()

    @QtCore.Slot()
    def createSampleProject(self) -> None:
        sample_dir = paths.repo_root() / "sample_datasets" / "nerf_synthetic_lego_12" / "images"
        if not sample_dir.exists():
            return
        files = [str(path) for path in sorted(sample_dir.glob("*.png"))]
        project = store.create_project("Sample Lego 12 Views", note="bundled_sample:nerf_synthetic_lego_12")
        copy_input_images(project["id"], files)
        self._active_project_id = project["id"]
        self.refresh()

    @QtCore.Slot(bool)
    def startTrainingDialog(self, restart: bool = False) -> None:
        if not self._active_project_id:
            return
        images = list_project_images(self._active_project_id)
        if not images:
            return
        latest = store.latest_job(self._active_project_id)
        if latest and latest["status"] == "running":
            return
        steps, ok = QtWidgets.QInputDialog.getInt(None, "Training Steps", "How many training steps?", 3000, 200, 20000, 100)
        if not ok:
            return
        settings = store.default_job_settings(force_restart=restart)
        settings["train_steps"] = int(steps)
        settings["densify_stop_iter"] = min(int(settings["densify_stop_iter"]), max(int(steps) - 1, 1))
        settings["refine_scale2d_stop_iter"] = int(steps)
        ensure_project_camera_manifests(self._active_project_id)
        job = store.create_job(self._active_project_id, settings)
        self._launch_worker(job["id"])
        self.refresh()

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
        preview = self._preview_payload(project, latest, images)
        resolution = self._detect_resolution(images) or "Unknown"
        percent = int(round(float(latest["progress"]) * 100)) if latest else 0
        last_loss = self._extract_last_loss(logs) or "--"
        camera = f"camera manifest: {int(manifest['usable_views'])} views" if manifest["mode"] == "manifest" else "SfM-only cameras"
        return {
            "header": {"title": project["name"], "status": project["status"], "subtitle": f"{len(images)} photos | {camera} | {project['workspace_dir']}"},
            "toolbar": {"canTrain": bool(images) and not (latest and latest["status"] == "running"), "canStop": bool(latest and latest["status"] == "running"), "canExport": bool(project.get("last_manifest_path"))},
            "preview": preview,
            "statusPanel": {"progress": percent, "progressLabel": f"{percent}%", "statusText": latest["status"].capitalize() if latest else "Ready", "stage": latest["stage"] if latest else "Ready", "timeTotal": self._job_duration(latest), "finalLoss": last_loss},
            "propertiesPanel": {"items": [{"label": "Source Directory", "value": project["workspace_dir"], "copyable": True}, {"label": "Image Count", "value": f"{len(images)} Frames"}, {"label": "Resolution", "value": resolution}]},
            "exportPanel": {"body": "Trained splats are compiled and ready. Choose a destination format to export the geometry." if project.get("last_manifest_path") else "Run a project first to generate the exported splat package."},
            "logs": logs,
            "consoleRows": self._console_rows(logs),
            "consoleRunning": bool(latest and latest["status"] == "running"),
            "consoleHtml": self._console_html(logs, running=bool(latest and latest["status"] == "running")),
            "photos": [{"name": image.name, "path": str(image), "url": QtCore.QUrl.fromLocalFile(str(image)).toString()} for image in images],
            "videoTile": {
                "name": "GOPR0042.MP4",
                "fps": "30 FPS",
                "url": QtCore.QUrl.fromLocalFile(str(images[0])).toString() if images else "",
            },
        }

    def _preview_payload(self, project: dict[str, Any], latest: dict[str, Any] | None, images: list[Path]) -> dict[str, Any]:
        preview_path = project.get("last_result_ply")
        if not preview_runtime_available():
            return {"hasScene": False, "path": "", "pointCount": 0, "emptyTitle": "Native preview unavailable", "footer": preview_runtime_error() or "Build the preview runtime to enable the embedded renderer."}
        if not preview_path or not Path(preview_path).exists():
            title = "Add a dataset to this project" if not images else "Training in progress" if latest and latest["status"] == "running" else "Ready to train"
            footer = "No photos have been added yet." if not images else "Training is running. Logs update live in the right panel." if latest and latest["status"] == "running" else f"{len(images)} photos loaded. Press Train Model to build the scene."
            return {"hasScene": False, "path": "", "pointCount": 0, "emptyTitle": title, "footer": footer}
        stats = self._preview_scene_stats(preview_path)
        return {"hasScene": True, "path": preview_path, "pointCount": int(stats["point_count"]), "emptyTitle": "", "footer": f"Bounds: {stats['bounds']['min']} -> {stats['bounds']['max']}"}

    def _preview_scene_stats(self, preview_path: str) -> dict[str, Any]:
        stamp = Path(preview_path).stat().st_mtime_ns
        if preview_path != self._preview_path or stamp != self._preview_stamp:
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
