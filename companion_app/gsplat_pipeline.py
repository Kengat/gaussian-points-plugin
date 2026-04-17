from __future__ import annotations

from collections import deque
import json
import math
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
import importlib.util
from pathlib import Path
from typing import Literal

from . import paths, store

_DEFAULT_TORCH_EXTENSIONS_DIR = Path(os.environ.get("TORCH_EXTENSIONS_DIR") or (paths.data_root() / "torch_extensions"))
_DEFAULT_TORCH_EXTENSIONS_DIR.mkdir(parents=True, exist_ok=True)
os.environ["TORCH_EXTENSIONS_DIR"] = str(_DEFAULT_TORCH_EXTENSIONS_DIR)

import pycolmap
import torch
import torch.nn.functional as F
import numpy as np
from PIL import Image, ImageOps

from gsplat.exporter import export_splats
from gsplat.rendering import rasterization
from gsplat.strategy import DefaultStrategy, MCMCStrategy
from gsplat.strategy.base import Strategy
from gsplat.strategy.ops import duplicate, inject_noise_to_position, remove, reset_opa
from gsplat.utils import normalized_quat_to_rotmat
from .gaussian_gasp import (
    export_ply_from_gaussian_gasp,
    result_gasp_path,
    safe_export_stem,
    write_gaussian_gasp_from_ply,
)
from .pipeline import load_project_import_summary
from .quality import (
    compute_dataset_diagnostics,
    merge_video_import_diagnostics,
    split_training_views,
    summarize_registered_views,
)


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
SH_C0 = 0.28209479177387814
_MCMC_RUNTIME_MODE: str | None = None
_SPZ_BACKEND = None


@dataclass
class TrainingView:
    view_index: int
    image_name: str
    rgb_tensor: torch.Tensor
    alpha_tensor: torch.Tensor
    has_alpha: bool
    camtoworld: torch.Tensor
    K: torch.Tensor
    width: int
    height: int
    camera_model: Literal["pinhole", "fisheye", "ftheta"] = "pinhole"
    radial_coeffs: torch.Tensor | None = None
    tangential_coeffs: torch.Tensor | None = None
    thin_prism_coeffs: torch.Tensor | None = None
    use_unscented_transform: bool = False
    projection_camera_model_name: str | None = None
    projection_camera_params: torch.Tensor | None = None
    source_camera_model_name: str | None = None


@dataclass(frozen=True)
class CameraProjectionSpec:
    camera_model: Literal["pinhole", "fisheye", "ftheta"] = "pinhole"
    radial_coeffs: torch.Tensor | None = None
    tangential_coeffs: torch.Tensor | None = None
    thin_prism_coeffs: torch.Tensor | None = None
    use_unscented_transform: bool = False
    projection_camera_model_name: str | None = None
    projection_camera_params: torch.Tensor | None = None
    requires_image_normalization: bool = False


@dataclass(frozen=True)
class SelfOrganizingCompressionConfig:
    enabled: bool
    method: Literal["auto", "plas", "pca"] = "auto"
    start_step: int = 0
    stop_step: int = 0
    sort_every: int = 100
    min_points: int = 256
    normalize: bool = True
    activated: bool = True
    shuffle: bool = True
    improvement_break: float = 1.0e-4
    blur_kernel_size: int = 5
    blur_sigma: float = 1.25
    loss_fn: Literal["mse", "huber"] = "huber"
    smoothness_weight: float = 1.0e-3
    sort_weights: dict[str, float] = field(
        default_factory=lambda: {
            "means": 1.0,
            "sh0": 1.0,
            "scales": 1.0,
            "opacities": 0.0,
            "quats": 0.0,
            "shN": 0.0,
        }
    )
    smoothness_weights: dict[str, float] = field(
        default_factory=lambda: {
            "means": 0.75,
            "sh0": 1.0,
            "scales": 0.65,
            "opacities": 0.20,
            "quats": 0.10,
            "shN": 0.0,
        }
    )


def _sample_stat_values(values: torch.Tensor, *, max_samples: int = 32768) -> torch.Tensor:
    flattened = values.reshape(-1)
    if flattened.numel() <= max_samples:
        return flattened
    stride = max(1, flattened.numel() // max_samples)
    return flattened[::stride][:max_samples]


@torch.no_grad()
def _summarize_splat_shape(
    splats: dict[str, torch.nn.Parameter] | torch.nn.ParameterDict,
) -> dict[str, float | int]:
    if len(splats["means"]) <= 0:
        return {
            "count": 0,
            "anisotropy_median": 1.0,
            "anisotropy_p90": 1.0,
            "anisotropy_p95": 1.0,
            "spherical_fraction": 1.0,
            "elongated_fraction": 0.0,
            "hyperelongated_fraction": 0.0,
            "low_opacity_fraction": 0.0,
        }

    scales = torch.exp(splats["scales"].detach())
    max_scales = scales.max(dim=-1).values
    min_scales = scales.min(dim=-1).values.clamp_min(1.0e-6)
    anisotropy = _sample_stat_values(max_scales / min_scales).float()
    anisotropy = torch.nan_to_num(anisotropy, nan=1.0, posinf=1.0, neginf=1.0).clamp_min(1.0)
    if anisotropy.numel() <= 0:
        anisotropy = torch.ones((1,), dtype=torch.float32, device=max_scales.device)
    quantiles = torch.quantile(anisotropy, torch.tensor([0.5, 0.9, 0.95], device=anisotropy.device))
    opacities = torch.sigmoid(_sample_stat_values(splats["opacities"].detach()).float())
    return {
        "count": int(len(splats["means"])),
        "anisotropy_median": float(quantiles[0].item()),
        "anisotropy_p90": float(quantiles[1].item()),
        "anisotropy_p95": float(quantiles[2].item()),
        "spherical_fraction": float((anisotropy <= 1.20).float().mean().item()),
        "elongated_fraction": float((anisotropy >= 2.0).float().mean().item()),
        "hyperelongated_fraction": float((anisotropy >= 12.0).float().mean().item()),
        "low_opacity_fraction": float((opacities <= 0.02).float().mean().item()),
    }


def _window_means(history: deque[float], *, window: int) -> tuple[float | None, float | None]:
    if len(history) < window:
        return None, None
    values = list(history)
    recent = values[-window:]
    previous = values[-(2 * window) : -window] if len(values) >= (2 * window) else []
    recent_mean = sum(recent) / float(len(recent)) if recent else None
    previous_mean = sum(previous) / float(len(previous)) if previous else None
    return recent_mean, previous_mean


def _tail_mean(history: deque[float], *, count: int) -> float | None:
    if len(history) <= 0:
        return None
    values = list(history)[-count:]
    if not values:
        return None
    return sum(values) / float(len(values))


def _initialize_training_runtime_monitor(
    *,
    max_steps: int,
    strategy_name: str,
    self_organizing_config: SelfOrganizingCompressionConfig,
) -> dict[str, object]:
    trend_window = max(12, min(40, max_steps // 40))
    analysis_every = max(20, min(60, max_steps // 80 if max_steps > 0 else 20))
    return {
        "strategy_name": strategy_name,
        "trend_window": int(trend_window),
        "analysis_every": int(analysis_every),
        "cooldown_until_step": -1,
        "loss_history": deque(maxlen=trend_window * 4),
        "l1_history": deque(maxlen=trend_window * 4),
        "ssim_history": deque(maxlen=trend_window * 4),
        "fill_history": deque(maxlen=16),
        "split_ratio_history": deque(maxlen=16),
        "shape_history": deque(maxlen=16),
        "last_shape": None,
        "actions": [],
        "event_count": 0,
        "sogs_weight_scale": 1.0 if self_organizing_config.enabled else 0.0,
        "last_summary": None,
    }


def _record_training_runtime_observation(
    monitor: dict[str, object],
    *,
    step: int,
    loss_value: float,
    l1_value: float,
    ssim_value: float,
    current_budget_cap: int,
    gaussian_count: int,
    shape_diagnostics: dict[str, float | int] | None,
    refine_stats: dict[str, object] | None,
) -> None:
    loss_history = monitor.get("loss_history")
    l1_history = monitor.get("l1_history")
    ssim_history = monitor.get("ssim_history")
    if isinstance(loss_history, deque):
        loss_history.append(float(loss_value))
    if isinstance(l1_history, deque):
        l1_history.append(float(l1_value))
    if isinstance(ssim_history, deque):
        ssim_history.append(float(ssim_value))
    if shape_diagnostics is not None:
        monitor["last_shape"] = dict(shape_diagnostics)
        shape_history = monitor.get("shape_history")
        if isinstance(shape_history, deque):
            shape_history.append(
                {
                    "step": int(step),
                    **shape_diagnostics,
                    "budget_fill": float(gaussian_count) / float(max(1, current_budget_cap)),
                }
            )
    if isinstance(refine_stats, dict) and int(refine_stats.get("step", -1)) == step:
        fill_history = monitor.get("fill_history")
        split_ratio_history = monitor.get("split_ratio_history")
        if isinstance(fill_history, deque):
            fill_history.append(float(refine_stats.get("growth_fill", 0.0)))
        if isinstance(split_ratio_history, deque):
            split_ratio_history.append(float(refine_stats.get("split_ratio", 0.0)))


def _apply_training_runtime_adaptation(
    monitor: dict[str, object],
    *,
    step: int,
    max_steps: int,
    strategy: Strategy,
    strategy_state: dict[str, object],
    self_organizing_config: SelfOrganizingCompressionConfig,
) -> list[dict[str, object]]:
    analysis_every = int(monitor.get("analysis_every") or 25)
    if step <= 0 or ((step + 1) % analysis_every) != 0:
        return []
    if step <= int(monitor.get("cooldown_until_step") or -1):
        return []

    loss_history = monitor.get("loss_history")
    l1_history = monitor.get("l1_history")
    ssim_history = monitor.get("ssim_history")
    if not isinstance(loss_history, deque) or not isinstance(l1_history, deque) or not isinstance(ssim_history, deque):
        return []

    trend_window = int(monitor.get("trend_window") or 20)
    recent_loss, previous_loss = _window_means(loss_history, window=trend_window)
    recent_l1, previous_l1 = _window_means(l1_history, window=trend_window)
    recent_ssim, previous_ssim = _window_means(ssim_history, window=trend_window)
    shape = monitor.get("last_shape")
    if not isinstance(shape, dict):
        return []

    actions: list[dict[str, object]] = []
    anis_median = float(shape.get("anisotropy_median") or 1.0)
    anis_p95 = float(shape.get("anisotropy_p95") or anis_median)
    spherical_fraction = float(shape.get("spherical_fraction") or 0.0)
    hyperelongated_fraction = float(shape.get("hyperelongated_fraction") or 0.0)
    fill_mean = _tail_mean(monitor.get("fill_history") if isinstance(monitor.get("fill_history"), deque) else deque(), count=4)
    split_ratio_mean = _tail_mean(
        monitor.get("split_ratio_history") if isinstance(monitor.get("split_ratio_history"), deque) else deque(),
        count=4,
    )

    if (
        self_organizing_config.enabled
        and previous_l1 is not None
        and previous_ssim is not None
        and recent_l1 is not None
        and recent_ssim is not None
    ):
        smoothness_scale = float(monitor.get("sogs_weight_scale") or 1.0)
        if smoothness_scale > 0.12 and recent_l1 > (previous_l1 * 1.04) and recent_ssim < (previous_ssim - 0.015):
            new_scale = max(0.10, smoothness_scale * 0.82)
            if new_scale < smoothness_scale:
                monitor["sogs_weight_scale"] = new_scale
                actions.append(
                    {
                        "kind": "sogs_relax",
                        "message": (
                            f"Runtime monitor softened self-organizing smoothness to {new_scale:.2f}x "
                            f"because detail retention worsened (l1 {previous_l1:.4f}->{recent_l1:.4f}, "
                            f"ssim {previous_ssim:.4f}->{recent_ssim:.4f})."
                        ),
                    }
                )

    if isinstance(strategy, _EdgeAwareLASStrategy):
        threshold_bias = float(strategy_state.get("adaptive_long_axis_bias") or 0.0)
        refine_every = int(strategy.refine_every)

        if hyperelongated_fraction > 0.04 or anis_p95 > max(16.0, anis_median * 5.5):
            new_bias = min(0.90, threshold_bias + 0.18)
            strategy_state["adaptive_long_axis_bias"] = new_bias
            strategy.las_offset_scale = max(0.24, strategy.las_offset_scale * 0.92)
            strategy.las_primary_shrink = min(2.80, strategy.las_primary_shrink * 1.04)
            strategy.las_opacity_factor = max(0.45, strategy.las_opacity_factor * 0.97)
            strategy.refine_every = min(180, max(40, int(round(refine_every * 1.08))))
            actions.append(
                {
                    "kind": "anisotropy_guard",
                    "message": (
                        f"Runtime monitor tightened long-axis split (median={anis_median:.2f}, p95={anis_p95:.2f}, "
                        f"hyper={hyperelongated_fraction:.2%}, bias={new_bias:.2f}, offset={strategy.las_offset_scale:.3f})."
                    ),
                }
            )

        elif (
            spherical_fraction > 0.48
            and anis_median < 1.65
            and (split_ratio_mean is None or split_ratio_mean < 0.22)
            and step < int(max_steps * 0.92)
        ):
            new_bias = max(-0.45, threshold_bias - 0.12)
            strategy_state["adaptive_long_axis_bias"] = new_bias
            strategy.las_offset_scale = min(0.58, strategy.las_offset_scale * 1.05)
            strategy.las_primary_shrink = max(1.65, strategy.las_primary_shrink * 0.98)
            strategy.refine_every = max(20, min(refine_every, int(round(refine_every * 0.94))))
            actions.append(
                {
                    "kind": "anisotropy_boost",
                    "message": (
                        f"Runtime monitor boosted anisotropy growth (median={anis_median:.2f}, spherical={spherical_fraction:.2%}, "
                        f"split_ratio={0.0 if split_ratio_mean is None else split_ratio_mean:.2f}, "
                        f"bias={new_bias:.2f}, offset={strategy.las_offset_scale:.3f})."
                    ),
                }
            )

        if fill_mean is not None and fill_mean < 0.28 and step < int(max_steps * 0.90):
            strategy.candidate_factor = min(8, max(strategy.candidate_factor, strategy.candidate_factor + 1))
            strategy.refine_every = max(20, int(round(strategy.refine_every * 0.92)))
            actions.append(
                {
                    "kind": "growth_recovery",
                    "message": (
                        f"Runtime monitor shortened refine cadence to {int(strategy.refine_every)} and raised "
                        f"candidate_factor to {int(strategy.candidate_factor)} because growth fill stayed low ({fill_mean:.2f})."
                    ),
                }
            )

    if actions:
        cooldown = max(analysis_every * 2, 50)
        monitor["cooldown_until_step"] = int(step + cooldown)
        monitor["event_count"] = int(monitor.get("event_count") or 0) + len(actions)
        existing_actions = monitor.get("actions")
        if not isinstance(existing_actions, list):
            existing_actions = []
        for action in actions:
            existing_actions.append({"step": int(step + 1), **action})
        monitor["actions"] = existing_actions[-24:]
    monitor["last_summary"] = {
        "step": int(step + 1),
        "anisotropy_median": anis_median,
        "anisotropy_p95": anis_p95,
        "spherical_fraction": spherical_fraction,
        "hyperelongated_fraction": hyperelongated_fraction,
        "loss_recent": recent_loss,
        "loss_previous": previous_loss,
        "l1_recent": recent_l1,
        "l1_previous": previous_l1,
        "ssim_recent": recent_ssim,
        "ssim_previous": previous_ssim,
        "fill_recent": fill_mean,
        "split_ratio_recent": split_ratio_mean,
        "sogs_weight_scale": float(monitor.get("sogs_weight_scale") or 0.0),
    }
    return actions


def _camera_coeff_tensor(values: list[float], *, target_length: int) -> torch.Tensor | None:
    trimmed = [float(value) for value in values[:target_length]]
    if len(trimmed) < target_length:
        trimmed.extend([0.0] * (target_length - len(trimmed)))
    if not any(abs(value) > 1.0e-12 for value in trimmed):
        return None
    return torch.tensor(trimmed, dtype=torch.float32)


def _camera_model_from_payload(payload: dict[str, object], frame: dict[str, object]) -> Literal["pinhole", "fisheye", "ftheta"]:
    raw_model = str(
        frame.get("camera_model")
        or frame.get("camera_type")
        or payload.get("camera_model")
        or payload.get("camera_type")
        or "pinhole"
    ).strip().lower()
    if "ftheta" in raw_model:
        return "ftheta"
    if "fisheye" in raw_model:
        return "fisheye"
    return "pinhole"


def _manifest_camera_metadata(
    payload: dict[str, object],
    frame: dict[str, object],
) -> tuple[Literal["pinhole", "fisheye", "ftheta"], torch.Tensor | None, torch.Tensor | None, torch.Tensor | None, bool]:
    camera_model = _camera_model_from_payload(payload, frame)
    coeff_source = {**payload, **frame}

    if camera_model == "fisheye":
        radial = _camera_coeff_tensor(
            [
                float(coeff_source.get("k1") or 0.0),
                float(coeff_source.get("k2") or 0.0),
                float(coeff_source.get("k3") or 0.0),
                float(coeff_source.get("k4") or 0.0),
            ],
            target_length=4,
        )
        tangential = None
        thin_prism = None
    else:
        radial = _camera_coeff_tensor(
            [
                float(coeff_source.get("k1") or 0.0),
                float(coeff_source.get("k2") or 0.0),
                float(coeff_source.get("k3") or 0.0),
                float(coeff_source.get("k4") or 0.0),
                float(coeff_source.get("k5") or 0.0),
                float(coeff_source.get("k6") or 0.0),
            ],
            target_length=6,
        )
        tangential = _camera_coeff_tensor(
            [
                float(coeff_source.get("p1") or 0.0),
                float(coeff_source.get("p2") or 0.0),
            ],
            target_length=2,
        )
        thin_prism = _camera_coeff_tensor(
            [
                float(coeff_source.get("s1") or 0.0),
                float(coeff_source.get("s2") or 0.0),
                float(coeff_source.get("s3") or 0.0),
                float(coeff_source.get("s4") or 0.0),
            ],
            target_length=4,
        )

    use_unscented_transform = (
        camera_model == "ftheta"
        or radial is not None
        or tangential is not None
        or thin_prism is not None
    )
    return camera_model, radial, tangential, thin_prism, use_unscented_transform


def _camera_has_nonzero_coeffs(*coeff_sets: torch.Tensor | None) -> bool:
    for coeffs in coeff_sets:
        if coeffs is not None and torch.any(torch.abs(coeffs) > 1.0e-12):
            return True
    return False


def _camera_tensor_params(values: list[float]) -> torch.Tensor:
    return torch.tensor([float(value) for value in values], dtype=torch.float32)


def _scaled_pycolmap_camera(camera: pycolmap.Camera, width: int, height: int) -> pycolmap.Camera:
    scaled_camera = pycolmap.Camera(camera.todict())
    if int(scaled_camera.width) != int(width) or int(scaled_camera.height) != int(height):
        scaled_camera.rescale(int(width), int(height))
    return scaled_camera


def _ideal_camera_params_from_calibration_matrix(camera: pycolmap.Camera) -> list[float]:
    K = np.asarray(camera.calibration_matrix(), dtype=np.float64)
    return [
        float(K[0, 0]),
        float(K[1, 1]),
        float(K[0, 2]),
        float(K[1, 2]),
    ]


def _colmap_camera_projection_spec(
    camera: pycolmap.Camera,
    *,
    enable_unscented_transform: bool,
) -> CameraProjectionSpec:
    model_name = str(camera.model_name).upper()
    params = np.asarray(camera.params, dtype=np.float64)
    projection_params = _camera_tensor_params(params.tolist())

    if model_name in {"SIMPLE_PINHOLE", "PINHOLE"}:
        return CameraProjectionSpec(
            camera_model="pinhole",
            projection_camera_model_name=model_name,
            projection_camera_params=projection_params,
        )

    if model_name == "SIMPLE_RADIAL":
        radial = _camera_coeff_tensor([float(params[3])], target_length=6)
        if not _camera_has_nonzero_coeffs(radial):
            ideal_params = _camera_tensor_params(_ideal_camera_params_from_calibration_matrix(camera))
            return CameraProjectionSpec(
                camera_model="pinhole",
                projection_camera_model_name="PINHOLE",
                projection_camera_params=ideal_params,
            )
        return CameraProjectionSpec(
            camera_model="pinhole",
            radial_coeffs=radial,
            use_unscented_transform=bool(enable_unscented_transform),
            projection_camera_model_name=model_name,
            projection_camera_params=projection_params,
        )

    if model_name == "RADIAL":
        radial = _camera_coeff_tensor([float(params[3]), float(params[4])], target_length=6)
        if not _camera_has_nonzero_coeffs(radial):
            ideal_params = _camera_tensor_params(_ideal_camera_params_from_calibration_matrix(camera))
            return CameraProjectionSpec(
                camera_model="pinhole",
                projection_camera_model_name="PINHOLE",
                projection_camera_params=ideal_params,
            )
        return CameraProjectionSpec(
            camera_model="pinhole",
            radial_coeffs=radial,
            use_unscented_transform=bool(enable_unscented_transform),
            projection_camera_model_name=model_name,
            projection_camera_params=projection_params,
        )

    if model_name == "OPENCV":
        radial = _camera_coeff_tensor([float(params[4]), float(params[5])], target_length=6)
        tangential = _camera_coeff_tensor([float(params[6]), float(params[7])], target_length=2)
        if not _camera_has_nonzero_coeffs(radial, tangential):
            ideal_params = _camera_tensor_params(_ideal_camera_params_from_calibration_matrix(camera))
            return CameraProjectionSpec(
                camera_model="pinhole",
                projection_camera_model_name="PINHOLE",
                projection_camera_params=ideal_params,
            )
        return CameraProjectionSpec(
            camera_model="pinhole",
            radial_coeffs=radial,
            tangential_coeffs=tangential,
            use_unscented_transform=bool(enable_unscented_transform),
            projection_camera_model_name=model_name,
            projection_camera_params=projection_params,
        )

    if model_name == "FULL_OPENCV":
        radial = _camera_coeff_tensor(
            [
                float(params[4]),
                float(params[5]),
                float(params[8]),
                float(params[9]),
                float(params[10]),
                float(params[11]),
            ],
            target_length=6,
        )
        tangential = _camera_coeff_tensor([float(params[6]), float(params[7])], target_length=2)
        if not _camera_has_nonzero_coeffs(radial, tangential):
            ideal_params = _camera_tensor_params(_ideal_camera_params_from_calibration_matrix(camera))
            return CameraProjectionSpec(
                camera_model="pinhole",
                projection_camera_model_name="PINHOLE",
                projection_camera_params=ideal_params,
            )
        return CameraProjectionSpec(
            camera_model="pinhole",
            radial_coeffs=radial,
            tangential_coeffs=tangential,
            use_unscented_transform=bool(enable_unscented_transform),
            projection_camera_model_name=model_name,
            projection_camera_params=projection_params,
        )

    if model_name in {"SIMPLE_FISHEYE", "FISHEYE"}:
        return CameraProjectionSpec(
            camera_model="fisheye",
            use_unscented_transform=bool(enable_unscented_transform),
            projection_camera_model_name=model_name,
            projection_camera_params=projection_params,
        )

    if model_name == "SIMPLE_RADIAL_FISHEYE":
        radial = _camera_coeff_tensor([float(params[3])], target_length=4)
        return CameraProjectionSpec(
            camera_model="fisheye",
            radial_coeffs=radial,
            use_unscented_transform=bool(enable_unscented_transform),
            projection_camera_model_name=model_name,
            projection_camera_params=projection_params,
        )

    if model_name == "RADIAL_FISHEYE":
        radial = _camera_coeff_tensor([float(params[3]), float(params[4])], target_length=4)
        return CameraProjectionSpec(
            camera_model="fisheye",
            radial_coeffs=radial,
            use_unscented_transform=bool(enable_unscented_transform),
            projection_camera_model_name=model_name,
            projection_camera_params=projection_params,
        )

    if model_name == "OPENCV_FISHEYE":
        radial = _camera_coeff_tensor(
            [float(params[4]), float(params[5]), float(params[6]), float(params[7])],
            target_length=4,
        )
        return CameraProjectionSpec(
            camera_model="fisheye",
            radial_coeffs=radial,
            use_unscented_transform=bool(enable_unscented_transform),
            projection_camera_model_name=model_name,
            projection_camera_params=projection_params,
        )

    raise ValueError(f"COLMAP camera model '{model_name}' requires normalization before training.")


def _normalized_projection_model_name(model_name: str) -> str:
    if model_name in {
        "SIMPLE_FISHEYE",
        "FISHEYE",
        "SIMPLE_RADIAL_FISHEYE",
        "RADIAL_FISHEYE",
        "OPENCV_FISHEYE",
        "THIN_PRISM_FISHEYE",
        "RAD_TAN_THIN_PRISM_FISHEYE",
        "FOV",
    }:
        return "FISHEYE"
    return "PINHOLE"


def _make_pycolmap_camera(model_name: str, width: int, height: int, params: tuple[float, ...]) -> pycolmap.Camera:
    focal_hint = float(max(width, height))
    camera = pycolmap.Camera.create_from_model_name(0, model_name, focal_hint, int(width), int(height))
    camera.params = np.asarray(params, dtype=np.float64)
    return camera


@lru_cache(maxsize=256)
def _cached_pycolmap_camera(model_name: str, width: int, height: int, params: tuple[float, ...]) -> pycolmap.Camera:
    return _make_pycolmap_camera(model_name, width, height, params)


def _sample_image_border(width: int, height: int, samples_per_edge: int = 96) -> np.ndarray:
    xs = np.linspace(0.0, max(float(width - 1), 0.0), num=samples_per_edge, dtype=np.float64)
    ys = np.linspace(0.0, max(float(height - 1), 0.0), num=samples_per_edge, dtype=np.float64)
    top = np.stack([xs, np.zeros_like(xs)], axis=1)
    bottom = np.stack([xs, np.full_like(xs, max(float(height - 1), 0.0))], axis=1)
    left = np.stack([np.zeros_like(ys), ys], axis=1)
    right = np.stack([np.full_like(ys, max(float(width - 1), 0.0)), ys], axis=1)
    return np.concatenate([top, bottom, left, right], axis=0)


def _zoom_camera_params(params: list[float], zoom: float) -> list[float]:
    zoomed = [float(value) for value in params]
    zoomed[0] *= zoom
    zoomed[1] *= zoom
    return zoomed


def _border_maps_inside_source(
    source_camera: pycolmap.Camera,
    target_model_name: str,
    target_params: list[float],
) -> bool:
    target_camera = _make_pycolmap_camera(target_model_name, int(source_camera.width), int(source_camera.height), tuple(target_params))
    border_pixels = _sample_image_border(int(target_camera.width), int(target_camera.height))
    target_rays = target_camera.cam_from_img(border_pixels)
    if target_rays is None:
        return False
    target_rays = np.asarray(target_rays, dtype=np.float64)
    if target_rays.size == 0 or not np.isfinite(target_rays).all():
        return False
    cam_points = np.concatenate([target_rays, np.ones((len(target_rays), 1), dtype=np.float64)], axis=1)
    source_pixels = source_camera.img_from_cam(cam_points)
    source_pixels = np.asarray(source_pixels, dtype=np.float64)
    if source_pixels.shape != (len(border_pixels), 2) or not np.isfinite(source_pixels).all():
        return False
    return bool(
        np.all(source_pixels[:, 0] >= 0.0)
        and np.all(source_pixels[:, 0] <= max(float(source_camera.width - 1), 0.0))
        and np.all(source_pixels[:, 1] >= 0.0)
        and np.all(source_pixels[:, 1] <= max(float(source_camera.height - 1), 0.0))
    )


def _solve_normalized_camera_params(source_camera: pycolmap.Camera, target_model_name: str) -> list[float]:
    base_params = _ideal_camera_params_from_calibration_matrix(source_camera)
    if _border_maps_inside_source(source_camera, target_model_name, base_params):
        return base_params

    lower = 1.0
    upper = 1.0
    for _ in range(12):
        upper *= 1.25
        if _border_maps_inside_source(source_camera, target_model_name, _zoom_camera_params(base_params, upper)):
            break
    else:
        return _zoom_camera_params(base_params, upper)

    for _ in range(24):
        midpoint = 0.5 * (lower + upper)
        if _border_maps_inside_source(source_camera, target_model_name, _zoom_camera_params(base_params, midpoint)):
            upper = midpoint
        else:
            lower = midpoint
    return _zoom_camera_params(base_params, upper)


def _undistort_training_image_from_colmap_camera(
    rgb_tensor: torch.Tensor,
    alpha_tensor: torch.Tensor,
    camera: pycolmap.Camera,
    target_model_name: str,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    import cv2

    target_params = _solve_normalized_camera_params(camera, target_model_name)
    target_camera = _make_pycolmap_camera(target_model_name, int(camera.width), int(camera.height), tuple(target_params))

    height = int(target_camera.height)
    width = int(target_camera.width)
    grid_x, grid_y = np.meshgrid(
        np.arange(width, dtype=np.float64),
        np.arange(height, dtype=np.float64),
        indexing="xy",
    )
    image_pixels = np.stack([grid_x.reshape(-1), grid_y.reshape(-1)], axis=1)
    target_rays = np.asarray(target_camera.cam_from_img(image_pixels), dtype=np.float64)
    cam_points = np.concatenate([target_rays, np.ones((len(target_rays), 1), dtype=np.float64)], axis=1)
    source_pixels = np.asarray(camera.img_from_cam(cam_points), dtype=np.float64)

    map_x = source_pixels[:, 0].reshape(height, width).astype(np.float32)
    map_y = source_pixels[:, 1].reshape(height, width).astype(np.float32)
    valid = np.isfinite(map_x) & np.isfinite(map_y)
    map_x[~valid] = -1.0
    map_y[~valid] = -1.0

    rgb_np = rgb_tensor.detach().cpu().numpy().astype(np.float32, copy=False)
    alpha_np = alpha_tensor.detach().cpu().numpy().astype(np.float32, copy=False)
    remapped_rgb = cv2.remap(
        rgb_np,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    remapped_alpha = cv2.remap(
        alpha_np,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    if remapped_alpha.ndim == 2:
        remapped_alpha = remapped_alpha[..., None]

    K = torch.tensor(target_camera.calibration_matrix(), dtype=torch.float32)
    return (
        torch.from_numpy(remapped_rgb.copy()),
        torch.from_numpy(remapped_alpha.copy()),
        K,
    )


def _normalize_colmap_camera_for_training(
    camera: pycolmap.Camera,
    *,
    enable_unscented_transform: bool,
) -> CameraProjectionSpec:
    target_model_name = _normalized_projection_model_name(str(camera.model_name).upper())
    target_params = _solve_normalized_camera_params(camera, target_model_name)
    projection_params = _camera_tensor_params(target_params)
    normalized_model = "fisheye" if target_model_name == "FISHEYE" else "pinhole"
    return CameraProjectionSpec(
        camera_model=normalized_model,
        use_unscented_transform=False,
        projection_camera_model_name=target_model_name,
        projection_camera_params=projection_params,
        requires_image_normalization=True,
    )


def _manifest_source_camera_model_and_params(
    camera_model: Literal["pinhole", "fisheye", "ftheta"],
    K: torch.Tensor,
    radial_coeffs: torch.Tensor | None,
    tangential_coeffs: torch.Tensor | None,
    thin_prism_coeffs: torch.Tensor | None,
) -> tuple[str, tuple[float, ...]] | None:
    fx = float(K[0, 0].item())
    fy = float(K[1, 1].item())
    cx = float(K[0, 2].item())
    cy = float(K[1, 2].item())

    if camera_model == "ftheta":
        return None
    if camera_model == "fisheye":
        radial = radial_coeffs.detach().cpu().tolist() if radial_coeffs is not None else [0.0, 0.0, 0.0, 0.0]
        radial = (radial + [0.0, 0.0, 0.0, 0.0])[:4]
        return "OPENCV_FISHEYE", (fx, fy, cx, cy, *[float(value) for value in radial])

    radial = radial_coeffs.detach().cpu().tolist() if radial_coeffs is not None else [0.0] * 6
    tangential = tangential_coeffs.detach().cpu().tolist() if tangential_coeffs is not None else [0.0, 0.0]
    thin_prism = thin_prism_coeffs.detach().cpu().tolist() if thin_prism_coeffs is not None else [0.0] * 4
    radial = (radial + ([0.0] * 6))[:6]
    tangential = (tangential + [0.0, 0.0])[:2]
    thin_prism = (thin_prism + ([0.0] * 4))[:4]

    if _camera_has_nonzero_coeffs(thin_prism_coeffs) or any(abs(value) > 1.0e-12 for value in radial[2:]):
        return (
            "FULL_OPENCV",
            (
                fx,
                fy,
                cx,
                cy,
                float(radial[0]),
                float(radial[1]),
                float(tangential[0]),
                float(tangential[1]),
                float(radial[2]),
                float(radial[3]),
                float(radial[4]),
                float(radial[5]),
            ),
        )
    if _camera_has_nonzero_coeffs(radial_coeffs, tangential_coeffs):
        return (
            "OPENCV",
            (
                fx,
                fy,
                cx,
                cy,
                float(radial[0]),
                float(radial[1]),
                float(tangential[0]),
                float(tangential[1]),
            ),
        )
    return "PINHOLE", (fx, fy, cx, cy)


def _manifest_camera_to_training_spec(
    payload: dict[str, object],
    frame: dict[str, object],
    *,
    K: torch.Tensor,
    width: int,
    height: int,
    normalize_distortion: bool,
) -> tuple[CameraProjectionSpec, pycolmap.Camera | None, str | None]:
    camera_model, radial_coeffs, tangential_coeffs, thin_prism_coeffs, use_ut = _manifest_camera_metadata(payload, frame)
    source_camera_spec = _manifest_source_camera_model_and_params(
        camera_model,
        K,
        radial_coeffs,
        tangential_coeffs,
        thin_prism_coeffs,
    )
    source_model_name = source_camera_spec[0] if source_camera_spec is not None else camera_model.upper()
    source_camera = (
        _make_pycolmap_camera(source_camera_spec[0], width, height, source_camera_spec[1])
        if source_camera_spec is not None
        else None
    )
    if (
        normalize_distortion
        and source_camera is not None
        and _camera_has_nonzero_coeffs(radial_coeffs, tangential_coeffs, thin_prism_coeffs)
    ):
        target_model_name = _normalized_projection_model_name(source_model_name)
        target_params = _solve_normalized_camera_params(source_camera, target_model_name)
        normalized_model = "fisheye" if target_model_name == "FISHEYE" else "pinhole"
        return (
            CameraProjectionSpec(
                camera_model=normalized_model,
                projection_camera_model_name=target_model_name,
                projection_camera_params=_camera_tensor_params(target_params),
                requires_image_normalization=True,
            ),
            source_camera,
            source_model_name,
        )
    if source_camera_spec is not None:
        return (
            CameraProjectionSpec(
                camera_model=camera_model,
                radial_coeffs=radial_coeffs,
                tangential_coeffs=tangential_coeffs,
                thin_prism_coeffs=thin_prism_coeffs,
                use_unscented_transform=use_ut,
                projection_camera_model_name=source_camera_spec[0],
                projection_camera_params=_camera_tensor_params(list(source_camera_spec[1])),
            ),
            source_camera,
            source_model_name,
        )
    return (
        CameraProjectionSpec(
            camera_model=camera_model,
            radial_coeffs=radial_coeffs,
            tangential_coeffs=tangential_coeffs,
            thin_prism_coeffs=thin_prism_coeffs,
            use_unscented_transform=use_ut,
        ),
        None,
        source_model_name,
    )


def _colmap_camera_to_training_spec(
    camera: pycolmap.Camera,
    *,
    enable_unscented_transform: bool,
    normalize_distortion: bool = True,
) -> CameraProjectionSpec:
    model_name = str(camera.model_name).upper()
    if normalize_distortion and model_name not in {"SIMPLE_PINHOLE", "PINHOLE"}:
        try:
            raw_spec = _colmap_camera_projection_spec(
                camera,
                enable_unscented_transform=enable_unscented_transform,
            )
        except ValueError:
            raw_spec = None
        if raw_spec is None or raw_spec.use_unscented_transform or raw_spec.requires_image_normalization:
            return _normalize_colmap_camera_for_training(
                camera,
                enable_unscented_transform=enable_unscented_transform,
            )
    try:
        return _colmap_camera_projection_spec(
            camera,
            enable_unscented_transform=enable_unscented_transform,
        )
    except ValueError:
        return _normalize_colmap_camera_for_training(
            camera,
            enable_unscented_transform=enable_unscented_transform,
        )


def _view_pycolmap_camera(view: TrainingView) -> pycolmap.Camera | None:
    if view.projection_camera_model_name is None or view.projection_camera_params is None:
        return None
    params = tuple(float(value) for value in view.projection_camera_params.detach().cpu().tolist())
    return _cached_pycolmap_camera(view.projection_camera_model_name, view.width, view.height, params)


def _project_camera_points_to_pixels(
    view: TrainingView,
    camera_points: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    device = camera_points.device
    positive_depth = camera_points[:, 2] > 1.0e-4
    pixels = torch.full((len(camera_points), 2), float("nan"), dtype=torch.float32, device=device)
    if not torch.any(positive_depth):
        return pixels, positive_depth

    camera = _view_pycolmap_camera(view)
    if camera is None:
        x = camera_points[:, 0] / camera_points[:, 2].clamp_min(1.0e-4)
        y = camera_points[:, 1] / camera_points[:, 2].clamp_min(1.0e-4)
        fx = float(view.K[0, 0].item())
        fy = float(view.K[1, 1].item())
        cx = float(view.K[0, 2].item())
        cy = float(view.K[1, 2].item())
        pixels[:, 0] = (x * fx) + cx
        pixels[:, 1] = (y * fy) + cy
    else:
        projected = np.asarray(
            camera.img_from_cam(camera_points[positive_depth, :3].detach().cpu().numpy().astype(np.float64, copy=False)),
            dtype=np.float32,
        )
        pixels[positive_depth] = torch.from_numpy(projected).to(device)

    inside = (
        positive_depth
        & torch.isfinite(pixels).all(dim=-1)
        & (pixels[:, 0] >= 0.0)
        & (pixels[:, 0] < float(view.width))
        & (pixels[:, 1] >= 0.0)
        & (pixels[:, 1] < float(view.height))
    )
    return pixels, inside


def _project_camera_points_to_pixel_indices(
    view: TrainingView,
    camera_points: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    pixels, inside = _project_camera_points_to_pixels(view, camera_points)
    u = torch.round(pixels[:, 0]).to(torch.int64)
    v = torch.round(pixels[:, 1]).to(torch.int64)
    inside = inside & (u >= 0) & (u < view.width) & (v >= 0) & (v < view.height)
    return u, v, inside


def _unproject_image_pixels_to_camera_rays(
    view: TrainingView,
    u: torch.Tensor,
    v: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    device = u.device
    image_points = torch.stack([u, v], dim=-1)
    valid = torch.isfinite(image_points).all(dim=-1)
    rays = torch.zeros((len(image_points), 3), dtype=torch.float32, device=device)
    if not torch.any(valid):
        return rays, valid

    camera = _view_pycolmap_camera(view)
    if camera is None:
        fx = float(view.K[0, 0].item())
        fy = float(view.K[1, 1].item())
        cx = float(view.K[0, 2].item())
        cy = float(view.K[1, 2].item())
        rays[:, 0] = (u - cx) / max(fx, 1.0e-6)
        rays[:, 1] = (v - cy) / max(fy, 1.0e-6)
        rays[:, 2] = 1.0
        return rays, valid

    unprojected = camera.cam_from_img(image_points[valid].detach().cpu().numpy().astype(np.float64, copy=False))
    if unprojected is None:
        return rays, torch.zeros_like(valid)
    unprojected = np.asarray(unprojected, dtype=np.float32)
    valid_values = torch.from_numpy(unprojected).to(device)
    subset_valid = torch.isfinite(valid_values).all(dim=-1)
    valid_indices = torch.nonzero(valid, as_tuple=False).squeeze(-1)
    good_indices = valid_indices[subset_valid]
    valid_mask = torch.zeros_like(valid)
    valid_mask[good_indices] = True
    rays[good_indices, :2] = valid_values[subset_valid]
    rays[valid_mask, 2] = 1.0
    return rays, valid_mask


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


def _result_temp_ply_path(project_id: str) -> Path:
    return paths.project_result_dir(project_id) / "_gaussian_export_tmp.ply"


def _result_temp_compressed_ply_path(project_id: str) -> Path:
    return paths.project_result_dir(project_id) / "_gaussian_export_tmp_compressed.ply"


def _result_temp_spz_path(project_id: str) -> Path:
    return paths.project_result_dir(project_id) / "_gaussian_export_tmp.spz"


def _result_manifest_path(project_id: str) -> Path:
    return paths.project_result_dir(project_id) / "scene_manifest.json"


def _training_summary_path(project_id: str) -> Path:
    return paths.project_result_dir(project_id) / "training_summary.json"


def _ensure_torch_extensions_dir() -> Path:
    configured = os.environ.get("TORCH_EXTENSIONS_DIR")
    extension_dir = Path(configured) if configured else (paths.data_root() / "torch_extensions")
    extension_dir.mkdir(parents=True, exist_ok=True)
    os.environ["TORCH_EXTENSIONS_DIR"] = str(extension_dir)
    return extension_dir


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        pass


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


def _project_is_video_derived(project: dict | None) -> bool:
    import_summary = (project or {}).get("last_import_summary") if project else None
    if not isinstance(import_summary, dict):
        return False
    aggregate = import_summary.get("aggregate")
    if not isinstance(aggregate, dict):
        return False
    return int(aggregate.get("source_videos") or 0) > 0


def _is_long_video_sequence(project: dict | None, image_count: int) -> bool:
    return _project_is_video_derived(project) and image_count >= 96


def _mcmc_target_count(
    current_count: int,
    cap_max: int,
    *,
    step: int | None = None,
    refine_stop_iter: int | None = None,
    refine_every: int | None = None,
) -> int:
    if current_count <= 0:
        return min(max(1, cap_max), 1)
    if cap_max <= current_count:
        return current_count

    growth: float | None = None
    if step is not None and refine_stop_iter is not None and refine_every is not None and refine_every > 0:
        last_refine_step = max(step, int(refine_stop_iter) - 1)
        remaining_events = max(1, 1 + ((last_refine_step - step) // int(refine_every)))
        if remaining_events == 1:
            return cap_max
        growth = float(cap_max / float(max(current_count, 1))) ** (1.0 / float(remaining_events))
        growth = max(1.01, growth)

    if growth is None:
        cap_ratio = float(cap_max) / float(max(current_count, 1))
        growth = 1.05
        if cap_ratio >= 16.0:
            growth = 1.08
        elif cap_ratio >= 8.0:
            growth = 1.075
        elif cap_ratio >= 4.0:
            growth = 1.07
        elif cap_ratio >= 2.0:
            growth = 1.06

    return min(cap_max, max(current_count + 1, int(math.ceil(current_count * growth))))


def _rgb_to_sh(rgb: torch.Tensor) -> torch.Tensor:
    return (rgb - 0.5) / SH_C0


def _manifest_intrinsics_matrix(payload: dict[str, object], width: int, height: int) -> torch.Tensor:
    source_width = float(payload.get("w") or width)
    source_height = float(payload.get("h") or height)
    scale_x = float(width) / max(source_width, 1.0)
    scale_y = float(height) / max(source_height, 1.0)

    fl_x = payload.get("fl_x")
    fl_y = payload.get("fl_y")
    cx = payload.get("cx")
    cy = payload.get("cy")

    focal_x = float(fl_x) * scale_x if fl_x is not None else None
    focal_y = float(fl_y) * scale_y if fl_y is not None else None
    principal_x = float(cx) * scale_x if cx is not None else (width * 0.5)
    principal_y = float(cy) * scale_y if cy is not None else (height * 0.5)

    if focal_x is None:
        camera_angle_x = payload.get("camera_angle_x")
        if camera_angle_x is not None:
            focal_x = 0.5 * width / math.tan(0.5 * float(camera_angle_x))
        else:
            focal_x = float(width)
    if focal_y is None:
        camera_angle_y = payload.get("camera_angle_y")
        if camera_angle_y is not None:
            focal_y = 0.5 * height / math.tan(0.5 * float(camera_angle_y))
        else:
            focal_y = focal_x

    return torch.tensor(
        [
            [focal_x, 0.0, principal_x],
            [0.0, focal_y, principal_y],
            [0.0, 0.0, 1.0],
        ],
        dtype=torch.float32,
    )


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


def _psnr_from_mse(mse: torch.Tensor) -> torch.Tensor:
    return -10.0 * torch.log10(mse.clamp_min(1e-8))


def _sh_degree_from_shn_dim(rest_coeff_count: int) -> int:
    if rest_coeff_count >= 15:
        return 3
    if rest_coeff_count >= 8:
        return 2
    if rest_coeff_count >= 3:
        return 1
    return 0


def _export_tensor_payload(splats: torch.nn.ParameterDict) -> dict[str, torch.Tensor]:
    return {
        "means": splats["means"].detach().cpu(),
        "scales": splats["scales"].detach().cpu(),
        "quats": F.normalize(splats["quats"].detach().cpu(), dim=-1),
        "opacities": splats["opacities"].detach().cpu(),
        "sh0": splats["sh0"].detach().cpu(),
        "shN": splats["shN"].detach().cpu(),
    }


def _sh0_to_rgb(sh0: torch.Tensor) -> torch.Tensor:
    if sh0.ndim == 3 and sh0.shape[1] == 1:
        sh0 = sh0[:, 0, :]
    return torch.clamp((sh0 * SH_C0) + 0.5, 0.0, 1.0)


def _self_organized_grid_shape(point_count: int) -> tuple[int, int]:
    if point_count <= 0:
        return 0, 0
    width = max(1, int(math.ceil(math.sqrt(point_count))))
    height = max(1, int(math.ceil(point_count / float(width))))
    return height, width


def _flatten_self_organizing_attribute(
    tensors: dict[str, torch.Tensor] | torch.nn.ParameterDict,
    name: str,
    *,
    activated: bool,
) -> torch.Tensor:
    value = tensors[name]
    if name == "scales" and activated:
        value = torch.exp(value)
    elif name == "opacities":
        value = torch.sigmoid(value) if activated else value
        if value.ndim == 1:
            value = value.unsqueeze(-1)
    elif name == "quats" and activated:
        value = F.normalize(value, dim=-1)
    elif name == "sh0" and activated:
        value = _sh0_to_rgb(value)
    elif name == "sh0" and value.ndim == 3 and value.shape[1] == 1:
        value = value[:, 0, :]
    if value.ndim == 1:
        value = value.unsqueeze(-1)
    return value.reshape(value.shape[0], -1).to(dtype=torch.float32)


def _normalize_self_organizing_channels(values: torch.Tensor) -> torch.Tensor:
    centered = values - values.mean(dim=0, keepdim=True)
    scale = centered.std(dim=0, unbiased=False, keepdim=True).clamp_min(1.0e-5)
    return centered / scale


def _build_self_organizing_feature_matrix(
    tensors: dict[str, torch.Tensor] | torch.nn.ParameterDict,
    weights: dict[str, float],
    *,
    normalize: bool,
    activated: bool,
) -> torch.Tensor:
    features: list[torch.Tensor] = []
    for name, weight in weights.items():
        if weight <= 0.0 or name not in tensors:
            continue
        flat = _flatten_self_organizing_attribute(tensors, name, activated=activated)
        if normalize:
            flat = _normalize_self_organizing_channels(flat)
        features.append(flat * float(weight))
    if not features:
        raise ValueError("Self-organizing layout requires at least one non-zero feature weight.")
    return torch.cat(features, dim=1)


def _project_self_organizing_features(features: torch.Tensor) -> torch.Tensor:
    point_count = features.shape[0]
    if point_count <= 1:
        return torch.zeros((point_count, 2), dtype=features.dtype, device=features.device)
    centered = features - features.mean(dim=0, keepdim=True)
    if centered.shape[1] == 1:
        second_axis = torch.linspace(-1.0, 1.0, point_count, device=features.device, dtype=features.dtype).unsqueeze(-1)
        return torch.cat([centered, second_axis], dim=1)
    covariance = centered.T @ centered / float(max(1, point_count - 1))
    eigenvalues, eigenvectors = torch.linalg.eigh(covariance)
    order = torch.argsort(eigenvalues, descending=True)
    basis = eigenvectors[:, order[: min(2, eigenvectors.shape[1])]]
    coords = centered @ basis
    if coords.shape[1] == 1:
        second_axis = torch.linspace(-1.0, 1.0, point_count, device=features.device, dtype=features.dtype).unsqueeze(-1)
        coords = torch.cat([coords, second_axis], dim=1)
    return coords[:, :2]


def _pca_snake_permutation(
    features: torch.Tensor,
    *,
    shuffle: bool,
) -> torch.Tensor:
    point_count = features.shape[0]
    shuffled_indices = torch.randperm(point_count, device=features.device) if shuffle else torch.arange(point_count, device=features.device)
    coords = _project_self_organizing_features(features[shuffled_indices])
    _, grid_width = _self_organized_grid_shape(point_count)
    y_order = torch.argsort(coords[:, 1])
    rows: list[torch.Tensor] = []
    for row_index, start in enumerate(range(0, point_count, grid_width)):
        row = y_order[start : start + grid_width]
        row = row[torch.argsort(coords[row, 0])]
        if row_index % 2 == 1:
            row = torch.flip(row, dims=(0,))
        rows.append(row)
    ordered = torch.cat(rows, dim=0)
    return shuffled_indices[ordered]


def _plas_permutation(
    features: torch.Tensor,
    *,
    shuffle: bool,
    improvement_break: float,
    verbose: bool,
) -> torch.Tensor | None:
    try:
        from plas import sort_with_plas
    except Exception:
        return None

    point_count = features.shape[0]
    shuffled_indices = torch.randperm(point_count, device=features.device) if shuffle else torch.arange(point_count, device=features.device)
    shuffled_features = features[shuffled_indices]
    side = max(1, int(math.ceil(math.sqrt(point_count))))
    padded_count = side * side
    if padded_count > point_count:
        filler = shuffled_features.mean(dim=0, keepdim=True).repeat(padded_count - point_count, 1)
        shuffled_features = torch.cat([shuffled_features, filler], dim=0)
    grid = shuffled_features.reshape(side, side, -1).permute(2, 0, 1).contiguous()
    _, sorted_indices = sort_with_plas(grid, improvement_break=improvement_break, verbose=verbose)
    sorted_indices = sorted_indices.reshape(-1)
    real_indices = sorted_indices[sorted_indices < point_count]
    if real_indices.numel() < point_count:
        missing_mask = torch.ones((point_count,), dtype=torch.bool, device=features.device)
        missing_mask[real_indices] = False
        missing = torch.arange(point_count, device=features.device)[missing_mask]
        real_indices = torch.cat([real_indices, missing], dim=0)
    return shuffled_indices[real_indices]


def _self_organizing_permutation(
    tensors: dict[str, torch.Tensor] | torch.nn.ParameterDict,
    config: SelfOrganizingCompressionConfig,
    *,
    verbose: bool = False,
) -> tuple[torch.Tensor | None, dict[str, object]]:
    point_count = int(len(tensors["means"]))
    metadata: dict[str, object] = {
        "applied": False,
        "method": None,
        "reason": None,
        "point_count": point_count,
        "grid_shape": _self_organized_grid_shape(point_count),
    }
    if point_count < config.min_points:
        metadata["reason"] = "point_count_below_threshold"
        return None, metadata
    features = _build_self_organizing_feature_matrix(
        tensors,
        config.sort_weights,
        normalize=config.normalize,
        activated=config.activated,
    )
    permutation: torch.Tensor | None = None
    preferred_method = config.method if config.method in {"plas", "pca"} else "auto"
    if preferred_method in {"auto", "plas"}:
        permutation = _plas_permutation(
            features,
            shuffle=config.shuffle,
            improvement_break=config.improvement_break,
            verbose=verbose,
        )
        if permutation is not None:
            metadata["method"] = "plas"
    if permutation is None:
        permutation = _pca_snake_permutation(features, shuffle=config.shuffle)
        metadata["method"] = "pca_snake"
    identity = torch.arange(point_count, device=permutation.device)
    metadata["changed_fraction"] = float((permutation != identity).to(torch.float32).mean().item())
    metadata["applied"] = True
    return permutation, metadata


def _reorder_tensor_payload(
    tensors: dict[str, torch.Tensor],
    permutation: torch.Tensor,
) -> dict[str, torch.Tensor]:
    reordered: dict[str, torch.Tensor] = {}
    for name, value in tensors.items():
        order = permutation.to(device=value.device)
        reordered[name] = value.index_select(0, order).clone()
    return reordered


def _pack_self_organizing_grid(values: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    point_count, channels = values.shape
    height, width = _self_organized_grid_shape(point_count)
    padded_count = height * width
    if padded_count > point_count:
        filler = values.mean(dim=0, keepdim=True).repeat(padded_count - point_count, 1)
        packed = torch.cat([values, filler], dim=0)
        valid_mask = torch.cat(
            [
                torch.ones((point_count,), dtype=torch.bool, device=values.device),
                torch.zeros((padded_count - point_count,), dtype=torch.bool, device=values.device),
            ],
            dim=0,
        )
    else:
        packed = values
        valid_mask = torch.ones((point_count,), dtype=torch.bool, device=values.device)
    grid = packed.reshape(height, width, channels).permute(2, 0, 1).unsqueeze(0)
    mask = valid_mask.reshape(1, 1, height, width)
    return grid, mask


def _gaussian_kernel_1d(kernel_size: int, sigma: float, device: torch.device, dtype: torch.dtype) -> torch.Tensor:
    radius = kernel_size // 2
    coords = torch.arange(-radius, radius + 1, device=device, dtype=dtype)
    kernel = torch.exp(-(coords * coords) / max(2.0 * sigma * sigma, 1.0e-6))
    kernel = kernel / kernel.sum().clamp_min(1.0e-6)
    return kernel


def _blur_self_organizing_grid(grid: torch.Tensor, kernel_size: int, sigma: float) -> torch.Tensor:
    channels = grid.shape[1]
    kernel = _gaussian_kernel_1d(kernel_size, sigma, grid.device, grid.dtype)
    kernel_x = kernel.view(1, 1, 1, kernel_size).repeat(channels, 1, 1, 1)
    kernel_y = kernel.view(1, 1, kernel_size, 1).repeat(channels, 1, 1, 1)
    pad_x = kernel_size // 2
    pad_y = kernel_size // 2
    pad_mode_x = "reflect" if grid.shape[-1] > pad_x else "replicate"
    pad_mode_y = "reflect" if grid.shape[-2] > pad_y else "replicate"
    blurred = F.conv2d(F.pad(grid, (pad_x, pad_x, 0, 0), mode=pad_mode_x), kernel_x, groups=channels)
    blurred = F.conv2d(F.pad(blurred, (0, 0, pad_y, pad_y), mode=pad_mode_y), kernel_y, groups=channels)
    return blurred


def _self_organizing_smoothness_loss(
    tensors: dict[str, torch.Tensor] | torch.nn.ParameterDict,
    config: SelfOrganizingCompressionConfig,
) -> tuple[torch.Tensor, dict[str, float]]:
    reference = tensors["means"]
    zero = torch.zeros((), dtype=reference.dtype, device=reference.device)
    point_count = int(len(reference))
    diagnostics = {
        "point_count": float(point_count),
        "grid_height": float(_self_organized_grid_shape(point_count)[0]),
        "grid_width": float(_self_organized_grid_shape(point_count)[1]),
    }
    if point_count < config.min_points:
        diagnostics["skipped"] = 1.0
        return zero, diagnostics
    terms: list[torch.Tensor] = []
    for name, weight in config.smoothness_weights.items():
        if weight <= 0.0 or name not in tensors:
            continue
        flat = _flatten_self_organizing_attribute(tensors, name, activated=config.activated)
        if config.normalize:
            flat = _normalize_self_organizing_channels(flat)
        grid, mask = _pack_self_organizing_grid(flat)
        blurred = _blur_self_organizing_grid(grid, config.blur_kernel_size, config.blur_sigma)
        if config.loss_fn == "mse":
            diff = (blurred - grid).pow(2)
        else:
            diff = F.smooth_l1_loss(blurred, grid, reduction="none")
        weighted_mask = mask.expand(-1, diff.shape[1], -1, -1).to(diff.dtype)
        term = (diff * weighted_mask).sum() / weighted_mask.sum().clamp_min(1.0)
        diagnostics[f"{name}_loss"] = float(term.detach().item())
        terms.append(term * float(weight))
    if not terms:
        diagnostics["skipped"] = 1.0
        return zero, diagnostics
    diagnostics["skipped"] = 0.0
    return torch.stack(terms).mean(), diagnostics


def _crop_export_splats(splats_payload: dict[str, torch.Tensor], n_crop: int) -> dict[str, torch.Tensor]:
    if n_crop <= 0:
        return {key: value.clone() for key, value in splats_payload.items()}
    keep_indices = torch.argsort(splats_payload["opacities"], descending=True)[:-n_crop]
    return {key: value[keep_indices].clone() for key, value in splats_payload.items()}


def _prepare_mobile_optimized_splats(
    splats_payload: dict[str, torch.Tensor],
    config: SelfOrganizingCompressionConfig | None = None,
) -> tuple[dict[str, torch.Tensor], dict[str, object]]:
    metadata: dict[str, object] = {
        "sort_applied": False,
        "sort_skipped_reason": None,
        "cropped_count": 0,
        "sort_error": None,
        "point_count": int(len(splats_payload["means"])),
    }
    optimized = {key: value.clone() for key, value in splats_payload.items()}
    try:
        export_config = config or SelfOrganizingCompressionConfig(enabled=True, min_points=256)
        permutation, sort_meta = _self_organizing_permutation(optimized, export_config, verbose=False)
        if permutation is not None:
            optimized = _reorder_tensor_payload(optimized, permutation)
            metadata["sort_applied"] = True
            metadata["sort_method"] = sort_meta.get("method")
            metadata["sort_changed_fraction"] = sort_meta.get("changed_fraction")
            metadata["grid_shape"] = sort_meta.get("grid_shape")
        else:
            metadata["sort_skipped_reason"] = sort_meta.get("reason") or "disabled"
            metadata["grid_shape"] = sort_meta.get("grid_shape")
    except Exception as error:
        metadata["sort_error"] = str(error)

    metadata["optimized_point_count"] = int(len(optimized["means"]))
    grid_height, grid_width = _self_organized_grid_shape(len(optimized["means"]))
    metadata["grid_height"] = int(grid_height)
    metadata["grid_width"] = int(grid_width)
    metadata["grid_side"] = int(max(grid_height, grid_width))
    return optimized, metadata


def _load_spz_backend():
    global _SPZ_BACKEND
    if _SPZ_BACKEND is not None:
        return _SPZ_BACKEND

    try:
        import spz as spz_backend

        if hasattr(spz_backend, "GaussianSplat"):
            _SPZ_BACKEND = spz_backend
            return _SPZ_BACKEND
    except Exception:
        pass

    package_spec = importlib.util.find_spec("spz")
    if package_spec is None or not package_spec.submodule_search_locations:
        raise ImportError("The SPZ package is not installed.")

    package_dir = Path(next(iter(package_spec.submodule_search_locations)))
    backend_path = next(package_dir.glob("spz*.pyd"), None)
    if backend_path is None:
        raise ImportError("The installed SPZ package does not expose a backend module.")

    backend_spec = importlib.util.spec_from_file_location("spz", backend_path)
    if backend_spec is None or backend_spec.loader is None:
        raise ImportError(f"Unable to load SPZ backend from {backend_path}.")

    previous_module = sys.modules.get("spz")
    spz_backend = importlib.util.module_from_spec(backend_spec)
    sys.modules["spz"] = spz_backend
    try:
        backend_spec.loader.exec_module(spz_backend)
    except Exception:
        if previous_module is None:
            sys.modules.pop("spz", None)
        else:
            sys.modules["spz"] = previous_module
        raise

    _SPZ_BACKEND = spz_backend
    return _SPZ_BACKEND


def _write_spz_from_splats(
    destination: Path,
    splats_payload: dict[str, torch.Tensor],
    *,
    antialiased: bool,
) -> Path:
    spz_backend = _load_spz_backend()
    sh_degree = _sh_degree_from_shn_dim(int(splats_payload["shN"].shape[1]))
    spherical_harmonics = (
        splats_payload["shN"].reshape(len(splats_payload["shN"]), -1).numpy()
        if sh_degree > 0
        else None
    )
    gaussian_splat = spz_backend.GaussianSplat(
        positions=splats_payload["means"].numpy().astype(np.float32, copy=False),
        scales=splats_payload["scales"].numpy().astype(np.float32, copy=False),
        rotations=splats_payload["quats"].numpy().astype(np.float32, copy=False),
        alphas=splats_payload["opacities"].numpy().astype(np.float32, copy=False),
        colors=splats_payload["sh0"].squeeze(1).numpy().astype(np.float32, copy=False),
        sh_degree=sh_degree,
        spherical_harmonics=spherical_harmonics,
        antialiased=antialiased,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    coordinate_system = getattr(spz_backend.CoordinateSystem, "RDF", spz_backend.CoordinateSystem.UNSPECIFIED)
    gaussian_splat.save(str(destination), from_coordinate_system=coordinate_system)
    return destination


def _compute_relocation_torch(
    opacities: torch.Tensor,
    scales: torch.Tensor,
    ratios: torch.Tensor,
    binoms: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    n = opacities.shape[0]
    if n == 0:
        return opacities, scales

    opacities = opacities.contiguous()
    scales = scales.contiguous()
    ratios = ratios.to(dtype=torch.int64, device=opacities.device).clamp_min(1)
    binoms = binoms.to(dtype=opacities.dtype, device=opacities.device)

    new_opacities = 1.0 - torch.pow(1.0 - opacities, 1.0 / ratios.to(opacities.dtype))
    max_ratio = int(ratios.max().item())
    denom_sum = torch.zeros_like(opacities)
    for i in range(1, max_ratio + 1):
        active = ratios >= i
        if not torch.any(active):
            continue
        active_indices = torch.nonzero(active, as_tuple=False).squeeze(-1)
        active_opacity = new_opacities[active_indices]
        partial = torch.zeros_like(active_opacity)
        for k in range(i):
            coeff = binoms[i - 1, k]
            sign = -1.0 if (k % 2) else 1.0
            partial = partial + (coeff * sign / math.sqrt(float(k + 1))) * torch.pow(active_opacity, k + 1)
        denom_sum[active_indices] = denom_sum[active_indices] + partial

    denom_sum = denom_sum.clamp_min(1e-8)
    coeff = (opacities / denom_sum).unsqueeze(-1)
    new_scales = coeff * scales
    return new_opacities, new_scales


def _multinomial_sample(weights: torch.Tensor, n: int, replacement: bool = True) -> torch.Tensor:
    num_elements = weights.size(0)
    if num_elements <= 2**24:
        return torch.multinomial(weights, n, replacement=replacement)
    normalized = weights / weights.sum().clamp_min(1e-12)
    sampled = np.random.choice(
        num_elements,
        size=n,
        replace=replacement,
        p=normalized.detach().cpu().numpy(),
    )
    return torch.from_numpy(sampled).to(weights.device)


@torch.no_grad()
def _update_param_with_optimizer(
    param_fn,
    optimizer_fn,
    params: dict[str, torch.nn.Parameter] | torch.nn.ParameterDict,
    optimizers: dict[str, torch.optim.Optimizer],
    names: list[str] | None = None,
) -> None:
    target_names = names or list(params.keys())
    for name in target_names:
        param = params[name]
        new_param = param_fn(name, param)
        params[name] = new_param
        if name not in optimizers:
            continue
        optimizer = optimizers[name]
        for index in range(len(optimizer.param_groups)):
            param_state = optimizer.state[param]
            del optimizer.state[param]
            for key, value in list(param_state.items()):
                if key != "step":
                    param_state[key] = optimizer_fn(key, value)
            optimizer.param_groups[index]["params"] = [new_param]
            optimizer.state[new_param] = param_state


@torch.no_grad()
def _reorder_splats_with_optimizer(
    splats: torch.nn.ParameterDict,
    optimizers: dict[str, torch.optim.Optimizer],
    state: dict[str, object],
    permutation: torch.Tensor,
) -> None:
    permutation = permutation.to(device=splats["means"].device, dtype=torch.long)
    point_count = int(len(permutation))

    def param_fn(_name: str, tensor: torch.Tensor) -> torch.nn.Parameter:
        updated = tensor.index_select(0, permutation).clone()
        return torch.nn.Parameter(updated, requires_grad=tensor.requires_grad)

    def optimizer_fn(_key: str, value: torch.Tensor) -> torch.Tensor:
        if isinstance(value, torch.Tensor) and value.ndim > 0 and value.shape[0] == point_count:
            return value.index_select(0, permutation).clone()
        return value.clone() if isinstance(value, torch.Tensor) else value

    _update_param_with_optimizer(param_fn, optimizer_fn, splats, optimizers)

    for key, value in list(state.items()):
        if isinstance(value, torch.Tensor) and value.ndim > 0 and value.shape[0] == point_count:
            state[key] = value.index_select(0, permutation).clone()


@torch.no_grad()
def _mcmc_relocate(
    params: dict[str, torch.nn.Parameter] | torch.nn.ParameterDict,
    optimizers: dict[str, torch.optim.Optimizer],
    mask: torch.Tensor,
    binoms: torch.Tensor,
    min_opacity: float,
) -> int:
    opacities = torch.sigmoid(params["opacities"])
    dead_indices = mask.nonzero(as_tuple=True)[0]
    alive_indices = (~mask).nonzero(as_tuple=True)[0]
    n_dead = int(dead_indices.numel())
    if n_dead == 0 or alive_indices.numel() == 0:
        return 0

    probs = opacities[alive_indices].flatten().clamp_min(1e-12)
    sampled_indices = alive_indices[_multinomial_sample(probs, n_dead, replacement=True)]
    ratios = torch.bincount(sampled_indices, minlength=opacities.shape[0])[sampled_indices] + 1
    new_opacities, new_scales = _compute_relocation_torch(
        opacities=opacities[sampled_indices],
        scales=torch.exp(params["scales"])[sampled_indices],
        ratios=ratios,
        binoms=binoms,
    )
    eps = torch.finfo(opacities.dtype).eps
    new_opacities = torch.clamp(new_opacities, min=min_opacity, max=1.0 - eps)

    def param_fn(name: str, tensor: torch.Tensor) -> torch.nn.Parameter:
        updated = tensor.clone()
        if name == "opacities":
            updated[sampled_indices] = torch.logit(new_opacities)
        elif name == "scales":
            updated[sampled_indices] = torch.log(new_scales)
        updated[dead_indices] = updated[sampled_indices]
        return torch.nn.Parameter(updated, requires_grad=tensor.requires_grad)

    def optimizer_fn(_key: str, value: torch.Tensor) -> torch.Tensor:
        updated = value.clone()
        updated[sampled_indices] = 0
        return updated

    _update_param_with_optimizer(param_fn, optimizer_fn, params, optimizers)
    return n_dead


@torch.no_grad()
def _mcmc_sample_add(
    params: dict[str, torch.nn.Parameter] | torch.nn.ParameterDict,
    optimizers: dict[str, torch.optim.Optimizer],
    n_new: int,
    binoms: torch.Tensor,
    min_opacity: float,
) -> int:
    if n_new <= 0:
        return 0

    opacities = torch.sigmoid(params["opacities"]).flatten().clamp_min(1e-12)
    sampled_indices = _multinomial_sample(opacities, n_new, replacement=True)
    ratios = torch.bincount(sampled_indices, minlength=opacities.shape[0])[sampled_indices] + 1
    new_opacities, new_scales = _compute_relocation_torch(
        opacities=opacities[sampled_indices],
        scales=torch.exp(params["scales"])[sampled_indices],
        ratios=ratios,
        binoms=binoms,
    )
    eps = torch.finfo(opacities.dtype).eps
    new_opacities = torch.clamp(new_opacities, min=min_opacity, max=1.0 - eps)

    def param_fn(name: str, tensor: torch.Tensor) -> torch.nn.Parameter:
        repeats = [1] * tensor.dim()
        if name == "means":
            addition = tensor[sampled_indices]
        elif name == "scales":
            addition = torch.log(new_scales)
        elif name == "opacities":
            addition = torch.logit(new_opacities)
        else:
            repeats[0] = 1
            addition = tensor[sampled_indices]
        return torch.nn.Parameter(torch.cat([tensor, addition], dim=0), requires_grad=tensor.requires_grad)

    def optimizer_fn(_key: str, value: torch.Tensor) -> torch.Tensor:
        zeros = torch.zeros((n_new, *value.shape[1:]), dtype=value.dtype, device=value.device)
        return torch.cat([value, zeros], dim=0)

    _update_param_with_optimizer(param_fn, optimizer_fn, params, optimizers)
    return n_new


class _TorchFallbackMCMCStrategy(Strategy):
    def __init__(
        self,
        *,
        cap_max: int = 1_000_000,
        noise_lr: float = 5e5,
        refine_start_iter: int = 500,
        refine_stop_iter: int = 25_000,
        refine_every: int = 100,
        min_opacity: float = 0.005,
        verbose: bool = False,
    ) -> None:
        self.cap_max = cap_max
        self.noise_lr = noise_lr
        self.refine_start_iter = refine_start_iter
        self.refine_stop_iter = refine_stop_iter
        self.refine_every = refine_every
        self.min_opacity = min_opacity
        self.verbose = verbose

    def initialize_state(self) -> dict[str, object]:
        n_max = 51
        binoms = torch.zeros((n_max, n_max), dtype=torch.float32)
        for n in range(n_max):
            for k in range(n + 1):
                binoms[n, k] = math.comb(n, k)
        return {"binoms": binoms}

    def check_sanity(
        self,
        params: dict[str, torch.nn.Parameter] | torch.nn.ParameterDict,
        optimizers: dict[str, torch.optim.Optimizer],
    ) -> None:
        super().check_sanity(params, optimizers)
        for key in ["means", "scales", "quats", "opacities"]:
            assert key in params, f"{key} is required in params but missing."

    def step_post_backward(
        self,
        params: dict[str, torch.nn.Parameter] | torch.nn.ParameterDict,
        optimizers: dict[str, torch.optim.Optimizer],
        state: dict[str, object],
        step: int,
        info: dict,
        lr: float,
    ) -> None:
        del info
        binoms = state["binoms"]
        assert isinstance(binoms, torch.Tensor)
        binoms = binoms.to(params["means"].device)
        state["binoms"] = binoms

        if step < self.refine_stop_iter and step > self.refine_start_iter and step % self.refine_every == 0:
            opacities = torch.sigmoid(params["opacities"].flatten())
            dead_mask = opacities <= self.min_opacity
            relocated = _mcmc_relocate(params, optimizers, dead_mask, binoms, self.min_opacity)
            current_count = len(params["means"])
            target_count = _mcmc_target_count(
                current_count,
                self.cap_max,
                step=step,
                refine_stop_iter=self.refine_stop_iter,
                refine_every=self.refine_every,
            )
            added = _mcmc_sample_add(params, optimizers, max(0, target_count - current_count), binoms, self.min_opacity)
            if self.verbose:
                print(f"Step {step}: relocated {relocated}, added {added}, total={len(params['means'])}")
            torch.cuda.empty_cache()

        inject_noise_to_position(params=params, optimizers=optimizers, state={}, scaler=lr * self.noise_lr)


def _ensure_mcmc_runtime(job: dict | None, device: torch.device) -> str:
    global _MCMC_RUNTIME_MODE
    if _MCMC_RUNTIME_MODE is not None:
        return _MCMC_RUNTIME_MODE

    import gsplat.relocation as relocation_module
    import gsplat.strategy.ops as strategy_ops_module

    try:
        opacities = torch.tensor([0.5], dtype=torch.float32, device=device)
        scales = torch.ones((1, 3), dtype=torch.float32, device=device)
        ratios = torch.ones((1,), dtype=torch.int32, device=device)
        binoms = torch.ones((2, 2), dtype=torch.float32, device=device)
        relocation_module.compute_relocation(opacities, scales, ratios, binoms)
        _MCMC_RUNTIME_MODE = "native"
    except Exception as error:
        relocation_module.compute_relocation = _compute_relocation_torch
        strategy_ops_module.compute_relocation = _compute_relocation_torch
        _MCMC_RUNTIME_MODE = "torch_fallback"
        if job is not None:
            _log_line(
                job,
                f"MCMC relocation backend fell back to pure PyTorch because native gsplat relocation is unavailable: {error}",
            )
    return _MCMC_RUNTIME_MODE


def _grayscale_image(image: torch.Tensor) -> torch.Tensor:
    if image.ndim != 4 or image.shape[-1] != 3:
        raise ValueError("Expected image tensor in [B, H, W, 3] format.")
    image_chw = image.permute(0, 3, 1, 2)
    weights = torch.tensor([0.299, 0.587, 0.114], dtype=image.dtype, device=image.device).view(1, 3, 1, 1)
    return (image_chw * weights).sum(dim=1, keepdim=True)


def _laplacian_edge_backbone(image: torch.Tensor) -> torch.Tensor:
    gray = _grayscale_image(image)
    median = torch.median(gray.reshape(gray.shape[0], -1), dim=1).values.view(-1, 1, 1, 1).clamp_min(1.0e-3)
    normalized = torch.clamp(gray / median, 0.0, 4.0)

    sobel_x = torch.tensor(
        [[[-1.0, 0.0, 1.0], [-2.0, 0.0, 2.0], [-1.0, 0.0, 1.0]]],
        dtype=image.dtype,
        device=image.device,
    ).unsqueeze(1)
    sobel_y = torch.tensor(
        [[[-1.0, -2.0, -1.0], [0.0, 0.0, 0.0], [1.0, 2.0, 1.0]]],
        dtype=image.dtype,
        device=image.device,
    ).unsqueeze(1)
    lap_kernel = torch.tensor(
        [[[0.0, 1.0, 0.0], [1.0, -4.0, 1.0], [0.0, 1.0, 0.0]]],
        dtype=image.dtype,
        device=image.device,
    ).unsqueeze(1)

    grad_x = F.conv2d(normalized, sobel_x, padding=1)
    grad_y = F.conv2d(normalized, sobel_y, padding=1)
    grad_mag = torch.sqrt((grad_x * grad_x) + (grad_y * grad_y) + 1.0e-8)
    lap_mag = torch.abs(F.conv2d(normalized, lap_kernel, padding=1))

    orientation = torch.rad2deg(torch.atan2(grad_y, grad_x))
    orientation = torch.remainder(orientation + 180.0, 180.0)
    bins = torch.round(orientation / 45.0).to(torch.int64) % 4

    padded = F.pad(grad_mag, (1, 1, 1, 1), mode="replicate")
    center = padded[:, :, 1:-1, 1:-1]
    east = padded[:, :, 1:-1, 2:]
    west = padded[:, :, 1:-1, :-2]
    north = padded[:, :, :-2, 1:-1]
    south = padded[:, :, 2:, 1:-1]
    northeast = padded[:, :, :-2, 2:]
    southwest = padded[:, :, 2:, :-2]
    northwest = padded[:, :, :-2, :-2]
    southeast = padded[:, :, 2:, 2:]

    keep_0 = (center >= east) & (center >= west)
    keep_45 = (center >= northeast) & (center >= southwest)
    keep_90 = (center >= north) & (center >= south)
    keep_135 = (center >= northwest) & (center >= southeast)
    nms_mask = torch.where(
        bins == 0,
        keep_0,
        torch.where(bins == 1, keep_45, torch.where(bins == 2, keep_90, keep_135)),
    )

    lap_norm = lap_mag / lap_mag.amax(dim=(-2, -1), keepdim=True).clamp_min(1.0e-6)
    backbone = grad_mag * (0.35 + (0.65 * lap_norm))
    backbone = backbone * nms_mask.to(backbone.dtype)
    backbone = backbone / backbone.amax(dim=(-2, -1), keepdim=True).clamp_min(1.0e-6)
    return backbone[:, 0]


def _sample_image_space_values(
    means2d: torch.Tensor,
    radii: torch.Tensor,
    image_map: torch.Tensor,
    width: int,
    height: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    if means2d.ndim != 3:
        raise ValueError("Expected means2d in [C, N, 2] format.")
    if image_map.ndim == 2:
        image_map = image_map.unsqueeze(0)

    visible = radii > 0.0
    if visible.ndim >= 3:
        visible = visible.all(dim=-1)
    elif visible.ndim != 2:
        raise ValueError("Expected radii in [C, N] or [C, N, K] format.")
    if not torch.any(visible):
        return torch.empty((0,), dtype=torch.int64, device=means2d.device), torch.empty((0,), dtype=means2d.dtype, device=means2d.device)

    selected = torch.nonzero(visible, as_tuple=False)
    camera_ids = selected[:, 0]
    gaussian_ids = selected[:, 1]
    coords = means2d[camera_ids, gaussian_ids]
    x = coords[:, 0].round().to(torch.int64).clamp(0, max(0, width - 1))
    y = coords[:, 1].round().to(torch.int64).clamp(0, max(0, height - 1))
    values = image_map[camera_ids, y, x]
    return gaussian_ids, values


def _normalize_positive_median(values: torch.Tensor) -> torch.Tensor:
    sanitized = torch.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0).clamp_min(0.0)
    positive = sanitized[sanitized > 0.0]
    if positive.numel() == 0:
        return torch.zeros_like(sanitized)
    median = positive.median().clamp_min(1.0e-12)
    return sanitized / median


@torch.no_grad()
def _hybrid_las_split(
    params: dict[str, torch.nn.Parameter] | torch.nn.ParameterDict,
    optimizers: dict[str, torch.optim.Optimizer],
    state: dict[str, object],
    mask: torch.Tensor,
    long_axis_mask: torch.Tensor,
    *,
    primary_shrink: float,
    secondary_shrink: float,
    opacity_factor: float,
    offset_scale: float,
) -> tuple[int, int]:
    sel = torch.where(mask)[0]
    if sel.numel() == 0:
        return 0, 0
    rest = torch.where(~mask)[0]
    device = params["means"].device
    eps = torch.finfo(torch.float32).eps

    scales = torch.exp(params["scales"][sel])
    quats = F.normalize(params["quats"][sel], dim=-1)
    rotmats = normalized_quat_to_rotmat(quats)
    local_long_axis = long_axis_mask[sel]
    n_long_axis = int(local_long_axis.sum().item())
    n_covariance = int(sel.numel() - n_long_axis)

    covariance_offsets = torch.einsum(
        "nij,nj,bnj->bni",
        rotmats,
        scales,
        torch.randn(2, len(scales), 3, device=device),
    )
    principal_axis = scales.argmax(dim=-1)
    axis_vectors = rotmats[torch.arange(len(sel), device=device), :, principal_axis]
    axis_lengths = scales[torch.arange(len(sel), device=device), principal_axis]
    long_axis_offsets = axis_vectors * (axis_lengths * offset_scale).unsqueeze(-1)

    long_axis_scales = scales / secondary_shrink
    long_axis_scales[torch.arange(len(sel), device=device), principal_axis] = (
        scales[torch.arange(len(sel), device=device), principal_axis] / primary_shrink
    )
    long_axis_scales = long_axis_scales.clamp_min(1.0e-6)
    covariance_scales = (scales / 1.6).clamp_min(1.0e-6)

    child_scales = covariance_scales.clone()
    if n_long_axis > 0:
        child_scales[local_long_axis] = long_axis_scales[local_long_axis]

    child_opacity = torch.sigmoid(params["opacities"][sel]).clamp(min=1.0e-4, max=1.0 - eps)
    if n_long_axis > 0:
        child_opacity[local_long_axis] = torch.clamp(
            child_opacity[local_long_axis] * opacity_factor,
            min=1.0e-4,
            max=1.0 - eps,
        )

    means_first = params["means"][sel] + covariance_offsets[0]
    means_second = params["means"][sel] + covariance_offsets[1]
    if n_long_axis > 0:
        means_first[local_long_axis] = params["means"][sel][local_long_axis] + long_axis_offsets[local_long_axis]
        means_second[local_long_axis] = params["means"][sel][local_long_axis] - long_axis_offsets[local_long_axis]

    def param_fn(name: str, tensor: torch.Tensor) -> torch.nn.Parameter:
        if name == "means":
            split_values = torch.cat([means_first, means_second], dim=0)
        elif name == "scales":
            split_values = torch.log(child_scales).repeat(2, 1)
        elif name == "opacities":
            split_values = torch.logit(child_opacity).repeat(2)
        else:
            repeats = [2] + [1] * (tensor.dim() - 1)
            split_values = tensor[sel].repeat(repeats)
        updated = torch.cat([tensor[rest], split_values], dim=0)
        return torch.nn.Parameter(updated, requires_grad=tensor.requires_grad)

    def optimizer_fn(_key: str, value: torch.Tensor) -> torch.Tensor:
        zeros = torch.zeros((2 * len(sel), *value.shape[1:]), dtype=value.dtype, device=value.device)
        return torch.cat([value[rest], zeros], dim=0)

    _update_param_with_optimizer(param_fn, optimizer_fn, params, optimizers)
    for key, value in list(state.items()):
        if isinstance(value, torch.Tensor):
            zeros = torch.zeros((2 * len(sel), *value.shape[1:]), dtype=value.dtype, device=value.device)
            state[key] = torch.cat([value[rest], zeros], dim=0)
    return n_long_axis, n_covariance


class _EdgeAwareLASStrategy(DefaultStrategy):
    def __init__(
        self,
        *,
        edge_threshold: float = 0.12,
        warmup_events: int = 0,
        candidate_factor: int = 4,
        edge_score_weight: float = 0.25,
        las_primary_shrink: float = 2.0,
        las_secondary_shrink: float = 1.0 / 0.85,
        las_opacity_factor: float = 0.6,
        las_offset_scale: float = 0.5,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.edge_threshold = edge_threshold
        self.warmup_events = warmup_events
        self.candidate_factor = max(1, int(candidate_factor))
        self.edge_score_weight = max(0.0, float(edge_score_weight))
        self.las_primary_shrink = las_primary_shrink
        self.las_secondary_shrink = las_secondary_shrink
        self.las_opacity_factor = las_opacity_factor
        self.las_offset_scale = las_offset_scale

    def initialize_state(self, scene_scale: float = 1.0) -> dict[str, object]:
        state = super().initialize_state(scene_scale=scene_scale)
        state["edge_score"] = None
        state["error_score_max"] = None
        state["refine_events"] = 0
        state["current_budget_cap"] = None
        state["last_refine_stats"] = None
        state["last_refine_step"] = -1
        state["last_growth_step"] = -1
        state["growth_stall_events"] = 0
        state["split_starvation_events"] = 0
        state["smoothed_growth_fill"] = 1.0
        state["densification_mode"] = "gradient"
        state["last_controller_interval"] = int(self.refine_every)
        state["last_growth_quota"] = 0
        state["last_budget_deficit"] = 0
        state["last_split_ratio"] = 0.0
        state["last_long_axis_split"] = 0
        state["last_covariance_split"] = 0
        state["last_pruned_requested"] = 0
        state["last_pruned_applied"] = 0
        state["last_prune_limited"] = False
        state["adaptive_long_axis_bias"] = 0.0
        return state

    def step_post_backward(
        self,
        params: dict[str, torch.nn.Parameter] | torch.nn.ParameterDict,
        optimizers: dict[str, torch.optim.Optimizer],
        state: dict[str, object],
        step: int,
        info: dict[str, object],
        packed: bool = False,
        gradientless: bool = False,
    ) -> None:
        if step >= self.refine_stop_iter:
            return

        state["densification_mode"] = "projection_fallback" if gradientless else "gradient"
        self._update_state(params, state, info, packed=packed, gradientless=gradientless)
        controller = self._growth_controller_plan(params, state, step)
        if bool(controller["should_refine"]):
            before_count = int(len(params["means"]))
            target_growth = int(controller["target_growth"])
            n_dupli, n_split = self._grow_gs(
                params,
                optimizers,
                state,
                step,
                target_growth=target_growth,
            )
            n_prune = self._prune_gs(params, optimizers, state, step)
            after_count = int(len(params["means"]))
            net_growth = max(0, after_count - before_count)
            fill_ratio = float(net_growth) / float(max(1, target_growth))
            previous_fill_value = state.get("smoothed_growth_fill")
            previous_fill = float(previous_fill_value) if previous_fill_value is not None else 1.0
            state["smoothed_growth_fill"] = (0.65 * previous_fill) + (0.35 * fill_ratio)
            split_ratio = float(n_split) / float(max(1, n_dupli + n_split))
            state["last_split_ratio"] = split_ratio
            if target_growth > 0 and fill_ratio < 0.35:
                state["growth_stall_events"] = int(state.get("growth_stall_events") or 0) + 1
            else:
                state["growth_stall_events"] = 0
            if str(state.get("densification_mode") or "gradient") == "projection_fallback" and split_ratio < 0.05:
                state["split_starvation_events"] = int(state.get("split_starvation_events") or 0) + 1
            else:
                state["split_starvation_events"] = 0
            state["last_refine_step"] = int(step)
            if net_growth > 0:
                state["last_growth_step"] = int(step)
            state["last_refine_stats"] = {
                "step": int(step),
                "before": before_count,
                "after": after_count,
                "duplicated": int(n_dupli),
                "split": int(n_split),
                "pruned": int(n_prune),
                "budget_cap": int(state.get("current_budget_cap") or len(params["means"])),
                "selected": int(state.get("last_selected_count") or 0),
                "target_selected": int(state.get("last_target_selected") or 0),
                "growth_quota": int(controller["target_growth"]),
                "growth_fill": float(fill_ratio),
                "split_ratio": float(split_ratio),
                "split_long_axis": int(state.get("last_long_axis_split") or 0),
                "split_covariance": int(state.get("last_covariance_split") or 0),
                "interval": int(controller["interval"]),
                "deficit": int(controller["deficit"]),
                "active": int(state.get("last_active_count") or 0),
                "error_nonzero": int(state.get("last_error_nonzero") or 0),
                "edge_nonzero": int(state.get("last_edge_nonzero") or 0),
                "mode": str(state.get("densification_mode") or "gradient"),
                "pruned_requested": int(state.get("last_pruned_requested") or 0),
                "prune_limited": bool(state.get("last_prune_limited") or False),
            }
            if not _splat_parameters_are_finite(params):
                raise RuntimeError("LAS densification produced non-finite Gaussian parameters.")
            for key in ("grad2d", "count", "radii"):
                value = state.get(key)
                if isinstance(value, torch.Tensor):
                    value.zero_()
            state["edge_score"] = torch.zeros(len(params["means"]), device=params["means"].device)
            state["error_score_max"] = torch.zeros(len(params["means"]), device=params["means"].device)
            if params["means"].is_cuda:
                torch.cuda.synchronize(params["means"].device)
            torch.cuda.empty_cache()

        if step > 0 and step % self.reset_every == 0:
            reset_opa(
                params=params,
                optimizers=optimizers,
                state=state,
                value=self.prune_opa * 2.0,
            )

    @torch.no_grad()
    def _prune_gs(
        self,
        params: dict[str, torch.nn.Parameter] | torch.nn.ParameterDict,
        optimizers: dict[str, torch.optim.Optimizer],
        state: dict[str, object],
        step: int,
    ) -> int:
        opacities = torch.sigmoid(params["opacities"].flatten())
        scales = torch.exp(params["scales"]).max(dim=-1).values
        is_prune = opacities < self.prune_opa
        is_too_big = torch.zeros_like(is_prune)
        if step > self.reset_every:
            is_too_big = scales > (self.prune_scale3d * state["scene_scale"])
            if step < self.refine_scale2d_stop_iter and isinstance(state.get("radii"), torch.Tensor):
                is_too_big |= state["radii"] > self.prune_scale2d
            is_prune = is_prune | is_too_big

        requested_prune = int(is_prune.sum().item())
        state["last_pruned_requested"] = int(requested_prune)
        state["last_prune_limited"] = False
        state["last_pruned_applied"] = 0
        if requested_prune <= 0:
            return 0

        mode = str(state.get("densification_mode") or "gradient")
        budget_cap = int(state.get("current_budget_cap") or len(params["means"]))
        deficit = max(0, budget_cap - int(len(params["means"])))
        growth_quota = max(0, int(state.get("last_target_selected") or 0))
        prune_cap: int | None = None
        if mode == "projection_fallback" and deficit > 0 and growth_quota > 0:
            min_net_growth = max(1, int(math.ceil(growth_quota * 0.30)))
            prune_cap = max(0, growth_quota - min_net_growth)
            prune_cap = min(requested_prune, prune_cap)

        if prune_cap is not None and requested_prune > prune_cap:
            prune_scores = torch.full_like(opacities, fill_value=-1.0, dtype=torch.float32)
            low_opacity_score = (self.prune_opa - opacities).clamp_min(0.0)
            prune_scores = torch.where(is_prune, low_opacity_score, prune_scores)
            if torch.any(is_too_big):
                oversize_score = ((scales / max(self.prune_scale3d * state["scene_scale"], 1.0e-6)) - 1.0).clamp_min(0.0)
                prune_scores = torch.where(is_too_big, torch.maximum(prune_scores, oversize_score + 1.0), prune_scores)
            if prune_cap <= 0:
                state["last_prune_limited"] = True
                state["last_pruned_applied"] = 0
                return 0
            top_indices = torch.topk(prune_scores, k=prune_cap, largest=True).indices
            limited_mask = torch.zeros_like(is_prune)
            limited_mask[top_indices] = True
            is_prune = limited_mask
            state["last_prune_limited"] = True

        n_prune = int(is_prune.sum().item())
        if n_prune > 0:
            remove(params=params, optimizers=optimizers, state=state, mask=is_prune)
        state["last_pruned_applied"] = int(n_prune)
        return n_prune

    def _ensure_tracking_buffers(
        self,
        params: dict[str, torch.nn.Parameter] | torch.nn.ParameterDict,
        state: dict[str, object],
        *,
        device: torch.device,
    ) -> None:
        gaussian_count = int(len(params["means"]))
        if state.get("grad2d") is None or not isinstance(state.get("grad2d"), torch.Tensor) or state["grad2d"].numel() != gaussian_count:
            state["grad2d"] = torch.zeros(gaussian_count, dtype=torch.float32, device=device)
        if state.get("count") is None or not isinstance(state.get("count"), torch.Tensor) or state["count"].numel() != gaussian_count:
            state["count"] = torch.zeros(gaussian_count, dtype=torch.float32, device=device)
        if self.refine_scale2d_stop_iter > 0 and (
            state.get("radii") is None or not isinstance(state.get("radii"), torch.Tensor) or state["radii"].numel() != gaussian_count
        ):
            state["radii"] = torch.zeros(gaussian_count, dtype=torch.float32, device=device)

    def _growth_controller_plan(
        self,
        params: dict[str, torch.nn.Parameter] | torch.nn.ParameterDict,
        state: dict[str, object],
        step: int,
    ) -> dict[str, float | int | bool]:
        budget_cap = int(state.get("current_budget_cap") or len(params["means"]))
        current_count = int(len(params["means"]))
        deficit = max(0, budget_cap - current_count)
        base_interval = max(1, int(self.refine_every))
        min_interval = max(10, min(base_interval, max(1, base_interval // 4)))
        max_interval = max(base_interval, min(int(self.reset_every), base_interval * 2))
        stall_events_value = state.get("growth_stall_events")
        stall_events = max(0, int(stall_events_value) if stall_events_value is not None else 0)
        smoothed_fill_value = state.get("smoothed_growth_fill")
        smoothed_fill = float(smoothed_fill_value) if smoothed_fill_value is not None else 1.0
        deficit_ratio = float(deficit) / float(max(1, budget_cap))
        urgency = 1.0 + (4.0 * deficit_ratio) + min(2.0, 0.75 * stall_events)
        if smoothed_fill < 0.5:
            urgency += (0.5 - smoothed_fill) * 2.0
        interval = int(round(base_interval / max(1.0, urgency)))
        interval = max(min_interval, min(max_interval, interval))

        last_refine_step_value = state.get("last_refine_step")
        last_refine_step = int(last_refine_step_value) if last_refine_step_value is not None else -1
        if last_refine_step < 0:
            steps_since_refine = step - self.refine_start_iter + interval
        else:
            steps_since_refine = step - last_refine_step

        remaining_steps = max(1, self.refine_stop_iter - step)
        min_growth = max(64, current_count // 256)
        max_growth = max(min_growth, int(current_count * (0.10 + (0.15 * deficit_ratio))))
        if stall_events > 0:
            max_growth = int(max_growth * min(2.0, 1.0 + (0.30 * stall_events)))
        burst_fraction = 0.10 + (0.20 * deficit_ratio) + min(0.20, 0.05 * stall_events)
        target_growth = min(deficit, max(min_growth, min(max_growth, int(math.ceil(deficit * burst_fraction)))))
        remaining_actions = max(1, int(math.ceil(remaining_steps / float(max(1, interval)))))
        target_growth = min(deficit, max(target_growth, int(math.ceil(deficit / float(remaining_actions)))))
        should_refine = (
            deficit > 0
            and step >= self.refine_start_iter
            and step < self.refine_stop_iter
            and step % self.reset_every >= self.pause_refine_after_reset
            and steps_since_refine >= interval
        )
        state["last_controller_interval"] = int(interval)
        state["last_growth_quota"] = int(target_growth)
        state["last_budget_deficit"] = int(deficit)
        return {
            "should_refine": bool(should_refine),
            "target_growth": int(target_growth),
            "interval": int(interval),
            "deficit": int(deficit),
            "deficit_ratio": float(deficit_ratio),
        }

    @torch.no_grad()
    def _force_duplicate_growth(
        self,
        params: dict[str, torch.nn.Parameter] | torch.nn.ParameterDict,
        optimizers: dict[str, torch.optim.Optimizer],
        state: dict[str, object],
        *,
        active_mask: torch.Tensor,
        is_small: torch.Tensor,
        normalized_error: torch.Tensor,
        normalized_edge: torch.Tensor,
        budget_for_alloc: int,
    ) -> int:
        candidate_mask = active_mask & is_small
        if int(candidate_mask.sum().item()) <= 0:
            candidate_mask = active_mask
        candidate_count = int(candidate_mask.sum().item())
        if candidate_count <= 0 or budget_for_alloc <= 0:
            return 0

        current_count = int(len(params["means"]))
        force_budget = min(candidate_count, budget_for_alloc, max(2048, current_count // 3))
        if force_budget <= 0:
            return 0

        opacity_scores = torch.sigmoid(params["opacities"].flatten()).clamp_min(1.0e-6)
        fallback_scores = (normalized_error + normalized_edge + opacity_scores).masked_fill(~candidate_mask, -1.0)
        selected = torch.topk(fallback_scores, k=force_budget, largest=True).indices
        selected_mask = torch.zeros_like(candidate_mask)
        selected_mask[selected] = True
        duplicate(params=params, optimizers=optimizers, state=state, mask=selected_mask)
        return int(force_budget)

    @torch.no_grad()
    def _projection_fallback_split_mask(
        self,
        *,
        selected_mask: torch.Tensor,
        sampling_scores: torch.Tensor,
        max_scales: torch.Tensor,
        min_scales: torch.Tensor,
        state: dict[str, object],
        normalized_error: torch.Tensor,
        normalized_edge: torch.Tensor,
    ) -> torch.Tensor:
        selected_indices = torch.where(selected_mask)[0]
        if selected_indices.numel() <= 1:
            return torch.zeros_like(selected_mask)

        radii_state = state.get("radii")
        if isinstance(radii_state, torch.Tensor) and radii_state.numel() == max_scales.numel():
            normalized_radii = _normalize_positive_median(radii_state)
        else:
            normalized_radii = torch.zeros_like(max_scales)

        anisotropy = max_scales / min_scales.clamp_min(1.0e-6)
        isotropy_bonus = 1.0 / anisotropy.clamp_min(1.0)
        split_priority = (
            sampling_scores
            + (0.35 * normalized_error)
            + (0.45 * normalized_edge)
            + (0.90 * _normalize_positive_median(max_scales))
            + (1.15 * normalized_radii)
            + (0.20 * isotropy_bonus)
        )
        split_priority = split_priority.masked_fill(~selected_mask, -1.0)

        selected_radii = normalized_radii[selected_indices]
        selected_scales = max_scales[selected_indices]
        selected_anisotropy = anisotropy[selected_indices]
        radii_threshold = selected_radii.median() if selected_radii.numel() > 0 else torch.tensor(0.0, device=max_scales.device)
        scale_threshold = selected_scales.median() if selected_scales.numel() > 0 else torch.tensor(0.0, device=max_scales.device)
        split_candidates = selected_mask & (
            (normalized_radii >= radii_threshold)
            | (max_scales >= scale_threshold)
            | (anisotropy <= max(1.35, float(selected_anisotropy.median().item()) if selected_anisotropy.numel() > 0 else 1.35))
        )
        if int(split_candidates.sum().item()) <= 0:
            split_candidates = selected_mask
        split_priority = split_priority.masked_fill(~split_candidates, -1.0)

        split_starvation = max(0, int(state.get("split_starvation_events") or 0))
        base_ratio = 0.22
        starvation_bonus = min(0.33, 0.06 * split_starvation)
        isotropy_bonus_ratio = 0.10 if float(selected_anisotropy.median().item()) <= 1.20 else 0.0
        target_ratio = min(0.65, base_ratio + starvation_bonus + isotropy_bonus_ratio)
        target_split = max(1, int(round(selected_indices.numel() * target_ratio)))
        selectable = int((split_priority > -0.5).sum().item())
        target_split = min(target_split, max(1, selectable))
        top_indices = torch.topk(split_priority, k=target_split, largest=True).indices
        split_mask = torch.zeros_like(selected_mask)
        split_mask[top_indices] = True
        return split_mask

    @torch.no_grad()
    def _long_axis_split_mask(
        self,
        *,
        selected_mask: torch.Tensor,
        max_scales: torch.Tensor,
        min_scales: torch.Tensor,
        state: dict[str, object],
    ) -> torch.Tensor:
        selected_indices = torch.where(selected_mask)[0]
        if selected_indices.numel() == 0:
            return torch.zeros_like(selected_mask)

        anisotropy = max_scales / min_scales.clamp_min(1.0e-6)
        selected_anisotropy = anisotropy[selected_indices]
        if selected_anisotropy.numel() == 0:
            return torch.zeros_like(selected_mask)

        anisotropy_baseline = float(selected_anisotropy.median().item())
        threshold_bias = float(state.get("adaptive_long_axis_bias") or 0.0)
        long_axis_threshold = min(3.40, max(1.55, (anisotropy_baseline * 1.35) + threshold_bias))
        long_axis_mask = selected_mask & (anisotropy >= long_axis_threshold)

        radii_state = state.get("radii")
        if isinstance(radii_state, torch.Tensor) and radii_state.numel() == max_scales.numel():
            normalized_radii = _normalize_positive_median(radii_state)
            selected_radii = normalized_radii[selected_indices]
            if selected_radii.numel() > 0:
                radii_threshold = float(selected_radii.median().item())
                secondary_threshold = max(1.35, long_axis_threshold * 0.85)
                long_axis_mask |= selected_mask & (normalized_radii >= radii_threshold) & (anisotropy >= secondary_threshold)

        return long_axis_mask

    def _update_state(
        self,
        params: dict[str, torch.nn.Parameter] | torch.nn.ParameterDict,
        state: dict[str, object],
        info: dict[str, object],
        packed: bool = False,
        gradientless: bool = False,
    ) -> None:
        edge_backbone = info.get("edge_backbone")
        means2d = info.get(self.key_for_gradient)
        radii = info.get("radii")
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        if packed or edge_backbone is None or means2d is None or radii is None or width <= 0 or height <= 0:
            return

        means2d = means2d.detach()
        radii = radii.detach()
        self._ensure_tracking_buffers(params, state, device=means2d.device)
        if gradientless:
            visible = radii > 0.0
            if visible.ndim >= 3:
                visible = visible.all(dim=-1)
            elif visible.ndim != 2:
                raise ValueError("Expected radii in [C, N] or [C, N, K] format.")
            if torch.any(visible):
                gaussian_ids = torch.where(visible)[1]
                counts = state["count"]
                assert isinstance(counts, torch.Tensor)
                counts.index_add_(0, gaussian_ids, torch.ones_like(gaussian_ids, dtype=torch.float32))
                if self.refine_scale2d_stop_iter > 0 and isinstance(state.get("radii"), torch.Tensor):
                    sampled_radii = radii[visible].max(dim=-1).values
                    normalized_radii = sampled_radii / float(max(width, height))
                    state["radii"][gaussian_ids] = torch.maximum(
                        state["radii"][gaussian_ids],
                        normalized_radii.to(state["radii"].dtype),
                    )
        else:
            super()._update_state(params, state, info, packed=packed)
        if state["edge_score"] is None or not isinstance(state["edge_score"], torch.Tensor) or state["edge_score"].numel() != len(params["means"]):
            state["edge_score"] = torch.zeros(len(params["means"]), device=means2d.device)
        if state.get("error_score_max") is None or not isinstance(state.get("error_score_max"), torch.Tensor) or state["error_score_max"].numel() != len(params["means"]):
            state["error_score_max"] = torch.zeros(len(params["means"]), device=means2d.device)
        gaussian_ids, values = _sample_image_space_values(means2d, radii, edge_backbone.detach(), width, height)
        if gaussian_ids.numel() > 0:
            edge_score = state["edge_score"]
            assert isinstance(edge_score, torch.Tensor)
            edge_score.index_add_(0, gaussian_ids, values.to(edge_score.dtype))
        error_backbone = info.get("error_backbone")
        if isinstance(error_backbone, torch.Tensor):
            error_ids, error_values = _sample_image_space_values(means2d, radii, error_backbone.detach(), width, height)
            if error_ids.numel() > 0:
                error_score_max = state["error_score_max"]
                assert isinstance(error_score_max, torch.Tensor)
                sampled_error = torch.zeros_like(error_score_max)
                sampled_error.scatter_reduce_(
                    0,
                    error_ids,
                    error_values.to(sampled_error.dtype).clamp_min(0.0),
                    reduce="amax",
                    include_self=False,
                )
                torch.maximum(error_score_max, sampled_error, out=error_score_max)

    @torch.no_grad()
    def _grow_gs(
        self,
        params: dict[str, torch.nn.Parameter] | torch.nn.ParameterDict,
        optimizers: dict[str, torch.optim.Optimizer],
        state: dict[str, object],
        step: int,
        *,
        target_growth: int | None = None,
    ) -> tuple[int, int]:
        count = state["count"]
        grad2d = torch.nan_to_num(state["grad2d"] / count.clamp_min(1), nan=0.0, posinf=0.0, neginf=0.0).clamp_min(0.0)
        edge_score = state["edge_score"]
        if not isinstance(edge_score, torch.Tensor):
            edge_score = torch.zeros_like(grad2d)
            state["edge_score"] = edge_score
        edge_score = torch.nan_to_num(edge_score / count.clamp_min(1), nan=0.0, posinf=0.0, neginf=0.0).clamp_min(0.0)
        error_score_max = state.get("error_score_max")
        if not isinstance(error_score_max, torch.Tensor):
            error_score_max = grad2d.clone()
            state["error_score_max"] = error_score_max
        error_score_max = torch.nan_to_num(error_score_max, nan=0.0, posinf=0.0, neginf=0.0).clamp_min(0.0)
        warmup = int(state.get("refine_events") or 0) < self.warmup_events
        budget_cap = int(state.get("current_budget_cap") or len(params["means"]))
        current_count = int(len(params["means"]))
        budget_for_alloc = max(0, budget_cap - current_count)
        growth_quota = budget_for_alloc if target_growth is None else max(0, min(budget_for_alloc, int(target_growth)))
        if growth_quota <= 0:
            state["refine_events"] = int(state.get("refine_events") or 0) + 1
            return 0, 0

        scale_components = torch.exp(params["scales"])
        scales = scale_components.max(dim=-1).values
        min_scales = scale_components.min(dim=-1).values
        opacities = torch.sigmoid(params["opacities"].flatten())
        is_small = scales <= (self.grow_scale3d * state["scene_scale"])
        active_mask = torch.isfinite(scales) & torch.isfinite(opacities) & (opacities > max(self.prune_opa * 0.5, 1.0e-4))
        total_active = int(active_mask.sum().item())
        if total_active <= 0:
            state["refine_events"] = int(state.get("refine_events") or 0) + 1
            return 0, 0

        candidate_budget = min(total_active, max(growth_quota, growth_quota * self.candidate_factor))
        normalized_error = _normalize_positive_median(error_score_max)
        normalized_edge = _normalize_positive_median(edge_score)
        state["last_active_count"] = int(total_active)
        state["last_error_nonzero"] = int((normalized_error[active_mask] > 0.0).sum().item())
        state["last_edge_nonzero"] = int((normalized_edge[active_mask] > 0.0).sum().item())

        candidate_mask = active_mask.clone()
        if candidate_budget < total_active:
            active_error = normalized_error[active_mask]
            positive_error = active_error[active_error > 0.0]
            if positive_error.numel() > 0:
                threshold = torch.topk(active_error, k=candidate_budget, largest=True).values[-1]
                candidate_mask &= normalized_error >= threshold

        sampling_scores = normalized_error
        if not warmup:
            sampling_scores = normalized_error * ((normalized_edge * self.edge_score_weight) + 1.0)
        sampling_scores = sampling_scores.masked_fill(~candidate_mask, 0.0)

        target_selected = min(growth_quota, total_active)
        selectable = int((sampling_scores > 0.0).sum().item())
        if selectable < target_selected:
            edge_fallback = normalized_edge.masked_fill(~active_mask, 0.0)
            if warmup:
                edge_fallback = edge_fallback.masked_fill(edge_score <= self.edge_threshold, 0.0)
            edge_selectable = int((edge_fallback > 0.0).sum().item())
            if edge_selectable > selectable:
                sampling_scores = edge_fallback
                selectable = edge_selectable

        if selectable < target_selected:
            density_fallback = (normalized_error + normalized_edge + opacities).masked_fill(~active_mask, 0.0)
            density_selectable = int((density_fallback > 0.0).sum().item())
            if density_selectable > selectable:
                sampling_scores = density_fallback
                selectable = density_selectable

        if selectable < target_selected:
            sampling_scores = active_mask.to(dtype=grad2d.dtype)
            selectable = total_active

        if selectable <= 0:
            state["refine_events"] = int(state.get("refine_events") or 0) + 1
            return 0, 0

        selected_count = min(target_selected, selectable)
        state["last_target_selected"] = int(target_selected)
        if selected_count <= 0:
            if target_selected > 0:
                sampling_scores = active_mask.to(dtype=grad2d.dtype)
                selected_count = min(target_selected, total_active)
            else:
                state["refine_events"] = int(state.get("refine_events") or 0) + 1
                return 0, 0
        state["last_selected_count"] = int(selected_count)
        sampled_indices = _multinomial_sample(sampling_scores.clamp_min(1.0e-12), selected_count, replacement=False)
        selected_mask = torch.zeros_like(active_mask)
        selected_mask[sampled_indices] = True

        densification_mode = str(state.get("densification_mode") or "gradient")
        duplicate_mask = torch.zeros_like(selected_mask)
        if densification_mode == "projection_fallback":
            split_mask = self._projection_fallback_split_mask(
                selected_mask=selected_mask,
                sampling_scores=sampling_scores,
                max_scales=scales,
                min_scales=min_scales,
                state=state,
                normalized_error=normalized_error,
                normalized_edge=normalized_edge,
            )
            duplicate_mask = selected_mask & ~split_mask
        else:
            duplicate_mask = selected_mask & is_small
            split_mask = selected_mask & ~is_small
        long_axis_mask = self._long_axis_split_mask(
            selected_mask=split_mask,
            max_scales=scales,
            min_scales=min_scales,
            state=state,
        )
        n_split = int(split_mask.sum().item())
        n_dupli = int(duplicate_mask.sum().item())
        if n_dupli > 0:
            duplicate(params=params, optimizers=optimizers, state=state, mask=duplicate_mask)
        n_long_axis = 0
        n_covariance = 0
        if n_split > 0:
            split_mask_after_duplicate = split_mask
            long_axis_mask_after_duplicate = long_axis_mask
            if n_dupli > 0:
                zeros = torch.zeros(n_dupli, dtype=torch.bool, device=split_mask.device)
                split_mask_after_duplicate = torch.cat([split_mask, zeros])
                long_axis_mask_after_duplicate = torch.cat([long_axis_mask, zeros])
            n_long_axis, n_covariance = _hybrid_las_split(
                params,
                optimizers,
                state,
                split_mask_after_duplicate,
                long_axis_mask_after_duplicate,
                primary_shrink=self.las_primary_shrink,
                secondary_shrink=self.las_secondary_shrink,
                opacity_factor=self.las_opacity_factor,
                offset_scale=self.las_offset_scale,
            )
        state["last_long_axis_split"] = int(n_long_axis)
        state["last_covariance_split"] = int(n_covariance)
        state["refine_events"] = int(state.get("refine_events") or 0) + 1
        return n_dupli, n_split


def _positive_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0.0 else None


def _view_projection_diagnostics(views: list[TrainingView]) -> dict[str, float | int | bool]:
    total_views = max(1, len(views))
    normalized_views = sum(
        1
        for view in views
        if view.source_camera_model_name is not None
        and view.projection_camera_model_name is not None
        and view.source_camera_model_name != view.projection_camera_model_name
    )
    ut_views = sum(1 for view in views if view.use_unscented_transform)
    fisheye_views = sum(
        1
        for view in views
        if str(view.camera_model).lower() == "fisheye"
        or str(view.projection_camera_model_name or "").upper().endswith("FISHEYE")
        or str(view.source_camera_model_name or "").upper().endswith("FISHEYE")
    )
    return {
        "view_count": int(len(views)),
        "normalized_views": int(normalized_views),
        "normalized_ratio": float(normalized_views / total_views),
        "ut_views": int(ut_views),
        "ut_ratio": float(ut_views / total_views),
        "fisheye_views": int(fisheye_views),
        "fisheye_ratio": float(fisheye_views / total_views),
        "has_normalized_cameras": bool(normalized_views > 0),
        "has_unscented_cameras": bool(ut_views > 0),
        "has_fisheye_cameras": bool(fisheye_views > 0),
    }


def _auto_strategy_name(
    *,
    dataset_diagnostics: dict[str, object] | None,
    projection_diagnostics: dict[str, float | int | bool] | None,
    view_count: int,
) -> tuple[str, str]:
    diagnostics = dataset_diagnostics or {}
    projection = projection_diagnostics or {}
    sharpness = float(diagnostics.get("sharpness_mean") or 0.005)
    quality_score = float(diagnostics.get("quality_score") or 80.0)
    clipped = float(diagnostics.get("exposure_clipped_mean") or 0.0)
    normalized_ratio = float(projection.get("normalized_ratio") or 0.0)
    fisheye_ratio = float(projection.get("fisheye_ratio") or 0.0)
    overlap_mean = diagnostics.get("selected_overlap_mean")
    overlap_value = float(overlap_mean) if overlap_mean is not None else None
    source_videos = int(diagnostics.get("source_videos") or 0)
    selected_frames = int(diagnostics.get("video_selected_frames") or 0)
    duplicate_like_pairs = int(diagnostics.get("duplicate_like_pairs") or 0)

    if normalized_ratio >= 0.5 and fisheye_ratio >= 0.5:
        return "mcmc", "normalized_distorted_camera_majority"
    if normalized_ratio >= 0.5 and sharpness < 0.0032:
        return "mcmc", "normalized_projection_with_low_sharpness"
    if source_videos > 0 and selected_frames >= 72 and duplicate_like_pairs >= max(8, int(selected_frames * 0.05)):
        return "mcmc", "video_dataset_with_many_near_duplicates"
    if source_videos > 0 and view_count >= 64 and duplicate_like_pairs >= 12:
        return "mcmc", "long_video_sequence_prefers_mcmc"
    if quality_score < 72.0 and sharpness < 0.0030 and view_count >= 16:
        return "mcmc", "low_sharpness_multiview_dataset"
    if overlap_value is not None and overlap_value < 0.18 and view_count >= 24:
        return "mcmc", "low_video_overlap"
    if clipped > 0.60 and sharpness < 0.0040:
        return "mcmc", "heavy_clipping_with_soft_images"
    return "las", "default_las"


def _auto_budget_scale(
    *,
    dataset_diagnostics: dict[str, object] | None,
    projection_diagnostics: dict[str, float | int | bool] | None,
) -> float:
    diagnostics = dataset_diagnostics or {}
    projection = projection_diagnostics or {}
    scale = 1.0
    sharpness = float(diagnostics.get("sharpness_mean") or 0.005)
    quality_score = float(diagnostics.get("quality_score") or 80.0)
    clipped = float(diagnostics.get("exposure_clipped_mean") or 0.0)
    normalized_ratio = float(projection.get("normalized_ratio") or 0.0)
    fisheye_ratio = float(projection.get("fisheye_ratio") or 0.0)

    if normalized_ratio >= 0.5:
        scale *= 0.82
    if fisheye_ratio >= 0.5 and normalized_ratio >= 0.5:
        scale *= 0.90
    if sharpness < 0.0030:
        scale *= 0.84
    elif sharpness < 0.0040:
        scale *= 0.92
    if quality_score < 70.0:
        scale *= 0.90
    if clipped > 0.50:
        scale *= 0.90
    return max(0.55, min(1.0, scale))


def _resolve_self_organizing_config(
    settings: dict[str, object],
    *,
    max_steps: int,
    refine_every: int | None = None,
    refine_start_iter: int | None = None,
    dataset_diagnostics: dict[str, object] | None = None,
    projection_diagnostics: dict[str, float | int | bool] | None = None,
) -> SelfOrganizingCompressionConfig:
    default_sort_every = int(refine_every or max(50, min(250, int(round(max_steps * 0.08)))))
    sort_every = max(1, int(settings.get("sogs_sort_every", default_sort_every)))
    blur_kernel_size = max(3, int(settings.get("sogs_blur_kernel_size", 5)))
    if blur_kernel_size % 2 == 0:
        blur_kernel_size += 1
    requested_method = str(settings.get("sogs_method") or "auto").strip().lower()
    if requested_method not in {"auto", "plas", "pca"}:
        requested_method = "auto"
    requested_loss_fn = str(settings.get("sogs_loss_fn") or "huber").strip().lower()
    if requested_loss_fn not in {"mse", "huber"}:
        requested_loss_fn = "huber"
    stop_step = _positive_int(settings.get("sogs_stop_step")) or max_steps
    stop_step = max(0, min(stop_step, max_steps))
    start_step = max(0, int(settings.get("sogs_start_step", 0)))
    diagnostics = dataset_diagnostics or {}
    projection = projection_diagnostics or {}
    sharpness = float(diagnostics.get("sharpness_mean") or 0.0)
    quality_score = float(diagnostics.get("quality_score") or 0.0)
    clipped = float(diagnostics.get("exposure_clipped_mean") or 0.0)
    normalized_ratio = float(projection.get("normalized_ratio") or 0.0)
    adaptive_smoothness = float(
        settings.get("sogs_smoothness_weight", settings.get("compression_smoothness_weight", 1.0e-3))
    )
    if normalized_ratio >= 0.5:
        adaptive_smoothness *= 0.35
        sort_every = max(sort_every, int(max(100, round(default_sort_every * 1.4))))
        start_step = max(start_step, int(max(refine_start_iter or 0, round(max_steps * 0.15))))
    if sharpness < 0.0030:
        adaptive_smoothness *= 0.45
        sort_every = max(sort_every, int(max(120, round(default_sort_every * 1.5))))
        start_step = max(start_step, int(max(refine_start_iter or 0, round(max_steps * 0.20))))
    elif sharpness < 0.0040:
        adaptive_smoothness *= 0.70
    if quality_score < 72.0:
        adaptive_smoothness *= 0.75
    if clipped > 0.50:
        adaptive_smoothness *= 0.80
    adaptive_smoothness = max(5.0e-5, min(adaptive_smoothness, 1.0e-3))
    return SelfOrganizingCompressionConfig(
        enabled=bool(settings.get("enable_self_organizing_compression", True)),
        method=requested_method,
        start_step=start_step,
        stop_step=max(start_step, stop_step),
        sort_every=sort_every,
        min_points=max(64, int(settings.get("sogs_min_points", 256))),
        normalize=bool(settings.get("sogs_normalize", True)),
        activated=bool(settings.get("sogs_use_activated_attrs", True)),
        shuffle=bool(settings.get("sogs_shuffle", True)),
        improvement_break=float(settings.get("sogs_improvement_break", 1.0e-4)),
        blur_kernel_size=blur_kernel_size,
        blur_sigma=float(settings.get("sogs_blur_sigma", 1.25)),
        loss_fn=requested_loss_fn,
        smoothness_weight=adaptive_smoothness,
        sort_weights={
            "means": float(settings.get("sogs_sort_weight_means", 1.0)),
            "sh0": float(settings.get("sogs_sort_weight_sh0", 1.0)),
            "scales": float(settings.get("sogs_sort_weight_scales", 1.0)),
            "opacities": float(settings.get("sogs_sort_weight_opacities", 0.0)),
            "quats": float(settings.get("sogs_sort_weight_quats", 0.0)),
            "shN": float(settings.get("sogs_sort_weight_shN", 0.0)),
        },
        smoothness_weights={
            "means": float(settings.get("sogs_smooth_weight_means", 0.75)),
            "sh0": float(settings.get("sogs_smooth_weight_sh0", 1.0)),
            "scales": float(settings.get("sogs_smooth_weight_scales", 0.65)),
            "opacities": float(settings.get("sogs_smooth_weight_opacities", 0.20)),
            "quats": float(settings.get("sogs_smooth_weight_quats", 0.10)),
            "shN": float(settings.get("sogs_smooth_weight_shN", 0.0)),
        },
    )


def _should_refresh_self_organizing_layout(
    step: int,
    config: SelfOrganizingCompressionConfig,
    *,
    last_sort_step: int | None,
) -> bool:
    if not config.enabled:
        return False
    step_number = step + 1
    if step_number < config.start_step or step_number > config.stop_step:
        return False
    if last_sort_step is None:
        return True
    return (step_number - last_sort_step) >= config.sort_every


@torch.no_grad()
def _refresh_self_organizing_layout(
    splats: torch.nn.ParameterDict,
    optimizers: dict[str, torch.optim.Optimizer],
    strategy_state: dict[str, object],
    config: SelfOrganizingCompressionConfig,
    *,
    step: int,
    runtime_state: dict[str, object],
    verbose: bool = False,
) -> dict[str, object]:
    permutation, metadata = _self_organizing_permutation(splats, config, verbose=verbose)
    metadata["step"] = int(step + 1)
    if permutation is None:
        return metadata
    _reorder_splats_with_optimizer(splats, optimizers, strategy_state, permutation)
    runtime_state["last_sort_step"] = int(step + 1)
    runtime_state["events"] = int(runtime_state.get("events") or 0) + 1
    runtime_state["last_method"] = metadata.get("method")
    runtime_state["last_grid_shape"] = metadata.get("grid_shape")
    runtime_state["last_changed_fraction"] = metadata.get("changed_fraction")
    return metadata


def _normalize_quality_preset(settings: dict) -> str:
    preset = str(settings.get("quality_preset") or "balanced").strip().lower()
    if preset not in {"compact", "balanced", "high"}:
        return "balanced"
    return preset


def _resolve_rasterize_mode(settings: dict, preset: str) -> str:
    mode = str(settings.get("rasterize_mode") or "auto").strip().lower()
    if mode not in {"auto", "classic", "antialiased"}:
        mode = "auto"
    if mode == "auto":
        return "classic" if preset == "compact" else "antialiased"
    return mode


def _resolve_sfm_match_mode(settings: dict, image_count: int, project: dict | None = None) -> str:
    mode = str(settings.get("sfm_match_mode") or "auto").strip().lower()
    if mode not in {"auto", "exhaustive", "sequential", "spatial"}:
        mode = "auto"
    if mode == "auto":
        if _is_long_video_sequence(project, image_count):
            return "sequential"
        if image_count >= 96:
            return "sequential"
        return "exhaustive"
    return mode


def _minimum_registered_view_count(image_count: int) -> int:
    image_count = max(0, int(image_count))
    if image_count <= 0:
        return 0
    if image_count <= 4:
        return min(image_count, 2)
    if image_count <= 8:
        return min(image_count, 3)
    if image_count <= 16:
        return min(image_count, 4)
    return min(image_count, max(6, int(math.ceil(image_count * 0.25))))


def _reconstruction_registration_stats(reconstruction: pycolmap.Reconstruction, image_count: int) -> dict[str, float | int | bool]:
    total_input_images = max(0, int(image_count))
    registered_images = sum(1 for image in reconstruction.images.values() if image.has_pose)
    min_registered = _minimum_registered_view_count(total_input_images)
    registration_ratio = (
        float(registered_images) / float(total_input_images)
        if total_input_images > 0
        else 0.0
    )
    return {
        "registered_images": int(registered_images),
        "total_input_images": int(total_input_images),
        "points3d": int(len(reconstruction.points3D)),
        "min_registered_images": int(min_registered),
        "registration_ratio": float(registration_ratio),
        "usable": bool(registered_images >= min_registered and registered_images >= 2),
    }


def _reconstruction_registration_summary(stats: dict[str, float | int | bool]) -> str:
    return (
        f"{int(stats['registered_images'])}/{int(stats['total_input_images'])} registered images "
        f"(min={int(stats['min_registered_images'])}, ratio={float(stats['registration_ratio']):.2f}, "
        f"points={int(stats['points3d'])})"
    )


def _alternate_sfm_match_mode(match_mode: str, image_count: int) -> str | None:
    resolved_mode = str(match_mode).strip().lower()
    if resolved_mode == "exhaustive" and image_count >= 8:
        return "sequential"
    if resolved_mode == "sequential":
        return "exhaustive"
    if resolved_mode == "spatial":
        return "exhaustive"
    return None


def _sfm_thread_count(settings: dict) -> int:
    return max(1, min(_positive_int(settings.get("sfm_num_threads")) or 6, 6))


def _auto_gaussian_budget(view_count: int, initial_points: int, train_resolution: int, preset: str) -> int:
    profile_table = {
        "compact": {"point_factor": 2.5, "view_resolution_weight": 8.0, "minimum": 40_000, "maximum": 300_000},
        "balanced": {"point_factor": 4.0, "view_resolution_weight": 12.0, "minimum": 80_000, "maximum": 650_000},
        "high": {"point_factor": 6.0, "view_resolution_weight": 18.0, "minimum": 140_000, "maximum": 1_250_000},
    }
    profile = profile_table[preset]
    budget = int(round((initial_points * profile["point_factor"]) + (view_count * train_resolution * profile["view_resolution_weight"])))
    return max(int(profile["minimum"]), min(int(profile["maximum"]), budget))


def _resolve_training_profile(
    settings: dict,
    view_count: int,
    initial_points: int,
    dataset_diagnostics: dict[str, object] | None = None,
    projection_diagnostics: dict[str, float | int | bool] | None = None,
) -> dict[str, object]:
    max_steps = int(settings.get("train_steps", 1200))
    train_resolution = int(settings.get("train_resolution", 640))
    preset = _normalize_quality_preset(settings)
    requested_strategy_name = str(settings.get("strategy_name") or "auto").strip().lower()
    if requested_strategy_name not in {"auto", "default", "mcmc", "las"}:
        requested_strategy_name = "auto"
    strategy_name = requested_strategy_name
    auto_strategy_reason = "user_selected"
    if requested_strategy_name == "auto":
        strategy_name, auto_strategy_reason = _auto_strategy_name(
            dataset_diagnostics=dataset_diagnostics,
            projection_diagnostics=projection_diagnostics,
            view_count=view_count,
        )

    max_gaussians = _positive_int(settings.get("max_gaussians"))
    if max_gaussians is None:
        budget_scale = _auto_budget_scale(
            dataset_diagnostics=dataset_diagnostics,
            projection_diagnostics=projection_diagnostics,
        )
        max_gaussians = int(
            round(
                _auto_gaussian_budget(
                    view_count,
                    initial_points,
                    train_resolution,
                    preset,
                )
                * budget_scale
            )
        )

    if strategy_name == "mcmc":
        start_ratio = {"compact": 0.10, "balanced": 0.10, "high": 0.12}[preset]
        stop_ratio = {"compact": 0.70, "balanced": 0.80, "high": 0.90}[preset]
    elif strategy_name == "las":
        start_ratio = 0.03
        stop_ratio = 0.92
    else:
        start_ratio = {"compact": 0.10, "balanced": 0.10, "high": 0.12}[preset]
        stop_ratio = {"compact": 0.45, "balanced": 0.55, "high": 0.65}[preset]
    max_refine_iter = max(1, max_steps - 1)
    refine_start_default = max(25, int(max_steps * start_ratio))
    if strategy_name == "las":
        refine_start_default = min(max_refine_iter, max(25, min(200, int(max_steps * 0.03))))
    refine_start_iter = _positive_int(settings.get("densify_start_iter")) or refine_start_default
    refine_start_iter = min(max_refine_iter, refine_start_iter)
    refine_stop_default = max(50, int(max_steps * stop_ratio))
    if strategy_name == "las":
        refine_stop_default = min(max_refine_iter, max(refine_start_iter + 1, int(max_steps * 0.92)))
    refine_stop_iter = _positive_int(settings.get("densify_stop_iter")) or refine_stop_default
    refine_stop_iter = min(max_refine_iter, refine_stop_iter)
    if refine_stop_iter <= refine_start_iter:
        refine_start_iter = max(1, min(refine_start_iter, max_refine_iter - 1))
        refine_stop_iter = min(max_refine_iter, max(refine_start_iter + 1, refine_stop_iter))
    refine_every_default = 75 if preset == "compact" else 50
    if strategy_name == "las":
        refine_every_default = max(25, min(120, int(max_steps * 0.025)))
    refine_every = _positive_int(settings.get("densify_interval")) or refine_every_default
    budget_schedule_default = "igs_plus" if strategy_name == "las" else "staged"
    configured_budget_schedule = str(settings.get("budget_schedule") or budget_schedule_default).strip().lower()
    if requested_strategy_name == "auto" and configured_budget_schedule == "staged":
        configured_budget_schedule = budget_schedule_default

    profile: dict[str, object] = {
        "preset": preset,
        "strategy_name": strategy_name,
        "auto_strategy_reason": auto_strategy_reason,
        "rasterize_mode": _resolve_rasterize_mode(settings, preset),
        "max_gaussians": int(max_gaussians),
        "budget_schedule": configured_budget_schedule,
        "adaptive_growth": bool(strategy_name == "las"),
        "refine_start_iter": int(refine_start_iter),
        "refine_stop_iter": int(refine_stop_iter),
        "refine_every": int(refine_every),
        "refine_scale2d_stop_iter": int(max_steps),
        "opacity_reset_interval": _positive_int(settings.get("opacity_reset_interval"))
        or (3000 if strategy_name == "las" else max(1000, int(max_steps * 0.6))),
    }

    if strategy_name == "mcmc":
        profile["min_opacity"] = _positive_float(settings.get("min_opacity")) or {
            "compact": 0.015,
            "balanced": 0.010,
            "high": 0.0075,
        }[preset]
        profile["noise_lr"] = _positive_float(settings.get("mcmc_noise_lr")) or {
            "compact": 3.0e5,
            "balanced": 4.0e5,
            "high": 5.0e5,
        }[preset]
        return profile

    absgrad_setting = settings.get("absgrad")
    absgrad = bool(absgrad_setting) if absgrad_setting is not None else (preset != "compact")
    grow_grad2d = _positive_float(settings.get("grow_grad2d"))
    if grow_grad2d is None:
        if strategy_name == "las":
            grow_grad2d = 2.0e-4
        else:
            grow_grad2d = 8.0e-4 if absgrad else 2.0e-4
            if preset == "compact":
                grow_grad2d *= 1.25
            elif preset == "high":
                grow_grad2d *= 0.85

    prune_opa = _positive_float(settings.get("prune_opa"))
    if prune_opa is None:
        prune_opa = 0.005 if strategy_name == "las" else {
            "compact": 0.015,
            "balanced": 0.010,
            "high": 0.0075,
        }[preset]

    profile.update(
        {
            "absgrad": absgrad,
            "grow_grad2d": grow_grad2d,
            "prune_opa": prune_opa,
            "grow_scale3d": float(settings.get("grow_scale3d", 0.01)),
            "grow_scale2d": float(settings.get("grow_scale2d", 0.05)),
            "prune_scale3d": float(settings.get("prune_scale3d", 0.1)),
            "prune_scale2d": float(settings.get("prune_scale2d", 0.15)),
            "revised_opacity": bool(settings.get("revised_opacity", True)),
        }
    )
    if strategy_name == "las":
        profile.update(
            {
                "edge_threshold": float(settings.get("edge_threshold", 0.12)),
                "warmup_events": max(0, int(settings.get("edge_warmup_events", 0))),
                "candidate_factor": max(1, int(settings.get("edge_candidate_factor", 4))),
                "edge_score_weight": float(settings.get("edge_score_weight", 0.25)),
                "las_primary_shrink": float(settings.get("las_primary_shrink", 2.0)),
                "las_secondary_shrink": float(settings.get("las_secondary_shrink", 1.0 / 0.85)),
                "las_opacity_factor": float(settings.get("las_opacity_factor", 0.6)),
                "las_offset_scale": float(settings.get("las_offset_scale", 0.5)),
            }
        )
    return profile


def _resolve_sh_increment_interval(settings: dict, max_steps: int, sh_degree: int) -> int:
    configured = _positive_int(settings.get("sh_increment_interval"))
    if configured:
        return configured
    if sh_degree <= 0:
        return max_steps
    return max(250, min(1000, max_steps // 6))


def _active_sh_degree(step: int, max_steps: int, sh_degree: int, settings: dict) -> int:
    if sh_degree <= 0:
        return 0
    interval = _resolve_sh_increment_interval(settings, max_steps, sh_degree)
    return min(sh_degree, step // max(1, interval))


def _scheduled_gaussian_budget(
    *,
    step: int,
    initial_gaussians: int,
    target_gaussians: int,
    refine_start_iter: int,
    refine_stop_iter: int,
    refine_every: int = 0,
    sh_degree_to_use: int,
    sh_degree: int,
    schedule: str,
) -> int:
    if target_gaussians <= initial_gaussians:
        return int(target_gaussians)
    if schedule == "igs_plus":
        if step < refine_start_iter:
            return int(initial_gaussians)
        if refine_stop_iter <= refine_start_iter or refine_every <= 0:
            return int(target_gaussians)
        total_events = ((refine_stop_iter - refine_start_iter) // refine_every) + 2
        total_events = max(2, int(total_events))
        progress = (min(refine_stop_iter, step) - refine_start_iter) / float(max(1, refine_stop_iter - refine_start_iter))
        progress = max(0.0, min(1.0, progress))
        current_event = float(total_events) * progress
        slope_lower_bound = (target_gaussians - initial_gaussians) / float(total_events)
        k = 2.0 * slope_lower_bound
        a = (target_gaussians - initial_gaussians - (k * total_events)) / float(total_events * total_events)
        b = k
        c = float(initial_gaussians)
        budget = int(round((a * (current_event**2)) + (b * current_event) + c))
        if sh_degree > 0:
            sh_fraction = max(0.35, min(1.0, 0.40 + (0.60 * (sh_degree_to_use / float(sh_degree)))))
            sh_budget = initial_gaussians + int(round((target_gaussians - initial_gaussians) * sh_fraction))
            budget = min(budget, sh_budget)
        return max(int(initial_gaussians), min(int(target_gaussians), budget))
    if schedule not in {"staged", "progressive", "ramped"}:
        return int(target_gaussians)

    if step <= refine_start_iter:
        progress = 0.0
    elif refine_stop_iter <= refine_start_iter:
        progress = 1.0
    else:
        progress = (step - refine_start_iter) / float(refine_stop_iter - refine_start_iter)
    progress = max(0.0, min(1.0, progress))
    progress = progress * progress * (3.0 - (2.0 * progress))

    if sh_degree > 0:
        sh_fraction = max(0.35, min(1.0, 0.40 + (0.60 * (sh_degree_to_use / float(sh_degree)))))
        progress = min(progress, sh_fraction)

    budget = initial_gaussians + int(round((target_gaussians - initial_gaussians) * progress))
    return max(int(initial_gaussians), min(int(target_gaussians), budget))


def _image_observation_error(image: pycolmap.Image, reconstruction: pycolmap.Reconstruction) -> float | None:
    errors: list[float] = []
    for point2d in image.points2D:
        if not point2d.has_point3D():
            continue
        try:
            point3d = reconstruction.points3D[point2d.point3D_id]
        except KeyError:
            continue
        error = float(point3d.error)
        if math.isfinite(error):
            errors.append(error)
    if not errors:
        return None
    return float(np.median(np.asarray(errors, dtype=np.float32)))


def _select_registered_training_images(
    reconstruction: pycolmap.Reconstruction,
    *,
    job: dict | None = None,
) -> set[str] | None:
    candidates = [image for image in reconstruction.images.values() if image.has_pose]
    if len(candidates) < 8:
        return None

    point_counts = np.asarray([float(image.num_points3D) for image in candidates], dtype=np.float32)
    if point_counts.size == 0:
        return None

    median_count = float(np.median(point_counts))
    count_threshold = max(24.0, median_count * 0.18)
    image_errors = [_image_observation_error(image, reconstruction) for image in candidates]
    finite_errors = np.asarray([error for error in image_errors if error is not None], dtype=np.float32)
    error_threshold = None
    if finite_errors.size >= max(6, len(candidates) // 2):
        error_threshold = max(2.5, float(np.quantile(finite_errors, 0.85)) * 1.6)

    kept_names: set[str] = set()
    removed_names: list[str] = []
    for image, error in zip(candidates, image_errors):
        weak_track_support = float(image.num_points3D) < count_threshold
        weak_reprojection = (
            error_threshold is not None
            and error is not None
            and error > error_threshold
            and float(image.num_points3D) < median_count
        )
        if weak_track_support or weak_reprojection:
            removed_names.append(image.name)
            continue
        kept_names.add(image.name)

    minimum_keep = max(6, int(math.ceil(len(candidates) * 0.6)))
    if len(kept_names) < minimum_keep or len(removed_names) == 0:
        return None

    if job is not None:
        _log_line(
            job,
            f"Filtered {len(removed_names)} weak COLMAP cameras before training "
            f"(min_points={count_threshold:.1f}, reproj_limit={error_threshold or 0.0:.2f}).",
        )
    return kept_names


def _filter_sparse_seed_points(
    points_tensor: torch.Tensor,
    rgb_tensor: torch.Tensor,
    point_errors: np.ndarray,
    track_lengths: np.ndarray,
    *,
    job: dict | None = None,
) -> tuple[torch.Tensor, torch.Tensor]:
    total = int(len(points_tensor))
    if total < 64 or point_errors.size != total or track_lengths.size != total:
        return points_tensor, rgb_tensor

    finite_errors = np.isfinite(point_errors)
    if int(finite_errors.sum()) < 32:
        return points_tensor, rgb_tensor

    median_track = float(np.median(track_lengths))
    min_track_length = 3 if median_track >= 3.0 else 2
    error_threshold = max(1.5, float(np.quantile(point_errors[finite_errors], 0.90)))
    keep_mask_np = finite_errors & (track_lengths >= min_track_length) & (point_errors <= error_threshold)
    kept = int(np.count_nonzero(keep_mask_np))
    filter_label = "strict"
    kept_ratio = float(kept) / float(max(total, 1))

    if total < 50_000 and kept_ratio < 0.80:
        relaxed_min_track = 2
        relaxed_error_threshold = max(error_threshold, float(np.quantile(point_errors[finite_errors], 0.95)))
        relaxed_keep_mask_np = (
            finite_errors
            & (track_lengths >= relaxed_min_track)
            & (point_errors <= relaxed_error_threshold)
        )
        relaxed_kept = int(np.count_nonzero(relaxed_keep_mask_np))
        if relaxed_kept > kept:
            keep_mask_np = relaxed_keep_mask_np
            kept = relaxed_kept
            min_track_length = relaxed_min_track
            error_threshold = relaxed_error_threshold
            filter_label = "relaxed"

    if kept >= total or kept < max(32, int(total * 0.35)):
        return points_tensor, rgb_tensor

    keep_mask = torch.from_numpy(keep_mask_np.astype(np.bool_))
    if job is not None:
        _log_line(
            job,
            f"Filtered sparse seed points to {kept:,} / {total:,} "
            f"(mode={filter_label}, min_track_length={min_track_length}, error_limit={error_threshold:.2f}).",
        )
    return points_tensor[keep_mask], rgb_tensor[keep_mask]


def _directory_mtime_stamp(path: Path) -> float:
    if not path.exists():
        return 0.0
    latest = float(path.stat().st_mtime)
    for child in path.rglob("*"):
        try:
            latest = max(latest, float(child.stat().st_mtime))
        except OSError:
            continue
    return latest


def _depth_bootstrap_workspace_dir(project_id: str) -> Path:
    return paths.project_colmap_scratch_dir(project_id) / "dense"


def _depth_bootstrap_meta_path(workspace_dir: Path) -> Path:
    return workspace_dir / "_bootstrap_meta.json"


def _resolve_depth_bootstrap_image_size(settings: dict) -> int:
    configured = int(settings.get("depth_bootstrap_max_image_size", 1024))
    if configured > 0:
        return max(256, configured)
    train_resolution = int(settings.get("train_resolution", 640))
    return max(512, min(1600, max(train_resolution * 2, 960)))


def _read_colmap_dense_array(path: Path) -> np.ndarray:
    with path.open("rb") as handle:
        header = bytearray()
        separators = 0
        while separators < 3:
            byte = handle.read(1)
            if not byte:
                raise ValueError(f"COLMAP dense array header is truncated: {path}")
            header.extend(byte)
            if byte == b"&":
                separators += 1

        parts = header.decode("ascii").split("&")
        if len(parts) < 3:
            raise ValueError(f"COLMAP dense array header is invalid: {path}")
        width = int(parts[0])
        height = int(parts[1])
        channels = int(parts[2])
        payload = np.frombuffer(handle.read(), dtype=np.float32)

    expected = width * height * channels
    if payload.size != expected:
        raise ValueError(
            f"COLMAP dense array payload has {payload.size} float32 values, expected {expected}: {path}"
        )
    if channels == 1:
        return payload.reshape((height, width))
    return payload.reshape((height, width, channels))


def _pick_colmap_depth_map_path(depth_dir: Path, image_name: str) -> Path | None:
    photometric = depth_dir / f"{image_name}.photometric.bin"
    if photometric.exists():
        return photometric
    geometric = depth_dir / f"{image_name}.geometric.bin"
    if geometric.exists():
        return geometric
    matches = sorted(depth_dir.glob(f"{image_name}.*.bin"))
    return matches[0] if matches else None


def _sample_depth_bootstrap_pixels(
    depth_map: np.ndarray,
    rgb_pixels: np.ndarray,
    target_count: int,
) -> tuple[np.ndarray, np.ndarray]:
    valid = np.isfinite(depth_map) & (depth_map > 1.0e-4)
    if not np.any(valid):
        return np.empty((0,), dtype=np.int64), np.empty((0,), dtype=np.int64)

    valid_depths = depth_map[valid]
    if valid_depths.size >= 64:
        low = float(np.quantile(valid_depths, 0.02))
        high = float(np.quantile(valid_depths, 0.98))
        valid &= depth_map >= low
        valid &= depth_map <= high
    if not np.any(valid):
        return np.empty((0,), dtype=np.int64), np.empty((0,), dtype=np.int64)

    rows, cols = np.nonzero(valid)
    if rows.size <= target_count:
        return cols.astype(np.int64, copy=False), rows.astype(np.int64, copy=False)

    stride = max(1, int(math.ceil(math.sqrt(rows.size / float(max(target_count, 1))))))
    row_grid = np.arange(depth_map.shape[0], dtype=np.int64)[:, None]
    col_grid = np.arange(depth_map.shape[1], dtype=np.int64)[None, :]
    grid_mask = valid & (row_grid % stride == stride // 2) & (col_grid % stride == stride // 2)
    grid_rows, grid_cols = np.nonzero(grid_mask)

    if grid_rows.size > target_count:
        keep = np.linspace(0, grid_rows.size - 1, num=target_count, dtype=np.int64)
        return grid_cols[keep], grid_rows[keep]

    remaining = max(0, target_count - grid_rows.size)
    if remaining == 0:
        return grid_cols.astype(np.int64, copy=False), grid_rows.astype(np.int64, copy=False)

    luminance = rgb_pixels.astype(np.float32, copy=False).mean(axis=2)
    horizontal = np.zeros_like(luminance)
    vertical = np.zeros_like(luminance)
    horizontal[:, 1:] = np.abs(luminance[:, 1:] - luminance[:, :-1])
    vertical[1:, :] = np.abs(luminance[1:, :] - luminance[:-1, :])
    edge_score = horizontal + vertical
    edge_score_max = float(np.max(edge_score)) if edge_score.size else 0.0
    edge_score = edge_score / max(edge_score_max, 1.0e-6)

    candidate_mask = valid & (~grid_mask)
    candidate_rows, candidate_cols = np.nonzero(candidate_mask)
    if candidate_rows.size == 0:
        return grid_cols.astype(np.int64, copy=False), grid_rows.astype(np.int64, copy=False)

    candidate_scores = edge_score[candidate_rows, candidate_cols]
    extra_count = min(remaining, candidate_scores.size)
    if extra_count >= candidate_scores.size:
        top_indices = np.arange(candidate_scores.size, dtype=np.int64)
    else:
        top_indices = np.argpartition(candidate_scores, -extra_count)[-extra_count:]
    extra_rows = candidate_rows[top_indices]
    extra_cols = candidate_cols[top_indices]

    sampled_rows = np.concatenate([grid_rows, extra_rows], axis=0)
    sampled_cols = np.concatenate([grid_cols, extra_cols], axis=0)
    return sampled_cols.astype(np.int64, copy=False), sampled_rows.astype(np.int64, copy=False)


def _voxel_downsample_seed_cloud(
    points: torch.Tensor,
    colors: torch.Tensor,
    *,
    voxel_size: float,
    max_points: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    if len(points) == 0 or voxel_size <= 0.0:
        return points, colors

    voxel_size = max(float(voxel_size), 1.0e-5)
    voxels = torch.floor((points - points.min(dim=0).values) / voxel_size).to(torch.int64).cpu().numpy()
    _, keep_indices = np.unique(voxels, axis=0, return_index=True)
    keep_indices = np.sort(keep_indices.astype(np.int64, copy=False))
    reduced_points = points[keep_indices]
    reduced_colors = colors[keep_indices]

    if len(reduced_points) > max_points:
        selection = np.linspace(0, len(reduced_points) - 1, num=max_points, dtype=np.int64)
        reduced_points = reduced_points[selection]
        reduced_colors = reduced_colors[selection]
    return reduced_points, reduced_colors


def _find_colmap_executable(settings: dict[str, object]) -> Path | None:
    configured = str(settings.get("depth_bootstrap_colmap_executable") or settings.get("colmap_executable") or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.exists():
            return candidate

    which_candidates = [
        "COLMAP.bat",
        "colmap.bat",
        "colmap.exe",
        "colmap",
    ]
    for candidate in which_candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return Path(resolved)

    common_candidates = [
        paths.data_root() / "tools" / "colmap" / "COLMAP.bat",
        paths.data_root() / "tools" / "colmap" / "colmap.exe",
        Path.home() / "AppData" / "Local" / "COLMAP" / "COLMAP.bat",
        Path.home() / "AppData" / "Local" / "COLMAP" / "colmap.exe",
    ]
    for candidate in common_candidates:
        if candidate.exists():
            return candidate
    versioned_roots = [
        paths.data_root() / "tools" / "colmap",
        Path.home() / "AppData" / "Local" / "COLMAP",
    ]
    for root in versioned_roots:
        if not root.exists():
            continue
        for pattern in ("**/COLMAP.bat", "**/colmap.exe"):
            matches = sorted(root.glob(pattern), key=lambda path: len(path.parts))
            if matches:
                return matches[0]
    return None


def _depth_bootstrap_gpu_index(settings: dict[str, object]) -> str:
    configured = str(settings.get("depth_bootstrap_gpu_index") or settings.get("colmap_gpu_index") or "0").strip()
    return configured or "0"


def _colmap_process_command(executable: Path, *args: str) -> list[str]:
    if executable.suffix.lower() in {".bat", ".cmd"}:
        return ["cmd.exe", "/c", str(executable), *args]
    return [str(executable), *args]


def _is_cuda_dense_stereo_error(error: BaseException | str) -> bool:
    message = str(error).lower()
    return "requires cuda" in message or "cuda is not available" in message or "no cuda device" in message


def _tail_text_file(path: Path, *, max_chars: int = 1200) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-max_chars:]


def _dense_stereo_progress_snapshot(workspace_dir: Path) -> dict[str, int]:
    depth_dir = workspace_dir / "stereo" / "depth_maps"
    if not depth_dir.exists():
        return {
            "photometric": 0,
            "geometric": 0,
        }
    return {
        "photometric": len(list(depth_dir.glob("*.photometric.bin"))),
        "geometric": len(list(depth_dir.glob("*.geometric.bin"))),
    }


def _run_colmap_patch_match_cli(
    executable: Path,
    workspace_dir: Path,
    *,
    patch_options: pycolmap.PatchMatchOptions,
    job: dict,
) -> None:
    image_dir = workspace_dir / "images"
    target_images = len([path for path in image_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES]) if image_dir.exists() else 0
    cli_log_path = workspace_dir / "_colmap_patch_match.log"
    command = _colmap_process_command(
        executable,
        "patch_match_stereo",
        "--workspace_path",
        str(workspace_dir),
        "--workspace_format",
        "COLMAP",
        f"--PatchMatchStereo.max_image_size={int(patch_options.max_image_size)}",
        f"--PatchMatchStereo.geom_consistency={1 if bool(patch_options.geom_consistency) else 0}",
        f"--PatchMatchStereo.filter={1 if bool(patch_options.filter) else 0}",
        f"--PatchMatchStereo.filter_min_num_consistent={int(patch_options.filter_min_num_consistent)}",
        f"--PatchMatchStereo.num_iterations={int(patch_options.num_iterations)}",
        f"--PatchMatchStereo.window_radius={int(patch_options.window_radius)}",
        f"--PatchMatchStereo.gpu_index={str(patch_options.gpu_index)}",
    )
    with cli_log_path.open("w", encoding="utf-8", errors="replace") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=str(workspace_dir),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        last_logged_at = 0.0
        last_snapshot = {"photometric": -1, "geometric": -1}
        while True:
            return_code = process.poll()
            now = time.time()
            snapshot = _dense_stereo_progress_snapshot(workspace_dir)
            should_log = (
                snapshot != last_snapshot
                or (now - last_logged_at) >= 20.0
            )
            if should_log:
                photo = int(snapshot["photometric"])
                geo = int(snapshot["geometric"])
                progress_ratio = 0.0
                if target_images > 0:
                    progress_ratio = min(1.0, max(photo, geo) / float(target_images))
                _update(
                    job["id"],
                    "Depth Bootstrap",
                    0.32 + (0.03 * progress_ratio),
                    f"Dense stereo running on GPU: photometric={photo}/{max(1, target_images)} geometric={geo}/{max(1, target_images)}",
                )
                _log_line(
                    job,
                    "Dense stereo progress "
                    f"photometric={photo}/{max(1, target_images)} "
                    f"geometric={geo}/{max(1, target_images)} "
                    f"backend=colmap_cli gpu_index={patch_options.gpu_index}.",
                )
                last_logged_at = now
                last_snapshot = snapshot
            if return_code is not None:
                if return_code != 0:
                    tail = _tail_text_file(cli_log_path) or "unknown error"
                    raise RuntimeError(
                        f"COLMAP CLI patch-match stereo failed (code={return_code}) using {executable}: {tail}"
                    )
                break
            if _should_stop(job["id"]):
                process.terminate()
                raise RuntimeError("Stopped during dense depth bootstrap stereo.")
            time.sleep(5.0)
    _log_line(job, f"Dense depth bootstrap used external COLMAP CUDA stereo backend at {executable}.")


def _run_patch_match_stereo_auto(
    workspace_dir: Path,
    *,
    settings: dict[str, object],
    patch_options: pycolmap.PatchMatchOptions,
    job: dict,
) -> dict[str, object]:
    requested_backend = str(settings.get("depth_bootstrap_backend") or "auto").strip().lower()
    if requested_backend not in {"auto", "pycolmap", "colmap_cli"}:
        requested_backend = "auto"

    colmap_executable = _find_colmap_executable(settings)
    if requested_backend == "colmap_cli" and colmap_executable is None:
        raise RuntimeError(
            "Depth bootstrap is configured for COLMAP CLI, but no CUDA-enabled COLMAP executable was found. "
            "Set depth_bootstrap_colmap_executable or colmap_executable."
        )

    if requested_backend in {"auto", "colmap_cli"} and colmap_executable is not None:
        _run_colmap_patch_match_cli(
            colmap_executable,
            workspace_dir,
            patch_options=patch_options,
            job=job,
        )
        return {
            "backend": "colmap_cli",
            "colmap_executable": str(colmap_executable),
        }

    try:
        pycolmap.patch_match_stereo(
            workspace_dir,
            workspace_format="COLMAP",
            options=patch_options,
        )
    except Exception as error:
        if requested_backend == "auto" and _is_cuda_dense_stereo_error(error) and colmap_executable is not None:
            _log_line(
                job,
                "PyCOLMAP dense stereo backend does not expose CUDA on this build; falling back to external COLMAP CLI.",
            )
            _run_colmap_patch_match_cli(
                colmap_executable,
                workspace_dir,
                patch_options=patch_options,
                job=job,
            )
            return {
                "backend": "colmap_cli_fallback",
                "colmap_executable": str(colmap_executable),
            }
        if _is_cuda_dense_stereo_error(error):
            raise RuntimeError(
                "Dense stereo requires a CUDA-enabled backend. The current PyCOLMAP build does not provide it, and no "
                "external CUDA-enabled COLMAP executable was found. Install COLMAP with CUDA support and set "
                "`depth_bootstrap_colmap_executable` or `colmap_executable`."
            ) from error
        raise
    return {
        "backend": "pycolmap",
        "colmap_executable": None,
    }


def _run_colmap_dense_bootstrap(
    project: dict,
    job: dict,
    settings: dict,
    reconstruction: pycolmap.Reconstruction,
) -> tuple[Path, dict[str, object]]:
    workspace_dir = _depth_bootstrap_workspace_dir(project["id"])
    meta_path = _depth_bootstrap_meta_path(workspace_dir)
    image_dir = _prepare_colmap_images(project)
    sparse_dir = paths.project_colmap_scratch_dir(project["id"]) / "sparse"
    source_sparse_dir = None
    if sparse_dir.exists():
        existing = sorted(path for path in sparse_dir.iterdir() if path.is_dir())
        if existing:
            source_sparse_dir = existing[0]

    target_image_size = _resolve_depth_bootstrap_image_size(settings)
    quality_preset = _normalize_quality_preset(settings)
    sfm_threads = _sfm_thread_count(settings)
    min_consistent = max(2, int(settings.get("depth_bootstrap_min_consistent", 3)))

    cache_payload = {
        "source_sparse_mtime": _directory_mtime_stamp(source_sparse_dir) if source_sparse_dir is not None else 0.0,
        "image_dir_mtime": _directory_mtime_stamp(image_dir),
        "target_image_size": target_image_size,
        "min_consistent": min_consistent,
    }
    cached_depth_dir = workspace_dir / "stereo" / "depth_maps"
    if meta_path.exists() and cached_depth_dir.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = None
        if isinstance(meta, dict) and all(meta.get(key) == value for key, value in cache_payload.items()):
            if any(cached_depth_dir.glob("*.bin")):
                _log_line(job, f"Reusing cached COLMAP dense prior from {workspace_dir}.")
                return workspace_dir, {
                    "cached": True,
                    "image_size": target_image_size,
                    "min_consistent": min_consistent,
                }

    if workspace_dir.exists():
        shutil.rmtree(workspace_dir, ignore_errors=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    source_model_dir = workspace_dir / "_source_sparse"
    source_model_dir.mkdir(parents=True, exist_ok=True)
    reconstruction.write(source_model_dir)

    undistort_options = pycolmap.UndistortCameraOptions()
    undistort_options.max_image_size = target_image_size

    _update(job["id"], "Depth Bootstrap", 0.30, "Undistorting views for dense depth bootstrap.")
    _log_line(
        job,
        f"Running COLMAP dense bootstrap undistortion at max_image_size={target_image_size}.",
    )
    pycolmap.undistort_images(
        workspace_dir,
        source_model_dir,
        image_dir,
        output_type="COLMAP",
        undistort_options=undistort_options,
        num_threads=sfm_threads,
    )
    if _should_stop(job["id"]):
        raise RuntimeError("Stopped during dense depth bootstrap undistortion.")

    patch_options = pycolmap.PatchMatchOptions()
    patch_options.max_image_size = target_image_size
    patch_options.gpu_index = _depth_bootstrap_gpu_index(settings)
    patch_options.geom_consistency = True
    patch_options.filter = True
    patch_options.filter_min_num_consistent = min_consistent
    patch_options.num_threads = sfm_threads
    if quality_preset == "compact":
        patch_options.num_iterations = 4
        patch_options.window_radius = 4
    elif quality_preset == "high":
        patch_options.num_iterations = 6
        patch_options.window_radius = 5
        patch_options.filter_min_num_consistent = max(min_consistent, 4)

    _update(job["id"], "Depth Bootstrap", 0.32, "Running COLMAP patch-match stereo for dense depth bootstrap.")
    _log_line(
        job,
        "COLMAP dense bootstrap setup "
        f"max_image_size={patch_options.max_image_size} "
        f"iterations={patch_options.num_iterations} "
        f"window_radius={patch_options.window_radius} "
        f"min_consistent={patch_options.filter_min_num_consistent} "
        f"gpu_index={patch_options.gpu_index}.",
    )
    stereo_backend = _run_patch_match_stereo_auto(
        workspace_dir,
        settings=settings,
        patch_options=patch_options,
        job=job,
    )
    if _should_stop(job["id"]):
        raise RuntimeError("Stopped during dense depth bootstrap stereo.")

    meta_path.write_text(json.dumps(cache_payload, indent=2), encoding="utf-8")
    return workspace_dir, {
        "cached": False,
        "image_size": target_image_size,
        "min_consistent": int(patch_options.filter_min_num_consistent),
        "backend": str(stereo_backend.get("backend") or "unknown"),
        "colmap_executable": stereo_backend.get("colmap_executable"),
        "gpu_index": str(patch_options.gpu_index),
    }


def _build_dense_depth_seed_points(
    project: dict,
    job: dict,
    settings: dict,
    reconstruction: pycolmap.Reconstruction,
    views: list[TrainingView],
    reference_points: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor, dict[str, object]]:
    target_points = max(0, int(settings.get("depth_bootstrap_points", 16000)))
    if target_points <= 0:
        return (
            torch.empty((0, 3), dtype=torch.float32),
            torch.empty((0, 3), dtype=torch.float32),
            {"enabled": False, "reason": "disabled_by_budget"},
        )

    workspace_dir, workspace_meta = _run_colmap_dense_bootstrap(project, job, settings, reconstruction)
    dense_reconstruction = pycolmap.Reconstruction(workspace_dir / "sparse")
    depth_dir = workspace_dir / "stereo" / "depth_maps"
    image_dir = workspace_dir / "images"

    reliable_names = _select_registered_training_images(dense_reconstruction)
    candidates = [image for image in dense_reconstruction.images.values() if image.has_pose]
    if reliable_names:
        candidates = [image for image in candidates if image.name in reliable_names]
    candidates.sort(key=lambda image: float(image.num_points3D), reverse=True)

    max_views = max(1, int(settings.get("depth_bootstrap_max_views", 16)))
    selected_images = candidates[:max_views]
    if not selected_images:
        return (
            torch.empty((0, 3), dtype=torch.float32),
            torch.empty((0, 3), dtype=torch.float32),
            {
                "enabled": True,
                "workspace": str(workspace_dir),
                "seeded_points": 0,
                "views_considered": 0,
                "views_used": 0,
                **workspace_meta,
            },
        )

    per_view_budget = max(256, int(math.ceil(target_points / float(max(1, len(selected_images))))))
    sampled_points: list[torch.Tensor] = []
    sampled_colors: list[torch.Tensor] = []
    used_views = 0

    for image in selected_images:
        depth_map_path = _pick_colmap_depth_map_path(depth_dir, image.name)
        image_path = image_dir / image.name
        if depth_map_path is None or not image_path.exists():
            continue

        depth_map = _read_colmap_dense_array(depth_map_path)
        if depth_map.ndim != 2:
            continue
        rgb_pixels = np.asarray(Image.open(image_path).convert("RGB"), dtype=np.float32) / 255.0
        if rgb_pixels.shape[:2] != depth_map.shape[:2]:
            continue

        u, v = _sample_depth_bootstrap_pixels(depth_map, rgb_pixels, per_view_budget)
        if u.size == 0:
            continue

        camera = dense_reconstruction.cameras[image.camera_id]
        image_points = np.stack([u.astype(np.float64, copy=False), v.astype(np.float64, copy=False)], axis=-1)
        rays_xy = camera.cam_from_img(image_points)
        if rays_xy is None:
            continue
        rays_xy = np.asarray(rays_xy, dtype=np.float32)
        valid = np.isfinite(rays_xy).all(axis=1)
        if not np.any(valid):
            continue

        u = u[valid]
        v = v[valid]
        rays_xy = rays_xy[valid]
        depth_values = depth_map[v, u].astype(np.float32, copy=False)
        camera_points = np.stack(
            [
                rays_xy[:, 0] * depth_values,
                rays_xy[:, 1] * depth_values,
                depth_values,
                np.ones_like(depth_values),
            ],
            axis=-1,
        )
        world_from_cam = np.asarray(image.cam_from_world.inverse().matrix(), dtype=np.float32)
        world_points = camera_points @ world_from_cam.T

        sampled_points.append(torch.from_numpy(world_points[:, :3].copy()))
        sampled_colors.append(torch.from_numpy(rgb_pixels[v, u, :3].copy()))
        used_views += 1

    if not sampled_points:
        return (
            torch.empty((0, 3), dtype=torch.float32),
            torch.empty((0, 3), dtype=torch.float32),
            {
                "enabled": True,
                "workspace": str(workspace_dir),
                "seeded_points": 0,
                "views_considered": len(selected_images),
                "views_used": 0,
                **workspace_meta,
            },
        )

    points = torch.cat(sampled_points, dim=0)
    colors = torch.cat(sampled_colors, dim=0)
    points, colors = _sanitize_depth_reinit_candidates(points, colors, reference_points)
    if len(points) == 0:
        return (
            points,
            colors,
            {
                "enabled": True,
                "workspace": str(workspace_dir),
                "seeded_points": 0,
                "views_considered": len(selected_images),
                "views_used": used_views,
                **workspace_meta,
            },
        )

    voxel_scale = float(settings.get("depth_bootstrap_voxel_factor", 0.75))
    neighbor_distance = _knn_mean_distance(reference_points if len(reference_points) >= 2 else points)
    voxel_size = float(torch.median(neighbor_distance).item()) * max(0.1, voxel_scale)
    points, colors = _voxel_downsample_seed_cloud(
        points,
        colors,
        voxel_size=voxel_size,
        max_points=max(target_points, 512),
    )
    points, colors = _filter_sparse_points_with_masks(
        points,
        colors,
        views,
        min_hits=int(settings.get("mask_min_views", 2)),
        alpha_threshold=float(settings.get("alpha_mask_threshold", 0.2)),
    )
    points, colors = _sanitize_depth_reinit_candidates(points, colors, reference_points)

    return (
        points,
        colors,
        {
            "enabled": True,
            "workspace": str(workspace_dir),
            "seeded_points": int(len(points)),
            "views_considered": len(selected_images),
            "views_used": used_views,
            **workspace_meta,
        },
    )


def _build_target_pixels(
    view: TrainingView,
    device: torch.device,
    *,
    random_background: bool,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    target_rgb = view.rgb_tensor.unsqueeze(0).to(device)
    target_alpha = view.alpha_tensor.unsqueeze(0).to(device)
    if view.has_alpha and random_background:
        backgrounds = torch.rand((1, 3), dtype=torch.float32, device=device)
    else:
        backgrounds = torch.ones((1, 3), dtype=torch.float32, device=device)
    pixels = (target_rgb * target_alpha) + (backgrounds[:, None, None, :] * (1.0 - target_alpha))
    return target_rgb, target_alpha, backgrounds, pixels


def _render_view_raw(
    splats: torch.nn.ParameterDict,
    view: TrainingView,
    device: torch.device,
    *,
    sh_degree_to_use: int,
    absgrad: bool,
    backgrounds: torch.Tensor,
    rasterize_mode: str,
    render_mode: Literal["RGB", "D", "ED", "RGB+D", "RGB+ED"] = "RGB",
) -> tuple[torch.Tensor, torch.Tensor, dict]:
    camtoworld = view.camtoworld.unsqueeze(0).to(device)
    K = view.K.unsqueeze(0).to(device)
    radial_coeffs = view.radial_coeffs.unsqueeze(0).to(device) if view.radial_coeffs is not None else None
    tangential_coeffs = view.tangential_coeffs.unsqueeze(0).to(device) if view.tangential_coeffs is not None else None
    thin_prism_coeffs = view.thin_prism_coeffs.unsqueeze(0).to(device) if view.thin_prism_coeffs is not None else None
    renders, alphas, info = rasterization(
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
        absgrad=absgrad,
        rasterize_mode=rasterize_mode,
        backgrounds=backgrounds,
        render_mode=render_mode,
        camera_model=view.camera_model,
        with_ut=view.use_unscented_transform,
        radial_coeffs=radial_coeffs,
        tangential_coeffs=tangential_coeffs,
        thin_prism_coeffs=thin_prism_coeffs,
    )
    return renders, alphas, info


def _render_view(
    splats: torch.nn.ParameterDict,
    view: TrainingView,
    device: torch.device,
    *,
    sh_degree_to_use: int,
    absgrad: bool,
    backgrounds: torch.Tensor,
    rasterize_mode: str,
) -> tuple[torch.Tensor, torch.Tensor, dict]:
    renders, alphas, info = _render_view_raw(
        splats,
        view,
        device,
        sh_degree_to_use=sh_degree_to_use,
        absgrad=absgrad,
        backgrounds=backgrounds,
        rasterize_mode=rasterize_mode,
        render_mode="RGB",
    )
    return renders[..., :3], alphas, info


def _create_bilateral_grid_params(
    view_count: int,
    device: torch.device,
    settings: dict,
) -> dict[str, torch.nn.Parameter | tuple[int, ...] | int]:
    grid_size = max(
        4,
        int(
            settings.get(
                "appearance_grid_size",
                settings.get("appearance_grid_high_res", settings.get("appearance_grid_low_res", 16)),
            )
        ),
    )
    luma_bins = max(4, int(settings.get("appearance_luma_bins", 8)))
    identity_grid = torch.zeros((view_count, 12, luma_bins, grid_size, grid_size), dtype=torch.float32, device=device)
    identity_grid[:, 0] = 1.0
    identity_grid[:, 5] = 1.0
    identity_grid[:, 10] = 1.0

    params: dict[str, torch.nn.Parameter | tuple[int, ...] | int | float | str] = {
        "mode": "bilateral",
        "grid_size": grid_size,
        "luma_bins": luma_bins,
        "grid": torch.nn.Parameter(identity_grid),
        "inverse_epsilon": float(settings.get("appearance_inverse_epsilon", 1.0e-4)),
        "tv_weight": float(settings.get("appearance_tv_weight", 5.0)),
    }
    return params


def _sample_bilateral_grid(
    grid: torch.Tensor,
    view_index: int,
    guide_rgb: torch.Tensor,
) -> torch.Tensor:
    if guide_rgb.ndim != 4 or guide_rgb.shape[-1] != 3:
        raise ValueError("Expected guide image tensor in [B, H, W, 3] format.")
    luma = _grayscale_image(torch.clamp(guide_rgb, 0.0, 1.0))
    _, _, height, width = luma.shape
    y_coords = torch.linspace(-1.0, 1.0, height, device=guide_rgb.device, dtype=guide_rgb.dtype)
    x_coords = torch.linspace(-1.0, 1.0, width, device=guide_rgb.device, dtype=guide_rgb.dtype)
    grid_y, grid_x = torch.meshgrid(y_coords, x_coords, indexing="ij")
    guide = torch.clamp((luma[0, 0] * 2.0) - 1.0, -1.0, 1.0)
    sample_grid = torch.stack([grid_x, grid_y, guide], dim=-1).unsqueeze(0).unsqueeze(0)
    sampled = F.grid_sample(
        grid[view_index:view_index + 1],
        sample_grid,
        mode="bilinear",
        padding_mode="border",
        align_corners=True,
    )
    return sampled[:, :, 0].permute(0, 2, 3, 1)


def _bilateral_affine_terms(sampled: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    affine = sampled.contiguous().view(*sampled.shape[:-1], 3, 4)
    return affine[..., :3], affine[..., 3]


def _apply_bilateral_affine(
    image_rgb: torch.Tensor,
    view: TrainingView,
    exposure_params: dict[str, torch.nn.Parameter | tuple[int, ...] | int] | None,
    *,
    guide_pixels: torch.Tensor | None = None,
) -> torch.Tensor:
    if exposure_params is None:
        return image_rgb
    grid = exposure_params.get("grid")
    if not isinstance(grid, torch.Tensor):
        return image_rgb
    guide = guide_pixels if guide_pixels is not None else image_rgb
    sampled = _sample_bilateral_grid(grid, view.view_index, guide)
    transform, bias = _bilateral_affine_terms(sampled)
    augmented = torch.cat([image_rgb, torch.ones_like(image_rgb[..., :1])], dim=-1).unsqueeze(-1)
    corrected = torch.matmul(torch.cat([transform, bias.unsqueeze(-1)], dim=-1), augmented).squeeze(-1)
    return torch.clamp(corrected, 0.0, 1.0)


def _invert_bilateral_affine(
    corrected_rgb: torch.Tensor,
    raw_rgb: torch.Tensor,
    view: TrainingView,
    exposure_params: dict[str, torch.nn.Parameter | tuple[int, ...] | int] | None,
) -> torch.Tensor:
    if exposure_params is None:
        return corrected_rgb
    grid = exposure_params.get("grid")
    if not isinstance(grid, torch.Tensor):
        return corrected_rgb
    sampled = _sample_bilateral_grid(grid, view.view_index, raw_rgb)
    transform, bias = _bilateral_affine_terms(sampled)
    epsilon = float(exposure_params.get("inverse_epsilon", 1.0e-4))
    eye = torch.eye(3, dtype=corrected_rgb.dtype, device=corrected_rgb.device).view(1, 1, 1, 3, 3)
    transform = transform + (eye * epsilon)
    rhs = (corrected_rgb - bias).reshape(-1, 3, 1)
    flat_transform = transform.reshape(-1, 3, 3)
    try:
        restored = torch.linalg.solve(flat_transform, rhs)
    except RuntimeError:
        restored = torch.matmul(torch.linalg.pinv(flat_transform), rhs)
    return torch.clamp(restored.reshape_as(corrected_rgb), 0.0, 1.0)


def _appearance_ssim_map(
    corrected: torch.Tensor,
    target: torch.Tensor,
    *,
    raw: torch.Tensor | None = None,
) -> torch.Tensor:
    corrected = corrected.permute(0, 3, 1, 2)
    target = target.permute(0, 3, 1, 2)
    raw_tensor = raw.permute(0, 3, 1, 2) if raw is not None else corrected
    c1 = 0.01 ** 2
    c2 = 0.03 ** 2
    mu_corrected = F.avg_pool2d(corrected, kernel_size=11, stride=1, padding=5)
    mu_target = F.avg_pool2d(target, kernel_size=11, stride=1, padding=5)
    mu_raw = F.avg_pool2d(raw_tensor, kernel_size=11, stride=1, padding=5)
    sigma_raw = F.avg_pool2d(raw_tensor * raw_tensor, kernel_size=11, stride=1, padding=5) - (mu_raw * mu_raw)
    sigma_target = F.avg_pool2d(target * target, kernel_size=11, stride=1, padding=5) - (mu_target * mu_target)
    sigma_raw_target = F.avg_pool2d(raw_tensor * target, kernel_size=11, stride=1, padding=5) - (mu_raw * mu_target)
    luminance = ((2.0 * mu_corrected * mu_target) + c1) / ((mu_corrected * mu_corrected) + (mu_target * mu_target) + c1)
    contrast_structure = ((2.0 * sigma_raw_target) + c2) / (sigma_raw + sigma_target + c2)
    return luminance * contrast_structure


def _appearance_ssim(
    corrected: torch.Tensor,
    target: torch.Tensor,
    *,
    raw: torch.Tensor | None = None,
    weights: torch.Tensor | None = None,
) -> torch.Tensor:
    ssim_map = _appearance_ssim_map(corrected, target, raw=raw)
    if weights is None:
        return ssim_map.mean()
    if weights.ndim == 4 and weights.shape[-1] == 1:
        weight_map = weights.permute(0, 3, 1, 2)
    elif weights.ndim == 3:
        weight_map = weights.unsqueeze(1)
    else:
        raise ValueError("Expected weights in [B, H, W, 1] or [B, H, W] format.")
    weighted = ssim_map * weight_map
    return weighted.sum() / (weight_map.sum() * ssim_map.shape[1]).clamp_min(1.0e-6)


def _appearance_regularization(
    exposure_params: dict[str, torch.nn.Parameter | tuple[int, ...] | int] | None,
) -> tuple[torch.Tensor, torch.Tensor]:
    reference_tensor = next(
        (
            value
            for key, value in (exposure_params or {}).items()
            if isinstance(value, torch.Tensor) and (key == "grid" or key.startswith("grid_"))
        ),
        None,
    )
    zero = (
        torch.zeros((), dtype=reference_tensor.dtype, device=reference_tensor.device)
        if isinstance(reference_tensor, torch.Tensor)
        else torch.zeros((), dtype=torch.float32)
    )
    if exposure_params is None:
        return zero, zero

    if isinstance(exposure_params.get("grid"), torch.Tensor):
        grid = exposure_params["grid"]
        identity = torch.zeros_like(grid)
        identity[:, 0] = 1.0
        identity[:, 5] = 1.0
        identity[:, 10] = 1.0
        regularization = (grid - identity).pow(2).mean()
        smooth_terms = [
            torch.abs(grid[:, :, 1:] - grid[:, :, :-1]).mean(),
            torch.abs(grid[:, :, :, 1:] - grid[:, :, :, :-1]).mean(),
            torch.abs(grid[:, :, :, :, 1:] - grid[:, :, :, :, :-1]).mean(),
        ]
        return regularization, torch.stack(smooth_terms).mean()

    grid_terms: list[torch.Tensor] = []
    smooth_terms: list[torch.Tensor] = []
    for key, value in exposure_params.items():
        if not key.startswith("grid_") or not isinstance(value, torch.Tensor):
            continue
        grid_terms.append(value.pow(2).mean())
        smooth_terms.append(torch.abs(value[:, :, 1:] - value[:, :, :-1]).mean())
        smooth_terms.append(torch.abs(value[:, :, :, 1:] - value[:, :, :, :-1]).mean())
        smooth_terms.append(torch.abs(value[:, :, :, :, 1:] - value[:, :, :, :, :-1]).mean())

    if not grid_terms:
        return zero, zero
    return torch.stack(grid_terms).mean(), torch.stack(smooth_terms).mean()


def _apply_exposure_compensation(
    colors_pred: torch.Tensor,
    view: TrainingView,
    exposure_params: dict[str, torch.nn.Parameter | tuple[int, ...] | int] | None,
    *,
    guide_pixels: torch.Tensor | None = None,
) -> torch.Tensor:
    if exposure_params is None:
        return colors_pred
    if "log_gains" in exposure_params and "rgb_bias" in exposure_params:
        gains = torch.exp(exposure_params["log_gains"][view.view_index]).view(1, 1, 1, 3)
        bias = exposure_params["rgb_bias"][view.view_index].view(1, 1, 1, 3)
        return torch.clamp((colors_pred * gains) + bias, 0.0, 1.0)
    return _apply_bilateral_affine(colors_pred, view, exposure_params, guide_pixels=guide_pixels)


def _invert_exposure_compensation(
    corrected_pixels: torch.Tensor,
    guide_pixels: torch.Tensor,
    view: TrainingView,
    exposure_params: dict[str, torch.nn.Parameter | tuple[int, ...] | int] | None,
) -> torch.Tensor:
    if exposure_params is None:
        return corrected_pixels
    if "log_gains" in exposure_params and "rgb_bias" in exposure_params:
        gains = torch.exp(exposure_params["log_gains"][view.view_index]).view(1, 1, 1, 3).clamp_min(1.0e-4)
        bias = exposure_params["rgb_bias"][view.view_index].view(1, 1, 1, 3)
        return torch.clamp((corrected_pixels - bias) / gains, 0.0, 1.0)
    return _invert_bilateral_affine(corrected_pixels, guide_pixels, view, exposure_params)


def _visible_gaussian_count(info: dict) -> int:
    radii = info.get("radii")
    if radii is None:
        return 0
    visible = radii > 0.0
    if visible.ndim >= 3:
        visible = visible.all(dim=-1)
    if visible.ndim == 2:
        visible = visible.any(dim=0)
    return int(visible.sum().item())


def _evaluate_splats(
    splats: torch.nn.ParameterDict,
    views: list[TrainingView],
    device: torch.device,
    *,
    sh_degree: int,
    absgrad: bool,
    rasterize_mode: str,
    exposure_params: dict[str, torch.nn.Parameter | tuple[int, ...] | int] | None = None,
) -> dict[str, float]:
    totals = {
        "l1": 0.0,
        "ssim": 0.0,
        "psnr": 0.0,
        "alpha_l1": 0.0,
        "visible_gaussians": 0.0,
    }
    if not views:
        return {key: 0.0 for key in totals}

    with torch.no_grad():
        for view in views:
            _target_rgb, target_alpha, backgrounds, pixels = _build_target_pixels(
                view,
                device,
                random_background=False,
            )
            colors_pred, alpha_pred, info = _render_view(
                splats,
                view,
                device,
                sh_degree_to_use=sh_degree,
                absgrad=absgrad,
                backgrounds=backgrounds,
                rasterize_mode=rasterize_mode,
            )
            colors_pred = _apply_exposure_compensation(colors_pred, view, exposure_params, guide_pixels=colors_pred)
            l1_loss = F.l1_loss(colors_pred, pixels)
            mse = F.mse_loss(colors_pred, pixels)
            totals["l1"] += float(l1_loss.item())
            totals["ssim"] += float(_ssim(colors_pred, pixels).item())
            totals["psnr"] += float(_psnr_from_mse(mse).item())
            if view.has_alpha:
                totals["alpha_l1"] += float(F.l1_loss(alpha_pred, target_alpha).item())
            totals["visible_gaussians"] += float(_visible_gaussian_count(info))

    scale = 1.0 / float(len(views))
    return {key: round(value * scale, 5) for key, value in totals.items()}


def _prepare_appearance_bake_targets(
    splats: torch.nn.ParameterDict,
    views: list[TrainingView],
    device: torch.device,
    *,
    sh_degree: int,
    absgrad: bool,
    rasterize_mode: str,
    exposure_params: dict[str, torch.nn.Parameter | tuple[int, ...] | int],
) -> list[dict[str, torch.Tensor | TrainingView]]:
    bake_targets: list[dict[str, torch.Tensor | TrainingView]] = []
    with torch.no_grad():
        for view in views:
            _target_rgb, target_alpha, backgrounds, pixels = _build_target_pixels(
                view,
                device,
                random_background=False,
            )
            raw_colors_pred, _alpha_pred, _ = _render_view(
                splats,
                view,
                device,
                sh_degree_to_use=sh_degree,
                absgrad=absgrad,
                backgrounds=backgrounds,
                rasterize_mode=rasterize_mode,
            )
            baked_target = _invert_exposure_compensation(
                pixels.detach(),
                raw_colors_pred.detach(),
                view,
                exposure_params,
            ).detach()
            bake_targets.append(
                {
                    "view": view,
                    "target_alpha": target_alpha.detach(),
                    "backgrounds": backgrounds.detach(),
                    "baked_target": baked_target,
                }
            )
    return bake_targets


def _bake_appearance_into_splats(
    splats: torch.nn.ParameterDict,
    views: list[TrainingView],
    device: torch.device,
    *,
    sh_degree: int,
    absgrad: bool,
    rasterize_mode: str,
    exposure_params: dict[str, torch.nn.Parameter | tuple[int, ...] | int] | None,
    settings: dict,
    job: dict,
) -> dict[str, object]:
    if exposure_params is None or not views:
        return {"applied": False, "steps": 0}

    bake_steps = max(0, int(settings.get("appearance_bake_steps", min(300, max(80, len(views) * 12)))))
    if bake_steps <= 0:
        return {"applied": False, "steps": 0, "reason": "disabled"}

    sh0_lr = float(settings.get("appearance_bake_sh0_lr", float(settings.get("sh0_lr", 2.5e-3)) * 0.35))
    shn_lr = float(settings.get("appearance_bake_shN_lr", float(settings.get("shN_lr", 1.25e-4)) * 0.5))
    bake_optimizers = {
        "sh0": torch.optim.Adam([{"params": splats["sh0"], "lr": sh0_lr}], eps=1e-15),
        "shN": torch.optim.Adam([{"params": splats["shN"], "lr": shn_lr}], eps=1e-15),
    }

    lambda_dssim = float(settings.get("lambda_dssim", 0.20))
    bake_targets = _prepare_appearance_bake_targets(
        splats,
        views,
        device,
        sh_degree=sh_degree,
        absgrad=absgrad,
        rasterize_mode=rasterize_mode,
        exposure_params=exposure_params,
    )
    if not bake_targets:
        return {"applied": False, "steps": 0, "reason": "no_targets"}

    view_order = list(range(len(bake_targets)))
    random.shuffle(view_order)
    view_cursor = 0
    last_loss = 0.0
    last_l1 = 0.0
    last_ssim = 0.0
    mode = str(exposure_params.get("mode", "appearance"))
    _log_line(job, f"Baking {mode} compensation into the exported static asset over {bake_steps} steps.")

    for step in range(bake_steps):
        if _should_stop(job["id"]):
            raise RuntimeError("Stopped during appearance baking.")
        if view_cursor >= len(view_order):
            random.shuffle(view_order)
            view_cursor = 0
        baked_sample = bake_targets[view_order[view_cursor]]
        view = baked_sample["view"]
        view_cursor += 1

        assert isinstance(view, TrainingView)
        target_alpha = baked_sample["target_alpha"]
        backgrounds = baked_sample["backgrounds"]
        baked_target = baked_sample["baked_target"]
        raw_colors_pred, _alpha_pred, _ = _render_view(
            splats,
            view,
            device,
            sh_degree_to_use=sh_degree,
            absgrad=absgrad,
            backgrounds=backgrounds,
            rasterize_mode=rasterize_mode,
        )

        if view.has_alpha:
            pixel_weights = 0.15 + (0.85 * target_alpha)
            l1_loss = (torch.abs(raw_colors_pred - baked_target) * pixel_weights).sum() / (
                pixel_weights.sum() * 3.0
            ).clamp_min(1.0e-6)
            ssim_score = _appearance_ssim(raw_colors_pred, baked_target, weights=target_alpha)
        else:
            l1_loss = F.l1_loss(raw_colors_pred, baked_target)
            ssim_score = _appearance_ssim(raw_colors_pred, baked_target)

        loss = ((1.0 - lambda_dssim) * l1_loss) + (lambda_dssim * (1.0 - ssim_score))
        loss.backward()
        last_loss = float(loss.item())
        last_l1 = float(l1_loss.item())
        last_ssim = float(ssim_score.item())

        for optimizer in bake_optimizers.values():
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
        for name, parameter in splats.items():
            if name not in {"sh0", "shN"}:
                parameter.grad = None

        if step == 0 or (step + 1) % 25 == 0 or step == bake_steps - 1:
            _update(
                job["id"],
                "Baking appearance",
                0.84 + (0.08 * ((step + 1) / bake_steps)),
                f"Appearance bake {step + 1}/{bake_steps} | loss={last_loss:.4f} | l1={last_l1:.4f} | ssim={last_ssim:.4f}",
            )
            _log_line(
                job,
                f"Appearance bake step {step + 1}/{bake_steps}: loss={last_loss:.5f}, l1={last_l1:.5f}, ssim={last_ssim:.5f}",
            )

    return {
        "applied": True,
        "steps": bake_steps,
        "last_loss": last_loss,
        "last_l1": last_l1,
        "last_ssim": last_ssim,
        "mode": mode,
    }


@torch.no_grad()
def _coordinate_outlier_keep_mask(means: torch.Tensor) -> tuple[torch.Tensor | None, dict[str, float]]:
    if means.ndim != 2 or means.shape[0] < 1024:
        return None, {}

    center = means.median(dim=0).values
    distances = torch.linalg.norm(means - center, dim=1)
    q99_value = float(torch.quantile(distances, 0.99).item())
    q995_value = float(torch.quantile(distances, 0.995).item())
    q999_value = float(torch.quantile(distances, 0.999).item())
    q9999_value = float(torch.quantile(distances, 0.9999).item())
    max_value = float(distances.max().item())
    if not all(math.isfinite(value) for value in (q99_value, q995_value, q999_value, q9999_value, max_value)):
        return None, {}

    extreme_tail_detected = (
        q9999_value > max(q99_value * 20.0, q995_value * 8.0)
        or max_value > max(q99_value * 50.0, q999_value * 6.0)
    )
    if not extreme_tail_detected:
        return None, {
            "q99": q99_value,
            "q995": q995_value,
            "q999": q999_value,
            "q9999": q9999_value,
            "max": max_value,
            "threshold": 0.0,
        }

    prune_threshold = max(q99_value * 3.0, q995_value * 1.75)
    keep_mask = distances <= prune_threshold
    removed = int((~keep_mask).sum().item())
    max_removed = max(1024, int(means.shape[0] * 0.02))
    if removed > max_removed:
        prune_threshold = float(torch.quantile(distances, 1.0 - (max_removed / float(means.shape[0]))).item())
        keep_mask = distances <= prune_threshold

    return keep_mask, {
        "q99": q99_value,
        "q995": q995_value,
        "q999": q999_value,
        "q9999": q9999_value,
        "max": max_value,
        "threshold": prune_threshold,
    }


@torch.no_grad()
def _prune_coordinate_outliers(
    splats: torch.nn.ParameterDict,
    *,
    job: dict | None = None,
) -> int:
    means = splats["means"].detach()
    keep_mask, stats = _coordinate_outlier_keep_mask(means)
    if keep_mask is None:
        return 0

    removed = int((~keep_mask).sum().item())
    if removed <= 0:
        return 0

    for name in list(splats.keys()):
        parameter = splats[name]
        filtered = parameter.detach()[keep_mask].clone()
        splats[name] = torch.nn.Parameter(filtered, requires_grad=parameter.requires_grad)

    if job is not None:
        _log_line(
            job,
            f"Pruned {removed:,} coordinate outlier splats after training "
            f"(q99={stats['q99']:.2f}, q995={stats['q995']:.2f}, "
            f"q999={stats['q999']:.2f}, q9999={stats['q9999']:.2f}, "
            f"max={stats['max']:.2f}, threshold={stats['threshold']:.2f}).",
        )
    return removed


@torch.no_grad()
def _sanitize_depth_reinit_candidates(
    points: torch.Tensor,
    colors: torch.Tensor,
    reference_means: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    if points.numel() == 0 or colors.numel() == 0:
        return (
            torch.empty((0, 3), dtype=torch.float32, device=reference_means.device),
            torch.empty((0, 3), dtype=torch.float32, device=reference_means.device),
        )

    finite_mask = torch.isfinite(points).all(dim=-1) & torch.isfinite(colors).all(dim=-1)
    if not torch.any(finite_mask):
        return (
            torch.empty((0, 3), dtype=torch.float32, device=reference_means.device),
            torch.empty((0, 3), dtype=torch.float32, device=reference_means.device),
        )

    points = points[finite_mask]
    colors = colors[finite_mask].clamp(0.0, 1.0)

    if len(points) == 0:
        return points, colors

    if len(reference_means) >= 64:
        center = reference_means.median(dim=0).values
        reference_distances = torch.linalg.norm(reference_means - center, dim=1)
        threshold = float(torch.quantile(reference_distances, 0.995).item()) * 1.5
        threshold = max(threshold, float(reference_distances.mean().item()) * 4.0, 0.25)
        candidate_distances = torch.linalg.norm(points - center, dim=1)
        distance_mask = candidate_distances <= threshold
        if torch.any(distance_mask):
            points = points[distance_mask]
            colors = colors[distance_mask]

    return points, colors


@torch.no_grad()
def _splat_parameters_are_finite(splats: torch.nn.ParameterDict) -> bool:
    required = ("means", "scales", "quats", "opacities", "sh0", "shN")
    for key in required:
        value = splats[key].detach()
        if not torch.isfinite(value).all():
            return False
    quat_norms = torch.linalg.norm(splats["quats"].detach(), dim=-1)
    if not torch.isfinite(quat_norms).all():
        return False
    return True


def _depth_reinit_points_from_views(
    splats: torch.nn.ParameterDict,
    views: list[TrainingView],
    device: torch.device,
    *,
    sh_degree: int,
    absgrad: bool,
    rasterize_mode: str,
    exposure_params: dict[str, torch.nn.Parameter | tuple[int, ...] | int] | None,
    max_views: int,
    max_points_per_view: int,
) -> tuple[torch.Tensor, torch.Tensor]:
    sampled_points: list[torch.Tensor] = []
    sampled_colors: list[torch.Tensor] = []
    chosen_views = random.sample(views, k=min(len(views), max(1, max_views)))

    for view in chosen_views:
        _target_rgb, _target_alpha, backgrounds, pixels = _build_target_pixels(
            view,
            device,
            random_background=False,
        )
        renders, alpha_pred, _ = _render_view_raw(
            splats,
            view,
            device,
            sh_degree_to_use=sh_degree,
            absgrad=absgrad,
            backgrounds=backgrounds,
            rasterize_mode=rasterize_mode,
            render_mode="RGB+D",
        )
        colors_pred = _apply_exposure_compensation(
            renders[..., :3],
            view,
            exposure_params,
            guide_pixels=renders[..., :3],
        )
        depth_pred = renders[..., 3:4]
        edge_backbone = _laplacian_edge_backbone(pixels)
        residual = torch.mean(torch.abs(colors_pred.detach() - pixels.detach()), dim=-1)
        residual = residual / residual.amax(dim=(-2, -1), keepdim=True).clamp_min(1.0e-6)

        importance = edge_backbone * (0.35 + (0.65 * residual))
        valid = (depth_pred[0, :, :, 0] > 1.0e-4) & (alpha_pred[0, :, :, 0] > 0.15)
        importance = importance[0] * valid.to(importance.dtype)
        positive = importance > 0.0
        if not torch.any(positive):
            continue

        flat_importance = importance.reshape(-1)
        candidate_count = min(int(positive.sum().item()), max_points_per_view)
        top_values, top_indices = torch.topk(flat_importance, k=max(1, candidate_count))
        keep = top_values > 0.0
        if not torch.any(keep):
            continue
        top_indices = top_indices[keep]

        u = (top_indices % view.width).to(torch.float32)
        v = (top_indices // view.width).to(torch.float32)
        ray_dirs, ray_valid = _unproject_image_pixels_to_camera_rays(view, u, v)
        if not torch.any(ray_valid):
            continue

        u = u[ray_valid]
        v = v[ray_valid]
        ray_dirs = ray_dirs[ray_valid]
        depth = depth_pred[0, v.long(), u.long(), 0]
        x = ray_dirs[:, 0] * depth
        y = ray_dirs[:, 1] * depth
        cam_points = torch.stack([x, y, depth, torch.ones_like(depth)], dim=-1)
        world_points = cam_points @ view.camtoworld.to(device).T

        sampled_points.append(world_points[:, :3])
        sampled_colors.append(pixels[0, v.long(), u.long(), :3])

    if not sampled_points:
        return (
            torch.empty((0, 3), dtype=torch.float32, device=device),
            torch.empty((0, 3), dtype=torch.float32, device=device),
        )
    return torch.cat(sampled_points, dim=0), torch.cat(sampled_colors, dim=0)


@torch.no_grad()
def _reinitialize_gaussians_from_depth_points(
    splats: torch.nn.ParameterDict,
    optimizers: dict[str, torch.optim.Optimizer],
    state: dict[str, object],
    points: torch.Tensor,
    colors: torch.Tensor,
    *,
    init_opacity: float,
) -> int:
    if points.shape[0] < 16:
        return 0

    points, colors = _sanitize_depth_reinit_candidates(points, colors, splats["means"].detach())
    if points.shape[0] < 16:
        return 0

    target_count = min(points.shape[0], max(64, int(len(splats["means"]) * 0.01)))
    if target_count <= 0:
        return 0

    replace_indices = torch.topk(
        -torch.sigmoid(splats["opacities"]).flatten(),
        k=min(target_count, len(splats["means"])),
    ).indices
    points = points[: len(replace_indices)]
    colors = colors[: len(replace_indices)]
    scales = _knn_mean_distance(points).unsqueeze(-1).repeat(1, 3).clamp_min(1.0e-3)
    valid_replacements = torch.isfinite(points).all(dim=-1) & torch.isfinite(colors).all(dim=-1) & torch.isfinite(scales).all(dim=-1)
    if not torch.any(valid_replacements):
        return 0
    points = points[valid_replacements]
    colors = colors[valid_replacements]
    scales = scales[valid_replacements]
    replace_indices = replace_indices[: len(points)]
    if len(replace_indices) < 16:
        return 0
    quats = torch.zeros((len(replace_indices), 4), dtype=torch.float32, device=points.device)
    quats[:, 0] = 1.0
    opacities = torch.full((len(replace_indices),), float(init_opacity), dtype=torch.float32, device=points.device)
    sh0 = _rgb_to_sh(colors).unsqueeze(1)
    shn_shape = splats["shN"].shape[1:]
    shn = torch.zeros((len(replace_indices), *shn_shape), dtype=torch.float32, device=points.device)

    replacement_map = {
        "means": points,
        "scales": torch.log(scales),
        "quats": quats,
        "opacities": torch.logit(opacities.clamp(1.0e-4, 1.0 - 1.0e-4)),
        "sh0": sh0,
        "shN": shn,
    }

    def param_fn(name: str, tensor: torch.Tensor) -> torch.nn.Parameter:
        updated = tensor.clone()
        updated[replace_indices] = replacement_map[name]
        return torch.nn.Parameter(updated, requires_grad=tensor.requires_grad)

    def optimizer_fn(_key: str, value: torch.Tensor) -> torch.Tensor:
        updated = value.clone()
        updated[replace_indices] = 0
        return updated

    _update_param_with_optimizer(param_fn, optimizer_fn, splats, optimizers)
    for key, value in list(state.items()):
        if isinstance(value, torch.Tensor):
            value[replace_indices] = 0
    if not _splat_parameters_are_finite(splats):
        raise RuntimeError("Depth reinitialization produced non-finite Gaussian parameters.")
    return int(len(replace_indices))


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
                view_index=len(aligned_views),
                image_name=view.image_name,
                rgb_tensor=view.rgb_tensor,
                alpha_tensor=view.alpha_tensor,
                has_alpha=view.has_alpha,
                camtoworld=aligned_pose,
                K=view.K,
                width=view.width,
                height=view.height,
                camera_model=view.camera_model,
                radial_coeffs=view.radial_coeffs,
                tangential_coeffs=view.tangential_coeffs,
                thin_prism_coeffs=view.thin_prism_coeffs,
                use_unscented_transform=view.use_unscented_transform,
                projection_camera_model_name=view.projection_camera_model_name,
                projection_camera_params=view.projection_camera_params,
                source_camera_model_name=view.source_camera_model_name,
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
        u, v, inside = _project_camera_points_to_pixel_indices(view, camera_points[:, :3])
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
        u, v, inside = _project_camera_points_to_pixel_indices(view, camera_points[:, :3])
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
        u, v, inside = _project_camera_points_to_pixel_indices(view, camera_points[:, :3])
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
        u, v, inside = _project_camera_points_to_pixel_indices(view, camera_points[:, :3])
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
        u, v, inside = _project_camera_points_to_pixel_indices(view, camera_points[:, :3])
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


def _adaptive_visual_hull_seed_points(
    seed_builder,
    *,
    grid_size: int,
    support_ratio: float,
    desired_seed_points: int,
    minimum_seed_points: int,
) -> tuple[torch.Tensor, torch.Tensor] | None:
    support_candidates = []
    for value in (support_ratio, min(support_ratio, 0.7), 0.6, 0.5):
        if value not in support_candidates:
            support_candidates.append(value)

    grid_candidates = []
    for value in (grid_size, max(grid_size, 48), max(grid_size, 56), max(grid_size, 64)):
        if value not in grid_candidates:
            grid_candidates.append(value)

    best_seed: tuple[torch.Tensor, torch.Tensor] | None = None
    best_count = -1
    for candidate_grid in grid_candidates:
        for candidate_support in support_candidates:
            seed = seed_builder(candidate_grid, candidate_support, desired_seed_points)
            if seed is None:
                continue
            point_count = int(len(seed[0]))
            if point_count > best_count:
                best_seed = seed
                best_count = point_count
            if point_count >= minimum_seed_points:
                return seed
    return best_seed


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


def _probe_sqlite_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    probe_path = database_path.with_name(f"{database_path.stem}_probe{database_path.suffix}")
    try:
        with sqlite3.connect(probe_path) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS gp_probe(id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO gp_probe(value) VALUES ('ok')")
            connection.execute("DELETE FROM gp_probe")
            connection.commit()
    except sqlite3.Error as error:
        raise RuntimeError(
            f"COLMAP scratch database is not writable at {database_path}. "
            "Move the companion scratch directory to a local temp folder."
        ) from error
    finally:
        if probe_path.exists():
            try:
                probe_path.unlink()
            except OSError:
                pass


def _run_colmap(project: dict, job: dict, settings: dict) -> pycolmap.Reconstruction:
    image_dir = _prepare_colmap_images(project)
    image_paths = sorted(path for path in image_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)
    colmap_dir = paths.project_colmap_scratch_dir(project["id"])
    database_path = colmap_dir / "database.db"
    sparse_dir = colmap_dir / "sparse"
    video_derived = _project_is_video_derived(project)
    long_video_sequence = _is_long_video_sequence(project, len(image_paths))
    quality_preset = _normalize_quality_preset(settings)

    preserve_sfm_cache = bool(settings.get("preserve_sfm_cache"))
    if settings.get("force_restart") and not preserve_sfm_cache:
        if colmap_dir.exists():
            shutil.rmtree(colmap_dir)
        colmap_dir.mkdir(parents=True, exist_ok=True)
    else:
        colmap_dir.mkdir(parents=True, exist_ok=True)

    if sparse_dir.exists():
        existing = sorted(path for path in sparse_dir.iterdir() if path.is_dir())
        if existing:
            cached_reconstruction = pycolmap.Reconstruction(existing[0])
            cached_stats = _reconstruction_registration_stats(cached_reconstruction, len(image_paths))
            if bool(cached_stats["usable"]):
                _log_line(
                    job,
                    f"Reusing cached COLMAP reconstruction from {existing[0]} "
                    f"({_reconstruction_registration_summary(cached_stats)}).",
                )
                return cached_reconstruction
            _log_line(
                job,
                f"Discarding cached COLMAP reconstruction from {existing[0]} because coverage is too low "
                f"({_reconstruction_registration_summary(cached_stats)}).",
            )
            shutil.rmtree(sparse_dir, ignore_errors=True)

    _log_line(job, f"COLMAP scratch workspace: {colmap_dir}")
    _log_line(job, f"Prepared {len(image_paths)} normalized images for SfM.")

    sfm_threads = _sfm_thread_count(settings)
    if long_video_sequence and sfm_threads > 4:
        _log_line(
            job,
            f"Capping SfM threads from {sfm_threads} to 4 for long video matching safety.",
        )
        sfm_threads = 4

    requested_sfm_image_size = int(settings.get("sfm_max_image_size", 1600))
    if long_video_sequence and requested_sfm_image_size > 1280:
        _log_line(
            job,
            f"Capping SfM image size from {requested_sfm_image_size} to 1280 for long video matching safety.",
        )
        requested_sfm_image_size = 1280

    mapping_options = pycolmap.IncrementalPipelineOptions()
    mapping_options.min_model_size = 3
    mapping_options.extract_colors = True
    mapping_options.num_threads = sfm_threads

    def _run_mapping_attempt(match_mode: str, *, retry_with_expanded_budget: bool = False) -> pycolmap.Reconstruction:
        if database_path.exists():
            database_path.unlink()
        if sparse_dir.exists():
            shutil.rmtree(sparse_dir, ignore_errors=True)
        sparse_dir.mkdir(parents=True, exist_ok=True)
        _probe_sqlite_database(database_path)

        extraction_options = pycolmap.FeatureExtractionOptions()
        extraction_options.max_image_size = requested_sfm_image_size
        extraction_options.num_threads = sfm_threads
        extraction_options.use_gpu = False
        if video_derived or quality_preset == "high" or retry_with_expanded_budget:
            default_feature_cap = 8_000 if video_derived and len(image_paths) >= 96 else 12_000 if video_derived else 10_000
            configured_cap = int(settings.get("sfm_max_num_features", default_feature_cap))
            if retry_with_expanded_budget:
                configured_cap = max(configured_cap, 16_000)
            extraction_options.sift.max_num_features = configured_cap
            extraction_options.sift.domain_size_pooling = True
        if video_derived:
            extraction_options.sift.darkness_adaptivity = True

        matching_options = pycolmap.FeatureMatchingOptions()
        matching_options.num_threads = sfm_threads
        matching_options.use_gpu = False
        matching_options.guided_matching = True
        if video_derived or retry_with_expanded_budget:
            configured_matches = int(settings.get("sfm_max_num_matches", 32_768))
            if retry_with_expanded_budget:
                configured_matches = max(configured_matches, 65_536)
            matching_options.max_num_matches = configured_matches

        _update(job["id"], "COLMAP", 0.08, "Extracting image features.")
        _log_line(
            job,
            "COLMAP feature setup "
            f"max_image_size={extraction_options.max_image_size} "
            f"max_features={extraction_options.sift.max_num_features} "
            f"max_matches={matching_options.max_num_matches} "
            f"threads={sfm_threads} "
            f"dsp={'on' if extraction_options.sift.domain_size_pooling else 'off'} "
            f"video={'yes' if video_derived else 'no'}"
            f"{' retry=expanded' if retry_with_expanded_budget else ''}",
        )

        pycolmap.extract_features(database_path, image_dir, extraction_options=extraction_options)
        if _should_stop(job["id"]):
            raise RuntimeError("Stopped during feature extraction.")
        _log_line(job, "COLMAP feature extraction finished.")

        _update(job["id"], "COLMAP", 0.14, f"Matching image features with {match_mode} mode.")
        if match_mode == "sequential":
            pairing_options = pycolmap.SequentialPairingOptions()
            default_overlap = 5 if long_video_sequence else 8 if video_derived else 10
            overlap = int(settings.get("sfm_sequential_overlap", default_overlap))
            if retry_with_expanded_budget:
                overlap = max(overlap, min(max(2, len(image_paths) - 1), 14 if long_video_sequence else 18))
            pairing_options.overlap = overlap
            pairing_options.quadratic_overlap = bool(settings.get("sfm_quadratic_overlap", not video_derived))
            if retry_with_expanded_budget:
                pairing_options.quadratic_overlap = True
            _log_line(
                job,
                "COLMAP sequential matching setup "
                f"overlap={pairing_options.overlap} "
                f"quadratic_overlap={'on' if pairing_options.quadratic_overlap else 'off'}",
            )
            pycolmap.match_sequential(database_path, matching_options=matching_options, pairing_options=pairing_options)
        elif match_mode == "spatial":
            pycolmap.match_spatial(database_path, matching_options=matching_options)
        else:
            pycolmap.match_exhaustive(database_path, matching_options=matching_options)
        if _should_stop(job["id"]):
            raise RuntimeError("Stopped during feature matching.")
        _log_line(job, f"COLMAP {match_mode} matching finished.")

        _update(job["id"], "COLMAP", 0.22, "Recovering camera poses and sparse points.")
        reconstructions = pycolmap.incremental_mapping(database_path, image_dir, sparse_dir, options=mapping_options)
        reconstruction = _pick_reconstruction(reconstructions)
        stats = _reconstruction_registration_stats(reconstruction, len(image_paths))
        _log_line(
            job,
            "COLMAP reconstruction recovered "
            f"{int(stats['registered_images'])} registered images and {int(stats['points3d'])} sparse points "
            f"({_reconstruction_registration_summary(stats)}).",
        )
        return reconstruction

    match_mode = _resolve_sfm_match_mode(settings, len(image_paths), project)
    reconstruction = _run_mapping_attempt(match_mode, retry_with_expanded_budget=False)
    coverage_stats = _reconstruction_registration_stats(reconstruction, len(image_paths))
    if bool(coverage_stats["usable"]):
        return reconstruction

    alternate_mode = _alternate_sfm_match_mode(match_mode, len(image_paths))
    if alternate_mode is not None:
        _log_line(
            job,
            "COLMAP coverage is too low for training "
            f"({_reconstruction_registration_summary(coverage_stats)}); retrying from scratch with "
            f"{alternate_mode} matching and expanded feature budget.",
        )
        reconstruction = _run_mapping_attempt(alternate_mode, retry_with_expanded_budget=True)
        coverage_stats = _reconstruction_registration_stats(reconstruction, len(image_paths))
        if bool(coverage_stats["usable"]):
            return reconstruction

    raise RuntimeError(
        "COLMAP registered too few cameras for stable Gaussian Splat training "
        f"({_reconstruction_registration_summary(coverage_stats)})."
    )


def _load_training_views_from_reconstruction(
    project: dict,
    reconstruction: pycolmap.Reconstruction,
    settings: dict,
) -> list[TrainingView]:
    image_dir = Path(project["input_dir"])
    target_resolution = int(settings.get("train_resolution", 640))
    enable_unscented_transform = bool(settings.get("enable_unscented_transform", True))
    selected_image_names = _select_registered_training_images(reconstruction)
    views: list[TrainingView] = []

    for image in reconstruction.images.values():
        if selected_image_names is not None and image.name not in selected_image_names:
            continue
        image_path = image_dir / image.name
        if not image_path.exists() or not image.has_pose:
            continue
        source_camera = reconstruction.cameras[image.camera_id]
        rgb_pixels, alpha_pixels, width, height, _scale, has_alpha = _load_image_tensor(image_path, target_resolution)
        camera = _scaled_pycolmap_camera(source_camera, width, height)
        projection_spec = _colmap_camera_to_training_spec(
            camera,
            enable_unscented_transform=enable_unscented_transform,
            normalize_distortion=bool(settings.get("normalize_colmap_cameras", True)),
        )

        if projection_spec.requires_image_normalization and projection_spec.projection_camera_model_name is not None:
            rgb_pixels, alpha_pixels, K = _undistort_training_image_from_colmap_camera(
                rgb_pixels,
                alpha_pixels,
                camera,
                projection_spec.projection_camera_model_name,
            )
        else:
            K = torch.tensor(camera.calibration_matrix(), dtype=torch.float32)

        views.append(
            TrainingView(
                view_index=len(views),
                image_name=image.name,
                rgb_tensor=rgb_pixels,
                alpha_tensor=alpha_pixels,
                has_alpha=has_alpha,
                camtoworld=_build_camtoworld(image.cam_from_world()),
                K=K,
                width=width,
                height=height,
                camera_model=projection_spec.camera_model,
                radial_coeffs=projection_spec.radial_coeffs,
                tangential_coeffs=projection_spec.tangential_coeffs,
                thin_prism_coeffs=projection_spec.thin_prism_coeffs,
                use_unscented_transform=projection_spec.use_unscented_transform,
                projection_camera_model_name=projection_spec.projection_camera_model_name,
                projection_camera_params=projection_spec.projection_camera_params,
                source_camera_model_name=str(camera.model_name).upper(),
            )
        )
    if len(views) < 2:
        raise RuntimeError("COLMAP registered too few cameras to start Gaussian Splat training.")
    return views


def _load_training_views(
    project: dict,
    reconstruction: pycolmap.Reconstruction,
    settings: dict,
    *,
    prefer_manifest: bool = True,
) -> list[TrainingView]:
    manifest_path = _find_transforms_manifest(project["id"]) if prefer_manifest else None
    if manifest_path:
        views = _load_training_views_from_transforms(project, manifest_path, reconstruction, settings)
        if views:
            return views
    return _load_training_views_from_reconstruction(project, reconstruction, settings)


def _resolve_manifest_image_path(project: dict, frame_path_value: object) -> Path | None:
    raw_path = Path(str(frame_path_value or "")).expanduser()
    if str(raw_path) == ".":
        return None
    if raw_path.is_absolute():
        return raw_path if raw_path.exists() else None

    project_root = paths.project_root(project["id"])
    input_dir = paths.project_input_dir(project["id"])
    candidates = [
        project_root / raw_path,
        input_dir / raw_path,
        input_dir / raw_path.name,
        project_root / raw_path.name,
    ]
    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(candidate.resolve(strict=False))
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate.exists():
            return candidate
    return None


def _load_training_views_from_transforms(
    project: dict,
    manifest_path: Path,
    reconstruction: pycolmap.Reconstruction | None,
    settings: dict,
) -> list[TrainingView]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    frames = payload.get("frames", [])
    if not frames:
        return []

    target_resolution = int(settings.get("train_resolution", 640))
    normalize_manifest_cameras = bool(settings.get("normalize_manifest_cameras", True))
    views: list[TrainingView] = []

    for frame in frames:
        image_path = _resolve_manifest_image_path(project, frame.get("file_path"))
        if image_path is None:
            continue
        rgb_pixels, alpha_pixels, width, height, _scale, has_alpha = _load_image_tensor(image_path, target_resolution)
        K = _manifest_intrinsics_matrix(payload, width, height)
        projection_spec, source_camera, source_camera_model_name = _manifest_camera_to_training_spec(
            payload,
            frame,
            K=K,
            width=width,
            height=height,
            normalize_distortion=normalize_manifest_cameras,
        )
        if projection_spec.requires_image_normalization and source_camera is not None and projection_spec.projection_camera_model_name is not None:
            rgb_pixels, alpha_pixels, K = _undistort_training_image_from_colmap_camera(
                rgb_pixels,
                alpha_pixels,
                source_camera,
                projection_spec.projection_camera_model_name,
            )
        if not bool(settings.get("enable_unscented_transform", True)) and projection_spec.use_unscented_transform:
            projection_spec = CameraProjectionSpec(
                camera_model=projection_spec.camera_model,
                radial_coeffs=projection_spec.radial_coeffs,
                tangential_coeffs=projection_spec.tangential_coeffs,
                thin_prism_coeffs=projection_spec.thin_prism_coeffs,
                use_unscented_transform=False,
                projection_camera_model_name=projection_spec.projection_camera_model_name,
                projection_camera_params=projection_spec.projection_camera_params,
                requires_image_normalization=projection_spec.requires_image_normalization,
            )

        views.append(
            TrainingView(
                view_index=len(views),
                image_name=image_path.name,
                rgb_tensor=rgb_pixels,
                alpha_tensor=alpha_pixels,
                has_alpha=has_alpha,
                camtoworld=_convert_blender_camtoworld(frame["transform_matrix"]),
                K=K,
                width=width,
                height=height,
                camera_model=projection_spec.camera_model,
                radial_coeffs=projection_spec.radial_coeffs,
                tangential_coeffs=projection_spec.tangential_coeffs,
                thin_prism_coeffs=projection_spec.thin_prism_coeffs,
                use_unscented_transform=projection_spec.use_unscented_transform,
                projection_camera_model_name=projection_spec.projection_camera_model_name,
                projection_camera_params=projection_spec.projection_camera_params,
                source_camera_model_name=source_camera_model_name,
            )
        )
    if reconstruction is None:
        return views
    return _align_views_to_reconstruction(views, reconstruction)


def _known_camera_manifest_skip_reason(views: list[TrainingView]) -> str | None:
    if not views:
        return "no usable project images matched the manifest"
    if len(views) < 4:
        return f"only {len(views)} usable view(s) matched the manifest; known-camera initialization needs at least 4"
    missing_alpha = sum(1 for view in views if not view.has_alpha)
    if missing_alpha:
        return f"{missing_alpha} matched view(s) are missing alpha masks"
    return None


def _train_gaussians(
    project: dict,
    job: dict,
    settings: dict,
    reconstruction: pycolmap.Reconstruction | None,
    views: list[TrainingView],
    dataset_diagnostics: dict[str, object] | None = None,
) -> tuple[Path, int, dict, dict[str, object]]:
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
    minimum_seed_points = max(256, min(desired_seed_points, 512))
    depth_bootstrap_summary: dict[str, object] = {
        "enabled": bool(settings.get("enable_depth_bootstrap", True)) and not known_camera_mode,
        "attempted": False,
        "seeded_points": 0,
    }

    if known_camera_mode:
        manifest_seed = _adaptive_visual_hull_seed_points(
            lambda candidate_grid, candidate_support, max_points: _build_manifest_visual_hull_seed_points(
                views,
                grid_size=candidate_grid,
                alpha_threshold=alpha_threshold,
                support_ratio=candidate_support,
                max_points=max(32, max_points),
            ),
            grid_size=grid_size,
            support_ratio=support_ratio,
            desired_seed_points=max(32, desired_seed_points),
            minimum_seed_points=minimum_seed_points,
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
        point_errors = []
        track_lengths = []
        for point in reconstruction.points3D.values():
            points.append(point.xyz)
            colors.append([channel / 255.0 for channel in point.color])
            point_errors.append(float(point.error) if math.isfinite(float(point.error)) else float("inf"))
            track_lengths.append(int(point.track.length()))
        points_tensor = torch.from_numpy(np.asarray(points, dtype=np.float32))
        rgb_tensor = torch.from_numpy(np.asarray(colors, dtype=np.float32))
        raw_sparse_points_tensor = points_tensor
        raw_sparse_rgb_tensor = rgb_tensor
        points_tensor, rgb_tensor = _filter_sparse_seed_points(
            points_tensor,
            rgb_tensor,
            np.asarray(point_errors, dtype=np.float32),
            np.asarray(track_lengths, dtype=np.int32),
            job=job,
        )
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
            if len(filtered_points) >= minimum_seed_points or len(points_tensor) < minimum_seed_points:
                points_tensor = filtered_points
                rgb_tensor = filtered_colors
            else:
                _log_line(
                    job,
                    f"Foreground mask filtering was too aggressive for initialization ({len(filtered_points)} seeds); keeping the unfiltered sparse cloud with {len(points_tensor)} points.",
                )

        if bool(settings.get("enable_depth_bootstrap", True)):
            depth_bootstrap_summary["attempted"] = True
            try:
                dense_points, dense_colors, dense_summary = _build_dense_depth_seed_points(
                    project,
                    job,
                    settings,
                    reconstruction,
                    views,
                    points_tensor,
                )
            except Exception as error:
                dense_points = torch.empty((0, 3), dtype=torch.float32)
                dense_colors = torch.empty((0, 3), dtype=torch.float32)
                dense_summary = {
                    "enabled": True,
                    "seeded_points": 0,
                    "error": str(error),
                }
                _log_line(job, f"Dense depth bootstrap fell back to sparse-only initialization: {error}")
            depth_bootstrap_summary.update(dense_summary)
            if len(dense_points) >= 128:
                merged_points = torch.cat([points_tensor, dense_points], dim=0)
                merged_colors = torch.cat([rgb_tensor, dense_colors], dim=0)
                merge_voxel_size = max(
                    1.0e-4,
                    float(torch.median(_knn_mean_distance(points_tensor if len(points_tensor) >= 2 else merged_points)).item()) * 0.35,
                )
                points_tensor, rgb_tensor = _voxel_downsample_seed_cloud(
                    merged_points,
                    merged_colors,
                    voxel_size=merge_voxel_size,
                    max_points=len(merged_points),
                )
                _log_line(
                    job,
                    f"Dense depth bootstrap added {len(dense_points)} seed points; merged initialization now has {len(points_tensor)} points.",
                )
            else:
                _log_line(job, "Dense depth bootstrap did not produce enough reliable points to augment initialization.")

        if len(points_tensor) < desired_seed_points:
            hull_seed = _adaptive_visual_hull_seed_points(
                lambda candidate_grid, candidate_support, max_points: _build_visual_hull_seed_points(
                    points_tensor,
                    views,
                    grid_size=candidate_grid,
                    alpha_threshold=alpha_threshold,
                    support_ratio=candidate_support,
                    max_points=max(0, max_points - len(points_tensor)),
                ),
                grid_size=grid_size,
                support_ratio=support_ratio,
                desired_seed_points=desired_seed_points,
                minimum_seed_points=minimum_seed_points,
            )
            if hull_seed is not None:
                hull_points, hull_colors = hull_seed
                if len(hull_points) > 0:
                    _log_line(job, f"Visual-hull seeding added {len(hull_points)} foreground init points from alpha masks.")
                    points_tensor = torch.cat([points_tensor, hull_points], dim=0)
                    rgb_tensor = torch.cat([rgb_tensor, hull_colors], dim=0)

        if len(points_tensor) < minimum_seed_points and len(raw_sparse_points_tensor) > len(points_tensor):
            _log_line(
                job,
                f"Initialization remained starved at {len(points_tensor)} seeds after mask/hull filtering; restoring the original sparse cloud with {len(raw_sparse_points_tensor)} points.",
            )
            points_tensor = raw_sparse_points_tensor
            rgb_tensor = raw_sparse_rgb_tensor

    if len(points_tensor) < 16:
        raise RuntimeError("Sparse reconstruction produced too few points for Gaussian initialization.")

    points_tensor = points_tensor.to(device)
    rgb_tensor = rgb_tensor.to(device)
    neighbor_distance = _knn_mean_distance(points_tensor)
    scene_scale = float(torch.linalg.norm(points_tensor - points_tensor.mean(dim=0), dim=1).max().item())
    initial_gaussian_count = int(len(points_tensor))
    projection_diagnostics = _view_projection_diagnostics(views)

    sh_degree = int(settings.get("sh_degree", 3))
    init_opacity = float(settings.get("init_opacity", 0.30))
    sh_dim = (sh_degree + 1) ** 2
    training_profile = _resolve_training_profile(
        settings,
        len(views),
        initial_gaussian_count,
        dataset_diagnostics=dataset_diagnostics,
        projection_diagnostics=projection_diagnostics,
    )
    train_views, validation_views = split_training_views(
        views,
        validation_fraction=float(settings.get("validation_fraction", 0.18)),
        min_validation_views=int(settings.get("min_validation_views", 2)),
    )
    rasterize_mode = str(training_profile["rasterize_mode"])
    strategy_name = str(training_profile["strategy_name"])
    target_gaussian_budget = int(training_profile["max_gaussians"])
    budget_schedule = str(training_profile.get("budget_schedule") or "staged")
    distorted_view_count = int(projection_diagnostics["ut_views"])
    normalized_camera_view_count = int(projection_diagnostics["normalized_views"])
    torch_extensions_dir = _ensure_torch_extensions_dir()
    _log_line(job, f"Using torch extensions cache at {torch_extensions_dir}")
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

    appearance_mode = str(settings.get("appearance_mode") or "off").strip().lower()
    if appearance_mode not in {"bilateral", "affine", "off"}:
        appearance_mode = "off"
    appearance_requested = bool(settings.get("enable_appearance_compensation", False)) and bool(
        settings.get("enable_exposure_compensation", True)
    )
    appearance_enabled = appearance_requested and appearance_mode != "off"
    exposure_params: dict[str, torch.nn.Parameter | tuple[int, ...] | int] | None = None
    if appearance_enabled and appearance_mode != "off":
        if appearance_mode == "affine":
            exposure_params = {
                "mode": "affine",
                "log_gains": torch.nn.Parameter(torch.zeros((len(views), 3), dtype=torch.float32, device=device)),
                "rgb_bias": torch.nn.Parameter(torch.zeros((len(views), 3), dtype=torch.float32, device=device)),
            }
        else:
            exposure_params = _create_bilateral_grid_params(len(views), device, settings)
        _log_line(job, f"Appearance compensation enabled in {appearance_mode} mode; export will bake it into the static asset.")

    base_means_lr = float(settings.get("means_lr", 1.6e-4))
    base_scales_lr = float(settings.get("scales_lr", 5.0e-3))
    optimizers = {
        "means": torch.optim.Adam([{"params": splats["means"], "lr": base_means_lr}], eps=1e-15),
        "scales": torch.optim.Adam([{"params": splats["scales"], "lr": base_scales_lr}], eps=1e-15),
        "quats": torch.optim.Adam([{"params": splats["quats"], "lr": float(settings.get("quats_lr", 1.0e-3))}], eps=1e-15),
        "opacities": torch.optim.Adam([{"params": splats["opacities"], "lr": float(settings.get("opacities_lr", 5.0e-2))}], eps=1e-15),
        "sh0": torch.optim.Adam([{"params": splats["sh0"], "lr": float(settings.get("sh0_lr", 2.5e-3))}], eps=1e-15),
        "shN": torch.optim.Adam([{"params": splats["shN"], "lr": float(settings.get("shN_lr", 1.25e-4))}], eps=1e-15),
    }
    auxiliary_optimizers: list[torch.optim.Optimizer] = []
    appearance_grid_optimizer: torch.optim.Optimizer | None = None
    appearance_grid_base_lr = float(settings.get("appearance_grid_lr", 2.0e-3))
    if exposure_params is not None:
        if "log_gains" in exposure_params and "rgb_bias" in exposure_params:
            auxiliary_optimizers.extend(
                [
                    torch.optim.Adam(
                        [{"params": exposure_params["log_gains"], "lr": float(settings.get("exposure_gain_lr", 2.0e-3))}],
                        eps=1e-15,
                    ),
                    torch.optim.Adam(
                        [{"params": exposure_params["rgb_bias"], "lr": float(settings.get("exposure_bias_lr", 1.0e-3))}],
                        eps=1e-15,
                    ),
                ]
            )
        else:
            grid_parameter = exposure_params.get("grid")
            if isinstance(grid_parameter, torch.nn.Parameter):
                appearance_grid_optimizer = torch.optim.Adam(
                    [{"params": [grid_parameter], "lr": appearance_grid_base_lr}],
                    eps=1e-15,
                )
                auxiliary_optimizers.append(appearance_grid_optimizer)

    max_steps = int(settings.get("train_steps", 1200))
    self_organizing_config = _resolve_self_organizing_config(
        settings,
        max_steps=max_steps,
        refine_every=int(training_profile["refine_every"]),
        refine_start_iter=int(training_profile["refine_start_iter"]),
        dataset_diagnostics=dataset_diagnostics,
        projection_diagnostics=projection_diagnostics,
    )
    self_organizing_runtime: dict[str, object] = {
        "events": 0,
        "last_sort_step": None,
        "last_method": None,
        "last_grid_shape": None,
        "last_changed_fraction": None,
    }
    training_runtime_monitor = _initialize_training_runtime_monitor(
        max_steps=max_steps,
        strategy_name=strategy_name,
        self_organizing_config=self_organizing_config,
    )
    screen_space_gradients_enabled = True
    screen_space_gradient_disable_reason: str | None = None
    last_self_organizing_meta: dict[str, object] = {
        "applied": False,
        "reason": "not_run",
        "step": 0,
    }
    mcmc_runtime_mode = "disabled"
    if strategy_name == "mcmc":
        mcmc_runtime_mode = _ensure_mcmc_runtime(job, device)
        strategy_class = MCMCStrategy if mcmc_runtime_mode == "native" else _TorchFallbackMCMCStrategy
        strategy = strategy_class(
            cap_max=target_gaussian_budget,
            noise_lr=float(training_profile["noise_lr"]),
            refine_start_iter=int(training_profile["refine_start_iter"]),
            refine_stop_iter=int(training_profile["refine_stop_iter"]),
            refine_every=int(training_profile["refine_every"]),
            min_opacity=float(training_profile["min_opacity"]),
            verbose=False,
        )
        strategy_absgrad = False
        strategy_state = strategy.initialize_state()
    else:
        default_strategy_kwargs = {
            "prune_opa": float(training_profile["prune_opa"]),
            "grow_grad2d": float(training_profile["grow_grad2d"]),
            "grow_scale3d": float(training_profile["grow_scale3d"]),
            "grow_scale2d": float(training_profile["grow_scale2d"]),
            "prune_scale3d": float(training_profile["prune_scale3d"]),
            "prune_scale2d": float(training_profile["prune_scale2d"]),
            "refine_scale2d_stop_iter": int(training_profile["refine_scale2d_stop_iter"]),
            "verbose": False,
            "refine_start_iter": int(training_profile["refine_start_iter"]),
            "refine_stop_iter": int(training_profile["refine_stop_iter"]),
            "refine_every": int(training_profile["refine_every"]),
            "reset_every": int(training_profile["opacity_reset_interval"]),
            "pause_refine_after_reset": len(views),
            "absgrad": bool(training_profile["absgrad"]),
            "revised_opacity": bool(training_profile["revised_opacity"]),
        }
        if strategy_name == "las":
            strategy = _EdgeAwareLASStrategy(
                edge_threshold=float(training_profile["edge_threshold"]),
                warmup_events=int(training_profile["warmup_events"]),
                candidate_factor=int(training_profile["candidate_factor"]),
                edge_score_weight=float(training_profile["edge_score_weight"]),
                las_primary_shrink=float(training_profile["las_primary_shrink"]),
                las_secondary_shrink=float(training_profile["las_secondary_shrink"]),
                las_opacity_factor=float(training_profile["las_opacity_factor"]),
                las_offset_scale=float(training_profile["las_offset_scale"]),
                **default_strategy_kwargs,
            )
            strategy_absgrad = bool(training_profile["absgrad"])
            strategy_state = strategy.initialize_state(scene_scale=max(scene_scale, 1.0))
        else:
            strategy = DefaultStrategy(**default_strategy_kwargs)
            strategy_absgrad = bool(training_profile["absgrad"])
            strategy_state = strategy.initialize_state(scene_scale=max(scene_scale, 1.0))
    _log_line(
        job,
        "Training profile "
        f"preset={training_profile['preset']} strategy={strategy_name} "
        f"runtime={mcmc_runtime_mode if strategy_name == 'mcmc' else strategy_name} "
        f"budget={target_gaussian_budget:,} init={initial_gaussian_count:,} "
        f"budget_schedule={budget_schedule} "
        f"refine={training_profile['refine_start_iter']}..{training_profile['refine_stop_iter']} "
        f"every={training_profile['refine_every']} adaptive_growth={bool(training_profile.get('adaptive_growth'))} "
        f"auto_reason={str(training_profile.get('auto_strategy_reason') or 'n/a')} "
        f"rasterize={rasterize_mode} "
        f"train_views={len(train_views)} val_views={len(validation_views)} "
        f"appearance={appearance_mode if exposure_params is not None else 'off'} "
        f"sogs={'on' if self_organizing_config.enabled else 'off'} "
        f"ut_views={distorted_view_count}/{len(views)} "
        f"normalized_views={normalized_camera_view_count}",
    )
    strategy.check_sanity(splats, optimizers)
    if self_organizing_config.enabled:
        last_self_organizing_meta = _refresh_self_organizing_layout(
            splats,
            optimizers,
            strategy_state,
            self_organizing_config,
            step=-1,
            runtime_state=self_organizing_runtime,
            verbose=False,
        )
        if last_self_organizing_meta.get("applied"):
            _log_line(
                job,
                "Initialized self-organizing gaussian layout "
                f"method={last_self_organizing_meta.get('method')} "
                f"grid={last_self_organizing_meta.get('grid_shape')} "
                f"changed={float(last_self_organizing_meta.get('changed_fraction') or 0.0):.3f}.",
            )
        elif last_self_organizing_meta.get("reason"):
            _log_line(
                job,
                "Self-organizing gaussian layout skipped at initialization "
                f"reason={last_self_organizing_meta.get('reason')}.",
            )

    random.seed(42)
    loss_value = 0.0
    l1_value = 0.0
    ssim_value = 0.0
    progress_base = 0.28
    progress_span = 0.54
    lambda_dssim = float(settings.get("lambda_dssim", 0.20))
    alpha_loss_weight = float(settings.get("alpha_loss_weight", 0.05))
    appearance_regularization = float(settings.get("appearance_regularization", settings.get("exposure_regularization", 1.0e-3)))
    appearance_smoothness = float(settings.get("appearance_tv_weight", settings.get("appearance_smoothness", 5.0)))
    random_background = bool(settings.get("random_background", True))
    depth_reinit_enabled = bool(settings.get("enable_depth_reinit", True))
    requested_strategy_name = str(settings.get("strategy_name") or "auto").strip().lower()
    configured_depth_reinit_every = _positive_int(settings.get("depth_reinit_every"))
    if requested_strategy_name == "auto" and configured_depth_reinit_every in {None, 200}:
        depth_reinit_every = 5000
    else:
        depth_reinit_every = configured_depth_reinit_every or 5000
    depth_reinit_views = max(1, int(settings.get("depth_reinit_views", 2)))
    depth_reinit_points = max(64, int(settings.get("depth_reinit_points", 2048)))
    scales_lr_warmup_multiplier = float(settings.get("scales_lr_warmup_multiplier", 1.35))
    scales_lr_final_multiplier = float(settings.get("scales_lr_final_multiplier", 0.55))
    scale_lr_warmup_steps = max(1, int(max_steps * 0.18))
    budget_pause_logged = False
    budget_resume_logged = False
    depth_reinit_replaced_total = 0
    train_view_order = list(range(len(train_views)))
    random.shuffle(train_view_order)
    train_view_cursor = 0

    for step in range(max_steps):
        if _should_stop(job["id"]):
            raise RuntimeError("Stopped during Gaussian Splat training.")

        if train_view_cursor >= len(train_view_order):
            random.shuffle(train_view_order)
            train_view_cursor = 0
        view = train_views[train_view_order[train_view_cursor]]
        train_view_cursor += 1
        _target_rgb, target_alpha, backgrounds, pixels = _build_target_pixels(
            view,
            device,
            random_background=random_background,
        )
        if step < scale_lr_warmup_steps:
            scheduled_scales_lr = base_scales_lr * scales_lr_warmup_multiplier
        else:
            decay_progress = float(step - scale_lr_warmup_steps) / float(max(1, max_steps - scale_lr_warmup_steps - 1))
            scheduled_scales_lr = base_scales_lr * (
                scales_lr_warmup_multiplier
                + ((scales_lr_final_multiplier - scales_lr_warmup_multiplier) * decay_progress)
            )
        optimizers["scales"].param_groups[0]["lr"] = scheduled_scales_lr
        if appearance_grid_optimizer is not None:
            appearance_warmup_steps = max(1, int(settings.get("appearance_grid_warmup_steps", max_steps * 0.1)))
            appearance_warmup_factor = float(settings.get("appearance_grid_warmup_factor", 0.01))
            appearance_final_factor = float(settings.get("appearance_grid_final_lr_factor", 0.01))
            if step < appearance_warmup_steps:
                warmup_progress = float(step + 1) / float(appearance_warmup_steps)
                appearance_lr = appearance_grid_base_lr * (
                    appearance_warmup_factor + ((1.0 - appearance_warmup_factor) * warmup_progress)
                )
            else:
                tail_steps = max(1, max_steps - appearance_warmup_steps)
                tail_progress = float(step - appearance_warmup_steps + 1) / float(tail_steps)
                appearance_lr = appearance_grid_base_lr * math.exp(math.log(appearance_final_factor) * tail_progress)
            appearance_grid_optimizer.param_groups[0]["lr"] = appearance_lr

        sh_degree_to_use = _active_sh_degree(step, max_steps, sh_degree, settings)
        current_budget_cap = _scheduled_gaussian_budget(
            step=step,
            initial_gaussians=initial_gaussian_count,
            target_gaussians=target_gaussian_budget,
            refine_start_iter=int(training_profile["refine_start_iter"]),
            refine_stop_iter=int(training_profile["refine_stop_iter"]),
            refine_every=int(training_profile["refine_every"]),
            sh_degree_to_use=sh_degree_to_use,
            sh_degree=sh_degree,
            schedule=budget_schedule,
        )
        if strategy_name == "mcmc":
            strategy.cap_max = current_budget_cap
        else:
            strategy_state["current_budget_cap"] = int(current_budget_cap)
        raw_colors_pred, alpha_pred, info = _render_view(
            splats,
            view,
            device,
            sh_degree_to_use=sh_degree_to_use,
            absgrad=strategy_absgrad,
            backgrounds=backgrounds,
            rasterize_mode=rasterize_mode,
        )
        info["edge_backbone"] = _laplacian_edge_backbone(pixels).detach()
        info["width"] = view.width
        info["height"] = view.height
        colors_pred = _apply_exposure_compensation(raw_colors_pred, view, exposure_params, guide_pixels=raw_colors_pred)
        info["error_backbone"] = torch.mean(torch.abs(colors_pred.detach() - pixels.detach()), dim=-1)

        budget_saturated = strategy_name != "mcmc" and len(splats["means"]) >= current_budget_cap
        if budget_saturated and not budget_pause_logged:
            budget_pause_logged = True
            _log_line(
                job,
                f"Reached scheduled gaussian budget at step {step + 1}; holding growth at {len(splats['means']):,}/{current_budget_cap:,} splats.",
            )
        elif not budget_saturated and budget_pause_logged and not budget_resume_logged:
            budget_resume_logged = True
            _log_line(job, f"Scheduled gaussian budget opened again at step {step + 1}; cap={current_budget_cap:,}.")

        gradient_tensor = None
        if strategy_name != "mcmc":
            gradient_key = getattr(strategy, "key_for_gradient", None)
            if isinstance(gradient_key, str):
                candidate_gradient_tensor = info.get(gradient_key)
                if isinstance(candidate_gradient_tensor, torch.Tensor):
                    gradient_tensor = candidate_gradient_tensor
            if gradient_tensor is not None and not gradient_tensor.requires_grad and screen_space_gradients_enabled:
                screen_space_gradients_enabled = False
                screen_space_gradient_disable_reason = (
                    "gsplat_unscented_projection_is_nondifferentiable"
                    if view.use_unscented_transform
                    else "projected_screen_means_missing_gradients"
                )
                if isinstance(strategy, _EdgeAwareLASStrategy):
                    _log_line(
                        job,
                        "Switched LAS densification to projection-only fallback because the current gsplat projection "
                        f"path does not expose differentiable screen-space means "
                        f"(reason={screen_space_gradient_disable_reason}, camera_model={view.camera_model}, "
                        f"with_ut={view.use_unscented_transform}).",
                    )
                else:
                    _log_line(
                        job,
                        "Disabled gradient-driven densification for this run because the current gsplat projection path "
                        f"does not expose differentiable screen-space means (reason={screen_space_gradient_disable_reason}, "
                        f"camera_model={view.camera_model}, with_ut={view.use_unscented_transform}).",
                    )

        if strategy_name != "mcmc":
            if screen_space_gradients_enabled:
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
            ssim_score = _appearance_ssim(
                colors_pred,
                pixels,
                raw=raw_colors_pred if exposure_params is not None else None,
                weights=target_alpha,
            )
        else:
            l1_loss = F.l1_loss(colors_pred, pixels)
            alpha_loss = torch.zeros((), dtype=torch.float32, device=device)
            ssim_score = _appearance_ssim(
                colors_pred,
                pixels,
                raw=raw_colors_pred if exposure_params is not None else None,
            )
        appearance_reg = torch.zeros((), dtype=torch.float32, device=device)
        appearance_smooth_reg = torch.zeros((), dtype=torch.float32, device=device)
        self_organizing_smooth_reg = torch.zeros((), dtype=torch.float32, device=device)
        if exposure_params is not None:
            if "log_gains" in exposure_params and "rgb_bias" in exposure_params:
                appearance_reg = (
                    exposure_params["log_gains"][view.view_index].pow(2).mean()
                    + exposure_params["rgb_bias"][view.view_index].pow(2).mean()
                )
            else:
                appearance_reg, appearance_smooth_reg = _appearance_regularization(exposure_params)
        if self_organizing_config.enabled:
            self_organizing_smooth_reg, _ = _self_organizing_smoothness_loss(
                splats,
                self_organizing_config,
            )
        effective_sogs_weight = float(self_organizing_config.smoothness_weight) * float(
            training_runtime_monitor.get("sogs_weight_scale") or 0.0
        )
        loss = (
            ((1.0 - lambda_dssim) * l1_loss)
            + (lambda_dssim * (1.0 - ssim_score))
            + (alpha_loss_weight * alpha_loss)
            + (appearance_regularization * appearance_reg)
            + (appearance_smoothness * appearance_smooth_reg)
            + (effective_sogs_weight * self_organizing_smooth_reg)
        )
        loss.backward()
        loss_value = float(loss.item())
        l1_value = float(l1_loss.item())
        ssim_value = float(ssim_score.item())

        for optimizer in optimizers.values():
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
        for optimizer in auxiliary_optimizers:
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)

        refine_stats = None
        if strategy_name == "mcmc":
            means_lr = float(optimizers["means"].param_groups[0]["lr"])
            strategy.step_post_backward(
                params=splats,
                optimizers=optimizers,
                state=strategy_state,
                step=step,
                info=info,
                lr=means_lr,
            )
        elif isinstance(strategy, _EdgeAwareLASStrategy):
            strategy.step_post_backward(
                params=splats,
                optimizers=optimizers,
                state=strategy_state,
                step=step,
                info=info,
                packed=False,
                gradientless=not screen_space_gradients_enabled,
            )
            refine_stats = strategy_state.get("last_refine_stats")
            if isinstance(refine_stats, dict) and int(refine_stats.get("step", -1)) == step:
                _log_line(
                    job,
                    "Refine event "
                    f"step={step + 1} before={int(refine_stats.get('before', len(splats['means']))):,} "
                    f"selected={int(refine_stats.get('selected', 0)):,}/{int(refine_stats.get('target_selected', 0)):,} "
                    f"quota={int(refine_stats.get('growth_quota', 0)):,} "
                    f"active={int(refine_stats.get('active', 0)):,} "
                    f"dup={int(refine_stats.get('duplicated', 0)):,} "
                    f"split={int(refine_stats.get('split', 0)):,} "
                    f"split_long={int(refine_stats.get('split_long_axis', 0)):,} "
                    f"split_cov={int(refine_stats.get('split_covariance', 0)):,} "
                    f"pruned={int(refine_stats.get('pruned', 0)):,}/{int(refine_stats.get('pruned_requested', refine_stats.get('pruned', 0))):,} "
                    f"after={int(refine_stats.get('after', len(splats['means']))):,} "
                    f"budget_cap={int(refine_stats.get('budget_cap', current_budget_cap)):,} "
                    f"deficit={int(refine_stats.get('deficit', 0)):,} "
                    f"fill={float(refine_stats.get('growth_fill', 0.0)):.2f} "
                    f"split_ratio={float(refine_stats.get('split_ratio', 0.0)):.2f} "
                    f"interval={int(refine_stats.get('interval', 0))} "
                    f"mode={str(refine_stats.get('mode') or 'gradient')} "
                    f"prune_limited={bool(refine_stats.get('prune_limited'))} "
                    f"error_nonzero={int(refine_stats.get('error_nonzero', 0)):,} "
                    f"edge_nonzero={int(refine_stats.get('edge_nonzero', 0)):,}",
                )
                strategy_state["last_refine_stats"] = None
        elif screen_space_gradients_enabled:
            strategy.step_post_backward(
                params=splats,
                optimizers=optimizers,
                state=strategy_state,
                step=step,
                info=info,
                packed=False,
            )

        should_analyze_runtime = bool(
            step == 0
            or (step + 1) % 25 == 0
            or step == max_steps - 1
            or (isinstance(refine_stats, dict) and int(refine_stats.get("step", -1)) == step)
        )
        shape_diagnostics = _summarize_splat_shape(splats) if should_analyze_runtime else None
        _record_training_runtime_observation(
            training_runtime_monitor,
            step=step,
            loss_value=loss_value,
            l1_value=l1_value,
            ssim_value=ssim_value,
            current_budget_cap=current_budget_cap,
            gaussian_count=int(len(splats["means"])),
            shape_diagnostics=shape_diagnostics,
            refine_stats=refine_stats if isinstance(refine_stats, dict) else None,
        )
        runtime_actions = _apply_training_runtime_adaptation(
            training_runtime_monitor,
            step=step,
            max_steps=max_steps,
            strategy=strategy,
            strategy_state=strategy_state,
            self_organizing_config=self_organizing_config,
        )
        for action in runtime_actions:
            _log_line(job, str(action.get("message") or "Runtime monitor applied an adaptive correction."))

        if _should_refresh_self_organizing_layout(
            step,
            self_organizing_config,
            last_sort_step=self_organizing_runtime.get("last_sort_step"),
        ):
            last_self_organizing_meta = _refresh_self_organizing_layout(
                splats,
                optimizers,
                strategy_state,
                self_organizing_config,
                step=step,
                runtime_state=self_organizing_runtime,
                verbose=False,
            )
            if last_self_organizing_meta.get("applied"):
                _log_line(
                    job,
                    "Refreshed self-organizing gaussian layout "
                    f"step={step + 1} method={last_self_organizing_meta.get('method')} "
                    f"grid={last_self_organizing_meta.get('grid_shape')} "
                    f"changed={float(last_self_organizing_meta.get('changed_fraction') or 0.0):.3f}.",
                )

        if (
            depth_reinit_enabled
            and step > int(training_profile["refine_start_iter"])
            and step < int(training_profile["refine_stop_iter"])
            and (step + 1) % depth_reinit_every == 0
        ):
            depth_points, depth_colors = _depth_reinit_points_from_views(
                splats,
                train_views,
                device,
                sh_degree=sh_degree_to_use,
                absgrad=strategy_absgrad,
                rasterize_mode=rasterize_mode,
                exposure_params=exposure_params,
                max_views=depth_reinit_views,
                max_points_per_view=depth_reinit_points,
            )
            try:
                replaced = _reinitialize_gaussians_from_depth_points(
                    splats,
                    optimizers,
                    strategy_state,
                    depth_points,
                    depth_colors,
                    init_opacity=init_opacity,
                )
            except RuntimeError as error:
                depth_reinit_enabled = False
                _log_line(
                    job,
                    f"Depth reinitialization disabled after step {step + 1} because candidate refresh became unsafe: {error}",
                )
            else:
                depth_reinit_replaced_total += replaced
                if replaced > 0:
                    if device.type == "cuda":
                        torch.cuda.synchronize(device)
                    _log_line(
                        job,
                        f"Depth reinitialization refreshed {replaced:,} low-opacity splats at step {step + 1}.",
                    )

        if step == 0 or (step + 1) % 25 == 0 or step == max_steps - 1:
            progress = progress_base + (progress_span * ((step + 1) / max_steps))
            _update(
                job["id"],
                "Training",
                progress,
                f"Training step {step + 1}/{max_steps} | loss={loss_value:.4f} | l1={l1_value:.4f} | "
                f"ssim={ssim_value:.4f} | sogs={float(self_organizing_smooth_reg.item()):.4f} | "
                f"gaussians={len(splats['means'])}/{current_budget_cap}",
            )
            _log_line(
                job,
                f"Train step {step + 1}/{max_steps}: loss={loss_value:.5f}, l1={l1_value:.5f}, ssim={ssim_value:.5f}, "
                f"sogs={float(self_organizing_smooth_reg.item()):.5f}, sogs_w={effective_sogs_weight:.6f}, "
                f"sh_degree={sh_degree_to_use}, "
                f"gaussians={len(splats['means'])}, budget_cap={current_budget_cap}, scales_lr={scheduled_scales_lr:.6f}",
            )

    pruned_outliers = _prune_coordinate_outliers(splats, job=job)

    appearance_assisted_train_evaluation: dict[str, float] | None = None
    appearance_assisted_validation_evaluation: dict[str, float] | None = None
    appearance_bake_summary: dict[str, object] = {"applied": False, "steps": 0}
    if exposure_params is not None:
        appearance_assisted_train_evaluation = _evaluate_splats(
            splats,
            train_views,
            device,
            sh_degree=sh_degree,
            absgrad=strategy_absgrad,
            rasterize_mode=rasterize_mode,
            exposure_params=exposure_params,
        )
        appearance_assisted_validation_evaluation = _evaluate_splats(
            splats,
            validation_views or train_views,
            device,
            sh_degree=sh_degree,
            absgrad=strategy_absgrad,
            rasterize_mode=rasterize_mode,
            exposure_params=exposure_params,
        )
        appearance_bake_summary = _bake_appearance_into_splats(
            splats,
            views,
            device,
            sh_degree=sh_degree,
            absgrad=strategy_absgrad,
            rasterize_mode=rasterize_mode,
            exposure_params=exposure_params,
            settings=settings,
            job=job,
        )
    train_evaluation = _evaluate_splats(
        splats,
        train_views,
        device,
        sh_degree=sh_degree,
        absgrad=strategy_absgrad,
        rasterize_mode=rasterize_mode,
        exposure_params=None,
    )
    validation_evaluation = _evaluate_splats(
        splats,
        validation_views or train_views,
        device,
        sh_degree=sh_degree,
        absgrad=strategy_absgrad,
        rasterize_mode=rasterize_mode,
        exposure_params=None,
    )
    final_self_organizing_loss = torch.zeros((), dtype=torch.float32, device=device)
    final_self_organizing_diagnostics: dict[str, float] = {}
    if self_organizing_config.enabled:
        final_self_organizing_loss, final_self_organizing_diagnostics = _self_organizing_smoothness_loss(
            splats,
            self_organizing_config,
        )
    training_summary = {
        "quality_preset": str(training_profile["preset"]),
        "strategy_name": strategy_name,
        "mcmc_runtime_mode": mcmc_runtime_mode if strategy_name == "mcmc" else "not_used",
        "rasterize_mode": rasterize_mode,
        "gaussian_budget": target_gaussian_budget,
        "initial_gaussians": initial_gaussian_count,
        "pruned_coordinate_outliers": pruned_outliers,
        "final_gaussians": int(len(splats["means"])),
        "train_steps": max_steps,
        "train_resolution": int(settings.get("train_resolution", 640)),
        "train_view_count": len(train_views),
        "validation_view_count": len(validation_views),
        "metrics": train_evaluation,
        "validation_metrics": validation_evaluation,
        "dataset_diagnostics": dataset_diagnostics or {},
        "unscented_transform": {
            "enabled": distorted_view_count > 0,
            "view_count": distorted_view_count,
            "normalized_view_count": normalized_camera_view_count,
        },
        "depth_reinitialization": {
            "enabled": depth_reinit_enabled,
            "every_steps": depth_reinit_every,
            "views_per_event": depth_reinit_views,
            "points_per_view": depth_reinit_points,
            "replaced_total": depth_reinit_replaced_total,
        },
        "depth_bootstrap": depth_bootstrap_summary,
        "appearance_compensation": {
            "enabled": exposure_params is not None,
            "mode": appearance_mode if exposure_params is not None else "off",
            "regularization": appearance_regularization,
            "smoothness": appearance_smoothness,
            "baked_to_static_asset": bool(appearance_bake_summary.get("applied", False)),
            "bake_summary": appearance_bake_summary,
        },
        "self_organizing_compression": {
            "enabled": self_organizing_config.enabled,
            "method": self_organizing_runtime.get("last_method"),
            "events": int(self_organizing_runtime.get("events") or 0),
            "sort_every": int(self_organizing_config.sort_every),
            "start_step": int(self_organizing_config.start_step),
            "stop_step": int(self_organizing_config.stop_step),
            "grid_shape": self_organizing_runtime.get("last_grid_shape"),
            "last_changed_fraction": self_organizing_runtime.get("last_changed_fraction"),
            "smoothness_weight": float(self_organizing_config.smoothness_weight),
            "effective_smoothness_weight": float(self_organizing_config.smoothness_weight)
            * float(training_runtime_monitor.get("sogs_weight_scale") or 0.0),
            "final_smoothness_loss": float(final_self_organizing_loss.item()),
            "sort_weights": dict(self_organizing_config.sort_weights),
            "smoothness_weights": dict(self_organizing_config.smoothness_weights),
            "loss_diagnostics": final_self_organizing_diagnostics,
            "last_layout_event": last_self_organizing_meta,
        },
        "runtime_monitor": {
            "enabled": True,
            "events": int(training_runtime_monitor.get("event_count") or 0),
            "last_summary": training_runtime_monitor.get("last_summary"),
            "recent_actions": list(training_runtime_monitor.get("actions") or []),
            "final_shape": _summarize_splat_shape(splats),
        },
        "gradient_densification": {
            "enabled": bool(screen_space_gradients_enabled),
            "disable_reason": screen_space_gradient_disable_reason,
            "mode": (
                "projection_fallback"
                if isinstance(strategy, _EdgeAwareLASStrategy) and not screen_space_gradients_enabled
                else "gradient"
            ),
        },
    }
    training_summary["exposure_compensation"] = dict(training_summary["appearance_compensation"])
    if appearance_assisted_train_evaluation is not None:
        training_summary["appearance_compensation"]["appearance_assisted_metrics"] = appearance_assisted_train_evaluation
        training_summary["appearance_compensation"]["appearance_assisted_validation_metrics"] = (
            appearance_assisted_validation_evaluation or {}
        )
    _log_line(
        job,
        "Final train evaluation "
        f"PSNR={train_evaluation['psnr']:.3f} SSIM={train_evaluation['ssim']:.4f} "
        f"L1={train_evaluation['l1']:.4f} alpha_l1={train_evaluation['alpha_l1']:.4f} "
        f"visible={train_evaluation['visible_gaussians']:.0f}",
    )
    _log_line(
        job,
        "Final validation evaluation "
        f"PSNR={validation_evaluation['psnr']:.3f} SSIM={validation_evaluation['ssim']:.4f} "
        f"L1={validation_evaluation['l1']:.4f} alpha_l1={validation_evaluation['alpha_l1']:.4f} "
        f"visible={validation_evaluation['visible_gaussians']:.0f}",
    )
    if appearance_assisted_train_evaluation is not None:
        _log_line(
            job,
            "Appearance-assisted evaluation before bake "
            f"train_PSNR={appearance_assisted_train_evaluation['psnr']:.3f} "
            f"train_SSIM={appearance_assisted_train_evaluation['ssim']:.4f} "
            f"val_PSNR={(appearance_assisted_validation_evaluation or {}).get('psnr', 0.0):.3f} "
            f"val_SSIM={(appearance_assisted_validation_evaluation or {}).get('ssim', 0.0):.4f}",
        )

    result_path = _result_temp_ply_path(project["id"])
    compressed_result_path = _result_temp_compressed_ply_path(project["id"])
    spz_result_path = _result_temp_spz_path(project["id"])
    _update(job["id"], "Exporting", 0.84, "Preparing export payloads from trained splats.")
    _log_line(job, "Preparing export payloads from trained splats.")
    export_payload = _export_tensor_payload(splats)
    optimized_export_payload, optimized_export_meta = _prepare_mobile_optimized_splats(
        export_payload,
        config=self_organizing_config if self_organizing_config.enabled else None,
    )
    _update(job["id"], "Exporting", 0.86, "Writing workspace PLY exports.")
    _log_line(
        job,
        f"Writing workspace PLY exports for {len(splats['means']):,} splats (optimized_sort={bool(optimized_export_meta.get('sort_applied'))}).",
    )
    export_splats(
        means=export_payload["means"],
        scales=export_payload["scales"],
        quats=export_payload["quats"],
        opacities=export_payload["opacities"],
        sh0=export_payload["sh0"],
        shN=export_payload["shN"],
        format="ply",
        save_to=str(result_path),
    )
    export_splats(
        means=optimized_export_payload["means"],
        scales=optimized_export_payload["scales"],
        quats=optimized_export_payload["quats"],
        opacities=optimized_export_payload["opacities"],
        sh0=optimized_export_payload["sh0"],
        shN=optimized_export_payload["shN"],
        format="ply_compressed",
        save_to=str(compressed_result_path),
    )
    training_summary["_workspace_compressed_ply"] = str(compressed_result_path)
    training_summary["optimized_sorting"] = optimized_export_meta
    _update(job["id"], "Exporting", 0.88, "Writing SPZ export.")
    _log_line(job, "Writing SPZ export.")
    try:
        _write_spz_from_splats(
            spz_result_path,
            export_payload,
            antialiased=(rasterize_mode == "antialiased"),
        )
    except Exception as error:
        training_summary["spz_export"] = {
            "enabled": False,
            "error": str(error),
        }
    else:
        training_summary["_workspace_spz"] = str(spz_result_path)
        training_summary["spz_export"] = {
            "enabled": True,
            "error": None,
        }

    bounds_min = splats["means"].detach().cpu().min(dim=0).values.tolist()
    bounds_max = splats["means"].detach().cpu().max(dim=0).values.tolist()
    return result_path, len(splats["means"]), {"min": bounds_min, "max": bounds_max}, training_summary


def _export_handoff(
    project: dict,
    job: dict,
    workspace_ply: Path,
    point_count: int,
    bounds: dict,
    training_summary: dict[str, object],
) -> dict:
    _update(job["id"], "Exporting", 0.9, "Writing the trained splat project and SketchUp handoff package.")
    _log_line(job, "Starting final export handoff.")
    workspace_compressed_ply = Path(
        str(
            training_summary.pop(
                "_workspace_compressed_ply",
                _result_temp_compressed_ply_path(project["id"]),
            )
        )
    )
    workspace_spz = Path(
        str(
            training_summary.pop(
                "_workspace_spz",
                _result_temp_spz_path(project["id"]),
            )
        )
    )
    _update(job["id"], "Exporting", 0.91, "Writing workspace GASP package.")
    _log_line(job, "Writing workspace GASP package.")
    workspace_gasp = write_gaussian_gasp_from_ply(
        workspace_ply,
        result_gasp_path(project["id"], project["name"]),
        project=project,
        extra_manifest={"bounds": bounds, "training_summary": training_summary},
    )
    export_stem = safe_export_stem(project["name"])
    export_dir = paths.exports_root()
    export_dir.mkdir(parents=True, exist_ok=True)

    exported_ply = export_dir / f"{export_stem}.ply"
    exported_compressed_ply = export_dir / f"{export_stem}.compressed.ply"
    exported_gasp = export_dir / f"{export_stem}.gasp"
    exported_spz = export_dir / f"{export_stem}.spz"
    _update(job["id"], "Exporting", 0.93, "Copying export assets to the public exports folder.")
    _log_line(job, f"Copying export assets to {export_dir}.")
    shutil.copy2(workspace_gasp, exported_gasp)
    export_ply_from_gaussian_gasp(workspace_gasp, exported_ply)
    if workspace_compressed_ply.exists():
        shutil.copy2(workspace_compressed_ply, exported_compressed_ply)
    if workspace_spz.exists():
        shutil.copy2(workspace_spz, exported_spz)
    _update(job["id"], "Exporting", 0.95, "Cleaning temporary export files and writing summaries.")
    _log_line(job, "Cleaning temporary export files and writing summaries.")
    _safe_unlink(workspace_ply)
    _safe_unlink(workspace_compressed_ply)
    _safe_unlink(workspace_spz)
    ply_size_bytes = int(exported_ply.stat().st_size)
    compressed_ply_size_bytes = int(exported_compressed_ply.stat().st_size) if exported_compressed_ply.exists() else 0
    spz_size_bytes = int(exported_spz.stat().st_size) if exported_spz.exists() else 0
    summary_payload = dict(training_summary)
    summary_payload["gasp_size_bytes"] = int(exported_gasp.stat().st_size)
    summary_payload["ply_size_bytes"] = ply_size_bytes
    summary_payload["compressed_ply_size_bytes"] = compressed_ply_size_bytes
    summary_payload["spz_size_bytes"] = spz_size_bytes
    summary_payload["scene_ply"] = str(exported_ply)
    summary_payload["scene_compressed_ply"] = str(exported_compressed_ply) if exported_compressed_ply.exists() else None
    summary_payload["scene_spz"] = str(exported_spz) if exported_spz.exists() else None
    summary_payload["scene_gasp"] = str(exported_gasp)
    summary_path = _training_summary_path(project["id"])
    summary_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")

    manifest = {
        "version": 3,
        "project_id": project["id"],
        "project_name": project["name"],
        "backend": project["backend"],
        "created_at": store.utc_now(),
        "point_count": point_count,
        "scene_ply": str(exported_ply),
        "scene_compressed_ply": str(exported_compressed_ply) if exported_compressed_ply.exists() else None,
        "scene_spz": str(exported_spz) if exported_spz.exists() else None,
        "scene_gasp": str(exported_gasp),
        "workspace_scene_gasp": str(workspace_gasp),
        "workspace_scene_ply": None,
        "bounds": bounds,
        "sketchup_import": {
            "type": "gaussian_ply",
            "path": str(exported_ply),
            "source_gasp": str(exported_gasp),
        },
        "optimized_assets": {
            "compressed_ply": str(exported_compressed_ply) if exported_compressed_ply.exists() else None,
            "spz": str(exported_spz) if exported_spz.exists() else None,
        },
        "training_summary_path": str(summary_path),
        "training_summary": summary_payload,
    }
    manifest_path = export_dir / f"{export_stem}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    package_path = export_dir / f"{export_stem}.gspkg"
    _update(job["id"], "Exporting", 0.97, "Packaging final export bundle.")
    _log_line(job, f"Packaging final export bundle at {package_path}.")
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(exported_ply, arcname=f"{export_stem}.ply")
        if exported_compressed_ply.exists():
            archive.write(exported_compressed_ply, arcname=f"{export_stem}.compressed.ply")
        if exported_spz.exists():
            archive.write(exported_spz, arcname=f"{export_stem}.spz")
        archive.write(exported_gasp, arcname=f"{export_stem}.gasp")
        archive.write(manifest_path, arcname=f"{export_stem}_manifest.json")

    paths.write_latest_export(
        {
            "project_id": project["id"],
            "project_name": project["name"],
            "manifest_path": str(manifest_path),
            "scene_ply": str(exported_ply),
            "scene_compressed_ply": str(exported_compressed_ply) if exported_compressed_ply.exists() else None,
            "scene_spz": str(exported_spz) if exported_spz.exists() else None,
            "scene_gasp": str(exported_gasp),
            "package_path": str(package_path),
            "created_at": manifest["created_at"],
        }
    )
    _result_manifest_path(project["id"]).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _update(job["id"], "Exporting", 0.99, "Finalizing export metadata.")
    _log_line(
        job,
        f"Finalizing export metadata (ply={ply_size_bytes:,} bytes, compressed_ply={compressed_ply_size_bytes:,} bytes, spz={spz_size_bytes:,} bytes).",
    )
    store.update_project(
        project["id"],
        status="ready",
        last_result_ply=str(exported_ply),
        last_result_gasp=str(workspace_gasp),
        last_manifest_path=str(manifest_path),
        last_training_summary=summary_payload,
    )
    return manifest


def run_gsplat_job(project: dict, job: dict, settings: dict) -> dict:
    dataset_images = _list_project_images(project["id"])
    dataset_diagnostics = compute_dataset_diagnostics(dataset_images)
    dataset_diagnostics = merge_video_import_diagnostics(
        dataset_diagnostics,
        load_project_import_summary(project["id"]) or project.get("last_import_summary"),
    )
    _log_line(
        job,
        "Dataset diagnostics "
        f"quality_score={dataset_diagnostics['quality_score']:.1f} "
        f"sharpness={dataset_diagnostics['sharpness_mean']:.5f} "
        f"brightness_std={dataset_diagnostics['brightness_std']:.5f} "
        f"duplicates={dataset_diagnostics['duplicate_like_pairs']}",
    )
    if dataset_diagnostics.get("selected_overlap_mean") is not None:
        _log_line(
            job,
            "Video import overlap "
            f"mean={float(dataset_diagnostics['selected_overlap_mean']):.3f} "
            f"min={float(dataset_diagnostics.get('selected_overlap_min') or 0.0):.3f}",
        )
    for warning in dataset_diagnostics.get("warnings", []):
        _log_line(job, f"Dataset warning: {warning}")

    manifest_path = _find_transforms_manifest(project["id"])
    manifest_fallback_reason: str | None = None
    if manifest_path:
        manifest_views = _load_training_views_from_transforms(project, manifest_path, None, settings)
        manifest_fallback_reason = _known_camera_manifest_skip_reason(manifest_views)
        if manifest_fallback_reason is None:
            _log_line(job, f"Loaded {len(manifest_views)} training views from camera manifest {manifest_path.name}.")
            _log_line(job, "Known-camera alpha dataset detected. Training will use the manifest frame directly and will not mix in COLMAP poses.")
            manifest_settings = dict(settings)
            manifest_settings["_known_camera_mode"] = True
            manifest_diagnostics = summarize_registered_views(dataset_diagnostics, len(manifest_views))
            try:
                workspace_ply, point_count, bounds, training_summary = _train_gaussians(
                    project,
                    job,
                    manifest_settings,
                    None,
                    manifest_views,
                    manifest_diagnostics,
                )
            except RuntimeError as error:
                if "Known-camera initialization failed to build a foreground visual hull from alpha masks." not in str(error):
                    raise
                manifest_fallback_reason = (
                    "manifest alpha initialization could not build a foreground visual hull"
                )
                _log_line(job, f"{error} Falling back to COLMAP sparse reconstruction.")
            else:
                manifest = _export_handoff(project, job, workspace_ply, point_count, bounds, training_summary)
                _log_line(job, f"Exported trained splats to {manifest['scene_ply']}.")
                return manifest
        else:
            _log_line(
                job,
                f"Camera manifest {manifest_path.name} is not usable for known-camera training: "
                f"{manifest_fallback_reason}. Falling back to COLMAP sparse reconstruction.",
            )

    reconstruction = _run_colmap(project, job, settings)
    views = _load_training_views(project, reconstruction, settings, prefer_manifest=False)
    dataset_diagnostics = summarize_registered_views(dataset_diagnostics, len(views))
    if manifest_path and manifest_fallback_reason:
        _log_line(
            job,
            f"Loaded {len(views)} registered training views from SfM after manifest fallback "
            f"({manifest_fallback_reason}).",
        )
    elif manifest_path:
        _log_line(job, f"Loaded {len(views)} registered training views from SfM with manifest metadata available.")
    else:
        _log_line(job, f"Loaded {len(views)} registered training views from SfM.")
    workspace_ply, point_count, bounds, training_summary = _train_gaussians(
        project,
        job,
        settings,
        reconstruction,
        views,
        dataset_diagnostics,
    )
    manifest = _export_handoff(project, job, workspace_ply, point_count, bounds, training_summary)
    _log_line(job, f"Exported trained splats to {manifest['scene_ply']}.")
    return manifest
