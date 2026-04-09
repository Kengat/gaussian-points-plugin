from __future__ import annotations

import itertools
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageOps


def _average_hash(image: Image.Image, size: int = 8) -> int:
    thumb = image.convert("L").resize((size, size), Image.Resampling.BILINEAR)
    pixels = np.asarray(thumb, dtype=np.float32)
    mean_value = float(pixels.mean())
    bits = pixels >= mean_value
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bool(bit))
    return value


def _hamming_distance(lhs: int, rhs: int) -> int:
    return (lhs ^ rhs).bit_count()


def _image_statistics(image_path: Path) -> dict[str, Any]:
    with Image.open(image_path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        width, height = image.size
        gray = np.asarray(image.convert("L"), dtype=np.float32) / 255.0
        rgb = np.asarray(image, dtype=np.float32) / 255.0

        diff_x = np.diff(gray, axis=1)
        diff_y = np.diff(gray, axis=0)
        sharpness = float(np.var(diff_x) + np.var(diff_y))
        brightness = float(gray.mean())
        contrast = float(gray.std())
        underexposed = float((gray < 0.08).mean())
        overexposed = float((gray > 0.92).mean())
        color_cast = np.abs(rgb.mean(axis=(0, 1)) - brightness).mean()

        return {
            "name": image_path.name,
            "width": width,
            "height": height,
            "pixels": width * height,
            "brightness": brightness,
            "contrast": contrast,
            "sharpness": sharpness,
            "underexposed_ratio": underexposed,
            "overexposed_ratio": overexposed,
            "color_cast": float(color_cast),
            "hash": _average_hash(image),
        }


def compute_dataset_diagnostics(image_paths: Sequence[Path]) -> dict[str, Any]:
    if not image_paths:
        return {
            "image_count": 0,
            "warnings": ["No images found."],
            "duplicate_like_pairs": 0,
            "sharpness_mean": 0.0,
            "brightness_mean": 0.0,
            "contrast_mean": 0.0,
            "registered_view_ratio": None,
        }

    per_image = [_image_statistics(path) for path in image_paths]
    widths = [item["width"] for item in per_image]
    heights = [item["height"] for item in per_image]
    sharpness_values = [item["sharpness"] for item in per_image]
    brightness_values = [item["brightness"] for item in per_image]
    contrast_values = [item["contrast"] for item in per_image]
    under_values = [item["underexposed_ratio"] for item in per_image]
    over_values = [item["overexposed_ratio"] for item in per_image]

    duplicate_pairs = 0
    duplicate_examples: list[list[str]] = []
    for lhs, rhs in itertools.combinations(per_image, 2):
        if _hamming_distance(lhs["hash"], rhs["hash"]) <= 2:
            duplicate_pairs += 1
            if len(duplicate_examples) < 5:
                duplicate_examples.append([lhs["name"], rhs["name"]])

    sharpness_mean = float(np.mean(sharpness_values))
    sharpness_min = float(np.min(sharpness_values))
    brightness_mean = float(np.mean(brightness_values))
    brightness_std = float(np.std(brightness_values))
    contrast_mean = float(np.mean(contrast_values))
    exposure_clipped_mean = float(np.mean(np.asarray(under_values) + np.asarray(over_values)))

    warnings: list[str] = []
    if duplicate_pairs > max(1, len(image_paths) // 8):
        warnings.append("Many frames appear near-duplicate; capture may have weak parallax.")
    if sharpness_mean < 0.010:
        warnings.append("Average sharpness is low; motion blur may limit fine detail reconstruction.")
    if sharpness_min < 0.003:
        warnings.append("Some frames are extremely soft; removing blurred shots may improve quality.")
    if brightness_std > 0.12:
        warnings.append("Exposure varies significantly across frames; exposure compensation is recommended.")
    if exposure_clipped_mean > 0.18:
        warnings.append("A large portion of pixels are clipped black/white; highlight and shadow detail may be unstable.")
    if contrast_mean < 0.12:
        warnings.append("The dataset has low contrast; COLMAP matching and texture fidelity may suffer.")

    quality_score = 100.0
    quality_score -= min(20.0, duplicate_pairs * 2.0)
    quality_score -= min(25.0, max(0.0, 0.010 - sharpness_mean) * 1600.0)
    quality_score -= min(20.0, max(0.0, brightness_std - 0.08) * 140.0)
    quality_score -= min(20.0, max(0.0, exposure_clipped_mean - 0.08) * 160.0)
    quality_score = max(0.0, min(100.0, quality_score))

    return {
        "image_count": len(image_paths),
        "resolution": {
            "min": [int(min(widths)), int(min(heights))],
            "max": [int(max(widths)), int(max(heights))],
            "median": [int(np.median(widths)), int(np.median(heights))],
        },
        "sharpness_mean": round(sharpness_mean, 5),
        "sharpness_min": round(sharpness_min, 5),
        "brightness_mean": round(brightness_mean, 5),
        "brightness_std": round(brightness_std, 5),
        "contrast_mean": round(contrast_mean, 5),
        "exposure_clipped_mean": round(exposure_clipped_mean, 5),
        "duplicate_like_pairs": duplicate_pairs,
        "duplicate_examples": duplicate_examples,
        "quality_score": round(quality_score, 2),
        "warnings": warnings,
        "registered_view_ratio": None,
    }


def summarize_registered_views(diagnostics: dict[str, Any], registered_views: int) -> dict[str, Any]:
    updated = dict(diagnostics)
    image_count = max(1, int(updated.get("image_count") or 0))
    ratio = float(registered_views) / float(image_count)
    warnings = list(updated.get("warnings") or [])
    if ratio < 0.6:
        warnings.append("COLMAP registered too few views; pose quality is likely limiting reconstruction fidelity.")
    updated["warnings"] = warnings
    updated["registered_views"] = int(registered_views)
    updated["registered_view_ratio"] = round(ratio, 5)
    return updated


def split_training_views(
    views: Sequence[Any],
    validation_fraction: float,
    min_validation_views: int,
) -> tuple[list[Any], list[Any]]:
    total = len(views)
    if total < 3 or validation_fraction <= 0.0:
        return list(views), []

    requested = int(round(total * validation_fraction))
    validation_count = max(int(min_validation_views), requested)
    validation_count = min(validation_count, max(1, total - 2))
    if validation_count <= 0:
        return list(views), []

    targets = np.linspace(0, total - 1, validation_count + 2, dtype=np.float64)[1:-1]
    chosen: list[int] = []
    used: set[int] = set()
    for target in targets:
        preferred = int(round(float(target)))
        candidates = [preferred]
        for offset in range(1, total):
            lower = preferred - offset
            upper = preferred + offset
            if lower >= 0:
                candidates.append(lower)
            if upper < total:
                candidates.append(upper)
        for candidate in candidates:
            if candidate not in used:
                used.add(candidate)
                chosen.append(candidate)
                break

    validation_indices = set(chosen)
    training_views = [view for index, view in enumerate(views) if index not in validation_indices]
    validation_views = [view for index, view in enumerate(views) if index in validation_indices]
    if len(training_views) < 2:
        return list(views), []
    return training_views, validation_views
