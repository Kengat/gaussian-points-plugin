from __future__ import annotations

import argparse
import ctypes
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from . import APP_VERSION, paths, store
from .native_preview import preview_bridge, preview_runtime_available, preview_runtime_error
from .pipeline import ensure_project_camera_manifests, ingest_media_sources, list_project_images
from .ply import read_preview_points


MEDIA_FILE_FILTER = (
    "Media Files (*.bmp;*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.webp;*.mp4;*.mov;*.m4v;*.avi;*.mkv;*.webm;*.zip)",
    "All files (*.*)",
)
PREVIEW_HIDDEN_X = -10000
PREVIEW_HIDDEN_Y = -10000
QUALITY_PRESET_OPTIONS = {"compact", "balanced", "high"}
SFM_MATCH_OPTIONS = {"auto", "exhaustive", "sequential", "spatial"}
LIVE_STALE_SECONDS = 120
LIVE_ASK_STOP_SECONDS = 20 * 60


def _preview_debug(message: str) -> None:
    try:
        log_path = paths.data_root() / "preview_debug.log"
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


@dataclass
class PreviewHostBounds:
    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0
    visible: bool = False

    @property
    def usable(self) -> bool:
        return self.visible and self.width > 8 and self.height > 8


class NativePreviewOverlay:
    def __init__(self) -> None:
        self.bridge = preview_bridge()
        self._window_created = False
        self._parent_hwnd: int | None = None
        self._form: Any | None = None
        self._panel: Any | None = None
        self._bounds = PreviewHostBounds()
        self._current_path: str | None = None
        self._pending_path: str | None = None
        self._lock = threading.RLock()

    @property
    def available(self) -> bool:
        return self.bridge is not None

    def set_host(self, form: Any | None, bounds: PreviewHostBounds) -> None:
        if not self.available:
            _preview_debug("overlay.set_host skipped: bridge unavailable")
            return
        with self._lock:
            _preview_debug(
                f"overlay.set_host form={bool(form)} bounds=({bounds.left},{bounds.top},{bounds.width},{bounds.height}) visible={bounds.visible}"
            )
            if form is None:
                self._hide_host_panel()
                self._hide()
                return
            host_hwnd = self._ensure_host_panel(form, bounds)
            if host_hwnd is None:
                _preview_debug("overlay.set_host skipped: host panel not ready")
                return
            if self._parent_hwnd is not None and host_hwnd != self._parent_hwnd and self._window_created:
                self._destroy_window()
            self._form = form
            self._parent_hwnd = host_hwnd
            self._bounds = bounds
            self._ensure_window()
            self._apply_geometry()
            self._apply_pending_scene()

    def clear_scene(self) -> None:
        if not self.available:
            return
        with self._lock:
            self._pending_path = None
            self._current_path = None
            if self._window_created:
                self.bridge.clear_splats()
                self.bridge.request_redraw()
            self._hide_host_panel()
            self._hide()

    def load_scene(self, path: str | Path, *, force_reload: bool = False) -> None:
        if not self.available:
            return
        with self._lock:
            target = str(path)
            self._pending_path = target
            if force_reload:
                self._current_path = None
            self._ensure_window()
            self._apply_geometry()
            self._apply_pending_scene()

    def destroy(self) -> None:
        if not self.available:
            return
        with self._lock:
            self._destroy_window()
            self._parent_hwnd = None
            self._form = None
            self._panel = None

    def preview_fps(self) -> float:
        if not self.available or not self._window_created:
            return 0.0
        try:
            return float(self.bridge.get_preview_fps())
        except Exception:
            return 0.0

    def _ensure_window(self) -> None:
        if not self.available or self._window_created or self._parent_hwnd is None or not self._bounds.usable:
            _preview_debug(
                f"overlay.ensure_window skipped available={self.available} created={self._window_created} parent={self._parent_hwnd} usable={self._bounds.usable}"
            )
            return
        created = self._invoke_on_form_thread(
            lambda: self.bridge.create_window(
                self._parent_hwnd,
                self._bounds.width,
                self._bounds.height,
                x=0,
                y=0,
            ),
            default=False,
        )
        self._window_created = bool(created)
        preview_hwnd = int(self._invoke_on_form_thread(lambda: self.bridge.window_handle(), default=0) or 0)
        _preview_debug(
            f"overlay.ensure_window create_window created={self._window_created} hwnd={preview_hwnd} parent={self._parent_hwnd} bounds=({self._bounds.left},{self._bounds.top},{self._bounds.width},{self._bounds.height})"
        )

    def _apply_geometry(self) -> None:
        if not self.available or not self._window_created:
            _preview_debug(
                f"overlay.apply_geometry skipped available={self.available} created={self._window_created}"
            )
            return
        if self._bounds.usable:
            _preview_debug(
                f"overlay.apply_geometry resize ({self._bounds.left},{self._bounds.top},{self._bounds.width},{self._bounds.height})"
            )
            self._invoke_on_form_thread(
                lambda: self.bridge.resize_window(
                    self._bounds.width,
                    self._bounds.height,
                    x=0,
                    y=0,
                )
            )
            self._show_host_panel()
        else:
            self._hide_host_panel()
            self._hide()

    def _hide(self) -> None:
        if self.available and self._window_created:
            _preview_debug("overlay.hide move offscreen")
            self._invoke_on_form_thread(
                lambda: self.bridge.resize_window(1, 1, x=PREVIEW_HIDDEN_X, y=PREVIEW_HIDDEN_Y)
            )

    def _apply_pending_scene(self) -> None:
        if not self.available or not self._window_created:
            _preview_debug(
                f"overlay.apply_pending_scene skipped available={self.available} created={self._window_created}"
            )
            return
        if not self._bounds.usable:
            _preview_debug("overlay.apply_pending_scene hidden: unusable bounds")
            self._hide()
            return
        if not self._pending_path:
            _preview_debug("overlay.apply_pending_scene clearing: no pending path")
            self._invoke_on_form_thread(lambda: self.bridge.clear_splats())
            self._invoke_on_form_thread(lambda: self.bridge.request_redraw())
            self._current_path = None
            return
        scene_path = Path(self._pending_path)
        if not scene_path.exists():
            _preview_debug(f"overlay.apply_pending_scene clearing: missing path {scene_path}")
            self._invoke_on_form_thread(lambda: self.bridge.clear_splats())
            self._invoke_on_form_thread(lambda: self.bridge.request_redraw())
            self._current_path = None
            return
        if self._current_path != self._pending_path:
            _preview_debug(f"overlay.apply_pending_scene loading {scene_path}")
            self._invoke_on_form_thread(lambda: self.bridge.load_ply(scene_path))
            self._invoke_on_form_thread(lambda: self.bridge.fit_camera())
            self._current_path = self._pending_path
        self._invoke_on_form_thread(lambda: self.bridge.request_redraw())

    def _destroy_window(self) -> None:
        if self.available and self._window_created:
            try:
                self._invoke_on_form_thread(lambda: self.bridge.destroy_window())
            except Exception:
                pass
            self._window_created = False

    def _ensure_host_panel(self, form: Any, bounds: PreviewHostBounds) -> int | None:
        panel = self._sync_host_panel(form, bounds, visible=bounds.usable)
        if panel is None:
            return None
        handle = getattr(panel, "Handle", None)
        if handle is None:
            return None
        if hasattr(handle, "ToInt64"):
            return int(handle.ToInt64())
        return int(handle)

    def _show_host_panel(self) -> None:
        self._sync_host_panel(self._form, self._bounds, visible=True)

    def _hide_host_panel(self) -> None:
        self._sync_host_panel(self._form, self._bounds, visible=False)

    def _sync_host_panel(self, form: Any, bounds: PreviewHostBounds, *, visible: bool) -> Any | None:
        if form is None:
            return None
        try:
            import clr

            clr.AddReference("System.Windows.Forms")
            clr.AddReference("System.Drawing")
            import System.Windows.Forms as WinForms
            from System import Action
            from System.Drawing import ColorTranslator
        except Exception as error:
            _preview_debug(f"overlay.host_panel import failed: {error}")
            return None

        holder: dict[str, Any] = {"panel": None}

        def apply() -> None:
            panel = self._panel
            if panel is None or getattr(panel, "IsDisposed", False) or panel.Parent != form:
                panel = WinForms.Panel()
                panel.Name = "NativePreviewHostPanel"
                panel.TabStop = False
                panel.BackColor = ColorTranslator.FromHtml("#07070A")
                form.Controls.Add(panel)
                self._panel = panel
            panel.SetBounds(int(bounds.left), int(bounds.top), max(int(bounds.width), 1), max(int(bounds.height), 1))
            panel.Visible = bool(visible and bounds.usable)
            panel.BringToFront()
            holder["panel"] = panel
            _preview_debug(
                f"overlay.host_panel sync visible={panel.Visible} bounds=({bounds.left},{bounds.top},{bounds.width},{bounds.height})"
            )

        try:
            if bool(getattr(form, "InvokeRequired", False)):
                form.Invoke(Action(apply))
            else:
                apply()
        except Exception as error:
            _preview_debug(f"overlay.host_panel sync failed: {error}")
            return None
        return holder["panel"]

    def _invoke_on_form_thread(self, callback: Any, *, default: Any = None) -> Any:
        form = self._form
        if form is None:
            try:
                return callback()
            except Exception as error:
                _preview_debug(f"overlay.invoke without form failed: {error}")
                return default
        try:
            from System import Action
        except Exception:
            try:
                return callback()
            except Exception as error:
                _preview_debug(f"overlay.invoke fallback failed: {error}")
                return default

        holder: dict[str, Any] = {"value": default}

        def run() -> Any:
            holder["value"] = callback()
            return holder["value"]

        try:
            if bool(getattr(form, "InvokeRequired", False)):
                form.Invoke(Action(run))
            else:
                holder["value"] = run()
        except Exception as error:
            _preview_debug(f"overlay.invoke failed: {error}")
            return default
        return holder["value"]


class CompanionApi:
    POLL_MS = 1200

    def __init__(self, plugin_root: str | None = None, webview_module: Any | None = None) -> None:
        self._plugin_root = plugin_root
        self._webview = webview_module
        self._window: Any | None = None
        self._preview_overlay = NativePreviewOverlay()
        self._preview_runtime_message = preview_runtime_error()
        self._lock = threading.RLock()
        self._preview_cache_path: str | None = None
        self._preview_cache_stamp: int | None = None
        self._preview_cache_stats: dict[str, Any] | None = None
        self._active_project_hint: str | None = None
        self._preview_host = PreviewHostBounds()

        paths.ensure_runtime_dirs()
        store.init_db()

    def attach_window(self, window: Any) -> None:
        self._window = window

    def boot(self) -> dict[str, Any]:
        with self._lock:
            return self._success(state=self._build_state(self._active_project_hint))

    def refresh(self, active_project_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            return self._success(state=self._build_state(active_project_id))

    def create_project(self, name: str) -> dict[str, Any]:
        with self._lock:
            clean_name = (name or "").strip()
            if not clean_name:
                return self._failure("Enter a project name first.")
            files = self._pick_media_files()
            if not files:
                return self._failure("Project creation cancelled.", cancelled=True)
            project = store.create_project(name=clean_name)
            ingest_media_sources(project["id"], list(files))
            self._active_project_hint = project["id"]
            return self._success("Project created.", state=self._build_state(project["id"]))

    def add_photos(self, project_id: str | None) -> dict[str, Any]:
        with self._lock:
            if not project_id:
                return self._failure("Select a project first.")
            project = store.get_project(project_id)
            if not project:
                return self._failure("Project was not found.")
            files = self._pick_media_files()
            if not files:
                return self._failure("No media was added.", cancelled=True)
            ingest_media_sources(project_id, list(files))
            self._active_project_hint = project_id
            return self._success("Media added.", state=self._build_state(project_id))

    def create_sample_project(self) -> dict[str, Any]:
        with self._lock:
            sample_dir = self._sample_dataset_dir()
            if not sample_dir:
                return self._failure("Bundled sample dataset was not found.")
            image_paths = [str(path) for path in sorted(sample_dir.glob("*.png"))]
            project = store.create_project(
                name="Sample Lego 12 Views",
                note="bundled_sample:nerf_synthetic_lego_12",
            )
            ingest_media_sources(project["id"], image_paths)
            self._active_project_hint = project["id"]
            return self._success("Sample project created.", state=self._build_state(project["id"]))

    def start_job(self, project_id: str | None, settings_payload: Any | None = None) -> dict[str, Any]:
        return self._start_project_job(project_id, force_restart=False, settings_payload=settings_payload)

    def restart_job(self, project_id: str | None, settings_payload: Any | None = None) -> dict[str, Any]:
        return self._start_project_job(project_id, force_restart=True, settings_payload=settings_payload)

    def stop_job(self, project_id: str | None) -> dict[str, Any]:
        with self._lock:
            if not project_id:
                return self._failure("Select a project first.")
            latest_job = store.latest_job(project_id)
            if not latest_job:
                return self._failure("This project has no job to stop.")
            store.request_job_stop(latest_job["id"])
            self._active_project_hint = project_id
            return self._success("Stop requested.", state=self._build_state(project_id))

    def open_export_folder(self, project_id: str | None) -> dict[str, Any]:
        with self._lock:
            if not project_id:
                return self._failure("Select a project first.")
            project = store.get_project(project_id)
            if not project or not project.get("last_manifest_path"):
                return self._failure("Run a project to generate an export first.")
            self._open_path(Path(project["last_manifest_path"]).parent)
            return self._success("Opened export folder.", state=self._build_state(project_id))

    def open_data_folder(self) -> dict[str, Any]:
        with self._lock:
            self._open_path(paths.data_root())
            return self._success("Opened data folder.", state=self._build_state(self._active_project_hint))

    def set_preview_host(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        with self._lock:
            _preview_debug(f"api.set_preview_host payload={payload!r}")
            bounds = PreviewHostBounds(
                left=int((payload or {}).get("left", 0)),
                top=int((payload or {}).get("top", 0)),
                width=int((payload or {}).get("width", 0)),
                height=int((payload or {}).get("height", 0)),
                visible=bool((payload or {}).get("visible", False)),
            )
            self._preview_host = bounds
            form = self._resolve_preview_form()
            host_bounds = self._preview_host_bounds()
            _preview_debug(
                f"api.set_preview_host resolved form={bool(form)} host_bounds=({host_bounds.left},{host_bounds.top},{host_bounds.width},{host_bounds.height}) visible={host_bounds.visible}"
            )
            self._preview_overlay.set_host(form, host_bounds)
            return {"ok": True}

    @staticmethod
    def _merge_training_settings(
        base_settings: dict[str, Any],
        payload: Any,
    ) -> tuple[dict[str, Any] | None, str | None]:
        settings = dict(base_settings)
        if payload is None:
            return settings, None

        if isinstance(payload, dict):
            if "train_steps" in payload:
                try:
                    requested_steps = int(payload.get("train_steps"))
                except (TypeError, ValueError):
                    return None, "Training steps must be a whole number."
                if requested_steps < 200 or requested_steps > 20000:
                    return None, "Training steps must be between 200 and 20000."
                settings["train_steps"] = requested_steps

            if "max_gaussians" in payload:
                try:
                    max_gaussians = int(payload.get("max_gaussians"))
                except (TypeError, ValueError):
                    return None, "Maximum splats must be a whole number."
                if max_gaussians < 0 or max_gaussians > 5_000_000:
                    return None, "Maximum splats must be between 0 and 5000000."
                settings["max_gaussians"] = max_gaussians

            if "quality_preset" in payload:
                preset = str(payload.get("quality_preset") or "").strip().lower()
                if preset not in QUALITY_PRESET_OPTIONS:
                    return None, "Quality preset was not recognized."
                settings["quality_preset"] = preset

            if "train_resolution" in payload:
                try:
                    train_resolution = int(payload.get("train_resolution"))
                except (TypeError, ValueError):
                    return None, "Training resolution must be a whole number."
                if train_resolution < 256 or train_resolution > 2048:
                    return None, "Training resolution must be between 256 and 2048."
                settings["train_resolution"] = train_resolution

            if "sfm_match_mode" in payload:
                sfm_match_mode = str(payload.get("sfm_match_mode") or "").strip().lower()
                if sfm_match_mode not in SFM_MATCH_OPTIONS:
                    return None, "SfM matching mode was not recognized."
                settings["sfm_match_mode"] = sfm_match_mode

            return settings, None

        try:
            requested_steps = int(payload)
        except (TypeError, ValueError):
            return None, "Training steps must be a whole number."
        if requested_steps < 200 or requested_steps > 20000:
            return None, "Training steps must be between 200 and 20000."
        settings["train_steps"] = requested_steps
        return settings, None

    def _start_project_job(
        self,
        project_id: str | None,
        *,
        force_restart: bool,
        settings_payload: Any | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if not project_id:
                return self._failure("Select a project first.")
            project = store.get_project(project_id)
            if not project:
                return self._failure("Project was not found.")
            images = list_project_images(project_id)
            if not images:
                return self._failure("Add media to the selected project first.")
            existing = store.latest_job(project_id)
            if existing and existing["status"] == "running":
                return self._failure("This project already has a running job.")

            settings = store.project_training_settings(project_id, force_restart=force_restart)
            settings, error_message = self._merge_training_settings(settings, settings_payload)
            if settings is None:
                return self._failure(error_message or "Training settings are invalid.")
            settings["densify_stop_iter"] = min(
                int(settings["densify_stop_iter"]),
                max(int(settings["train_steps"]) - 1, 1),
            )
            settings["refine_scale2d_stop_iter"] = int(settings["train_steps"])
            store.save_project_training_settings(project_id, settings)
            manifest_status = ensure_project_camera_manifests(project_id)
            job = store.create_job(project_id, settings)
            self._launch_worker(job["id"])
            self._active_project_hint = project_id

            message = "Training started."
            if manifest_status["mode"] == "manifest":
                repaired_count = int(manifest_status["repaired_manifests"])
                view_count = int(manifest_status["usable_views"])
                message = f"Training started with camera manifest ({view_count} views)."
                if repaired_count > 0:
                    message = f"Training started after repairing camera manifest ({view_count} views)."
            elif manifest_status["mode"] == "sfm":
                message = "Training started without transforms.json. It will rely on SfM-only cameras."

            return self._success(message, state=self._build_state(project_id))

    def _build_state(self, active_project_id: str | None) -> dict[str, Any]:
        projects = store.list_projects()
        selected_id = self._resolve_active_project_id(projects, active_project_id)
        active_project = store.get_project(selected_id) if selected_id else None

        state = {
            "appVersion": APP_VERSION,
            "projectCount": len(projects),
            "sampleAvailable": bool(self._sample_dataset_dir()),
            "previewRuntimeAvailable": preview_runtime_available(),
            "previewRuntimeMessage": self._preview_runtime_message,
            "projects": [self._project_summary(project) for project in projects],
            "activeProjectId": selected_id,
            "activeDetail": None,
        }
        if active_project:
            state["activeDetail"] = self._project_detail(active_project)
        else:
            self._preview_overlay.clear_scene()
        return state

    def _project_summary(self, project: dict[str, Any]) -> dict[str, Any]:
        latest_job = store.latest_job(project["id"])
        return {
            "id": project["id"],
            "name": project["name"],
            "status": project["status"],
            "jobStage": latest_job["stage"] if latest_job else None,
            "updatedAt": project["updated_at"],
        }

    def _project_detail(self, project: dict[str, Any]) -> dict[str, Any]:
        images = list_project_images(project["id"])
        latest_job = store.latest_job(project["id"])
        manifest_status = ensure_project_camera_manifests(project["id"]) if images else {
            "mode": "sfm",
            "manifest_path": None,
            "usable_views": 0,
            "repaired_manifests": 0,
        }
        logs_text = self._read_log_text(latest_job)
        live_monitor = self._live_monitor_payload(latest_job, logs_text)
        preview = self._preview_payload(project, latest_job, images)
        self._sync_preview_overlay(preview)

        resolution = self._detect_resolution(images)
        image_count = len(images)
        last_loss = self._extract_last_loss(logs_text)
        percent = int(round(float(latest_job["progress"]) * 100)) if latest_job else 0
        camera_summary = "SfM-only cameras"
        if manifest_status["mode"] == "manifest":
            camera_summary = f"camera manifest: {int(manifest_status['usable_views'])} views"

        status_title = "No active job"
        status_body = "Ready to train."
        if latest_job:
            status_title = f"{percent}%"
            status_body = latest_job["status"].capitalize()

        latest_manifest_path = project.get("last_manifest_path")
        point_count = preview.get("pointCount")
        preview_fps = self._preview_overlay.preview_fps()
        import_summary = project.get("last_import_summary") or {}
        import_aggregate = import_summary.get("aggregate") or {}
        training_settings = store.project_training_settings(project["id"], force_restart=False)
        properties = [
            {"label": "Source Directory", "value": project["workspace_dir"], "copyable": True},
            {"label": "Image Count", "value": f"{image_count} frames"},
            {"label": "Resolution", "value": resolution or "Unknown"},
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
        if last_loss:
            properties.append({"label": "Final Loss", "value": last_loss})
        if point_count is not None:
            properties.append({"label": "Splats", "value": f"{point_count:,}"})

        return {
            "project": {
                "id": project["id"],
                "name": project["name"],
                "status": project["status"],
                "workspaceDir": project["workspace_dir"],
                "inputDir": project["input_dir"],
                "resultDir": project["result_dir"],
                "latestManifestPath": latest_manifest_path,
            },
            "header": {
                "title": project["name"],
                "subtitle": f"{image_count} frames | {camera_summary} | {project['status']} | {project['workspace_dir']}",
            },
            "toolbar": {
                "canAddPhotos": True,
                "canTrain": image_count > 0 and (not latest_job or latest_job["status"] != "running"),
                "canStop": bool(latest_job and latest_job["status"] == "running"),
                "canOpenExport": bool(latest_manifest_path),
              },
              "hud": {
                  "splats": point_count,
                  "performance": f"{preview_fps:0.1f} FPS",
              },
            "statusPanel": {
                "progressPercent": percent,
                "progressLabel": status_title,
                "statusText": status_body,
                "jobStatus": latest_job["status"] if latest_job else None,
                "stage": latest_job["stage"] if latest_job else "Ready",
                "message": latest_job["message"] if latest_job else "No active job.",
                "timeTotal": self._format_duration(latest_job["started_at"], latest_job["finished_at"]) if latest_job else "--",
                "finalLoss": last_loss or "--",
            },
            "propertiesPanel": {
                "items": properties,
                "latestManifestPath": latest_manifest_path,
            },
            "exportPanel": {
                "title": "Export Options",
                "body": (
                    "Trained splats are compiled and ready. Choose a destination format to export the geometry."
                    if latest_manifest_path
                    else "Run a project first to generate the exported splat package."
                ),
                "canExport": bool(latest_manifest_path),
            },
            "preview": preview,
            "liveMonitor": live_monitor,
            "logs": logs_text,
            "photos": [
                {
                    "name": image.name,
                    "path": str(image),
                    "uri": image.resolve().as_uri(),
                }
                for image in images
            ],
            "tabs": {
                "inspectLabel": "Inspect",
                "consoleLabel": "Console",
                "datasetLabel": f"Dataset ({image_count})",
            },
            "trainingSettings": {
                "trainSteps": int(training_settings.get("train_steps", 3000)),
                "qualityPreset": str(training_settings.get("quality_preset", "balanced")),
                "maxGaussians": int(training_settings.get("max_gaussians", 0)),
                "trainResolution": int(training_settings.get("train_resolution", 640)),
                "sfmMatchMode": str(training_settings.get("sfm_match_mode", "auto")),
            },
        }

    def _preview_payload(self, project: dict[str, Any], latest_job: dict[str, Any] | None, images: list[Path]) -> dict[str, Any]:
        preview_path = project.get("last_result_ply")
        if not preview_runtime_available():
            return {
                "title": "Scene Preview (Native Gaussian)",
                "hint": self._preview_runtime_message or "Native preview runtime is unavailable.",
                "footer": self._preview_runtime_message or "Native preview runtime is unavailable.",
                "hasScene": False,
                "emptyTitle": "Native preview unavailable",
                "emptyBody": self._preview_runtime_message or "Build the preview runtime to enable the embedded renderer.",
                "pointCount": None,
                "path": None,
            }

        if not preview_path or not Path(preview_path).exists():
            if not images:
                empty_title = "Add a dataset to this project"
                empty_body = "Use Add Dataset or create a new project from photos."
                footer = "No photos have been added yet."
            elif latest_job and latest_job.get("status") == "running":
                empty_title = "Training in progress"
                empty_body = "The native preview will appear here automatically when the job finishes."
                footer = "Training is running. Logs update live in the right panel."
            else:
                empty_title = "Ready to train"
                empty_body = "Photos are loaded. Press Train Model to generate the scene."
                footer = f"{len(images)} photos loaded. Press Train Model to build the scene."
            return {
                "title": "Scene Preview (Native Gaussian)",
                "hint": "Same gaussian renderer path as SketchUp. LMB orbit, Shift+LMB or RMB/MMB pan, wheel zoom.",
                "footer": footer,
                "hasScene": False,
                "emptyTitle": empty_title,
                "emptyBody": empty_body,
                "pointCount": None,
                "path": None,
            }

        try:
            preview_stats = self._preview_stats(preview_path)
        except Exception as error:
            return {
                "title": "Scene Preview (Native Gaussian)",
                "hint": "Same gaussian renderer path as SketchUp. LMB orbit, Shift+LMB or RMB/MMB pan, wheel zoom.",
                "footer": f"Preview load failed: {error}",
                "hasScene": False,
                "emptyTitle": "Preview failed",
                "emptyBody": str(error),
                "pointCount": None,
                "path": None,
            }
        point_count = int(preview_stats["point_count"])
        bounds = preview_stats["bounds"]
        return {
            "title": "Scene Preview (Native Gaussian)",
            "hint": "Same gaussian renderer path as SketchUp. LMB orbit, Shift+LMB or RMB/MMB pan, wheel zoom.",
            "footer": (
                f"Rendering {point_count} splats with the native gaussian preview. "
                f"Same renderer family as SketchUp, but with an interactive standalone camera. "
                f"Bounds: {bounds['min']} -> {bounds['max']}"
            ),
            "hasScene": True,
            "emptyTitle": "",
            "emptyBody": "",
            "pointCount": point_count,
            "path": preview_path,
        }

    def _preview_stats(self, preview_path: str) -> dict[str, Any]:
        path = Path(preview_path)
        stamp = path.stat().st_mtime_ns
        if preview_path != self._preview_cache_path or stamp != self._preview_cache_stamp:
            _points, stats = read_preview_points(preview_path, sample_limit=64)
            self._preview_cache_path = preview_path
            self._preview_cache_stamp = stamp
            self._preview_cache_stats = stats
        return dict(self._preview_cache_stats or {})

    def _sync_preview_overlay(self, preview: dict[str, Any]) -> None:
        self._preview_overlay.set_host(self._resolve_preview_form(), self._preview_host_bounds())
        if preview.get("hasScene") and preview.get("path"):
            self._preview_overlay.load_scene(preview["path"], force_reload=False)
        else:
            self._preview_overlay.clear_scene()

    def _resolve_active_project_id(self, projects: list[dict[str, Any]], requested_id: str | None) -> str | None:
        project_ids = {project["id"] for project in projects}
        candidate = requested_id or self._active_project_hint
        if candidate in project_ids:
            self._active_project_hint = candidate
            return candidate
        if projects:
            self._active_project_hint = projects[0]["id"]
            return projects[0]["id"]
        self._active_project_hint = None
        return None

    def _resolve_preview_form(self) -> Any | None:
        if not self._window:
            return None
        return getattr(self._window, "native", None)

    def _preview_host_bounds(self) -> PreviewHostBounds:
        if not self._window:
            return self._preview_host
        native = getattr(self._window, "native", None)
        webview_control = getattr(native, "webview", None) if native is not None else None
        offset_left = self._safe_int(getattr(webview_control, "Left", 0)) if webview_control is not None else 0
        offset_top = self._safe_int(getattr(webview_control, "Top", 0)) if webview_control is not None else 0
        scale = self._safe_float(getattr(native, "scale_factor", 1.0)) if native is not None else 1.0
        if scale <= 0:
            scale = 1.0
        left = int(round((self._preview_host.left * scale) + offset_left))
        top = int(round((self._preview_host.top * scale) + offset_top))
        width = int(round(self._preview_host.width * scale))
        height = int(round(self._preview_host.height * scale))
        return PreviewHostBounds(
            left=left,
            top=top,
            width=width,
            height=height,
            visible=self._preview_host.visible,
        )

    def _read_log_text(self, latest_job: dict[str, Any] | None) -> str:
        if not latest_job or not latest_job.get("log_path"):
            return ""
        log_path = Path(latest_job["log_path"])
        if not log_path.exists():
            return ""
        return log_path.read_text(encoding="utf-8")

    def _detect_resolution(self, images: list[Path]) -> str | None:
        if not images:
            return None
        try:
            with Image.open(images[0]) as image:
                return f"{image.width}x{image.height}"
        except Exception:
            return None

    def _extract_last_loss(self, log_text: str) -> str | None:
        matches = re.findall(r"loss=([0-9]*\.?[0-9]+)", log_text)
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

    def _seconds_since_iso(self, value: str | None) -> int | None:
        parsed = self._parse_iso_datetime(value)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))

    def _live_monitor_payload(self, latest_job: dict[str, Any] | None, log_text: str) -> dict[str, Any]:
        if not latest_job:
            return {
                "state": "idle",
                "label": "REST",
                "detail": "No active worker.",
                "ageSeconds": None,
                "showStopPrompt": False,
            }

        job_status = str(latest_job.get("status") or "idle")
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

        activity_age = self._seconds_since_iso(str(latest_job.get("monitor_last_activity_at") or ""))
        if activity_age is None:
            log_activity = self._latest_non_heartbeat_log_datetime(log_text)
            if log_activity is not None:
                activity_age = max(0, int((datetime.now().astimezone() - log_activity).total_seconds()))
        if activity_age is None:
            activity_age = self._seconds_since_iso(str(latest_job.get("updated_at") or ""))

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

        activity_kind = str(latest_job.get("monitor_last_activity_kind") or "activity")
        return {
            "state": state,
            "label": label,
            "detail": f"Last worker {activity_kind}: {self._format_monitor_elapsed(activity_age)}.",
            "ageSeconds": activity_age,
            "showStopPrompt": show_stop_prompt,
            "staleAfterSeconds": LIVE_STALE_SECONDS,
            "askStopAfterSeconds": LIVE_ASK_STOP_SECONDS,
        }

    def _format_duration(self, started_at: str | None, finished_at: str | None) -> str:
        if not started_at:
            return "--"
        start = self._parse_iso_datetime(started_at)
        end = self._parse_iso_datetime(finished_at) if finished_at else datetime.now(timezone.utc)
        if not start or not end:
            return "--"
        seconds = max(0, int((end - start).total_seconds()))
        hours, remainder = divmod(seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        return f"{hours:02d}h {minutes:02d}m"

    def _parse_iso_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _sample_dataset_dir(self) -> Path | None:
        root = Path(__file__).resolve().parents[1]
        sample_dir = root / "sample_datasets" / "nerf_synthetic_lego_12" / "images"
        return sample_dir if sample_dir.exists() else None

    def _pick_media_files(self) -> tuple[str, ...] | None:
        if not self._window or not self._webview:
            return None
        result = self._window.create_file_dialog(
            self._webview.OPEN_DIALOG,
            allow_multiple=True,
            file_types=MEDIA_FILE_FILTER,
        )
        return tuple(result) if result else None

    def _launch_worker(self, job_id: str) -> None:
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW
        worker_python = paths.preferred_worker_python()
        python_executable = str(worker_python or Path(sys.executable))
        repo_root = str(paths.repo_root())
        env = os.environ.copy()
        pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = repo_root if not pythonpath else f"{repo_root}{os.pathsep}{pythonpath}"
        subprocess.Popen(
            [python_executable, "-m", "companion_app.worker_entry", job_id],
            cwd=repo_root,
            env=env,
            creationflags=creationflags,
        )

    def _open_path(self, path: Path) -> None:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
            return
        raise RuntimeError(f"Opening paths is only implemented for Windows right now: {path}")

    def _success(self, message: str | None = None, *, state: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"ok": True}
        if message:
            payload["message"] = message
        if state is not None:
            payload["state"] = state
        return payload

    def _failure(self, message: str, *, cancelled: bool = False) -> dict[str, Any]:
        return {
            "ok": False,
            "cancelled": cancelled,
            "error": message,
            "state": self._build_state(self._active_project_hint),
        }

    def _coerce_hwnd(self, native_window: Any) -> int | None:
        handle = getattr(native_window, "Handle", None)
        if handle is None:
            return None
        if hasattr(handle, "ToInt64"):
            return int(handle.ToInt64())
        try:
            return int(handle)
        except Exception:
            return None

    def _safe_int(self, value: Any) -> int:
        try:
            return int(value)
        except Exception:
            return 0

    def _safe_float(self, value: Any) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0


def _frontend_entrypoint() -> str:
    return str(Path(__file__).resolve().parent / "web_ui" / "index.html")


def _show_error_dialog(title: str, message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
    else:
        print(f"{title}: {message}", file=sys.stderr)


def launch(plugin_root: str | None = None) -> int:
    try:
        import webview
    except ImportError:
        _show_error_dialog(
            "Missing Dependency",
            "The companion app now uses pywebview.\n\nInstall it with:\n\npip install pywebview",
        )
        return 1

    api = CompanionApi(plugin_root=plugin_root, webview_module=webview)
    window = webview.create_window(
        "Gaussian Points Studio",
        _frontend_entrypoint(),
        js_api=api,
        width=1600,
        height=980,
        min_size=(1320, 820),
        background_color="#07070A",
    )
    api.attach_window(window)
    window.events.closed += api._preview_overlay.destroy
    webview.start(debug=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-root", default=None)
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(APP_VERSION)
        return 0
    return launch(plugin_root=args.plugin_root)
