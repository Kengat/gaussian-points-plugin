from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths


_LOCK = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _store_path() -> Path:
    return paths.data_root() / "store.json"


def _default_state() -> dict[str, Any]:
    return {"projects": {}, "jobs": {}}


def _load_state() -> dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return _default_state()
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(state: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def init_db() -> None:
    with _LOCK:
        if not _store_path().exists():
            _save_state(_default_state())


def create_project(name: str, backend: str = "gsplat_colmap", note: str | None = None) -> dict[str, Any]:
    project_id = uuid.uuid4().hex
    paths.ensure_project_dirs(project_id)
    now = utc_now()
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
        "last_manifest_path": None,
        "note": note,
    }
    with _LOCK:
        state = _load_state()
        state["projects"][project_id] = payload
        _save_state(state)
    return payload


def list_projects() -> list[dict[str, Any]]:
    with _LOCK:
        state = _load_state()
        projects = list(state["projects"].values())
    return sorted(projects, key=lambda item: (item["updated_at"], item["created_at"]), reverse=True)


def get_project(project_id: str) -> dict[str, Any] | None:
    with _LOCK:
        state = _load_state()
        project = state["projects"].get(project_id)
    return dict(project) if project else None


def update_project(project_id: str, **updates: Any) -> dict[str, Any] | None:
    with _LOCK:
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
        "train_steps": 3000,
        "train_resolution": 640,
        "sh_degree": 3,
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
        "densify_start_iter": 250,
        "densify_stop_iter": 7000,
        "densify_interval": 100,
        "opacity_reset_interval": 1500,
        "alpha_loss_weight": 0.05,
        "random_background": True,
        "mask_min_views": 2,
        "alpha_mask_threshold": 0.2,
        "visual_hull_seed_points": 1400,
        "visual_hull_init_grid": 40,
        "visual_hull_support_ratio": 0.8,
        "grow_grad2d": 7.5e-5,
        "absgrad": True,
        "grid_size": 56,
        "max_image_edge": 960,
        "mask_threshold": 46,
        "camera_fov_degrees": 38.0,
        "camera_distance": 2.8,
        "subject_fill_ratio": 0.68,
        "force_restart": force_restart,
    }


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
    with _LOCK:
        state = _load_state()
        state["jobs"][job_id] = payload
        _save_state(state)
    update_project(project_id, status="queued")
    return payload


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        state = _load_state()
        job = state["jobs"].get(job_id)
    return dict(job) if job else None


def list_jobs(project_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        state = _load_state()
        jobs = [dict(job) for job in state["jobs"].values() if job["project_id"] == project_id]
    return sorted(jobs, key=lambda item: item["created_at"], reverse=True)


def latest_job(project_id: str) -> dict[str, Any] | None:
    jobs = list_jobs(project_id)
    return jobs[0] if jobs else None


def update_job(job_id: str, **updates: Any) -> dict[str, Any] | None:
    with _LOCK:
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
    with _LOCK:
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
    update_job(job_id, stop_requested=1, message="Stop requested by user.")


def clear_job_stop(job_id: str) -> None:
    update_job(job_id, stop_requested=0)


def job_stop_requested(job_id: str) -> bool:
    job = get_job(job_id)
    return bool(job and job.get("stop_requested"))
