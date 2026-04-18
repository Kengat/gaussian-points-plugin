from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path


APP_DIR_NAME = "GaussianPointsCompanion"
EXPORTS_DIR_NAME = "companion_exports"
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


def bridge_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / APP_DIR_NAME
    return data_root()


def bridge_dir() -> Path:
    return bridge_root() / "bridge"


def bridge_status_path() -> Path:
    return bridge_dir() / "status.json"


def bridge_commands_dir() -> Path:
    return bridge_dir() / "commands"


def bridge_session_commands_dir(session_id: str) -> Path:
    return bridge_commands_dir() / _safe_session_id(session_id)


def bridge_responses_dir() -> Path:
    return bridge_dir() / "responses"


def bridge_sessions_dir() -> Path:
    return bridge_dir() / "sessions"


def bridge_previews_dir() -> Path:
    return bridge_dir() / "previews"


def bridge_latest_export_path() -> Path:
    return bridge_root() / "latest_export.json"


def projects_root() -> Path:
    return data_root() / "projects"


def exports_root() -> Path:
    return repo_root() / EXPORTS_DIR_NAME


def ensure_runtime_dirs() -> None:
    for path in (data_root(), projects_root(), exports_root()):
        path.mkdir(parents=True, exist_ok=True)


def ensure_bridge_dirs() -> None:
    for path in (
        bridge_root(),
        bridge_dir(),
        bridge_commands_dir(),
        bridge_responses_dir(),
        bridge_sessions_dir(),
        bridge_previews_dir(),
    ):
        path.mkdir(parents=True, exist_ok=True)


def export_session_dir(project_name: str | None, created_at: str | None = None) -> Path:
    root = exports_root()
    root.mkdir(parents=True, exist_ok=True)
    stem = _safe_export_stem(project_name)
    timestamp = _export_timestamp(created_at)
    candidate = root / f"{stem}_{timestamp}"
    counter = 2
    while candidate.exists():
        candidate = root / f"{stem}_{timestamp}_{counter}"
        counter += 1
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def migrate_legacy_exports() -> dict[str, str]:
    target_root = exports_root()
    target_root.mkdir(parents=True, exist_ok=True)

    moved_paths: dict[str, str] = {}
    seen: set[str] = set()
    for legacy_root in _legacy_export_roots():
        legacy_key = str(legacy_root).lower()
        if legacy_key in seen:
            continue
        seen.add(legacy_key)
        if not legacy_root.exists() or legacy_root.resolve() == target_root.resolve():
            continue
        for child in sorted(legacy_root.iterdir(), key=lambda item: item.name.lower()):
            destination = _move_export_entry(child, target_root)
            moved_paths[str(child)] = str(destination)
        try:
            legacy_root.rmdir()
        except OSError:
            pass

    if moved_paths:
        _rewrite_export_json_files(target_root, moved_paths)
        _rewrite_json_file_paths(latest_export_path(), moved_paths)
    return moved_paths


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


def remap_payload_paths(payload, mapping: dict[str, str]):
    if isinstance(payload, dict):
        changed = False
        remapped: dict = {}
        for key, value in payload.items():
            next_value = remap_payload_paths(value, mapping)
            remapped[key] = next_value
            changed = changed or next_value is not value
        return remapped if changed else payload
    if isinstance(payload, list):
        changed = False
        remapped_items = []
        for value in payload:
            next_value = remap_payload_paths(value, mapping)
            remapped_items.append(next_value)
            changed = changed or next_value is not value
        return remapped_items if changed else payload
    if isinstance(payload, str):
        remapped = remap_path_string(payload, mapping)
        return remapped
    return payload


def remap_path_string(value: str, mapping: dict[str, str]) -> str:
    if not value:
        return value
    normalized = str(Path(value))
    lowered = normalized.lower()
    for source, destination in sorted(
        ((str(Path(src)), str(Path(dst))) for src, dst in mapping.items()),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        source_lower = source.lower()
        if lowered == source_lower:
            return destination
        for separator in ("\\", "/"):
            prefix = f"{source_lower}{separator}"
            if lowered.startswith(prefix):
                suffix = normalized[len(source) :].lstrip("\\/")
                return str(Path(destination) / suffix)
    return value


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
    serialized = json.dumps(payload, indent=2)
    latest_export_path().write_text(serialized, encoding="utf-8")
    bridge_path = bridge_latest_export_path()
    if bridge_path.resolve() != latest_export_path().resolve():
        bridge_path.parent.mkdir(parents=True, exist_ok=True)
        bridge_path.write_text(serialized, encoding="utf-8")


def managed_export_roots() -> list[Path]:
    roots = [exports_root(), *_legacy_export_roots()]
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def is_within_dir(path: str | Path, root: str | Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _legacy_export_roots() -> list[Path]:
    roots: list[Path] = []
    override = os.environ.get("GAUSSIAN_POINTS_COMPANION_HOME")
    if override:
        roots.append(Path(override) / "exports")
    base = os.environ.get("LOCALAPPDATA")
    if base:
        roots.append(Path(base) / APP_DIR_NAME / "exports")
    roots.append(Path(tempfile.gettempdir()) / APP_DIR_NAME / "exports")
    roots.append(repo_root() / "companion_app_data" / "exports")
    roots.append(data_root() / "exports")
    return roots


def _safe_session_id(value: str) -> str:
    text = (value or "").strip()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return safe or "default"


def _move_export_entry(source: Path, target_root: Path) -> Path:
    destination = target_root / source.name
    if destination.exists() and not source.is_dir():
        destination = _unique_export_destination(target_root, source.name)
    try:
        shutil.move(str(source), str(destination))
    except (OSError, PermissionError):
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination, dirs_exist_ok=True)
            try:
                shutil.rmtree(source)
            except OSError:
                pass
        else:
            shutil.copy2(source, destination)
            try:
                source.unlink()
            except OSError:
                pass
    return destination


def _unique_export_destination(target_root: Path, name: str) -> Path:
    candidate = target_root / name
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 2
    while candidate.exists():
        candidate = target_root / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def _rewrite_export_json_files(target_root: Path, mapping: dict[str, str]) -> None:
    if not target_root.exists():
        return
    for payload_path in target_root.rglob("*.json"):
        _rewrite_json_file_paths(payload_path, mapping)


def _rewrite_json_file_paths(path: Path, mapping: dict[str, str]) -> None:
    if not path.exists() or path.suffix.lower() != ".json":
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    remapped = remap_payload_paths(payload, mapping)
    if remapped != payload:
        path.write_text(json.dumps(remapped, indent=2), encoding="utf-8")


def _safe_export_stem(project_name: str | None) -> str:
    stem = (project_name or "").strip()
    stem = "".join("_" if char in '<>:"/\\|?*' or ord(char) < 32 else char for char in stem)
    stem = " ".join(stem.split()).strip(" .")
    return stem or "Untitled_Project"


def _export_timestamp(created_at: str | None) -> str:
    if created_at:
        cleaned = created_at.strip()
        try:
            normalized = cleaned.replace("Z", "+00:00")
            moment = datetime.fromisoformat(normalized)
        except ValueError:
            pass
        else:
            return moment.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
