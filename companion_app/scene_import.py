from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from . import paths, store
from .gaussian_gasp import (
    export_ply_from_gaussian_gasp,
    read_gaussian_gasp_metadata,
    result_gasp_path,
    safe_export_stem,
    write_gaussian_gasp_from_ply,
)
from .ply import read_preview_points


SUPPORTED_SCENE_SUFFIXES = {".gasp", ".ply"}
IMPORT_MODE_CONVERT = "convert"
IMPORT_MODE_DIRECT = "direct"


def _copy_if_different(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == destination.resolve():
        return
    shutil.copy2(source, destination)


def import_gaussian_scene_file(source_path: str | Path, mode: str = IMPORT_MODE_CONVERT) -> dict[str, Any]:
    source = Path(source_path)
    if not source.exists():
        raise FileNotFoundError(f"Scene file was not found: {source}")
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SCENE_SUFFIXES:
        raise ValueError("Choose a Gaussian .gasp or .ply file.")
    mode = mode if mode in {IMPORT_MODE_CONVERT, IMPORT_MODE_DIRECT} else IMPORT_MODE_CONVERT

    project = store.create_project(name=source.stem, backend="imported_gaussian", note=f"imported_scene:{source}")
    workspace_gasp = result_gasp_path(project["id"], project["name"])
    export_stem = safe_export_stem(project["name"])
    export_dir = paths.exports_root()
    export_dir.mkdir(parents=True, exist_ok=True)
    exported_ply = export_dir / f"{export_stem}.ply"
    exported_gasp = export_dir / f"{export_stem}.gasp"

    if suffix == ".gasp":
        if mode == IMPORT_MODE_CONVERT:
            _copy_if_different(source, workspace_gasp)
            _copy_if_different(source, exported_gasp)
            export_ply_from_gaussian_gasp(source, exported_ply)
        metadata = read_gaussian_gasp_metadata(source)
        point_count = int(metadata.get("vertex_count") or metadata.get("point_count") or 0)
        bounds = metadata.get("bounds") or {"min": "unknown", "max": "unknown"}
        scene_gasp = str(exported_gasp if mode == IMPORT_MODE_CONVERT else source)
        scene_ply = str(exported_ply) if mode == IMPORT_MODE_CONVERT else None
        last_result_gasp = str(workspace_gasp if mode == IMPORT_MODE_CONVERT else source)
        last_result_ply = str(exported_ply) if mode == IMPORT_MODE_CONVERT else None
    else:
        _points, stats = read_preview_points(source, sample_limit=64)
        point_count = int(stats.get("point_count") or 0)
        bounds = stats.get("bounds") or {"min": "unknown", "max": "unknown"}
        if mode == IMPORT_MODE_CONVERT:
            write_gaussian_gasp_from_ply(
                source,
                workspace_gasp,
                project=project,
                extra_manifest={"bounds": bounds, "source_path": str(source)},
            )
            _copy_if_different(workspace_gasp, exported_gasp)
            _copy_if_different(source, exported_ply)
        scene_gasp = str(exported_gasp) if mode == IMPORT_MODE_CONVERT else None
        scene_ply = str(exported_ply if mode == IMPORT_MODE_CONVERT else source)
        last_result_gasp = str(workspace_gasp) if mode == IMPORT_MODE_CONVERT else None
        last_result_ply = str(exported_ply if mode == IMPORT_MODE_CONVERT else source)

    manifest = {
        "version": 3,
        "project_id": project["id"],
        "project_name": project["name"],
        "backend": project["backend"],
        "created_at": store.utc_now(),
        "point_count": point_count,
        "source_path": str(source),
        "import_mode": mode,
        "scene_ply": scene_ply,
        "scene_gasp": scene_gasp,
        "workspace_scene_gasp": str(workspace_gasp) if mode == IMPORT_MODE_CONVERT else None,
        "workspace_scene_ply": None,
        "bounds": bounds,
        "sketchup_import": {
            "type": "gaussian_gasp" if scene_gasp else "gaussian_ply",
            "path": scene_gasp or scene_ply,
            "source_ply": scene_ply,
        },
    }
    manifest_path = export_dir / f"{export_stem}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    package_path = export_dir / f"{export_stem}.gspkg"
    if mode == IMPORT_MODE_CONVERT:
        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(exported_ply, arcname=f"{export_stem}.ply")
            archive.write(exported_gasp, arcname=f"{export_stem}.gasp")
            archive.write(manifest_path, arcname=f"{export_stem}_manifest.json")

    paths.write_latest_export(
        {
            "project_id": project["id"],
            "project_name": project["name"],
            "manifest_path": str(manifest_path),
            "scene_ply": scene_ply,
            "scene_gasp": scene_gasp,
            "package_path": str(package_path) if mode == IMPORT_MODE_CONVERT else None,
            "created_at": manifest["created_at"],
        }
    )
    (paths.project_result_dir(project["id"]) / "scene_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    store.update_project(
        project["id"],
        status="ready",
        last_result_ply=last_result_ply,
        last_result_gasp=last_result_gasp,
        last_manifest_path=str(manifest_path),
    )
    updated = store.get_project(project["id"]) or project
    return {"project": updated, "manifest": manifest}
