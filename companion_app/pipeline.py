from __future__ import annotations

import gzip
import json
import math
import os
import re
import shutil
import time
import zipfile
from array import array
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps

try:
    import cv2  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional dependency in some UI runtimes
    cv2 = None

from . import paths, store
from .ply import write_gaussian_ply


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
ARCHIVE_SUFFIXES = {".zip"}
SAMPLE_DATASET_NAME = "nerf_synthetic_lego_12"


@dataclass
class ViewConfig:
    image_path: Path
    mask_path: Path
    angle_radians: float
    width: int
    height: int


def copy_input_images(project_id: str, image_paths: list[str]) -> list[Path]:
    paths.ensure_project_dirs(project_id)
    target_dir = paths.project_input_dir(project_id)
    copied: list[Path] = []
    source_to_destination: dict[str, str] = {}
    start_index = len(list_project_images(project_id)) if target_dir.exists() else 0
    for offset, source in enumerate(image_paths):
        source_path = Path(source)
        extension = source_path.suffix.lower() or ".png"
        destination = target_dir / f"{start_index + offset:03d}_{source_path.stem}{extension}"
        shutil.copy2(source_path, destination)
        copied.append(destination)
        source_to_destination[source_path.name] = destination.name
    _copy_matching_transforms(project_id, image_paths, source_to_destination)
    return copied


def ingest_media_sources(project_id: str, media_paths: list[str]) -> dict[str, object]:
    if not media_paths:
        return {
            "copied_files": [],
            "image_count": 0,
            "source_images": 0,
            "source_videos": 0,
            "video_frames": 0,
            "source_archives": 0,
            "source_directories": 0,
        }

    paths.ensure_project_dirs(project_id)
    import_root = stage_file(project_id, "imports")
    import_root.mkdir(parents=True, exist_ok=True)

    groups: list[list[Path]] = []
    seen_sources: set[str] = set()
    stats = {
        "source_images": 0,
        "source_videos": 0,
        "video_frames": 0,
        "source_archives": 0,
        "source_directories": 0,
    }

    for raw_source in media_paths:
        source_path = Path(raw_source)
        groups.extend(_expand_media_source(project_id, source_path, import_root, seen_sources, stats))

    copied: list[Path] = []
    for group in groups:
        if group:
            copied.extend(copy_input_images(project_id, [str(path) for path in group]))

    if not copied:
        raise RuntimeError("No supported media files were found. Use images, videos, folders, or .zip datasets.")

    return {
        "copied_files": [str(path) for path in copied],
        "image_count": len(copied),
        **stats,
    }


def _expand_media_source(
    project_id: str,
    source_path: Path,
    import_root: Path,
    seen_sources: set[str],
    stats: dict[str, int],
) -> list[list[Path]]:
    if not source_path.exists():
        return []

    if source_path.is_dir():
        stats["source_directories"] += 1
        return _collect_directory_media(project_id, source_path, import_root, seen_sources, stats)

    try:
        source_key = str(source_path.resolve())
    except OSError:
        source_key = str(source_path)
    if source_key in seen_sources:
        return []
    seen_sources.add(source_key)

    suffix = source_path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        stats["source_images"] += 1
        return [[source_path]]
    if suffix in VIDEO_SUFFIXES:
        stats["source_videos"] += 1
        frames = _extract_video_frames(project_id, source_path, import_root)
        stats["video_frames"] += len(frames)
        return [frames] if frames else []
    if suffix in ARCHIVE_SUFFIXES:
        stats["source_archives"] += 1
        extracted_dir = _extract_archive(source_path, import_root)
        return _collect_directory_media(project_id, extracted_dir, import_root, seen_sources, stats)
    return []


def _collect_directory_media(
    project_id: str,
    directory: Path,
    import_root: Path,
    seen_sources: set[str],
    stats: dict[str, int],
) -> list[list[Path]]:
    image_groups: dict[Path, list[Path]] = {}
    nested_groups: list[list[Path]] = []

    for file_path in sorted(path for path in directory.rglob("*") if path.is_file()):
        suffix = file_path.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            try:
                file_key = str(file_path.resolve())
            except OSError:
                file_key = str(file_path)
            if file_key in seen_sources:
                continue
            seen_sources.add(file_key)
            stats["source_images"] += 1
            image_groups.setdefault(file_path.parent, []).append(file_path)
        elif suffix in VIDEO_SUFFIXES or suffix in ARCHIVE_SUFFIXES:
            nested_groups.extend(_expand_media_source(project_id, file_path, import_root, seen_sources, stats))

    grouped_images = [image_groups[parent] for parent in sorted(image_groups)]
    return grouped_images + nested_groups


def _safe_media_name(path: Path) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("._")
    return safe or "media"


def _extract_archive(archive_path: Path, import_root: Path) -> Path:
    extract_dir = import_root / f"archive_{_safe_media_name(archive_path)}"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    root_resolved = extract_dir.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            member_target = (extract_dir / member.filename).resolve()
            if member_target != root_resolved and root_resolved not in member_target.parents:
                raise RuntimeError(f"Archive {archive_path.name} contains unsafe paths.")
            archive.extract(member, extract_dir)
    return extract_dir


def _extract_video_frames(project_id: str, video_path: Path, import_root: Path) -> list[Path]:
    if cv2 is None:
        raise RuntimeError("Video import requires OpenCV (cv2), but it is not available in this runtime.")

    target_dir = import_root / f"video_{_safe_media_name(video_path)}"
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video file {video_path.name}.")

    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    desired_frames = 16
    if frame_count > 0:
        desired_frames = min(24, max(8, frame_count // 24))
        frame_indices = sorted(
            {
                int(round(((frame_count - 1) * index) / max(1, desired_frames - 1)))
                for index in range(desired_frames)
            }
        )
    else:
        frame_indices = list(range(desired_frames))

    frames: list[Path] = []
    for output_index, frame_index in enumerate(frame_indices):
        capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        frame_path = target_dir / f"{output_index:03d}_{video_path.stem}.jpg"
        Image.fromarray(rgb_frame).save(frame_path, format="JPEG", quality=92)
        frames.append(frame_path)

    capture.release()
    if not frames:
        raise RuntimeError(f"No usable frames were extracted from {video_path.name}.")
    return frames


def _normalized_source_name(name: str) -> str:
    return re.sub(r"^\d+_", "", name)


def _project_input_lookup(project_id: str) -> tuple[dict[str, Path], dict[str, Path]]:
    input_dir = paths.project_input_dir(project_id)
    exact: dict[str, Path] = {}
    normalized: dict[str, Path] = {}
    for path in sorted(candidate for candidate in input_dir.iterdir() if candidate.suffix.lower() in IMAGE_SUFFIXES):
        exact[path.name] = path
        normalized[_normalized_source_name(path.name)] = path
    return exact, normalized


def _rewrite_manifest_to_project_inputs(project_id: str, payload: dict) -> dict:
    exact_lookup, normalized_lookup = _project_input_lookup(project_id)
    rewritten_frames = []
    for frame in payload.get("frames", []):
        frame_name = Path(frame.get("file_path", "")).name
        target = exact_lookup.get(frame_name) or normalized_lookup.get(frame_name)
        if target is None:
            target = normalized_lookup.get(_normalized_source_name(frame_name))
        if target is None:
            continue
        updated = dict(frame)
        updated["file_path"] = f"input/{target.name}"
        rewritten_frames.append(updated)
    updated_payload = dict(payload)
    updated_payload["frames"] = rewritten_frames
    return updated_payload


def _bundled_sample_manifest_path() -> Path:
    return paths.repo_root() / "sample_datasets" / SAMPLE_DATASET_NAME / "transforms_train_subset.json"


def ensure_project_camera_manifests(project_id: str) -> dict[str, object]:
    project_root = paths.project_root(project_id)
    manifests = sorted(project_root.glob("transforms*.json"))
    repaired = 0
    usable_views = 0
    chosen_manifest: Path | None = None

    for manifest_path in manifests:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        rewritten = _rewrite_manifest_to_project_inputs(project_id, payload)
        if rewritten.get("frames") != payload.get("frames"):
            manifest_path.write_text(json.dumps(rewritten, indent=2), encoding="utf-8")
            repaired += 1
        if not chosen_manifest and rewritten.get("frames"):
            chosen_manifest = manifest_path
            usable_views = len(rewritten["frames"])

    if chosen_manifest:
        return {
            "mode": "manifest",
            "manifest_path": str(chosen_manifest),
            "usable_views": usable_views,
            "repaired_manifests": repaired,
        }

    sample_manifest = _bundled_sample_manifest_path()
    if sample_manifest.exists():
        sample_payload = json.loads(sample_manifest.read_text(encoding="utf-8-sig"))
        rewritten = _rewrite_manifest_to_project_inputs(project_id, sample_payload)
        if rewritten.get("frames"):
            target_path = project_root / sample_manifest.name
            target_path.write_text(json.dumps(rewritten, indent=2), encoding="utf-8")
            return {
                "mode": "manifest",
                "manifest_path": str(target_path),
                "usable_views": len(rewritten["frames"]),
                "repaired_manifests": repaired + 1,
            }

    return {
        "mode": "sfm",
        "manifest_path": None,
        "usable_views": 0,
        "repaired_manifests": repaired,
    }


def _copy_matching_transforms(project_id: str, image_paths: list[str], source_to_destination: dict[str, str]) -> None:
    if not image_paths:
        return
    source_roots = {Path(path).resolve().parent for path in image_paths}
    if len(source_roots) != 1:
        return

    root = next(iter(source_roots))
    manifest_dirs = [root, root.parent]
    for manifest_dir in manifest_dirs:
        for manifest_path in sorted(manifest_dir.glob("transforms*.json")):
            payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            frames = payload.get("frames", [])
            filtered_frames = []
            for frame in frames:
                frame_name = Path(frame.get("file_path", "")).name
                destination_name = source_to_destination.get(frame_name)
                if not destination_name:
                    continue
                updated_frame = dict(frame)
                updated_frame["file_path"] = f"input/{destination_name}"
                filtered_frames.append(updated_frame)
            if not filtered_frames:
                continue
            payload["frames"] = filtered_frames
            target_manifest = paths.project_root(project_id) / manifest_path.name
            target_manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def list_project_images(project_id: str) -> list[Path]:
    input_dir = paths.project_input_dir(project_id)
    return sorted(path for path in input_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)


def stage_file(project_id: str, name: str) -> Path:
    return paths.project_stage_dir(project_id) / name


def result_ply_path(project_id: str) -> Path:
    return paths.project_result_dir(project_id) / "scene.ply"


def result_manifest_path(project_id: str) -> Path:
    return paths.project_result_dir(project_id) / "scene_manifest.json"


def log_line(job: dict, message: str) -> None:
    log_path = Path(job["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%H:%M:%S")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def update_progress(job_id: str, stage: str, progress: float, message: str) -> dict | None:
    return store.update_job(job_id, stage=stage, progress=progress, message=message)


def should_stop(job_id: str) -> bool:
    return store.job_stop_requested(job_id)


def load_stage_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_stage_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def clear_stage_outputs(project_id: str) -> None:
    for file_name in ("views.json", "occupancy.bin.gz"):
        candidate = stage_file(project_id, file_name)
        if candidate.exists():
            candidate.unlink()
    for candidate in (result_ply_path(project_id), result_manifest_path(project_id)):
        if candidate.exists():
            candidate.unlink()
    for directory_name in ("normalized", "masks"):
        directory = stage_file(project_id, directory_name)
        if directory.exists():
            shutil.rmtree(directory)


def prepare_views(project: dict, job: dict, settings: dict) -> list[ViewConfig] | None:
    cache_path = stage_file(project["id"], "views.json")
    if not settings.get("force_restart"):
        cached = load_stage_json(cache_path)
        if cached:
            return [
                ViewConfig(
                    image_path=Path(entry["image_path"]),
                    mask_path=Path(entry["mask_path"]),
                    angle_radians=entry["angle_radians"],
                    width=entry["width"],
                    height=entry["height"],
                )
                for entry in cached["views"]
            ]

    update_progress(job["id"], "Preparing Views", 0.05, "Normalizing capture images.")
    normalized_dir = stage_file(project["id"], "normalized")
    masks_dir = stage_file(project["id"], "masks")
    normalized_dir.mkdir(parents=True, exist_ok=True)
    masks_dir.mkdir(parents=True, exist_ok=True)

    images = list_project_images(project["id"])
    if len(images) < 4:
        raise RuntimeError("At least 4 photos are required for the built-in capture backend.")

    views: list[ViewConfig] = []
    total = len(images)
    max_edge = int(settings["max_image_edge"])
    fill_ratio = float(settings["subject_fill_ratio"])
    threshold = int(settings["mask_threshold"])

    for index, image_path in enumerate(images):
        if should_stop(job["id"]):
            return None

        image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
        if max(image.size) > max_edge:
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

        width, height = image.size
        step = max(1, min(width, height) // 40)
        border_pixels = []
        for x in range(0, width, step):
            border_pixels.append(image.getpixel((x, 0)))
            border_pixels.append(image.getpixel((x, height - 1)))
        for y in range(0, height, step):
            border_pixels.append(image.getpixel((0, y)))
            border_pixels.append(image.getpixel((width - 1, y)))
        avg_color = tuple(int(sum(channel) / max(1, len(border_pixels))) for channel in zip(*border_pixels))

        diff = ImageChops.difference(image, Image.new("RGB", image.size, avg_color)).convert("L")
        mask = diff.point(lambda value: 255 if value > threshold else 0).filter(ImageFilter.MaxFilter(5))
        mask = mask.filter(ImageFilter.MedianFilter(3))
        bbox = mask.getbbox()
        if bbox is None:
            raise RuntimeError(f"Could not isolate the subject in {image_path.name}.")

        object_width = max(1, bbox[2] - bbox[0])
        object_height = max(1, bbox[3] - bbox[1])
        target_fill = min(width, height) * fill_ratio
        scale_factor = target_fill / max(object_width, object_height)
        resized_size = (
            max(1, int(round(width * scale_factor))),
            max(1, int(round(height * scale_factor))),
        )
        image = image.resize(resized_size, Image.Resampling.LANCZOS)
        mask = mask.resize(resized_size, Image.Resampling.NEAREST)

        bbox = mask.getbbox()
        if bbox is None:
            raise RuntimeError(f"Mask became empty during normalization for {image_path.name}.")

        canvas = Image.new("RGB", (max_edge, max_edge), avg_color)
        mask_canvas = Image.new("L", (max_edge, max_edge), 0)
        offset_x = (canvas.width - (bbox[2] - bbox[0])) // 2 - bbox[0]
        offset_y = (canvas.height - (bbox[3] - bbox[1])) // 2 - bbox[1]
        canvas.paste(image, (offset_x, offset_y))
        mask_canvas.paste(mask, (offset_x, offset_y))
        mask_canvas = mask_canvas.filter(ImageFilter.MaxFilter(3))

        normalized_path = normalized_dir / f"{index:03d}.png"
        mask_path = masks_dir / f"{index:03d}.png"
        canvas.save(normalized_path)
        mask_canvas.save(mask_path)

        views.append(
            ViewConfig(
                image_path=normalized_path,
                mask_path=mask_path,
                angle_radians=(2.0 * math.pi * index) / total,
                width=canvas.width,
                height=canvas.height,
            )
        )
        progress = 0.05 + (0.15 * (index + 1) / total)
        update_progress(job["id"], "Preparing Views", progress, f"Prepared {index + 1} of {total} images.")

    save_stage_json(
        cache_path,
        {
            "views": [
                {
                    "image_path": str(view.image_path),
                    "mask_path": str(view.mask_path),
                    "angle_radians": view.angle_radians,
                    "width": view.width,
                    "height": view.height,
                }
                for view in views
            ]
        },
    )
    return views


def _camera_basis(angle_radians: float, distance: float) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    cam_pos = (math.sin(angle_radians) * distance, 0.0, math.cos(angle_radians) * distance)
    forward = (-cam_pos[0], -cam_pos[1], -cam_pos[2])
    forward_len = math.sqrt(sum(value * value for value in forward)) or 1.0
    forward = tuple(value / forward_len for value in forward)
    up = (0.0, 1.0, 0.0)
    right = (
        forward[2] * up[1] - forward[1] * up[2],
        forward[0] * up[2] - forward[2] * up[0],
        forward[1] * up[0] - forward[0] * up[1],
    )
    right_len = math.sqrt(sum(value * value for value in right)) or 1.0
    right = tuple(value / right_len for value in right)
    true_up = (
        right[1] * forward[2] - right[2] * forward[1],
        right[2] * forward[0] - right[0] * forward[2],
        right[0] * forward[1] - right[1] * forward[0],
    )
    return cam_pos, right, true_up, forward


def _project(point: tuple[float, float, float], view: ViewConfig, distance: float, fov_degrees: float) -> tuple[int, int] | None:
    cam_pos, right, up, forward = _camera_basis(view.angle_radians, distance)
    rel = (point[0] - cam_pos[0], point[1] - cam_pos[1], point[2] - cam_pos[2])
    x_cam = rel[0] * right[0] + rel[1] * right[1] + rel[2] * right[2]
    y_cam = rel[0] * up[0] + rel[1] * up[1] + rel[2] * up[2]
    z_cam = rel[0] * forward[0] + rel[1] * forward[1] + rel[2] * forward[2]
    if z_cam <= 1e-5:
        return None
    focal = (view.width * 0.5) / math.tan(math.radians(fov_degrees) * 0.5)
    px = int(round((x_cam / z_cam) * focal + (view.width * 0.5)))
    py = int(round(((-y_cam) / z_cam) * focal + (view.height * 0.5)))
    if px < 0 or py < 0 or px >= view.width or py >= view.height:
        return None
    return px, py


def _generate_voxel_positions(grid_size: int) -> list[tuple[float, float, float]]:
    coords = [(-1.0 + (2.0 * index / (grid_size - 1))) for index in range(grid_size)]
    return [(coords[x], coords[y], coords[z]) for z in range(grid_size) for y in range(grid_size) for x in range(grid_size)]


def save_occupancy(path: Path, indices: list[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = array("I", indices)
    with gzip.open(path, "wb") as handle:
        handle.write(payload.tobytes())


def load_occupancy(path: Path) -> list[int]:
    payload = array("I")
    with gzip.open(path, "rb") as handle:
        payload.frombytes(handle.read())
    return list(payload)


def carve_visual_hull(project: dict, job: dict, settings: dict, views: list[ViewConfig]) -> list[int] | None:
    cache_path = stage_file(project["id"], "occupancy.bin.gz")
    if not settings.get("force_restart") and cache_path.exists():
        return load_occupancy(cache_path)

    update_progress(job["id"], "Carving Volume", 0.22, "Building a visual hull from the silhouettes.")
    positions = _generate_voxel_positions(int(settings["grid_size"]))
    occupied = list(range(len(positions)))
    distance = float(settings["camera_distance"])
    fov = float(settings["camera_fov_degrees"])

    for view_index, view in enumerate(views):
        if should_stop(job["id"]):
            return None

        mask = Image.open(view.mask_path).convert("L")
        mask_pixels = mask.load()
        next_occupied: list[int] = []
        total = max(1, len(occupied))

        for index, voxel_index in enumerate(occupied):
            if (index % 2000) == 0:
                if should_stop(job["id"]):
                    return None
                stage_progress = 0.22 + (0.38 * ((view_index + (index / total)) / len(views)))
                update_progress(
                    job["id"],
                    "Carving Volume",
                    stage_progress,
                    f"Evaluating view {view_index + 1}/{len(views)} against {len(occupied)} candidate voxels.",
                )
            projected = _project(positions[voxel_index], view, distance, fov)
            if projected is None:
                continue
            px, py = projected
            if mask_pixels[px, py] > 0:
                next_occupied.append(voxel_index)

        occupied = next_occupied
        log_line(job, f"View {view_index + 1}/{len(views)} kept {len(occupied)} voxels.")
        if not occupied:
            raise RuntimeError("The silhouette intersection removed every voxel. Try cleaner background photos.")

    save_occupancy(cache_path, occupied)
    return occupied


def build_colored_splats(
    job: dict,
    settings: dict,
    views: list[ViewConfig],
    occupied: list[int],
) -> list[dict] | None:
    update_progress(job["id"], "Colorizing", 0.62, "Sampling color across the reconstructed hull.")
    positions = _generate_voxel_positions(int(settings["grid_size"]))
    distance = float(settings["camera_distance"])
    fov = float(settings["camera_fov_degrees"])
    images = [Image.open(view.image_path).convert("RGB") for view in views]
    masks = [Image.open(view.mask_path).convert("L") for view in views]
    pixel_images = [image.load() for image in images]
    pixel_masks = [mask.load() for mask in masks]

    voxel_world_size = 40.0 / max(2, int(settings["grid_size"]) - 1)
    scale_value = math.log(max(0.04, voxel_world_size * 0.55) / 20.0)
    splats: list[dict] = []

    total = max(1, len(occupied))
    for index, voxel_index in enumerate(occupied):
        if (index % 200) == 0:
            if should_stop(job["id"]):
                return None
            progress = 0.62 + (0.20 * (index / total))
            update_progress(job["id"], "Colorizing", progress, f"Sampling color for {index}/{len(occupied)} voxels.")

        point = positions[voxel_index]
        r_total = g_total = b_total = 0.0
        samples = 0
        for view, image_pixels, mask_pixels in zip(views, pixel_images, pixel_masks):
            projected = _project(point, view, distance, fov)
            if projected is None:
                continue
            px, py = projected
            if mask_pixels[px, py] <= 0:
                continue
            r, g, b = image_pixels[px, py]
            r_total += r / 255.0
            g_total += g / 255.0
            b_total += b / 255.0
            samples += 1

        if samples == 0:
            continue

        alpha = 0.55 + (0.35 * min(1.0, samples / max(2.0, len(views) * 0.45)))
        splats.append(
            {
                "position": point,
                "color": (r_total / samples, g_total / samples, b_total / samples),
                "alpha": alpha,
                "scale": scale_value,
            }
        )

    if not splats:
        raise RuntimeError("No splats survived the colorization stage.")
    return splats


def export_result(project: dict, job: dict, splats: list[dict]) -> dict:
    update_progress(job["id"], "Exporting", 0.85, "Writing the Gaussian PLY and SketchUp handoff package.")
    ply_path = write_gaussian_ply(splats, result_ply_path(project["id"]))

    xs = [point["position"][0] for point in splats]
    ys = [point["position"][1] for point in splats]
    zs = [point["position"][2] for point in splats]

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    export_dir = paths.exports_root() / f"{project['name'].replace(' ', '_')}_{timestamp}"
    export_dir.mkdir(parents=True, exist_ok=True)
    exported_ply = export_dir / "scene.ply"
    shutil.copy2(ply_path, exported_ply)

    manifest = {
        "version": 1,
        "project_id": project["id"],
        "project_name": project["name"],
        "backend": project["backend"],
        "created_at": store.utc_now(),
        "point_count": len(splats),
        "scene_ply": str(exported_ply),
        "workspace_scene_ply": str(ply_path),
        "bounds": {
            "min": [min(xs), min(ys), min(zs)],
            "max": [max(xs), max(ys), max(zs)],
        },
        "sketchup_import": {
            "type": "gaussian_ply",
            "path": str(exported_ply),
        },
    }
    manifest_path = export_dir / "scene_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    package_path = export_dir / "scene_package.gspkg"
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(exported_ply, arcname="scene.ply")
        archive.write(manifest_path, arcname="scene_manifest.json")

    paths.write_latest_export(
        {
            "project_id": project["id"],
            "project_name": project["name"],
            "manifest_path": str(manifest_path),
            "scene_ply": str(exported_ply),
            "package_path": str(package_path),
            "created_at": manifest["created_at"],
        }
    )
    result_manifest_path(project["id"]).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    store.update_project(
        project["id"],
        status="ready",
        last_result_ply=str(ply_path),
        last_manifest_path=str(manifest_path),
    )
    return manifest


def run_job(job_id: str) -> int:
    store.init_db()
    job = store.get_job(job_id)
    if not job:
        raise RuntimeError(f"Unknown job id: {job_id}")
    project = store.get_project(job["project_id"])
    if not project:
        raise RuntimeError(f"Unknown project for job {job_id}")

    settings = dict(job["settings"])
    Path(job["log_path"]).write_text("", encoding="utf-8")
    store.update_job(job_id, status="running", started_at=store.utc_now(), pid=os.getpid(), stop_requested=0)
    store.update_project(project["id"], status="running")
    log_line(job, f"Started job {job_id} for project '{project['name']}'.")

    try:
        if settings.get("force_restart"):
            clear_stage_outputs(project["id"])
            log_line(job, "Cleared cached stage outputs for a full rebuild.")

        if settings.get("trainer_backend") == "gsplat_colmap" or project.get("backend") == "gsplat_colmap":
            manifest_status = ensure_project_camera_manifests(project["id"])
            if manifest_status["mode"] == "manifest":
                repair_note = ""
                if int(manifest_status["repaired_manifests"]) > 0:
                    repair_note = " after repairing project paths"
                log_line(
                    job,
                    f"Using camera manifest with {int(manifest_status['usable_views'])} views{repair_note}.",
                )
            else:
                log_line(job, "No usable camera manifest found. Falling back to SfM-only cameras.")
            from .gsplat_pipeline import run_gsplat_job

            manifest = run_gsplat_job(project, job, settings)
        else:
            views = prepare_views(project, job, settings)
            if views is None:
                store.update_job(job_id, status="stopped", finished_at=store.utc_now(), progress=0.0, stage="Stopped")
                return 0

            occupied = carve_visual_hull(project, job, settings, views)
            if occupied is None:
                store.update_job(job_id, status="stopped", finished_at=store.utc_now(), stage="Stopped")
                return 0

            splats = build_colored_splats(job, settings, views, occupied)
            if splats is None:
                store.update_job(job_id, status="stopped", finished_at=store.utc_now(), stage="Stopped")
                return 0

            manifest = export_result(project, job, splats)

        log_line(job, f"Exported {manifest['point_count']} splats to {manifest['scene_ply']}.")
        store.update_job(
            job_id,
            status="completed",
            stage="Completed",
            progress=1.0,
            message=f"Finished with {manifest['point_count']} splats.",
            finished_at=store.utc_now(),
            error_text=None,
        )
        return 0
    except Exception as error:
        log_line(job, f"ERROR: {error}")
        store.update_job(
            job_id,
            status="failed",
            stage="Failed",
            message=str(error),
            error_text=str(error),
            finished_at=store.utc_now(),
        )
        store.update_project(project["id"], status="failed")
        return 1
