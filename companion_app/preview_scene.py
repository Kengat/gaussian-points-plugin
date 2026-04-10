from __future__ import annotations

from pathlib import Path
from typing import Any

def preview_scene_path(project: dict[str, Any]) -> str | None:
    for key in ("last_result_gasp", "last_result_ply"):
        candidate = project.get(key)
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None
