from __future__ import annotations

import shutil
import unittest
from pathlib import Path
import uuid

from PIL import Image

from companion_app.gsplat_pipeline import _resolve_rasterize_mode, _resolve_sfm_match_mode
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
        self.assertEqual("sequential", _resolve_sfm_match_mode({"sfm_match_mode": "auto"}, 64))
        self.assertEqual("spatial", _resolve_sfm_match_mode({"sfm_match_mode": "spatial"}, 64))

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
