from __future__ import annotations

import shutil
import unittest
from pathlib import Path
import uuid

import numpy as np
from PIL import Image
import torch

from companion_app.gsplat_pipeline import (
    _active_sh_degree,
    _coordinate_outlier_keep_mask,
    _filter_sparse_seed_points,
    _manifest_intrinsics_matrix,
    _mcmc_target_count,
    _resolve_rasterize_mode,
    _resolve_sfm_match_mode,
    _scheduled_gaussian_budget,
    _sfm_thread_count,
)
from companion_app.quality import compute_dataset_diagnostics, split_training_views


class _DummyView:
    def __init__(self, image_name: str) -> None:
        self.image_name = image_name


class TrainingQualityTest(unittest.TestCase):
    def test_split_training_views_keeps_reasonable_holdout(self) -> None:
        views = [_DummyView(f"{index:03d}.png") for index in range(10)]
        train_views, val_views = split_training_views(views, validation_fraction=0.2, min_validation_views=2)
        self.assertEqual(8, len(train_views))
        self.assertEqual(2, len(val_views))
        self.assertEqual(["003.png", "006.png"], [view.image_name for view in val_views])

    def test_rasterize_mode_prefers_antialiasing_outside_compact(self) -> None:
        self.assertEqual("classic", _resolve_rasterize_mode({"rasterize_mode": "auto"}, "compact"))
        self.assertEqual("antialiased", _resolve_rasterize_mode({"rasterize_mode": "auto"}, "balanced"))
        self.assertEqual("classic", _resolve_rasterize_mode({"rasterize_mode": "classic"}, "high"))

    def test_sfm_match_mode_auto_switches_for_larger_sets(self) -> None:
        self.assertEqual("exhaustive", _resolve_sfm_match_mode({"sfm_match_mode": "auto"}, 22))
        self.assertEqual("exhaustive", _resolve_sfm_match_mode({"sfm_match_mode": "auto"}, 64))
        short_video_project = {"last_import_summary": {"aggregate": {"source_videos": 1}}}
        self.assertEqual("exhaustive", _resolve_sfm_match_mode({"sfm_match_mode": "auto"}, 22, short_video_project))
        self.assertEqual("sequential", _resolve_sfm_match_mode({"sfm_match_mode": "auto"}, 96))
        self.assertEqual("sequential", _resolve_sfm_match_mode({"sfm_match_mode": "auto"}, 96, short_video_project))
        self.assertEqual("spatial", _resolve_sfm_match_mode({"sfm_match_mode": "spatial"}, 64))

    def test_sfm_threads_are_capped_for_laptop_safety(self) -> None:
        self.assertEqual(6, _sfm_thread_count({"sfm_num_threads": 12}))
        self.assertEqual(2, _sfm_thread_count({"sfm_num_threads": 2}))

    def test_mcmc_target_count_grows_faster_when_far_from_budget(self) -> None:
        self.assertEqual(1050, _mcmc_target_count(1000, 1500))
        self.assertEqual(1080, _mcmc_target_count(1000, 20_000))
        early_target = _mcmc_target_count(1000, 4000, step=300, refine_stop_iter=2400, refine_every=50)
        late_target = _mcmc_target_count(1000, 4000, step=2100, refine_stop_iter=2400, refine_every=50)
        self.assertEqual(
            2000,
            _mcmc_target_count(1000, 2000, step=2350, refine_stop_iter=2400, refine_every=50),
        )
        self.assertGreater(early_target, 1000)
        self.assertGreater(late_target, early_target)

    def test_sh_degree_reaches_full_before_late_refinement(self) -> None:
        self.assertEqual(0, _active_sh_degree(999, 6000, 3, {}))
        self.assertEqual(1, _active_sh_degree(1000, 6000, 3, {}))
        self.assertEqual(2, _active_sh_degree(2500, 6000, 3, {}))
        self.assertEqual(3, _active_sh_degree(3000, 6000, 3, {}))

    def test_staged_budget_reserves_splats_for_later_training(self) -> None:
        early_cap = _scheduled_gaussian_budget(
            step=3300,
            initial_gaussians=98_383,
            target_gaussians=1_200_000,
            refine_start_iter=720,
            refine_stop_iter=5400,
            sh_degree_to_use=3,
            sh_degree=3,
            schedule="staged",
        )
        final_cap = _scheduled_gaussian_budget(
            step=5400,
            initial_gaussians=98_383,
            target_gaussians=1_200_000,
            refine_start_iter=720,
            refine_stop_iter=5400,
            sh_degree_to_use=3,
            sh_degree=3,
            schedule="staged",
        )

        self.assertLess(early_cap, 850_000)
        self.assertEqual(1_200_000, final_cap)

    def test_coordinate_outlier_mask_catches_tiny_extreme_tail(self) -> None:
        generator = torch.Generator().manual_seed(7)
        core = torch.randn((20_000, 3), generator=generator) * 2.0
        outliers = torch.tensor(
            [
                [120_000.0, 0.0, 0.0],
                [-160_000.0, 20.0, 0.0],
                [0.0, 140_000.0, -220_000.0],
            ],
            dtype=torch.float32,
        )
        keep_mask, stats = _coordinate_outlier_keep_mask(torch.cat([core, outliers], dim=0))

        self.assertIsNotNone(keep_mask)
        self.assertEqual(3, int((~keep_mask).sum().item()))
        self.assertGreater(stats["max"], stats["q99"] * 50.0)

    def test_sparse_seed_filter_relaxes_for_medium_sized_reconstructions(self) -> None:
        total = 100
        points = torch.zeros((total, 3), dtype=torch.float32)
        colors = torch.zeros((total, 3), dtype=torch.float32)
        track_lengths = np.full((total,), 3, dtype=np.int32)
        point_errors = np.linspace(0.1, 2.4, total, dtype=np.float32)
        track_lengths[70:] = 2

        filtered_points, _filtered_colors = _filter_sparse_seed_points(
            points,
            colors,
            point_errors,
            track_lengths,
        )

        self.assertGreater(len(filtered_points), 70)

    def test_manifest_intrinsics_rescale_full_resolution_parameters(self) -> None:
        K = _manifest_intrinsics_matrix(
            {
                "fl_x": 1200.0,
                "fl_y": 1000.0,
                "cx": 540.0,
                "cy": 960.0,
                "w": 1080,
                "h": 1920,
            },
            135,
            240,
        )
        self.assertAlmostEqual(150.0, float(K[0, 0].item()))
        self.assertAlmostEqual(125.0, float(K[1, 1].item()))
        self.assertAlmostEqual(67.5, float(K[0, 2].item()))
        self.assertAlmostEqual(120.0, float(K[1, 2].item()))

    def test_dataset_diagnostics_detect_duplicates_and_low_sharpness(self) -> None:
        temp_dir = Path("companion_app") / "tests" / f"_tmp_quality_{uuid.uuid4().hex}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            blurry_a = temp_dir / "blurry_a.png"
            blurry_b = temp_dir / "blurry_b.png"
            sharp = temp_dir / "sharp.png"

            Image.new("RGB", (128, 128), (120, 120, 120)).save(blurry_a)
            Image.new("RGB", (128, 128), (120, 120, 120)).save(blurry_b)

            sharp_image = Image.new("RGB", (128, 128), (16, 16, 16))
            for x in range(0, 128, 8):
                for y in range(0, 128, 8):
                    if (x // 8 + y // 8) % 2 == 0:
                        for dx in range(8):
                            for dy in range(8):
                                sharp_image.putpixel((x + dx, y + dy), (240, 240, 240))
            sharp_image.save(sharp)

            diagnostics = compute_dataset_diagnostics([blurry_a, blurry_b, sharp])
            self.assertEqual(3, diagnostics["image_count"])
            self.assertGreaterEqual(diagnostics["duplicate_like_pairs"], 1)
            self.assertTrue(any("duplicate" in warning.lower() for warning in diagnostics["warnings"]))
            self.assertTrue(any("sharpness" in warning.lower() or "soft" in warning.lower() for warning in diagnostics["warnings"]))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
