from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import paths


STATUS_STALE_SECONDS = 6.0
POLL_INTERVAL_SECONDS = 0.1
DEFAULT_TIMEOUT_SECONDS = 8.0


def list_sessions() -> list[dict[str, str]]:
    paths.ensure_bridge_dirs()
    sessions: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for session_file in sorted(paths.bridge_sessions_dir().glob("*.json")):
        payload = _read_json(session_file)
        session = _normalize_session(payload, fallback_id=session_file.stem, command_scope="session")
        if session is None:
            continue
        session_id = str(session.get("id") or "")
        if not session_id or session_id in seen_ids:
            continue
        seen_ids.add(session_id)
        sessions.append(session)

    if sessions:
        return sorted(sessions, key=_session_sort_key)

    legacy_status = _read_json(paths.bridge_status_path())
    legacy_session = _normalize_session(
        legacy_status,
        fallback_id=str((legacy_status or {}).get("sketchup_pid") or "default"),
        command_scope="legacy",
    )
    if legacy_session is not None:
        sessions.append(legacy_session)
    return sorted(sessions, key=_session_sort_key)


def request_import(
    scene_path: str | Path,
    *,
    session_id: str | None = None,
    scene_name: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    process_events: Callable[[], None] | None = None,
) -> tuple[bool, str]:
    sessions = list_sessions()
    bridge_error = bridge_availability_error(sessions)
    if bridge_error:
        return False, bridge_error
    selected_session = _pick_session(sessions, session_id=session_id)
    if selected_session is None:
        return False, "SketchUp session is no longer available. Reopen the target SketchUp window and try again."

    paths.ensure_bridge_dirs()
    command_id = uuid.uuid4().hex
    if str(selected_session.get("command_scope") or "") == "legacy":
        command_path = paths.bridge_commands_dir() / f"{command_id}.json"
    else:
        command_path = paths.bridge_session_commands_dir(str(selected_session["id"])) / f"{command_id}.json"
    response_path = paths.bridge_responses_dir() / f"{command_id}.json"
    payload = {
        "id": command_id,
        "command": "import_scene",
        "target_session_id": str(selected_session["id"]),
        "scene_path": str(Path(scene_path)),
        "scene_name": scene_name or Path(scene_path).name,
        "requested_at": _utc_now(),
    }
    _write_json(command_path, payload)

    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        if process_events is not None:
            process_events()
        response = _read_json(response_path)
        if response is not None:
            _safe_unlink(response_path)
            _safe_unlink(command_path)
            if bool(response.get("ok")):
                return True, str(response.get("message") or "Imported into SketchUp.")
            return False, str(response.get("error") or "SketchUp failed to import the scene.")
        time.sleep(POLL_INTERVAL_SECONDS)

    if command_path.exists():
        _safe_unlink(command_path)
        return (
            False,
            f'SketchUp session "{selected_session.get("name") or "Untitled.skp"}" did not consume the import request. '
            "Reload the Gaussian Points plugin in that SketchUp window once so it picks up the updated bridge code.",
        )

    _safe_unlink(command_path)
    return False, "SketchUp did not respond. Make sure SketchUp is open and the Gaussian Points plugin is loaded."


def bridge_availability_error(sessions: list[dict[str, str]] | None = None) -> str | None:
    available = list_sessions() if sessions is None else sessions
    if not available:
        return "No open SketchUp windows were found. Open a SketchUp project and make sure the Gaussian Points plugin is loaded."
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _normalize_session(payload: dict | None, *, fallback_id: str, command_scope: str) -> dict[str, str] | None:
    if not isinstance(payload, dict):
        return None
    updated_at = _parse_timestamp(str(payload.get("updated_at") or ""))
    if updated_at is None:
        return None
    age_seconds = (datetime.now(timezone.utc) - updated_at).total_seconds()
    if age_seconds > STATUS_STALE_SECONDS:
        return None

    session_id = str(payload.get("id") or fallback_id or "").strip()
    if not session_id:
        return None
    name = str(payload.get("model_name") or "").strip() or "Untitled.skp"
    description = str(payload.get("description") or "").strip() or "SketchUp"
    preview_path = str(payload.get("preview_path") or "").strip()
    if preview_path and not Path(preview_path).exists():
        preview_path = ""
    return {
        "id": session_id,
        "name": name,
        "description": description,
        "preview_path": preview_path,
        "updated_at": updated_at.isoformat(),
        "sketchup_pid": str(payload.get("sketchup_pid") or ""),
        "command_scope": command_scope,
    }


def _pick_session(sessions: list[dict[str, str]], *, session_id: str | None) -> dict[str, str] | None:
    if session_id:
        target = str(session_id)
        for session in sessions:
            if str(session.get("id") or "") == target:
                return session
        return None
    if len(sessions) == 1:
        return sessions[0]
    return None


def _session_sort_key(session: dict[str, str]) -> tuple[str, str]:
    return (str(session.get("name") or "").lower(), str(session.get("id") or ""))
