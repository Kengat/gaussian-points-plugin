from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from unittest import mock

import torch

from companion_app.gsplat_pipeline import (
    TrainingView,
    _known_camera_manifest_skip_reason,
    run_gsplat_job,
)


def _make_view(name: str, *, has_alpha: bool = True) -> TrainingView:
    return TrainingView(
        view_index=0,
        image_name=name,
        rgb_tensor=torch.ones((1, 1, 3), dtype=torch.float32),
        alpha_tensor=torch.ones((1, 1, 1), dtype=torch.float32),
        has_alpha=has_alpha,
        camtoworld=torch.eye(4, dtype=torch.float32),
        K=torch.eye(3, dtype=torch.float32),
        width=1,
        height=1,
    )


class GsplatManifestFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = Path("companion_app") / "tests" / f"_tmp_gsplat_{uuid.uuid4().hex}"
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        self._job = {
            "id": "job-test",
            "log_path": str((self._temp_dir / "job.log").resolve()),
        }
        self._project = {
            "id": "project-test",
            "input_dir": str((self._temp_dir / "input").resolve()),
        }
        self._settings = {"train_resolution": 640}

    def tearDown(self) -> None:
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def test_known_camera_manifest_skip_reason_requires_four_alpha_views(self) -> None:
        self.assertEqual(
            "only 1 usable view(s) matched the manifest; known-camera initialization needs at least 4",
            _known_camera_manifest_skip_reason([_make_view("only.png")]),
        )
        self.assertEqual(
            "1 matched view(s) are missing alpha masks",
            _known_camera_manifest_skip_reason(
                [_make_view("0.png"), _make_view("1.png"), _make_view("2.png"), _make_view("3.png", has_alpha=False)]
            ),
        )
        self.assertIsNone(_known_camera_manifest_skip_reason([_make_view(str(index)) for index in range(4)]))

    def test_run_job_falls_back_to_sfm_when_manifest_has_too_few_views(self) -> None:
        dataset_diagnostics = {
            "quality_score": 72.0,
            "sharpness_mean": 0.12,
            "brightness_std": 0.08,
            "duplicate_like_pairs": 0,
            "warnings": [],
        }
        reconstruction = object()
        sfm_views = [_make_view(f"{index}.png", has_alpha=False) for index in range(5)]

        with (
            mock.patch("companion_app.gsplat_pipeline._list_project_images", return_value=[]),
            mock.patch("companion_app.gsplat_pipeline.compute_dataset_diagnostics", return_value=dataset_diagnostics),
            mock.patch("companion_app.gsplat_pipeline.merge_video_import_diagnostics", side_effect=lambda diagnostics, _summary: diagnostics),
            mock.patch("companion_app.gsplat_pipeline.load_project_import_summary", return_value=None),
            mock.patch("companion_app.gsplat_pipeline._find_transforms_manifest", return_value=Path("transforms_train_subset.json")),
            mock.patch("companion_app.gsplat_pipeline._load_training_views_from_transforms", return_value=[_make_view("only.png")]),
            mock.patch("companion_app.gsplat_pipeline._run_colmap", return_value=reconstruction) as mock_run_colmap,
            mock.patch("companion_app.gsplat_pipeline._load_training_views", return_value=sfm_views) as mock_load_views,
            mock.patch("companion_app.gsplat_pipeline.summarize_registered_views", side_effect=lambda diagnostics, _count: diagnostics),
            mock.patch("companion_app.gsplat_pipeline._train_gaussians", return_value=(Path("scene.ply"), 5, {}, {})) as mock_train,
            mock.patch("companion_app.gsplat_pipeline._export_handoff", return_value={"scene_ply": "scene.ply"}),
        ):
            result = run_gsplat_job(self._project, self._job, self._settings)

        self.assertEqual({"scene_ply": "scene.ply"}, result)
        mock_run_colmap.assert_called_once_with(self._project, self._job, self._settings)
        mock_load_views.assert_called_once_with(self._project, reconstruction, self._settings, prefer_manifest=False)

        train_args = mock_train.call_args.args
        self.assertEqual(self._settings, train_args[2])
        self.assertIs(reconstruction, train_args[3])
        self.assertEqual(sfm_views, train_args[4])


if __name__ == "__main__":
    unittest.main()
