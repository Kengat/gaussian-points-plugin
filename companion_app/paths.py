from __future__ import annotations

import json
import os
import tempfile
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
    candidates = []
    if base:
        candidates.append(Path(base) / APP_DIR_NAME)
    candidates.append(Path(tempfile.gettempdir()) / APP_DIR_NAME)
    candidates.append(Path.cwd() / "companion_app_data")
    _DATA_ROOT = _pick_writable_dir(candidates)
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


def _pick_writable_dir(candidates: list[Path]) -> Path:
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except OSError:
            continue
    fallback = candidates[-1]
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


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


def scratch_root() -> Path:
    override = os.environ.get("GAUSSIAN_POINTS_COMPANION_SCRATCH_HOME")
    if override:
        candidate = Path(override)
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    local_app_data = os.environ.get("LOCALAPPDATA")
    candidates = []
    if local_app_data:
        candidates.append(Path(local_app_data) / APP_DIR_NAME / "scratch")
    candidates.append(Path(tempfile.gettempdir()) / APP_DIR_NAME / "scratch")
    candidates.append(data_root() / "scratch")
    return _pick_writable_dir(candidates)


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


def project_scratch_dir(project_id: str) -> Path:
    return scratch_root() / "projects" / project_id


def project_colmap_scratch_dir(project_id: str) -> Path:
    return project_scratch_dir(project_id) / "colmap"


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
