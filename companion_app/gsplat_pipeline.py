from __future__ import annotations

import json
import math
import os
import random
import shutil
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pycolmap
import torch
import torch.nn.functional as F
import numpy as np
from PIL import Image, ImageOps
from gsplat.exporter import export_splats
from gsplat.rendering import rasterization
from gsplat.strategy import DefaultStrategy

from . import paths, store


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
SH_C0 = 0.28209479177387814


@dataclass
class TrainingView:
    image_name: str
    rgb_tensor: torch.Tensor
    alpha_tensor: torch.Tensor
    has_alpha: bool
    camtoworld: torch.Tensor
    K: torch.Tensor
    width: int
    height: int


def _log_line(job: dict, message: str) -> None:
    log_path = Path(job["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%H:%M:%S")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def _update(job_id: str, stage: str, progress: float, message: str) -> None:
    store.update_job(job_id, stage=stage, progress=progress, message=message)


def _should_stop(job_id: str) -> bool:
    return store.job_stop_requested(job_id)


def _stage_dir(project_id: str, name: str) -> Path:
    directory = paths.project_stage_dir(project_id) / name
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _result_ply_path(project_id: str) -> Path:
    return paths.project_result_dir(project_id) / "scene.ply"


def _result_manifest_path(project_id: str) -> Path:
    return paths.project_result_dir(project_id) / "scene_manifest.json"


def _list_project_images(project_id: str) -> list[Path]:
    input_dir = paths.project_input_dir(project_id)
    return sorted(path for path in input_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)


def _find_transforms_manifest(project_id: str) -> Path | None:
    project_root = paths.project_root(project_id)
    manifests = sorted(project_root.glob("transforms*.json"))
    return manifests[0] if manifests else None


def _load_image_tensor(image_path: Path, target_resolution: int) -> tuple[torch.Tensor, torch.Tensor, int, int, float, bool]:
    pil_image = ImageOps.exif_transpose(Image.open(image_path))
    has_alpha = "A" in pil_image.getbands() or ("transparency" in pil_image.info)
    pil_image = pil_image.convert("RGBA")

    original_width, original_height = pil_image.size
    scale = min(1.0, float(target_resolution) / max(original_width, original_height))
    width = max(1, int(round(original_width * scale)))
    height = max(1, int(round(original_height * scale)))
    if scale != 1.0:
        pil_image = pil_image.resize((width, height), Image.Resampling.LANCZOS)

    pixels = np.asarray(pil_image, dtype=np.float32) / 255.0
    rgb = torch.from_numpy(pixels[..., :3].copy())
    alpha = torch.from_numpy(pixels[..., 3:4].copy())
    return rgb, alpha, width, height, scale, has_alpha


def _prepare_colmap_images(project: dict) -> Path:
    source_dir = Path(project["input_dir"])
    prepared_dir = _stage_dir(project["id"], "colmap_images")
    prepared_dir.mkdir(parents=True, exist_ok=True)

    for image_path in sorted(path for path in source_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES):
        target_path = prepared_dir / image_path.name
        if target_path.exists() and target_path.stat().st_mtime >= image_path.stat().st_mtime:
            continue

        pil_image = ImageOps.exif_transpose(Image.open(image_path))
        if "A" in pil_image.getbands() or ("transparency" in pil_image.info):
            pil_image = Image.alpha_composite(
                Image.new("RGBA", pil_image.size, (255, 255, 255, 255)),
                pil_image.convert("RGBA"),
            ).convert("RGB")
        else:
            pil_image = pil_image.convert("RGB")

        save_format = "PNG" if image_path.suffix.lower() in {".png", ".bmp"} else None
        pil_image.save(target_path, format=save_format)

    return prepared_dir


def _rgb_to_sh(rgb: torch.Tensor) -> torch.Tensor:
    return (rgb - 0.5) / SH_C0


def _knn_mean_distance(points: torch.Tensor, neighbors: int = 4) -> torch.Tensor:
    if len(points) < 2:
        return torch.full((len(points),), 0.05, device=points.device)
    chunk = max(128, min(1024, len(points)))
    rows = []
    for start in range(0, len(points), chunk):
        distances = torch.cdist(points[start:start + chunk], points)
        k = min(neighbors, distances.shape[1])
        topk = torch.topk(distances, k=k, dim=1, largest=False).values
        mean_distance = topk[:, 1:].mean(dim=1) if topk.shape[1] > 1 else topk[:, 0]
        rows.append(mean_distance)
    return torch.cat(rows, dim=0).clamp_min(1e-3)


def _ssim(pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    pred = pred.permute(0, 3, 1, 2)
    target = target.permute(0, 3, 1, 2)
    c1 = 0.01 ** 2
    c2 = 0.03 ** 2
    mu_x = F.avg_pool2d(pred, kernel_size=11, stride=1, padding=5)
    mu_y = F.avg_pool2d(target, kernel_size=11, stride=1, padding=5)
    sigma_x = F.avg_pool2d(pred * pred, kernel_size=11, stride=1, padding=5) - (mu_x * mu_x)
    sigma_y = F.avg_pool2d(target * target, kernel_size=11, stride=1, padding=5) - (mu_y * mu_y)
    sigma_xy = F.avg_pool2d(pred * target, kernel_size=11, stride=1, padding=5) - (mu_x * mu_y)
    ssim_map = ((2.0 * mu_x * mu_y + c1) * (2.0 * sigma_xy + c2)) / ((mu_x * mu_x + mu_y * mu_y + c1) * (sigma_x + sigma_y + c2))
    return ssim_map.mean()


def _convert_blender_camtoworld(transform_matrix: list[list[float]]) -> torch.Tensor:
    camtoworld = torch.tensor(transform_matrix, dtype=torch.float32)
    converted = camtoworld.clone()
    converted[:3, 1:3] *= -1.0
    return converted


def _fit_similarity_transform(source_points: torch.Tensor, target_points: torch.Tensor) -> tuple[float, torch.Tensor, torch.Tensor]:
    source_mean = source_points.mean(dim=0)
    target_mean = target_points.mean(dim=0)
    source_centered = source_points - source_mean
    target_centered = target_points - target_mean

    covariance = (target_centered.T @ source_centered) / float(len(source_points))
    u, singular_values, vt = torch.linalg.svd(covariance)
    reflection_fix = torch.eye(3, dtype=torch.float32)
    if torch.linalg.det(u @ vt) < 0:
        reflection_fix[-1, -1] = -1.0

    rotation = u @ reflection_fix @ vt
    source_variance = torch.mean(torch.sum(source_centered * source_centered, dim=1)).clamp_min(1e-8)
    scale = float((singular_values * torch.diag(reflection_fix)).sum().item() / source_variance.item())
    translation = target_mean - (scale * (rotation @ source_mean))
    return scale, rotation, translation


def _apply_similarity_to_camtoworld(camtoworld: torch.Tensor, scale: float, rotation: torch.Tensor, translation: torch.Tensor) -> torch.Tensor:
    aligned = torch.eye(4, dtype=torch.float32)
    aligned[:3, :3] = rotation @ camtoworld[:3, :3]
    aligned[:3, 3] = (scale * (rotation @ camtoworld[:3, 3])) + translation
    return aligned


def _align_views_to_reconstruction(views: list[TrainingView], reconstruction: pycolmap.Reconstruction) -> list[TrainingView]:
    colmap_poses: dict[str, torch.Tensor] = {}
    for image in reconstruction.images.values():
        if image.has_pose:
            colmap_poses[image.name] = _build_camtoworld(image.cam_from_world())

    matched_views = [view for view in views if view.image_name in colmap_poses]
    if len(matched_views) < 3:
        return views

    source_points = torch.stack([view.camtoworld[:3, 3] for view in matched_views], dim=0)
    target_points = torch.stack([colmap_poses[view.image_name][:3, 3] for view in matched_views], dim=0)
    scale, rotation, translation = _fit_similarity_transform(source_points, target_points)

    aligned_views: list[TrainingView] = []
    for view in views:
        aligned_pose = colmap_poses.get(view.image_name)
        if aligned_pose is None:
            aligned_pose = _apply_similarity_to_camtoworld(view.camtoworld, scale, rotation, translation)
        aligned_views.append(
            TrainingView(
                image_name=view.image_name,
                rgb_tensor=view.rgb_tensor,
                alpha_tensor=view.alpha_tensor,
                has_alpha=view.has_alpha,
                camtoworld=aligned_pose,
                K=view.K,
                width=view.width,
                height=view.height,
            )
        )
    return aligned_views


def _filter_sparse_points_with_masks(
    points_tensor: torch.Tensor,
    rgb_tensor: torch.Tensor,
    views: list[TrainingView],
    min_hits: int,
    alpha_threshold: float,
) -> tuple[torch.Tensor, torch.Tensor]:
    alpha_views = [view for view in views if view.has_alpha]
    if len(alpha_views) < 2 or len(points_tensor) < 32:
        return points_tensor, rgb_tensor

    ones = torch.ones((len(points_tensor), 1), dtype=torch.float32)
    points_h = torch.cat([points_tensor, ones], dim=1)
    foreground_hits = torch.zeros((len(points_tensor),), dtype=torch.int32)

    for view in alpha_views:
        world_to_cam = torch.linalg.inv(view.camtoworld)
        camera_points = points_h @ world_to_cam.T
        positive_depth = camera_points[:, 2] > 1e-4
        if not torch.any(positive_depth):
            continue

        x = camera_points[:, 0] / camera_points[:, 2].clamp_min(1e-4)
        y = camera_points[:, 1] / camera_points[:, 2].clamp_min(1e-4)
        fx = float(view.K[0, 0].item())
        fy = float(view.K[1, 1].item())
        cx = float(view.K[0, 2].item())
        cy = float(view.K[1, 2].item())
        u = torch.round((x * fx) + cx).to(torch.int64)
        v = torch.round((y * fy) + cy).to(torch.int64)

        inside = positive_depth & (u >= 0) & (u < view.width) & (v >= 0) & (v < view.height)
        if not torch.any(inside):
            continue

        alpha_map = view.alpha_tensor[..., 0]
        visible_indices = torch.nonzero(inside, as_tuple=False).squeeze(-1)
        alpha_values = alpha_map[v[visible_indices], u[visible_indices]]
        keep_visible = alpha_values >= alpha_threshold
        if torch.any(keep_visible):
            foreground_hits[visible_indices[keep_visible]] += 1

    keep_mask = foreground_hits >= max(1, min_hits)
    if int(keep_mask.sum().item()) < 32 and min_hits > 1:
        keep_mask = foreground_hits >= 1
    if int(keep_mask.sum().item()) < 16:
        return points_tensor, rgb_tensor
    return points_tensor[keep_mask], rgb_tensor[keep_mask]


def _build_visual_hull_seed_points(
    points_tensor: torch.Tensor,
    views: list[TrainingView],
    grid_size: int,
    alpha_threshold: float,
    support_ratio: float,
    max_points: int,
) -> tuple[torch.Tensor, torch.Tensor] | None:
    alpha_views = [view for view in views if view.has_alpha]
    if len(alpha_views) < 4 or max_points <= 0:
        return None

    bounds_min = points_tensor.min(dim=0).values
    bounds_max = points_tensor.max(dim=0).values
    extent = (bounds_max - bounds_min).clamp_min(0.05)
    bounds_min = bounds_min - (0.25 * extent)
    bounds_max = bounds_max + (0.25 * extent)

    axes = [
        torch.linspace(float(bounds_min[axis].item()), float(bounds_max[axis].item()), steps=grid_size)
        for axis in range(3)
    ]
    candidate_points = torch.cartesian_prod(*axes).to(torch.float32)
    candidate_points_h = torch.cat([candidate_points, torch.ones((len(candidate_points), 1), dtype=torch.float32)], dim=1)
    visible_hits = torch.zeros((len(candidate_points),), dtype=torch.int32)
    foreground_hits = torch.zeros((len(candidate_points),), dtype=torch.int32)

    for view in alpha_views:
        world_to_cam = torch.linalg.inv(view.camtoworld)
        camera_points = candidate_points_h @ world_to_cam.T
        positive_depth = camera_points[:, 2] > 1e-4
        if not torch.any(positive_depth):
            continue

        x = camera_points[:, 0] / camera_points[:, 2].clamp_min(1e-4)
        y = camera_points[:, 1] / camera_points[:, 2].clamp_min(1e-4)
        fx = float(view.K[0, 0].item())
        fy = float(view.K[1, 1].item())
        cx = float(view.K[0, 2].item())
        cy = float(view.K[1, 2].item())
        u = torch.round((x * fx) + cx).to(torch.int64)
        v = torch.round((y * fy) + cy).to(torch.int64)
        inside = positive_depth & (u >= 0) & (u < view.width) & (v >= 0) & (v < view.height)
        visible_hits += inside.to(torch.int32)
        if not torch.any(inside):
            continue

        alpha_map = view.alpha_tensor[..., 0]
        visible_indices = torch.nonzero(inside, as_tuple=False).squeeze(-1)
        alpha_values = alpha_map[v[visible_indices], u[visible_indices]]
        foreground_visible = alpha_values >= alpha_threshold
        if torch.any(foreground_visible):
            foreground_hits[visible_indices[foreground_visible]] += 1

    min_visible = max(3, math.ceil(len(alpha_views) * 0.5))
    required_hits = torch.ceil(visible_hits.to(torch.float32) * support_ratio).to(torch.int32)
    required_hits = torch.maximum(required_hits, torch.full_like(required_hits, min_visible))
    keep_mask = (visible_hits >= min_visible) & (foreground_hits >= required_hits)
    if not torch.any(keep_mask):
        return None

    occupied = candidate_points[keep_mask]
    if len(occupied) > max_points:
        keep_indices = torch.linspace(0, len(occupied) - 1, steps=max_points).round().to(torch.int64)
        occupied = occupied[keep_indices]

    occupied_h = torch.cat([occupied, torch.ones((len(occupied), 1), dtype=torch.float32)], dim=1)
    color_sum = torch.zeros((len(occupied), 3), dtype=torch.float32)
    color_hits = torch.zeros((len(occupied), 1), dtype=torch.float32)

    for view in alpha_views:
        world_to_cam = torch.linalg.inv(view.camtoworld)
        camera_points = occupied_h @ world_to_cam.T
        positive_depth = camera_points[:, 2] > 1e-4
        if not torch.any(positive_depth):
            continue

        x = camera_points[:, 0] / camera_points[:, 2].clamp_min(1e-4)
        y = camera_points[:, 1] / camera_points[:, 2].clamp_min(1e-4)
        fx = float(view.K[0, 0].item())
        fy = float(view.K[1, 1].item())
        cx = float(view.K[0, 2].item())
        cy = float(view.K[1, 2].item())
        u = torch.round((x * fx) + cx).to(torch.int64)
        v = torch.round((y * fy) + cy).to(torch.int64)
        inside = positive_depth & (u >= 0) & (u < view.width) & (v >= 0) & (v < view.height)
        if not torch.any(inside):
            continue

        inside_indices = torch.nonzero(inside, as_tuple=False).squeeze(-1)
        alpha_values = view.alpha_tensor[v[inside_indices], u[inside_indices], 0]
        foreground_visible = alpha_values >= alpha_threshold
        if not torch.any(foreground_visible):
            continue

        valid_indices = inside_indices[foreground_visible]
        sampled_colors = view.rgb_tensor[v[valid_indices], u[valid_indices]]
        color_sum[valid_indices] += sampled_colors
        color_hits[valid_indices] += 1.0

    valid_color = color_hits[:, 0] > 0
    if not torch.any(valid_color):
        return None
    return occupied[valid_color], color_sum[valid_color] / color_hits[valid_color]


def _estimate_focus_center_and_radius(views: list[TrainingView]) -> tuple[torch.Tensor, float]:
    eye = torch.eye(3, dtype=torch.float32)
    system_matrix = torch.zeros((3, 3), dtype=torch.float32)
    system_rhs = torch.zeros((3,), dtype=torch.float32)
    camera_centers = []

    for view in views:
        camera_center = view.camtoworld[:3, 3]
        forward = view.camtoworld[:3, 2]
        forward = forward / forward.norm().clamp_min(1e-6)
        projection = eye - torch.outer(forward, forward)
        system_matrix += projection
        system_rhs += projection @ camera_center
        camera_centers.append(camera_center)

    if torch.linalg.det(system_matrix).abs() < 1e-6:
        center = torch.stack(camera_centers, dim=0).mean(dim=0)
    else:
        center = torch.linalg.solve(system_matrix, system_rhs)

    distances = torch.stack([torch.linalg.norm(center - camera_center) for camera_center in camera_centers], dim=0)
    camera_radius = float(distances.median().item()) if len(distances) else 2.0
    object_radius = max(0.2, camera_radius * 0.35)
    return center, object_radius


def _build_visual_hull_seed_points_from_bounds(
    bounds_min: torch.Tensor,
    bounds_max: torch.Tensor,
    views: list[TrainingView],
    grid_size: int,
    alpha_threshold: float,
    support_ratio: float,
    max_points: int,
) -> tuple[torch.Tensor, torch.Tensor] | None:
    alpha_views = [view for view in views if view.has_alpha]
    if len(alpha_views) < 4 or max_points <= 0:
        return None

    axes = [
        torch.linspace(float(bounds_min[axis].item()), float(bounds_max[axis].item()), steps=grid_size)
        for axis in range(3)
    ]
    candidate_points = torch.cartesian_prod(*axes).to(torch.float32)
    candidate_points_h = torch.cat([candidate_points, torch.ones((len(candidate_points), 1), dtype=torch.float32)], dim=1)
    visible_hits = torch.zeros((len(candidate_points),), dtype=torch.int32)
    foreground_hits = torch.zeros((len(candidate_points),), dtype=torch.int32)

    for view in alpha_views:
        world_to_cam = torch.linalg.inv(view.camtoworld)
        camera_points = candidate_points_h @ world_to_cam.T
        positive_depth = camera_points[:, 2] > 1e-4
        if not torch.any(positive_depth):
            continue

        x = camera_points[:, 0] / camera_points[:, 2].clamp_min(1e-4)
        y = camera_points[:, 1] / camera_points[:, 2].clamp_min(1e-4)
        fx = float(view.K[0, 0].item())
        fy = float(view.K[1, 1].item())
        cx = float(view.K[0, 2].item())
        cy = float(view.K[1, 2].item())
        u = torch.round((x * fx) + cx).to(torch.int64)
        v = torch.round((y * fy) + cy).to(torch.int64)
        inside = positive_depth & (u >= 0) & (u < view.width) & (v >= 0) & (v < view.height)
        visible_hits += inside.to(torch.int32)
        if not torch.any(inside):
            continue

        alpha_map = view.alpha_tensor[..., 0]
        visible_indices = torch.nonzero(inside, as_tuple=False).squeeze(-1)
        alpha_values = alpha_map[v[visible_indices], u[visible_indices]]
        foreground_visible = alpha_values >= alpha_threshold
        if torch.any(foreground_visible):
            foreground_hits[visible_indices[foreground_visible]] += 1

    min_visible = max(3, math.ceil(len(alpha_views) * 0.5))
    required_hits = torch.ceil(visible_hits.to(torch.float32) * support_ratio).to(torch.int32)
    required_hits = torch.maximum(required_hits, torch.full_like(required_hits, min_visible))
    keep_mask = (visible_hits >= min_visible) & (foreground_hits >= required_hits)
    if not torch.any(keep_mask):
        return None

    occupied = candidate_points[keep_mask]
    if len(occupied) > max_points:
        keep_indices = torch.linspace(0, len(occupied) - 1, steps=max_points).round().to(torch.int64)
        occupied = occupied[keep_indices]

    occupied_h = torch.cat([occupied, torch.ones((len(occupied), 1), dtype=torch.float32)], dim=1)
    color_sum = torch.zeros((len(occupied), 3), dtype=torch.float32)
    color_hits = torch.zeros((len(occupied), 1), dtype=torch.float32)

    for view in alpha_views:
        world_to_cam = torch.linalg.inv(view.camtoworld)
        camera_points = occupied_h @ world_to_cam.T
        positive_depth = camera_points[:, 2] > 1e-4
        if not torch.any(positive_depth):
            continue

        x = camera_points[:, 0] / camera_points[:, 2].clamp_min(1e-4)
        y = camera_points[:, 1] / camera_points[:, 2].clamp_min(1e-4)
        fx = float(view.K[0, 0].item())
        fy = float(view.K[1, 1].item())
        cx = float(view.K[0, 2].item())
        cy = float(view.K[1, 2].item())
        u = torch.round((x * fx) + cx).to(torch.int64)
        v = torch.round((y * fy) + cy).to(torch.int64)
        inside = positive_depth & (u >= 0) & (u < view.width) & (v >= 0) & (v < view.height)
        if not torch.any(inside):
            continue

        inside_indices = torch.nonzero(inside, as_tuple=False).squeeze(-1)
        alpha_values = view.alpha_tensor[v[inside_indices], u[inside_indices], 0]
        foreground_visible = alpha_values >= alpha_threshold
        if not torch.any(foreground_visible):
            continue

        valid_indices = inside_indices[foreground_visible]
        sampled_colors = view.rgb_tensor[v[valid_indices], u[valid_indices]]
        color_sum[valid_indices] += sampled_colors
        color_hits[valid_indices] += 1.0

    valid_color = color_hits[:, 0] > 0
    if not torch.any(valid_color):
        return None
    return occupied[valid_color], color_sum[valid_color] / color_hits[valid_color]


def _build_manifest_visual_hull_seed_points(
    views: list[TrainingView],
    grid_size: int,
    alpha_threshold: float,
    support_ratio: float,
    max_points: int,
) -> tuple[torch.Tensor, torch.Tensor] | None:
    center, radius = _estimate_focus_center_and_radius(views)
    extent = torch.full((3,), radius, dtype=torch.float32)
    bounds_min = center - extent
    bounds_max = center + extent
    return _build_visual_hull_seed_points_from_bounds(
        bounds_min=bounds_min,
        bounds_max=bounds_max,
        views=views,
        grid_size=grid_size,
        alpha_threshold=alpha_threshold,
        support_ratio=support_ratio,
        max_points=max_points,
    )


def _build_camtoworld(pose: pycolmap.Rigid3d) -> torch.Tensor:
    world_to_cam = torch.eye(4, dtype=torch.float32)
    matrix = torch.tensor(pose.matrix(), dtype=torch.float32)
    world_to_cam[:3, :4] = matrix
    return torch.linalg.inv(world_to_cam)


def _pick_reconstruction(reconstructions: dict[int, pycolmap.Reconstruction]) -> pycolmap.Reconstruction:
    if not reconstructions:
        raise RuntimeError("COLMAP could not recover any reconstruction from the input photos.")
    return max(
        reconstructions.values(),
        key=lambda recon: (len(recon.images), len(recon.points3D)),
    )


def _run_colmap(project: dict, job: dict, settings: dict) -> pycolmap.Reconstruction:
    image_dir = _prepare_colmap_images(project)
    colmap_dir = _stage_dir(project["id"], "colmap")
    database_path = colmap_dir / "database.db"
    sparse_dir = colmap_dir / "sparse"

    if settings.get("force_restart"):
        if database_path.exists():
            database_path.unlink()
        if sparse_dir.exists():
            shutil.rmtree(sparse_dir)

    if sparse_dir.exists():
        existing = sorted(path for path in sparse_dir.iterdir() if path.is_dir())
        if existing:
            return pycolmap.Reconstruction(existing[0])

    _update(job["id"], "COLMAP", 0.08, "Extracting image features.")
    extraction_options = pycolmap.FeatureExtractionOptions()
    extraction_options.max_image_size = int(settings.get("sfm_max_image_size", 1600))
    extraction_options.num_threads = int(settings.get("sfm_num_threads", 6))
    extraction_options.use_gpu = False

    matching_options = pycolmap.FeatureMatchingOptions()
    matching_options.num_threads = int(settings.get("sfm_num_threads", 6))
    matching_options.use_gpu = False
    matching_options.guided_matching = True

    mapping_options = pycolmap.IncrementalPipelineOptions()
    mapping_options.min_model_size = 3
    mapping_options.extract_colors = True
    mapping_options.num_threads = int(settings.get("sfm_num_threads", 6))

    pycolmap.extract_features(database_path, image_dir, extraction_options=extraction_options)
    if _should_stop(job["id"]):
        raise RuntimeError("Stopped during feature extraction.")
    _log_line(job, "COLMAP feature extraction finished.")

    _update(job["id"], "COLMAP", 0.14, "Matching image features.")
    pycolmap.match_exhaustive(database_path, matching_options=matching_options)
    if _should_stop(job["id"]):
        raise RuntimeError("Stopped during feature matching.")
    _log_line(job, "COLMAP exhaustive matching finished.")

    _update(job["id"], "COLMAP", 0.22, "Recovering camera poses and sparse points.")
    reconstructions = pycolmap.incremental_mapping(database_path, image_dir, sparse_dir, options=mapping_options)
    reconstruction = _pick_reconstruction(reconstructions)
    _log_line(
        job,
        f"COLMAP reconstruction recovered {len(reconstruction.images)} registered images and {len(reconstruction.points3D)} sparse points.",
    )
    return reconstruction


def _load_training_views(project: dict, reconstruction: pycolmap.Reconstruction, settings: dict) -> list[TrainingView]:
    manifest_path = _find_transforms_manifest(project["id"])
    if manifest_path:
        views = _load_training_views_from_transforms(project, manifest_path, reconstruction, settings)
        if views:
            return views

    image_dir = Path(project["input_dir"])
    target_resolution = int(settings.get("train_resolution", 640))
    views: list[TrainingView] = []

    for image in reconstruction.images.values():
        image_path = image_dir / image.name
        if not image_path.exists() or not image.has_pose:
            continue
        camera = reconstruction.cameras[image.camera_id]
        rgb_pixels, alpha_pixels, width, height, scale, has_alpha = _load_image_tensor(image_path, target_resolution)

        K = torch.tensor(camera.calibration_matrix(), dtype=torch.float32)
        if scale != 1.0:
            K[0, :] *= scale
            K[1, :] *= scale
            K[2, 2] = 1.0

        views.append(
            TrainingView(
                image_name=image.name,
                rgb_tensor=rgb_pixels,
                alpha_tensor=alpha_pixels,
                has_alpha=has_alpha,
                camtoworld=_build_camtoworld(image.cam_from_world()),
                K=K,
                width=width,
                height=height,
            )
        )
    if len(views) < 2:
        raise RuntimeError("COLMAP registered too few cameras to start Gaussian Splat training.")
    return views


def _load_training_views_from_transforms(
    project: dict,
    manifest_path: Path,
    reconstruction: pycolmap.Reconstruction | None,
    settings: dict,
) -> list[TrainingView]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    frames = payload.get("frames", [])
    if not frames:
        return []

    target_resolution = int(settings.get("train_resolution", 640))
    project_root = paths.project_root(project["id"])
    views: list[TrainingView] = []

    for frame in frames:
        image_path = project_root / frame["file_path"]
        if not image_path.exists():
            continue
        rgb_pixels, alpha_pixels, width, height, _scale, has_alpha = _load_image_tensor(image_path, target_resolution)
        camera_angle_x = float(payload["camera_angle_x"])
        focal = 0.5 * width / math.tan(0.5 * camera_angle_x)
        K = torch.tensor(
            [
                [focal, 0.0, width * 0.5],
                [0.0, focal, height * 0.5],
                [0.0, 0.0, 1.0],
            ],
            dtype=torch.float32,
        )

        views.append(
            TrainingView(
                image_name=image_path.name,
                rgb_tensor=rgb_pixels,
                alpha_tensor=alpha_pixels,
                has_alpha=has_alpha,
                camtoworld=_convert_blender_camtoworld(frame["transform_matrix"]),
                K=K,
                width=width,
                height=height,
            )
        )
    if reconstruction is None:
        return views
    return _align_views_to_reconstruction(views, reconstruction)


def _train_gaussians(project: dict, job: dict, settings: dict, reconstruction: pycolmap.Reconstruction | None, views: list[TrainingView]) -> tuple[Path, int, dict]:
    known_camera_mode = bool(settings.get("_known_camera_mode", False))
    if known_camera_mode:
        _update(job["id"], "Training", 0.28, "Initializing Gaussians from known-camera alpha visual hull.")
    else:
        _update(job["id"], "Training", 0.28, "Initializing Gaussians from sparse reconstruction.")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        raise RuntimeError("CUDA is required for the gsplat training backend.")

    desired_seed_points = int(settings.get("visual_hull_seed_points", 1400))
    alpha_threshold = float(settings.get("alpha_mask_threshold", 0.2))
    support_ratio = float(settings.get("visual_hull_support_ratio", 0.8))
    grid_size = int(settings.get("visual_hull_init_grid", 40))

    if known_camera_mode:
        manifest_seed = _build_manifest_visual_hull_seed_points(
            views,
            grid_size=grid_size,
            alpha_threshold=alpha_threshold,
            support_ratio=support_ratio,
            max_points=max(32, desired_seed_points),
        )
        if manifest_seed is None:
            raise RuntimeError("Known-camera initialization failed to build a foreground visual hull from alpha masks.")
        points_tensor, rgb_tensor = manifest_seed
        _log_line(job, f"Known-camera visual-hull initialization produced {len(points_tensor)} foreground seed points.")
    else:
        if reconstruction is None:
            raise RuntimeError("Sparse reconstruction is required when known-camera initialization is unavailable.")
        points = []
        colors = []
        for point in reconstruction.points3D.values():
            points.append(point.xyz)
            colors.append([channel / 255.0 for channel in point.color])
        points_tensor = torch.tensor(points, dtype=torch.float32)
        rgb_tensor = torch.tensor(colors, dtype=torch.float32)
        filtered_points, filtered_colors = _filter_sparse_points_with_masks(
            points_tensor,
            rgb_tensor,
            views,
            min_hits=int(settings.get("mask_min_views", 2)),
            alpha_threshold=alpha_threshold,
        )
        if len(filtered_points) >= 16:
            if len(filtered_points) != len(points_tensor):
                _log_line(job, f"Foreground alpha masks kept {len(filtered_points)} / {len(points_tensor)} sparse init points.")
            points_tensor = filtered_points
            rgb_tensor = filtered_colors

        if len(points_tensor) < desired_seed_points:
            hull_seed = _build_visual_hull_seed_points(
                points_tensor,
                views,
                grid_size=grid_size,
                alpha_threshold=alpha_threshold,
                support_ratio=support_ratio,
                max_points=max(0, desired_seed_points - len(points_tensor)),
            )
            if hull_seed is not None:
                hull_points, hull_colors = hull_seed
                if len(hull_points) > 0:
                    _log_line(job, f"Visual-hull seeding added {len(hull_points)} foreground init points from alpha masks.")
                    points_tensor = torch.cat([points_tensor, hull_points], dim=0)
                    rgb_tensor = torch.cat([rgb_tensor, hull_colors], dim=0)

    if len(points_tensor) < 16:
        raise RuntimeError("Sparse reconstruction produced too few points for Gaussian initialization.")

    points_tensor = points_tensor.to(device)
    rgb_tensor = rgb_tensor.to(device)
    neighbor_distance = _knn_mean_distance(points_tensor)
    scene_scale = float(torch.linalg.norm(points_tensor - points_tensor.mean(dim=0), dim=1).max().item())

    sh_degree = int(settings.get("sh_degree", 3))
    init_opacity = float(settings.get("init_opacity", 0.10))
    sh_dim = (sh_degree + 1) ** 2

    splats = torch.nn.ParameterDict(
        {
            "means": torch.nn.Parameter(points_tensor),
            "scales": torch.nn.Parameter(torch.log(neighbor_distance).unsqueeze(-1).repeat(1, 3)),
            "quats": torch.nn.Parameter(torch.randn((len(points_tensor), 4), device=device)),
            "opacities": torch.nn.Parameter(torch.logit(torch.full((len(points_tensor),), init_opacity, device=device))),
            "sh0": torch.nn.Parameter(_rgb_to_sh(rgb_tensor).unsqueeze(1)),
            "shN": torch.nn.Parameter(torch.zeros((len(points_tensor), sh_dim - 1, 3), dtype=torch.float32, device=device)),
        }
    ).to(device)

    optimizers = {
        "means": torch.optim.Adam([{"params": splats["means"], "lr": float(settings.get("means_lr", 1.6e-4))}], eps=1e-15),
        "scales": torch.optim.Adam([{"params": splats["scales"], "lr": float(settings.get("scales_lr", 5.0e-3))}], eps=1e-15),
        "quats": torch.optim.Adam([{"params": splats["quats"], "lr": float(settings.get("quats_lr", 1.0e-3))}], eps=1e-15),
        "opacities": torch.optim.Adam([{"params": splats["opacities"], "lr": float(settings.get("opacities_lr", 5.0e-2))}], eps=1e-15),
        "sh0": torch.optim.Adam([{"params": splats["sh0"], "lr": float(settings.get("sh0_lr", 2.5e-3))}], eps=1e-15),
        "shN": torch.optim.Adam([{"params": splats["shN"], "lr": float(settings.get("shN_lr", 1.25e-4))}], eps=1e-15),
    }

    max_steps = int(settings.get("train_steps", 1200))
    strategy = DefaultStrategy(
        prune_opa=float(settings.get("prune_opa", 0.005)),
        grow_grad2d=float(settings.get("grow_grad2d", 7.5e-5)),
        grow_scale3d=float(settings.get("grow_scale3d", 0.01)),
        grow_scale2d=float(settings.get("grow_scale2d", 0.05)),
        prune_scale3d=float(settings.get("prune_scale3d", 0.1)),
        prune_scale2d=float(settings.get("prune_scale2d", 0.15)),
        refine_scale2d_stop_iter=int(settings.get("refine_scale2d_stop_iter", max_steps)),
        verbose=False,
        refine_start_iter=int(settings.get("densify_start_iter", 250)),
        refine_stop_iter=int(settings.get("densify_stop_iter", min(max_steps - 1, 7000))),
        refine_every=int(settings.get("densify_interval", 100)),
        reset_every=int(settings.get("opacity_reset_interval", 1500)),
        pause_refine_after_reset=len(views),
        absgrad=bool(settings.get("absgrad", True)),
    )
    strategy_state = strategy.initialize_state(scene_scale=max(scene_scale, 1.0))
    strategy.check_sanity(splats, optimizers)

    random.seed(42)
    loss_value = 0.0
    l1_value = 0.0
    ssim_value = 0.0
    progress_base = 0.28
    progress_span = 0.54
    lambda_dssim = float(settings.get("lambda_dssim", 0.20))
    alpha_loss_weight = float(settings.get("alpha_loss_weight", 0.05))
    random_background = bool(settings.get("random_background", True))

    for step in range(max_steps):
        if _should_stop(job["id"]):
            raise RuntimeError("Stopped during Gaussian Splat training.")

        view = random.choice(views)
        target_rgb = view.rgb_tensor.unsqueeze(0).to(device)
        target_alpha = view.alpha_tensor.unsqueeze(0).to(device)
        if view.has_alpha and random_background:
            backgrounds = torch.rand((1, 3), dtype=torch.float32, device=device)
        else:
            backgrounds = torch.ones((1, 3), dtype=torch.float32, device=device)
        pixels = (target_rgb * target_alpha) + (backgrounds[:, None, None, :] * (1.0 - target_alpha))
        camtoworld = view.camtoworld.unsqueeze(0).to(device)
        K = view.K.unsqueeze(0).to(device)

        sh_degree_to_use = min(sh_degree, step // max(1, max_steps // max(1, sh_degree + 1)))
        renders, _alphas, info = rasterization(
            means=splats["means"],
            quats=splats["quats"],
            scales=torch.exp(splats["scales"]),
            opacities=torch.sigmoid(splats["opacities"]),
            colors=torch.cat([splats["sh0"], splats["shN"]], dim=1),
            viewmats=torch.linalg.inv(camtoworld),
            Ks=K,
            width=view.width,
            height=view.height,
            sh_degree=sh_degree_to_use,
            packed=False,
            absgrad=strategy.absgrad,
            rasterize_mode="classic",
            backgrounds=backgrounds,
        )
        colors_pred = renders[..., :3]
        alpha_pred = _alphas

        strategy.step_pre_backward(
            params=splats,
            optimizers=optimizers,
            state=strategy_state,
            step=step,
            info=info,
        )
        if view.has_alpha:
            pixel_weights = 0.15 + (0.85 * target_alpha)
            l1_loss = (torch.abs(colors_pred - pixels) * pixel_weights).sum() / (pixel_weights.sum() * 3.0).clamp_min(1e-6)
            alpha_loss = F.l1_loss(alpha_pred, target_alpha)
        else:
            l1_loss = F.l1_loss(colors_pred, pixels)
            alpha_loss = torch.zeros((), dtype=torch.float32, device=device)
        ssim_score = _ssim(colors_pred, pixels)
        loss = ((1.0 - lambda_dssim) * l1_loss) + (lambda_dssim * (1.0 - ssim_score)) + (alpha_loss_weight * alpha_loss)
        loss.backward()
        loss_value = float(loss.item())
        l1_value = float(l1_loss.item())
        ssim_value = float(ssim_score.item())

        for optimizer in optimizers.values():
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)

        strategy.step_post_backward(
            params=splats,
            optimizers=optimizers,
            state=strategy_state,
            step=step,
            info=info,
            packed=False,
        )

        if step == 0 or (step + 1) % 25 == 0 or step == max_steps - 1:
            progress = progress_base + (progress_span * ((step + 1) / max_steps))
            _update(
                job["id"],
                "Training",
                progress,
                f"Training step {step + 1}/{max_steps} | loss={loss_value:.4f} | l1={l1_value:.4f} | ssim={ssim_value:.4f} | gaussians={len(splats['means'])}",
            )
            _log_line(
                job,
                f"Train step {step + 1}/{max_steps}: loss={loss_value:.5f}, l1={l1_value:.5f}, ssim={ssim_value:.5f}, sh_degree={sh_degree_to_use}, gaussians={len(splats['means'])}",
            )

    result_path = _result_ply_path(project["id"])
    export_splats(
        means=splats["means"].detach().cpu(),
        scales=splats["scales"].detach().cpu(),
        quats=splats["quats"].detach().cpu(),
        opacities=splats["opacities"].detach().cpu(),
        sh0=splats["sh0"].detach().cpu(),
        shN=splats["shN"].detach().cpu(),
        format="ply",
        save_to=str(result_path),
    )

    bounds_min = splats["means"].detach().cpu().min(dim=0).values.tolist()
    bounds_max = splats["means"].detach().cpu().max(dim=0).values.tolist()
    return result_path, len(splats["means"]), {"min": bounds_min, "max": bounds_max}


def _export_handoff(project: dict, job: dict, workspace_ply: Path, point_count: int, bounds: dict) -> dict:
    _update(job["id"], "Exporting", 0.9, "Writing the trained splat and SketchUp handoff package.")
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    export_dir = paths.exports_root() / f"{project['name'].replace(' ', '_')}_{timestamp}"
    export_dir.mkdir(parents=True, exist_ok=True)

    exported_ply = export_dir / "scene.ply"
    shutil.copy2(workspace_ply, exported_ply)

    manifest = {
        "version": 2,
        "project_id": project["id"],
        "project_name": project["name"],
        "backend": project["backend"],
        "created_at": store.utc_now(),
        "point_count": point_count,
        "scene_ply": str(exported_ply),
        "workspace_scene_ply": str(workspace_ply),
        "bounds": bounds,
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
    _result_manifest_path(project["id"]).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    store.update_project(
        project["id"],
        status="ready",
        last_result_ply=str(workspace_ply),
        last_manifest_path=str(manifest_path),
    )
    return manifest


def run_gsplat_job(project: dict, job: dict, settings: dict) -> dict:
    manifest_path = _find_transforms_manifest(project["id"])
    if manifest_path:
        manifest_views = _load_training_views_from_transforms(project, manifest_path, None, settings)
        if manifest_views and all(view.has_alpha for view in manifest_views):
            _log_line(job, f"Loaded {len(manifest_views)} training views from camera manifest {manifest_path.name}.")
            _log_line(job, "Known-camera alpha dataset detected. Training will use the manifest frame directly and will not mix in COLMAP poses.")
            manifest_settings = dict(settings)
            manifest_settings["_known_camera_mode"] = True
            workspace_ply, point_count, bounds = _train_gaussians(project, job, manifest_settings, None, manifest_views)
            manifest = _export_handoff(project, job, workspace_ply, point_count, bounds)
            _log_line(job, f"Exported trained splats to {manifest['scene_ply']}.")
            return manifest

    reconstruction = _run_colmap(project, job, settings)
    views = _load_training_views(project, reconstruction, settings)
    if manifest_path:
        _log_line(job, f"Loaded {len(views)} training views from camera manifest {manifest_path.name}.")
    else:
        _log_line(job, f"Loaded {len(views)} registered training views from SfM.")
    workspace_ply, point_count, bounds = _train_gaussians(project, job, settings, reconstruction, views)
    manifest = _export_handoff(project, job, workspace_ply, point_count, bounds)
    _log_line(job, f"Exported trained splats to {manifest['scene_ply']}.")
    return manifest
