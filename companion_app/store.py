from __future__ import annotations

import ctypes
import json
import os
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
    with _state_lock():
        if not _store_path().exists():
            _save_state(_default_state())


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
        "budget_schedule": "staged",
        "train_steps": 3000,
        "train_resolution": 640,
        "validation_fraction": 0.18,
        "min_validation_views": 2,
        "sh_degree": 3,
        "sh_increment_interval": 0,
        "init_opacity": 0.10,
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
        "opacity_reset_interval": 1500,
        "alpha_loss_weight": 0.05,
        "random_background": True,
        "enable_exposure_compensation": True,
        "exposure_gain_lr": 2.0e-3,
        "exposure_bias_lr": 1.0e-3,
        "exposure_regularization": 1.0e-3,
        "mask_min_views": 2,
        "alpha_mask_threshold": 0.2,
        "visual_hull_seed_points": 1400,
        "visual_hull_init_grid": 40,
        "visual_hull_support_ratio": 0.8,
        "grow_grad2d": 0.0,
        "prune_opa": 0.0,
        "min_opacity": 0.0,
        "mcmc_noise_lr": 0.0,
        "absgrad": None,
        "revised_opacity": True,
        "grid_size": 56,
        "max_image_edge": 960,
        "mask_threshold": 46,
        "camera_fov_degrees": 38.0,
        "camera_distance": 2.8,
        "subject_fill_ratio": 0.68,
        "force_restart": force_restart,
    }


def sanitize_training_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    defaults = default_job_settings(force_restart=False)
    sanitized: dict[str, Any] = {}
    source = settings or {}
    for key, default_value in defaults.items():
        if key in {"force_restart", "preserve_sfm_cache"}:
            continue
        sanitized[key] = source.get(key, default_value)
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
    payload = {
        "id": job_id,
        "project_id": project_id,
        "status": "queued",
        "stage": "Queued",
        "progress": 0.0,
        "message": "Waiting to start.",
        "log_path": log_path,
        "settings": settings,
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
        training_settings=sanitize_training_settings(settings),
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


def delete_project(project_id: str) -> bool:
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
