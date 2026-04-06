from __future__ import annotations

import json
import os
from pathlib import Path


APP_DIR_NAME = "GaussianPointsCompanion"
_DATA_ROOT: Path | None = None


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def data_root() -> Path:
    global _DATA_ROOT
    if _DATA_ROOT is not None:
        return _DATA_ROOT

    override = os.environ.get("GAUSSIAN_POINTS_COMPANION_HOME")
    if override:
        _DATA_ROOT = Path(override)
        return _DATA_ROOT

    base = os.environ.get("LOCALAPPDATA")
    preferred = Path(base) / APP_DIR_NAME if base else Path.home() / f".{APP_DIR_NAME.lower()}"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        _DATA_ROOT = preferred
    except PermissionError:
        _DATA_ROOT = Path.cwd() / "companion_app_data"
    return _DATA_ROOT


def db_path() -> Path:
    return data_root() / "companion.db"


def latest_export_path() -> Path:
    return data_root() / "latest_export.json"


def projects_root() -> Path:
    return data_root() / "projects"


def exports_root() -> Path:
    return data_root() / "exports"


def ensure_runtime_dirs() -> None:
    for path in (data_root(), projects_root(), exports_root()):
        path.mkdir(parents=True, exist_ok=True)


def preferred_worker_python() -> Path | None:
    candidates = [
        repo_root() / ".gstrain310" / "Scripts" / "python.exe",
        repo_root() / ".gstrain310" / "bin" / "python",
        repo_root() / ".gstrain311" / "Scripts" / "python.exe",
        repo_root() / ".gstrain311" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def project_root(project_id: str) -> Path:
    return projects_root() / project_id


def project_input_dir(project_id: str) -> Path:
    return project_root(project_id) / "input"


def project_stage_dir(project_id: str) -> Path:
    return project_root(project_id) / "stages"


def project_result_dir(project_id: str) -> Path:
    return project_root(project_id) / "result"


def project_log_dir(project_id: str) -> Path:
    return project_root(project_id) / "logs"


def ensure_project_dirs(project_id: str) -> None:
    for path in (
        project_root(project_id),
        project_input_dir(project_id),
        project_stage_dir(project_id),
        project_result_dir(project_id),
        project_log_dir(project_id),
    ):
        path.mkdir(parents=True, exist_ok=True)


def write_latest_export(payload: dict) -> None:
    ensure_runtime_dirs()
    latest_export_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")
