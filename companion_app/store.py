from __future__ import annotations

import ctypes
import json
import os
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths


_LOCK = threading.RLock()
_FILE_LOCK_DEPTH = threading.local()
_STORE_READ_RETRIES = 6
_STORE_READ_RETRY_DELAY_SECONDS = 0.035


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _store_path() -> Path:
    return paths.data_root() / "store.json"


def _default_state() -> dict[str, Any]:
    return {"projects": {}, "jobs": {}}


@contextmanager
def _state_file_lock():
    depth = int(getattr(_FILE_LOCK_DEPTH, "value", 0))
    if depth > 0:
        _FILE_LOCK_DEPTH.value = depth + 1
        try:
            yield
        finally:
            _FILE_LOCK_DEPTH.value = depth
        return

    lock_path = _store_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:  # pragma: no cover - developer convenience outside Windows.
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

        _FILE_LOCK_DEPTH.value = 1
        try:
            yield
        finally:
            _FILE_LOCK_DEPTH.value = 0
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - developer convenience outside Windows.
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


@contextmanager
def _state_lock():
    with _LOCK:
        with _state_file_lock():
            yield


def _read_state_file() -> dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return _default_state()
    last_error: Exception | None = None
    for attempt in range(_STORE_READ_RETRIES):
        try:
            raw_state = path.read_text(encoding="utf-8")
            if not raw_state.strip():
                raise json.JSONDecodeError("Empty state file", raw_state, 0)
            return json.loads(raw_state)
        except (OSError, json.JSONDecodeError) as error:
            last_error = error
            if attempt + 1 >= _STORE_READ_RETRIES:
                break
            time.sleep(_STORE_READ_RETRY_DELAY_SECONDS * (attempt + 1))
    raise RuntimeError(f"Companion state file is temporarily unreadable: {last_error}") from last_error


def _pid_exists(pid: Any) -> bool:
    try:
        pid_value = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_value <= 0:
        return False
    if ctypes is None:  # pragma: no cover
        return False
    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid_value)
    if not process:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code)):
            return False
        return int(exit_code.value) == 259
    finally:
        ctypes.windll.kernel32.CloseHandle(process)


def _reconcile_job_record(job: dict[str, Any], *, mark_stop_requested: bool = False) -> tuple[dict[str, Any], bool]:
    updated = False
    status = str(job.get("status") or "")
    if status == "running":
        pid = job.get("pid")
        if pid and not _pid_exists(pid):
            job["finished_at"] = job.get("finished_at") or utc_now()
            if mark_stop_requested or job.get("stop_requested"):
                job["status"] = "stopped"
                job["stage"] = "Stopped"
                job["message"] = "Stopped after the worker process exited."
                job["error_text"] = None
            else:
                job["status"] = "failed"
                job["stage"] = "Failed"
                message = f"Worker process {pid} is no longer running."
                job["message"] = message
                job["error_text"] = message
            updated = True
        elif mark_stop_requested:
            job["stop_requested"] = 1
            job["message"] = "Stop requested by user."
            updated = True
    elif mark_stop_requested:
        job["stop_requested"] = 1
        job["message"] = "Stop requested by user."
        updated = True
    return job, updated


def _project_status_from_jobs(state: dict[str, Any], project_id: str) -> str:
    project_jobs = [job for job in state["jobs"].values() if job["project_id"] == project_id]
    if not project_jobs:
        project = state["projects"].get(project_id) or {}
        return str(project.get("status") or "idle")
    latest = max(project_jobs, key=lambda item: (item.get("created_at") or "", item.get("updated_at") or ""))
    status = str(latest.get("status") or "idle")
    return "ready" if status == "completed" else status


def _reconcile_state(state: dict[str, Any]) -> bool:
    changed = False
    touched_projects: set[str] = set()
    for job_id, job in state["jobs"].items():
        reconciled, updated = _reconcile_job_record(job)
        if updated:
            reconciled["updated_at"] = utc_now()
            state["jobs"][job_id] = reconciled
            touched_projects.add(reconciled["project_id"])
            changed = True
    for project_id in touched_projects:
        project = state["projects"].get(project_id)
        if not project:
            continue
        desired_status = _project_status_from_jobs(state, project_id)
        if project.get("status") != desired_status:
            project["status"] = desired_status
            project["updated_at"] = utc_now()
            state["projects"][project_id] = project
            changed = True
    return changed


def _load_state() -> dict[str, Any]:
    state = _read_state_file()
    if _reconcile_state(state):
        _save_state(state)
    return state


def _save_state(state: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, indent=2)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp_path.write_text(payload, encoding="utf-8")
        os.replace(tmp_path, path)
    except PermissionError:
        # Some sandboxes and overly eager filesystem guards deny atomic renames.
        # The interprocess state lock still prevents companion readers from
        # observing a half-written file, so fall back to a guarded direct write.
        path.write_text(payload, encoding="utf-8")
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except PermissionError:
            pass


def init_db() -> None:
    paths.ensure_runtime_dirs()
    migrated_paths = paths.migrate_legacy_exports()
    with _state_lock():
        if not _store_path().exists():
            _save_state(_default_state())
        elif migrated_paths:
            state = _load_state()
            remapped = paths.remap_payload_paths(state, migrated_paths)
            if remapped != state:
                _save_state(remapped)


def create_project(name: str, backend: str = "gsplat_colmap", note: str | None = None) -> dict[str, Any]:
    project_id = uuid.uuid4().hex
    paths.ensure_project_dirs(project_id)
    now = utc_now()
    initial_training_settings = sanitize_training_settings(default_job_settings(force_restart=False))
    payload = {
        "id": project_id,
        "name": name.strip() or "Untitled Project",
        "status": "idle",
        "backend": backend,
        "created_at": now,
        "updated_at": now,
        "workspace_dir": str(paths.project_root(project_id)),
        "input_dir": str(paths.project_input_dir(project_id)),
        "result_dir": str(paths.project_result_dir(project_id)),
        "last_result_ply": None,
        "last_result_gasp": None,
        "last_manifest_path": None,
        "last_import_summary": None,
        "last_training_summary": None,
        "training_settings": initial_training_settings,
        "note": note,
    }
    with _state_lock():
        state = _load_state()
        state["projects"][project_id] = payload
        _save_state(state)
    return payload


def list_projects() -> list[dict[str, Any]]:
    with _state_lock():
        state = _load_state()
        projects = list(state["projects"].values())
    return sorted(projects, key=lambda item: (item["updated_at"], item["created_at"]), reverse=True)


def get_project(project_id: str) -> dict[str, Any] | None:
    with _state_lock():
        state = _load_state()
        project = state["projects"].get(project_id)
    return dict(project) if project else None


def update_project(project_id: str, **updates: Any) -> dict[str, Any] | None:
    with _state_lock():
        state = _load_state()
        project = state["projects"].get(project_id)
        if not project:
            return None
        project.update(updates)
        project["updated_at"] = utc_now()
        state["projects"][project_id] = project
        _save_state(state)
        return dict(project)


def default_job_settings(force_restart: bool = False) -> dict[str, Any]:
    return {
        "trainer_backend": "gsplat_colmap",
        "quality_preset": "balanced",
        "strategy_name": "auto",
        "rasterize_mode": "auto",
        "sfm_match_mode": "auto",
        "max_gaussians": 0,
        "budget_schedule": "igs_plus",
        "train_steps": 3000,
        "train_resolution": 640,
        "validation_fraction": 0.18,
        "min_validation_views": 2,
        "sh_degree": 3,
        "sh_increment_interval": 0,
        "init_opacity": 0.30,
        "lambda_dssim": 0.20,
        "means_lr": 1.6e-4,
        "scales_lr": 5.0e-3,
        "opacities_lr": 5.0e-2,
        "quats_lr": 1.0e-3,
        "sh0_lr": 2.5e-3,
        "shN_lr": 1.25e-4,
        "sfm_max_image_size": 1600,
        "sfm_num_threads": 6,
        "preserve_sfm_cache": False,
        "densify_start_iter": 0,
        "densify_stop_iter": 0,
        "densify_interval": 0,
        "opacity_reset_interval": 3000,
        "alpha_loss_weight": 0.05,
        "random_background": True,
        "enable_appearance_compensation": False,
        "appearance_mode": "off",
        "appearance_grid_size": 16,
        "appearance_grid_low_res": 8,
        "appearance_grid_high_res": 16,
        "appearance_luma_bins": 8,
        "appearance_grid_lr": 2.0e-3,
        "appearance_regularization": 7.5e-4,
        "appearance_smoothness": 5.0,
        "appearance_tv_weight": 5.0,
        "appearance_grid_warmup_factor": 0.01,
        "appearance_grid_final_lr_factor": 0.01,
        "appearance_bake_steps": 240,
        "appearance_ssim_gate_scale": 2.5,
        "enable_exposure_compensation": True,
        "exposure_gain_lr": 2.0e-3,
        "exposure_bias_lr": 1.0e-3,
        "exposure_regularization": 1.0e-3,
        "enable_depth_reinit": True,
        "depth_reinit_every": 5000,
        "depth_reinit_views": 2,
        "depth_reinit_points": 2048,
        "enable_depth_bootstrap": True,
        "depth_bootstrap_points": 16000,
        "depth_bootstrap_max_views": 16,
        "depth_bootstrap_max_image_size": 1024,
        "depth_bootstrap_min_consistent": 3,
        "depth_bootstrap_voxel_factor": 0.75,
        "enable_unscented_transform": True,
        "mask_min_views": 2,
        "alpha_mask_threshold": 0.2,
        "visual_hull_seed_points": 1400,
        "visual_hull_init_grid": 40,
        "visual_hull_support_ratio": 0.8,
        "grow_grad2d": 2.0e-4,
        "prune_opa": 0.005,
        "min_opacity": 0.0,
        "mcmc_noise_lr": 0.0,
        "edge_threshold": 0.12,
        "edge_warmup_events": 0,
        "las_primary_shrink": 1.6,
        "las_secondary_shrink": 1.2,
        "las_opacity_factor": 0.85,
        "las_offset_scale": 0.55,
        "edge_candidate_factor": 4,
        "edge_score_weight": 0.25,
        "absgrad": None,
        "revised_opacity": True,
        "scales_lr_warmup_multiplier": 1.35,
        "scales_lr_final_multiplier": 0.55,
        "grid_size": 56,
        "max_image_edge": 960,
        "mask_threshold": 46,
        "camera_fov_degrees": 38.0,
        "camera_distance": 2.8,
        "subject_fill_ratio": 0.68,
        "force_restart": force_restart,
    }


def _normalize_auto_research_defaults(settings: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(settings)
    strategy_name = str(normalized.get("strategy_name") or defaults["strategy_name"]).strip().lower()
    if strategy_name != "auto":
        return normalized

    if normalized.get("budget_schedule") in {None, "", "staged"}:
        normalized["budget_schedule"] = defaults["budget_schedule"]
    if normalized.get("init_opacity") in {None, "", 0, 0.1, 0.10}:
        normalized["init_opacity"] = defaults["init_opacity"]
    if normalized.get("opacity_reset_interval") in {None, "", 0, 1500}:
        normalized["opacity_reset_interval"] = defaults["opacity_reset_interval"]
    if normalized.get("depth_reinit_every") in {None, "", 0, 200}:
        normalized["depth_reinit_every"] = defaults["depth_reinit_every"]
    if normalized.get("grow_grad2d") in {None, "", 0, 0.0}:
        normalized["grow_grad2d"] = defaults["grow_grad2d"]
    if normalized.get("prune_opa") in {None, "", 0, 0.0}:
        normalized["prune_opa"] = defaults["prune_opa"]
    if normalized.get("edge_warmup_events") in {None, "", 3}:
        normalized["edge_warmup_events"] = defaults["edge_warmup_events"]
    if normalized.get("edge_candidate_factor") in {None, "", 0}:
        normalized["edge_candidate_factor"] = defaults["edge_candidate_factor"]
    if normalized.get("edge_score_weight") in {None, ""}:
        normalized["edge_score_weight"] = defaults["edge_score_weight"]
    return normalized


def sanitize_training_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    defaults = default_job_settings(force_restart=False)
    sanitized: dict[str, Any] = {}
    source = _normalize_auto_research_defaults(settings or {}, defaults)
    for key, default_value in defaults.items():
        if key in {"force_restart", "preserve_sfm_cache"}:
            continue
        value = source.get(key, default_value)
        sanitized[key] = default_value if value in {None, ""} else value
    return sanitized


def project_training_settings(project_id: str, force_restart: bool = False) -> dict[str, Any]:
    merged = default_job_settings(force_restart=force_restart)
    project = get_project(project_id)
    saved_settings = sanitize_training_settings((project or {}).get("training_settings"))
    merged.update(saved_settings)
    merged["force_restart"] = force_restart
    return merged


def save_project_training_settings(project_id: str, settings: dict[str, Any]) -> dict[str, Any] | None:
    return update_project(project_id, training_settings=sanitize_training_settings(settings))


def create_job(project_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    project = get_project(project_id)
    if not project:
        raise ValueError(f"Unknown project: {project_id}")
    job_id = uuid.uuid4().hex
    log_path = str(Path(project["workspace_dir"]) / "logs" / f"{job_id}.log")
    now = utc_now()
    normalized_settings = sanitize_training_settings(settings)
    payload = {
        "id": job_id,
        "project_id": project_id,
        "status": "queued",
        "stage": "Queued",
        "progress": 0.0,
        "message": "Waiting to start.",
        "log_path": log_path,
        "settings": normalized_settings,
        "error_text": None,
        "pid": None,
        "stop_requested": 0,
        "created_at": now,
        "started_at": None,
        "finished_at": None,
        "updated_at": now,
    }
    with _state_lock():
        state = _load_state()
        state["jobs"][job_id] = payload
        _save_state(state)
    update_project(
        project_id,
        status="queued",
        training_settings=normalized_settings,
    )
    return payload


def get_job(job_id: str) -> dict[str, Any] | None:
    with _state_lock():
        state = _load_state()
        job = state["jobs"].get(job_id)
    return dict(job) if job else None


def list_jobs(project_id: str) -> list[dict[str, Any]]:
    with _state_lock():
        state = _load_state()
        jobs = [dict(job) for job in state["jobs"].values() if job["project_id"] == project_id]
    return sorted(jobs, key=lambda item: item["created_at"], reverse=True)


def latest_job(project_id: str) -> dict[str, Any] | None:
    jobs = list_jobs(project_id)
    return jobs[0] if jobs else None


def update_job(job_id: str, **updates: Any) -> dict[str, Any] | None:
    with _state_lock():
        state = _load_state()
        job = state["jobs"].get(job_id)
        if not job:
            return None
        job.update(updates)
        job["updated_at"] = utc_now()
        state["jobs"][job_id] = job
        _save_state(state)
        result = dict(job)

    project_status = result["status"] if result["status"] != "completed" else "ready"
    update_project(result["project_id"], status=project_status)
    return result


def delete_project(project_id: str, *, delete_files: bool = False) -> bool:
    project_snapshot: dict[str, Any] | None = None
    with _state_lock():
        state = _load_state()
        project_snapshot = dict(state["projects"].get(project_id) or {})
        if not project_snapshot:
            return False
    if delete_files and project_snapshot:
        _delete_project_files(project_snapshot)
    with _state_lock():
        state = _load_state()
        if project_id not in state["projects"]:
            return False
        del state["projects"][project_id]
        job_ids_to_remove = [jid for jid, j in state["jobs"].items() if j["project_id"] == project_id]
        for jid in job_ids_to_remove:
            del state["jobs"][jid]
        _save_state(state)
    return True


def _delete_project_files(project: dict[str, Any]) -> None:
    workspace_dir = Path(str(project.get("workspace_dir") or ""))
    if workspace_dir.exists() and paths.is_within_dir(workspace_dir, paths.projects_root()):
        _remove_path(workspace_dir)

    scratch_dir = paths.project_scratch_dir(str(project.get("id") or ""))
    if scratch_dir.exists() and paths.is_within_dir(scratch_dir, paths.scratch_root()):
        _remove_path(scratch_dir)

    for target in _project_export_targets(project):
        if not target.exists():
            continue
        _remove_path(target)

    _delete_latest_export_reference(str(project.get("id") or ""))


def _project_export_targets(project: dict[str, Any]) -> list[Path]:
    targets: list[Path] = []
    seen: set[str] = set()

    def add_target(path: Path | None) -> None:
        if path is None:
            return
        key = str(path.resolve() if path.exists() else path).lower()
        if key in seen:
            return
        seen.add(key)
        targets.append(path)

    manifest_value = str(project.get("last_manifest_path") or "").strip()
    if manifest_value:
        for target in _targets_from_manifest_path(Path(manifest_value)):
            add_target(target)

    for manifest_path in _find_project_export_manifests(str(project.get("id") or "")):
        for target in _targets_from_manifest_path(manifest_path):
            add_target(target)

    return targets


def _find_project_export_manifests(project_id: str) -> list[Path]:
    if not project_id:
        return []
    manifests: list[Path] = []
    seen: set[str] = set()
    for root in paths.managed_export_roots():
        if not root.exists():
            continue
        candidates = list(root.rglob("scene_manifest.json")) + list(root.glob("*_manifest.json"))
        for manifest_path in candidates:
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            manifest_project_id = str(payload.get("project_id") or payload.get("projectId") or "").strip()
            if manifest_project_id != project_id:
                continue
            key = str(manifest_path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            manifests.append(manifest_path)
    return manifests


def _targets_from_manifest_path(manifest_path: Path) -> list[Path]:
    parent = manifest_path.parent
    if manifest_path.name.lower() == "scene_manifest.json" and _is_managed_export_container(parent):
        return [parent]

    if any(parent.resolve() == root.resolve() for root in paths.managed_export_roots() if root.exists()):
        stem = manifest_path.stem
        if stem.endswith("_manifest"):
            stem = stem[: -len("_manifest")]
        return [
            parent / f"{stem}.ply",
            parent / f"{stem}.compressed.ply",
            parent / f"{stem}.gasp",
            parent / f"{stem}.spz",
            parent / f"{stem}.gspkg",
            parent / f"{stem}_manifest.json",
        ]

    return []


def _is_managed_export_container(path: Path) -> bool:
    for root in paths.managed_export_roots():
        if root.exists() and path != root and paths.is_within_dir(path, root):
            return True
    return False


def _delete_latest_export_reference(project_id: str) -> None:
    for latest_path in (paths.latest_export_path(), paths.bridge_latest_export_path()):
        if not latest_path.exists():
            continue
        try:
            payload = json.loads(latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(payload.get("project_id") or "") != project_id:
            continue
        try:
            latest_path.unlink()
        except OSError:
            pass


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=False, onerror=_retry_remove_with_chmod)
        return
    try:
        path.unlink()
    except PermissionError:
        os.chmod(path, 0o666)
        path.unlink()


def _retry_remove_with_chmod(func, path, exc_info) -> None:
    _error = exc_info[1]
    os.chmod(path, 0o777)
    func(path)


def request_job_stop(job_id: str) -> None:
    with _state_lock():
        state = _read_state_file()
        job = state["jobs"].get(job_id)
        if not job:
            return
        job, updated = _reconcile_job_record(job, mark_stop_requested=True)
        if updated:
            job["updated_at"] = utc_now()
            state["jobs"][job_id] = job
            project = state["projects"].get(job["project_id"])
            if project:
                project["status"] = _project_status_from_jobs(state, job["project_id"])
                project["updated_at"] = utc_now()
                state["projects"][job["project_id"]] = project
            _save_state(state)
            return
    update_job(job_id, stop_requested=1, message="Stop requested by user.")


def clear_job_stop(job_id: str) -> None:
    update_job(job_id, stop_requested=0)


def job_stop_requested(job_id: str) -> bool:
    job = get_job(job_id)
    return bool(job and job.get("stop_requested"))
