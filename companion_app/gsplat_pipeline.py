from __future__ import annotations

import json
import math
import os
import random
import shutil
import sqlite3
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
from gsplat.strategy import DefaultStrategy, MCMCStrategy
from gsplat.strategy.base import Strategy
from gsplat.strategy.ops import inject_noise_to_position

from . import paths, store
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
            target_count = min(self.cap_max, int(1.05 * current_count))
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
        import_summary = (project or {}).get("last_import_summary") if project else None
        aggregate = (import_summary or {}).get("aggregate") if isinstance(import_summary, dict) else {}
        if int((aggregate or {}).get("source_videos") or 0) > 0:
            return "sequential"
        if image_count >= 48:
            return "sequential"
        return "exhaustive"
    return mode


def _sfm_thread_count(settings: dict) -> int:
    return max(1, min(_positive_int(settings.get("sfm_num_threads")) or 4, 4))


def _auto_gaussian_budget(view_count: int, initial_points: int, train_resolution: int, preset: str) -> int:
    profile_table = {
        "compact": {"point_factor": 2.5, "view_resolution_weight": 8.0, "minimum": 40_000, "maximum": 300_000},
        "balanced": {"point_factor": 4.0, "view_resolution_weight": 12.0, "minimum": 80_000, "maximum": 650_000},
        "high": {"point_factor": 6.0, "view_resolution_weight": 18.0, "minimum": 140_000, "maximum": 1_250_000},
    }
    profile = profile_table[preset]
    budget = int(round((initial_points * profile["point_factor"]) + (view_count * train_resolution * profile["view_resolution_weight"])))
    return max(int(profile["minimum"]), min(int(profile["maximum"]), budget))


def _resolve_training_profile(settings: dict, view_count: int, initial_points: int) -> dict[str, object]:
    max_steps = int(settings.get("train_steps", 1200))
    train_resolution = int(settings.get("train_resolution", 640))
    preset = _normalize_quality_preset(settings)
    strategy_name = str(settings.get("strategy_name") or "auto").strip().lower()
    if strategy_name not in {"auto", "default", "mcmc"}:
        strategy_name = "auto"
    if strategy_name == "auto":
        strategy_name = "mcmc"

    max_gaussians = _positive_int(settings.get("max_gaussians")) or _auto_gaussian_budget(
        view_count,
        initial_points,
        train_resolution,
        preset,
    )

    if strategy_name == "mcmc":
        start_ratio = {"compact": 0.10, "balanced": 0.10, "high": 0.12}[preset]
        stop_ratio = {"compact": 0.70, "balanced": 0.80, "high": 0.90}[preset]
    else:
        start_ratio = {"compact": 0.10, "balanced": 0.10, "high": 0.12}[preset]
        stop_ratio = {"compact": 0.45, "balanced": 0.55, "high": 0.65}[preset]
    max_refine_iter = max(1, max_steps - 1)
    refine_start_iter = _positive_int(settings.get("densify_start_iter")) or max(25, int(max_steps * start_ratio))
    refine_start_iter = min(max_refine_iter, refine_start_iter)
    refine_stop_iter = _positive_int(settings.get("densify_stop_iter")) or max(50, int(max_steps * stop_ratio))
    refine_stop_iter = min(max_refine_iter, refine_stop_iter)
    if refine_stop_iter <= refine_start_iter:
        refine_start_iter = max(1, min(refine_start_iter, max_refine_iter - 1))
        refine_stop_iter = min(max_refine_iter, max(refine_start_iter + 1, refine_stop_iter))
    refine_every = _positive_int(settings.get("densify_interval")) or (75 if preset == "compact" else 50)

    profile: dict[str, object] = {
        "preset": preset,
        "strategy_name": strategy_name,
        "rasterize_mode": _resolve_rasterize_mode(settings, preset),
        "max_gaussians": int(max_gaussians),
        "budget_schedule": str(settings.get("budget_schedule") or "staged").strip().lower(),
        "refine_start_iter": int(refine_start_iter),
        "refine_stop_iter": int(refine_stop_iter),
        "refine_every": int(refine_every),
        "refine_scale2d_stop_iter": int(max_steps),
        "opacity_reset_interval": _positive_int(settings.get("opacity_reset_interval")) or max(1000, int(max_steps * 0.6)),
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
        grow_grad2d = 8.0e-4 if absgrad else 2.0e-4
        if preset == "compact":
            grow_grad2d *= 1.25
        elif preset == "high":
            grow_grad2d *= 0.85

    profile.update(
        {
            "absgrad": absgrad,
            "grow_grad2d": grow_grad2d,
            "prune_opa": _positive_float(settings.get("prune_opa")) or {
                "compact": 0.015,
                "balanced": 0.010,
                "high": 0.0075,
            }[preset],
            "grow_scale3d": float(settings.get("grow_scale3d", 0.01)),
            "grow_scale2d": float(settings.get("grow_scale2d", 0.05)),
            "prune_scale3d": float(settings.get("prune_scale3d", 0.1)),
            "prune_scale2d": float(settings.get("prune_scale2d", 0.15)),
            "revised_opacity": bool(settings.get("revised_opacity", True)),
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
    sh_degree_to_use: int,
    sh_degree: int,
    schedule: str,
) -> int:
    if target_gaussians <= initial_gaussians:
        return int(target_gaussians)
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
    if kept >= total or kept < max(32, int(total * 0.35)):
        return points_tensor, rgb_tensor

    keep_mask = torch.from_numpy(keep_mask_np.astype(np.bool_))
    if job is not None:
        _log_line(
            job,
            f"Filtered sparse seed points to {kept:,} / {total:,} "
            f"(min_track_length={min_track_length}, error_limit={error_threshold:.2f}).",
        )
    return points_tensor[keep_mask], rgb_tensor[keep_mask]


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
    camtoworld = view.camtoworld.unsqueeze(0).to(device)
    K = view.K.unsqueeze(0).to(device)
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
    )
    return renders[..., :3], alphas, info


def _apply_exposure_compensation(
    colors_pred: torch.Tensor,
    view: TrainingView,
    exposure_params: dict[str, torch.nn.Parameter] | None,
) -> torch.Tensor:
    if exposure_params is None:
        return colors_pred
    gains = torch.exp(exposure_params["log_gains"][view.view_index]).view(1, 1, 1, 3)
    bias = exposure_params["rgb_bias"][view.view_index].view(1, 1, 1, 3)
    return torch.clamp((colors_pred * gains) + bias, 0.0, 1.0)


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
    exposure_params: dict[str, torch.nn.Parameter] | None = None,
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
            colors_pred = _apply_exposure_compensation(colors_pred, view, exposure_params)
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
            _log_line(job, f"Reusing cached COLMAP reconstruction from {existing[0]}.")
            return pycolmap.Reconstruction(existing[0])

    if database_path.exists():
        database_path.unlink()
    _probe_sqlite_database(database_path)

    _log_line(job, f"COLMAP scratch workspace: {colmap_dir}")
    _log_line(job, f"Prepared {len(image_paths)} normalized images for SfM.")

    sfm_threads = _sfm_thread_count(settings)
    _update(job["id"], "COLMAP", 0.08, "Extracting image features.")
    extraction_options = pycolmap.FeatureExtractionOptions()
    requested_sfm_image_size = int(settings.get("sfm_max_image_size", 1280))
    if video_derived and len(image_paths) >= 96 and requested_sfm_image_size > 1280:
        _log_line(
            job,
            f"Capping SfM image size from {requested_sfm_image_size} to 1280 for long video matching safety.",
        )
        requested_sfm_image_size = 1280
    extraction_options.max_image_size = requested_sfm_image_size
    extraction_options.num_threads = sfm_threads
    extraction_options.use_gpu = False
    if video_derived or quality_preset == "high":
        default_feature_cap = 8_000 if video_derived and len(image_paths) >= 96 else 12_000 if video_derived else 10_000
        extraction_options.sift.max_num_features = int(settings.get("sfm_max_num_features", default_feature_cap))
        extraction_options.sift.domain_size_pooling = True
    if video_derived:
        extraction_options.sift.darkness_adaptivity = True

    matching_options = pycolmap.FeatureMatchingOptions()
    matching_options.num_threads = sfm_threads
    matching_options.use_gpu = False
    matching_options.guided_matching = True
    if video_derived:
        matching_options.max_num_matches = int(settings.get("sfm_max_num_matches", 32_768))

    mapping_options = pycolmap.IncrementalPipelineOptions()
    mapping_options.min_model_size = 3
    mapping_options.extract_colors = True
    mapping_options.num_threads = sfm_threads

    match_mode = _resolve_sfm_match_mode(settings, len(image_paths), project)

    _log_line(
        job,
        "COLMAP feature setup "
        f"max_image_size={extraction_options.max_image_size} "
        f"max_features={extraction_options.sift.max_num_features} "
        f"max_matches={matching_options.max_num_matches} "
        f"threads={sfm_threads} "
        f"dsp={'on' if extraction_options.sift.domain_size_pooling else 'off'} "
        f"video={'yes' if video_derived else 'no'}",
    )

    pycolmap.extract_features(database_path, image_dir, extraction_options=extraction_options)
    if _should_stop(job["id"]):
        raise RuntimeError("Stopped during feature extraction.")
    _log_line(job, "COLMAP feature extraction finished.")

    _update(job["id"], "COLMAP", 0.14, f"Matching image features with {match_mode} mode.")
    if match_mode == "sequential":
        pairing_options = pycolmap.SequentialPairingOptions()
        default_overlap = 5 if video_derived and len(image_paths) >= 96 else 8 if video_derived else 10
        pairing_options.overlap = int(settings.get("sfm_sequential_overlap", default_overlap))
        pairing_options.quadratic_overlap = bool(settings.get("sfm_quadratic_overlap", not video_derived))
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
    selected_image_names = _select_registered_training_images(reconstruction)
    views: list[TrainingView] = []

    for image in reconstruction.images.values():
        if selected_image_names is not None and image.name not in selected_image_names:
            continue
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
                view_index=len(views),
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
        K = _manifest_intrinsics_matrix(payload, width, height)

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
            )
        )
    if reconstruction is None:
        return views
    return _align_views_to_reconstruction(views, reconstruction)


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
        point_errors = []
        track_lengths = []
        for point in reconstruction.points3D.values():
            points.append(point.xyz)
            colors.append([channel / 255.0 for channel in point.color])
            point_errors.append(float(point.error) if math.isfinite(float(point.error)) else float("inf"))
            track_lengths.append(int(point.track.length()))
        points_tensor = torch.from_numpy(np.asarray(points, dtype=np.float32))
        rgb_tensor = torch.from_numpy(np.asarray(colors, dtype=np.float32))
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
    initial_gaussian_count = int(len(points_tensor))

    sh_degree = int(settings.get("sh_degree", 3))
    init_opacity = float(settings.get("init_opacity", 0.10))
    sh_dim = (sh_degree + 1) ** 2
    training_profile = _resolve_training_profile(settings, len(views), initial_gaussian_count)
    train_views, validation_views = split_training_views(
        views,
        validation_fraction=float(settings.get("validation_fraction", 0.18)),
        min_validation_views=int(settings.get("min_validation_views", 2)),
    )
    rasterize_mode = str(training_profile["rasterize_mode"])
    strategy_name = str(training_profile["strategy_name"])
    target_gaussian_budget = int(training_profile["max_gaussians"])
    budget_schedule = str(training_profile.get("budget_schedule") or "staged")
    if strategy_name == "mcmc":
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

    exposure_params: dict[str, torch.nn.Parameter] | None = None
    if bool(settings.get("enable_exposure_compensation", True)):
        exposure_params = {
            "log_gains": torch.nn.Parameter(torch.zeros((len(views), 3), dtype=torch.float32, device=device)),
            "rgb_bias": torch.nn.Parameter(torch.zeros((len(views), 3), dtype=torch.float32, device=device)),
        }

    optimizers = {
        "means": torch.optim.Adam([{"params": splats["means"], "lr": float(settings.get("means_lr", 1.6e-4))}], eps=1e-15),
        "scales": torch.optim.Adam([{"params": splats["scales"], "lr": float(settings.get("scales_lr", 5.0e-3))}], eps=1e-15),
        "quats": torch.optim.Adam([{"params": splats["quats"], "lr": float(settings.get("quats_lr", 1.0e-3))}], eps=1e-15),
        "opacities": torch.optim.Adam([{"params": splats["opacities"], "lr": float(settings.get("opacities_lr", 5.0e-2))}], eps=1e-15),
        "sh0": torch.optim.Adam([{"params": splats["sh0"], "lr": float(settings.get("sh0_lr", 2.5e-3))}], eps=1e-15),
        "shN": torch.optim.Adam([{"params": splats["shN"], "lr": float(settings.get("shN_lr", 1.25e-4))}], eps=1e-15),
    }
    auxiliary_optimizers: list[torch.optim.Optimizer] = []
    if exposure_params is not None:
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

    max_steps = int(settings.get("train_steps", 1200))
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
        strategy = DefaultStrategy(
            prune_opa=float(training_profile["prune_opa"]),
            grow_grad2d=float(training_profile["grow_grad2d"]),
            grow_scale3d=float(training_profile["grow_scale3d"]),
            grow_scale2d=float(training_profile["grow_scale2d"]),
            prune_scale3d=float(training_profile["prune_scale3d"]),
            prune_scale2d=float(training_profile["prune_scale2d"]),
            refine_scale2d_stop_iter=int(training_profile["refine_scale2d_stop_iter"]),
            verbose=False,
            refine_start_iter=int(training_profile["refine_start_iter"]),
            refine_stop_iter=int(training_profile["refine_stop_iter"]),
            refine_every=int(training_profile["refine_every"]),
            reset_every=int(training_profile["opacity_reset_interval"]),
            pause_refine_after_reset=len(views),
            absgrad=bool(training_profile["absgrad"]),
            revised_opacity=bool(training_profile["revised_opacity"]),
        )
        strategy_absgrad = bool(training_profile["absgrad"])
        strategy_state = strategy.initialize_state(scene_scale=max(scene_scale, 1.0))
    _log_line(
        job,
        "Training profile "
        f"preset={training_profile['preset']} strategy={strategy_name} "
        f"runtime={mcmc_runtime_mode if strategy_name == 'mcmc' else 'default'} "
        f"budget={target_gaussian_budget:,} init={initial_gaussian_count:,} "
        f"budget_schedule={budget_schedule} "
        f"refine={training_profile['refine_start_iter']}..{training_profile['refine_stop_iter']} "
        f"every={training_profile['refine_every']} rasterize={rasterize_mode} "
        f"train_views={len(train_views)} val_views={len(validation_views)} "
        f"exposure={'on' if exposure_params is not None else 'off'}",
    )
    strategy.check_sanity(splats, optimizers)

    random.seed(42)
    loss_value = 0.0
    l1_value = 0.0
    ssim_value = 0.0
    progress_base = 0.28
    progress_span = 0.54
    lambda_dssim = float(settings.get("lambda_dssim", 0.20))
    alpha_loss_weight = float(settings.get("alpha_loss_weight", 0.05))
    exposure_regularization = float(settings.get("exposure_regularization", 1.0e-3))
    random_background = bool(settings.get("random_background", True))
    budget_pause_logged = False
    budget_resume_logged = False
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

        sh_degree_to_use = _active_sh_degree(step, max_steps, sh_degree, settings)
        current_budget_cap = _scheduled_gaussian_budget(
            step=step,
            initial_gaussians=initial_gaussian_count,
            target_gaussians=target_gaussian_budget,
            refine_start_iter=int(training_profile["refine_start_iter"]),
            refine_stop_iter=int(training_profile["refine_stop_iter"]),
            sh_degree_to_use=sh_degree_to_use,
            sh_degree=sh_degree,
            schedule=budget_schedule,
        )
        if strategy_name == "mcmc":
            strategy.cap_max = current_budget_cap
        colors_pred, alpha_pred, info = _render_view(
            splats,
            view,
            device,
            sh_degree_to_use=sh_degree_to_use,
            absgrad=strategy_absgrad,
            backgrounds=backgrounds,
            rasterize_mode=rasterize_mode,
        )
        colors_pred = _apply_exposure_compensation(colors_pred, view, exposure_params)

        budget_paused = strategy_name == "default" and len(splats["means"]) >= current_budget_cap
        if budget_paused and not budget_pause_logged:
            budget_pause_logged = True
            _log_line(
                job,
                f"Reached scheduled gaussian budget at step {step + 1}; pausing densification at {len(splats['means']):,}/{current_budget_cap:,} splats.",
            )
        elif not budget_paused and budget_pause_logged and not budget_resume_logged:
            budget_resume_logged = True
            _log_line(job, f"Scheduled gaussian budget opened again at step {step + 1}; cap={current_budget_cap:,}.")

        if strategy_name == "default" and not budget_paused:
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
        exposure_reg = torch.zeros((), dtype=torch.float32, device=device)
        if exposure_params is not None:
            exposure_reg = (
                exposure_params["log_gains"][view.view_index].pow(2).mean()
                + exposure_params["rgb_bias"][view.view_index].pow(2).mean()
            )
        loss = (
            ((1.0 - lambda_dssim) * l1_loss)
            + (lambda_dssim * (1.0 - ssim_score))
            + (alpha_loss_weight * alpha_loss)
            + (exposure_regularization * exposure_reg)
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
        elif not budget_paused:
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
                f"Training step {step + 1}/{max_steps} | loss={loss_value:.4f} | l1={l1_value:.4f} | ssim={ssim_value:.4f} | gaussians={len(splats['means'])}/{current_budget_cap}",
            )
            _log_line(
                job,
                f"Train step {step + 1}/{max_steps}: loss={loss_value:.5f}, l1={l1_value:.5f}, ssim={ssim_value:.5f}, sh_degree={sh_degree_to_use}, gaussians={len(splats['means'])}, budget_cap={current_budget_cap}",
            )

    pruned_outliers = _prune_coordinate_outliers(splats, job=job)

    train_evaluation = _evaluate_splats(
        splats,
        train_views,
        device,
        sh_degree=sh_degree,
        absgrad=strategy_absgrad,
        rasterize_mode=rasterize_mode,
        exposure_params=exposure_params,
    )
    validation_evaluation = _evaluate_splats(
        splats,
        validation_views or train_views,
        device,
        sh_degree=sh_degree,
        absgrad=strategy_absgrad,
        rasterize_mode=rasterize_mode,
        exposure_params=exposure_params,
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
        "exposure_compensation": {
            "enabled": exposure_params is not None,
            "regularization": exposure_regularization,
        },
    }
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

    result_path = _result_temp_ply_path(project["id"])
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
    exported_gasp = export_dir / f"{export_stem}.gasp"
    shutil.copy2(workspace_gasp, exported_gasp)
    export_ply_from_gaussian_gasp(workspace_gasp, exported_ply)
    workspace_ply.unlink(missing_ok=True)
    ply_size_bytes = int(exported_ply.stat().st_size)
    summary_payload = dict(training_summary)
    summary_payload["gasp_size_bytes"] = int(exported_gasp.stat().st_size)
    summary_payload["ply_size_bytes"] = ply_size_bytes
    summary_payload["scene_ply"] = str(exported_ply)
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
        "scene_gasp": str(exported_gasp),
        "workspace_scene_gasp": str(workspace_gasp),
        "workspace_scene_ply": None,
        "bounds": bounds,
        "sketchup_import": {
            "type": "gaussian_ply",
            "path": str(exported_ply),
            "source_gasp": str(exported_gasp),
        },
        "training_summary_path": str(summary_path),
        "training_summary": summary_payload,
    }
    manifest_path = export_dir / f"{export_stem}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    package_path = export_dir / f"{export_stem}.gspkg"
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(exported_ply, arcname=f"{export_stem}.ply")
        archive.write(exported_gasp, arcname=f"{export_stem}.gasp")
        archive.write(manifest_path, arcname=f"{export_stem}_manifest.json")

    paths.write_latest_export(
        {
            "project_id": project["id"],
            "project_name": project["name"],
            "manifest_path": str(manifest_path),
            "scene_ply": str(exported_ply),
            "scene_gasp": str(exported_gasp),
            "package_path": str(package_path),
            "created_at": manifest["created_at"],
        }
    )
    _result_manifest_path(project["id"]).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
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
    if manifest_path:
        manifest_views = _load_training_views_from_transforms(project, manifest_path, None, settings)
        if manifest_views and all(view.has_alpha for view in manifest_views):
            _log_line(job, f"Loaded {len(manifest_views)} training views from camera manifest {manifest_path.name}.")
            _log_line(job, "Known-camera alpha dataset detected. Training will use the manifest frame directly and will not mix in COLMAP poses.")
            manifest_settings = dict(settings)
            manifest_settings["_known_camera_mode"] = True
            manifest_diagnostics = summarize_registered_views(dataset_diagnostics, len(manifest_views))
            workspace_ply, point_count, bounds, training_summary = _train_gaussians(
                project,
                job,
                manifest_settings,
                None,
                manifest_views,
                manifest_diagnostics,
            )
            manifest = _export_handoff(project, job, workspace_ply, point_count, bounds, training_summary)
            _log_line(job, f"Exported trained splats to {manifest['scene_ply']}.")
            return manifest

    reconstruction = _run_colmap(project, job, settings)
    views = _load_training_views(project, reconstruction, settings)
    dataset_diagnostics = summarize_registered_views(dataset_diagnostics, len(views))
    if manifest_path:
        _log_line(job, f"Loaded {len(views)} training views from camera manifest {manifest_path.name}.")
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
