from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest import mock

import pycolmap
import torch

from companion_app.gsplat_pipeline import (
    _EdgeAwareLASStrategy,
    SelfOrganizingCompressionConfig,
    TrainingView,
    _alternate_sfm_match_mode,
    _apply_training_runtime_adaptation,
    _apply_exposure_compensation,
    _colmap_camera_to_training_spec,
    _colmap_process_command,
    _dense_stereo_progress_snapshot,
    _appearance_regularization,
    _create_bilateral_grid_params,
    _find_colmap_executable,
    _initialize_training_runtime_monitor,
    _is_cuda_dense_stereo_error,
    _invert_exposure_compensation,
    _laplacian_edge_backbone,
    _load_training_views_from_transforms,
    _manifest_camera_metadata,
    _manifest_camera_to_training_spec,
    _known_camera_manifest_skip_reason,
    _hybrid_las_split,
    _read_colmap_dense_array,
    _minimum_registered_view_count,
    _project_camera_points_to_pixels,
    _record_training_runtime_observation,
    _reconstruction_registration_stats,
    _resolve_training_profile,
    _resolve_self_organizing_config,
    _run_patch_match_stereo_auto,
    _scheduled_gaussian_budget,
    _sanitize_depth_reinit_candidates,
    _summarize_splat_shape,
    _unproject_image_pixels_to_camera_rays,
    _voxel_downsample_seed_cloud,
    _export_handoff,
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


class _FakeReconstructionImage:
    def __init__(self, *, has_pose: bool) -> None:
        self.has_pose = has_pose


class _FakeReconstruction:
    def __init__(self, registered_images: int, total_images: int, points3d: int = 1024) -> None:
        self.images = {
            index: _FakeReconstructionImage(has_pose=index < registered_images)
            for index in range(total_images)
        }
        self.points3D = {index: object() for index in range(points3d)}


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


class GsplatResearchFeaturesTest(unittest.TestCase):
    def test_colmap_opencv_fisheye_camera_spec_normalizes_to_trainable_projection(self) -> None:
        camera = pycolmap.Camera.create_from_model_name(1, "OPENCV_FISHEYE", 1000.0, 800, 600)
        camera.params = [510.0, 505.0, 401.0, 299.0, 0.08, -0.03, 0.002, -0.001]

        spec = _colmap_camera_to_training_spec(camera, enable_unscented_transform=True)

        self.assertEqual("fisheye", spec.camera_model)
        self.assertFalse(spec.use_unscented_transform)
        self.assertEqual("FISHEYE", spec.projection_camera_model_name)
        self.assertTrue(spec.requires_image_normalization)
        self.assertIsNone(spec.radial_coeffs)

    def test_colmap_zero_distortion_opencv_normalizes_to_pinhole_without_remap(self) -> None:
        camera = pycolmap.Camera.create_from_model_name(1, "OPENCV", 1000.0, 800, 600)
        camera.params = [500.0, 505.0, 400.0, 300.0, 0.0, 0.0, 0.0, 0.0]

        spec = _colmap_camera_to_training_spec(camera, enable_unscented_transform=True)

        self.assertEqual("pinhole", spec.camera_model)
        self.assertFalse(spec.use_unscented_transform)
        self.assertEqual("PINHOLE", spec.projection_camera_model_name)
        self.assertFalse(spec.requires_image_normalization)
        self.assertIsNone(spec.radial_coeffs)
        self.assertIsNone(spec.tangential_coeffs)

    def test_colmap_thin_prism_fisheye_requests_normalized_fisheye_training_view(self) -> None:
        camera = pycolmap.Camera.create_from_model_name(1, "THIN_PRISM_FISHEYE", 1000.0, 800, 600)
        camera.params = [500.0, 505.0, 400.0, 300.0, 0.08, -0.03, 0.002, -0.001, 0.004, -0.002, 0.0006, -0.0005]

        spec = _colmap_camera_to_training_spec(camera, enable_unscented_transform=True)

        self.assertEqual("fisheye", spec.camera_model)
        self.assertFalse(spec.use_unscented_transform)
        self.assertEqual("FISHEYE", spec.projection_camera_model_name)
        self.assertTrue(spec.requires_image_normalization)
        self.assertIsNone(spec.radial_coeffs)

    def test_gradient_las_keeps_small_selected_gaussians_on_duplicate_path(self) -> None:
        params = torch.nn.ParameterDict(
            {
                "means": torch.nn.Parameter(torch.zeros((4, 3), dtype=torch.float32)),
                "scales": torch.nn.Parameter(
                    torch.log(
                        torch.tensor(
                            [
                                [0.01, 0.01, 0.01],
                                [0.01, 0.01, 0.01],
                                [0.20, 0.12, 0.10],
                                [0.22, 0.14, 0.11],
                            ],
                            dtype=torch.float32,
                        )
                    )
                ),
                "quats": torch.nn.Parameter(torch.tensor([[1.0, 0.0, 0.0, 0.0]] * 4, dtype=torch.float32)),
                "opacities": torch.nn.Parameter(torch.full((4,), 2.0, dtype=torch.float32)),
                "sh0": torch.nn.Parameter(torch.zeros((4, 1, 3), dtype=torch.float32)),
                "shN": torch.nn.Parameter(torch.zeros((4, 0, 3), dtype=torch.float32)),
            }
        )
        optimizers = {key: torch.optim.Adam([parameter], lr=1.0e-3) for key, parameter in params.items()}
        strategy = _EdgeAwareLASStrategy(
            refine_start_iter=0,
            refine_stop_iter=100,
            refine_every=10,
            reset_every=10_000,
            prune_opa=0.005,
            grow_scale3d=0.05,
            grow_scale2d=0.05,
            prune_scale3d=10.0,
            prune_scale2d=10.0,
        )
        strategy.check_sanity(params, optimizers)
        state = strategy.initialize_state(scene_scale=1.0)
        state["grad2d"] = torch.ones((4,), dtype=torch.float32)
        state["count"] = torch.ones((4,), dtype=torch.float32)
        state["edge_score"] = torch.ones((4,), dtype=torch.float32)
        state["error_score_max"] = torch.ones((4,), dtype=torch.float32)
        state["current_budget_cap"] = 8

        n_dupli, n_split = strategy._grow_gs(params, optimizers, state, step=0, target_growth=4)

        self.assertEqual(2, n_dupli)
        self.assertEqual(2, n_split)

    def test_projection_helpers_round_trip_distorted_colmap_view(self) -> None:
        camera = pycolmap.Camera.create_from_model_name(1, "OPENCV", 1000.0, 800, 600)
        camera.params = [500.0, 505.0, 400.0, 300.0, 0.08, -0.03, 0.002, -0.001]
        view = TrainingView(
            view_index=0,
            image_name="opencv.png",
            rgb_tensor=torch.ones((2, 2, 3), dtype=torch.float32),
            alpha_tensor=torch.ones((2, 2, 1), dtype=torch.float32),
            has_alpha=True,
            camtoworld=torch.eye(4, dtype=torch.float32),
            K=torch.tensor(camera.calibration_matrix(), dtype=torch.float32),
            width=800,
            height=600,
            camera_model="pinhole",
            radial_coeffs=torch.tensor([0.08, -0.03, 0.0, 0.0, 0.0, 0.0], dtype=torch.float32),
            tangential_coeffs=torch.tensor([0.002, -0.001], dtype=torch.float32),
            use_unscented_transform=True,
            projection_camera_model_name="OPENCV",
            projection_camera_params=torch.tensor(camera.params, dtype=torch.float32),
            source_camera_model_name="OPENCV",
        )
        camera_points = torch.tensor(
            [
                [0.0, 0.0, 1.0],
                [0.08, -0.04, 1.0],
                [-0.06, 0.05, 1.0],
            ],
            dtype=torch.float32,
        )

        pixels, inside = _project_camera_points_to_pixels(view, camera_points)
        rays, valid = _unproject_image_pixels_to_camera_rays(view, pixels[:, 0], pixels[:, 1])

        self.assertTrue(torch.all(inside))
        self.assertTrue(torch.all(valid))
        self.assertTrue(
            torch.allclose(
                rays[:, :2],
                camera_points[:, :2] / camera_points[:, 2:].clamp_min(1.0e-6),
                atol=2.0e-4,
            )
        )

    def test_manifest_loader_accepts_utf8_bom(self) -> None:
        temp_dir = Path("companion_app") / "tests" / f"_tmp_manifest_bom_{uuid.uuid4().hex}"
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = temp_dir / "transforms_train_subset.json"
            image_path = temp_dir / "frame.png"
            image_path.write_bytes(b"fake")
            manifest_path.write_text(
                json.dumps(
                    {
                        "camera_angle_x": 0.7,
                        "frames": [
                            {
                                "file_path": "frame.png",
                                "transform_matrix": [
                                    [1.0, 0.0, 0.0, 0.0],
                                    [0.0, 1.0, 0.0, 0.0],
                                    [0.0, 0.0, 1.0, 0.0],
                                    [0.0, 0.0, 0.0, 1.0],
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8-sig",
            )

            with (
                mock.patch("companion_app.gsplat_pipeline.paths.project_root", return_value=temp_dir),
                mock.patch(
                    "companion_app.gsplat_pipeline._load_image_tensor",
                    return_value=(
                        torch.ones((1, 1, 3), dtype=torch.float32),
                        torch.ones((1, 1, 1), dtype=torch.float32),
                        1,
                        1,
                        1.0,
                        True,
                    ),
                ),
            ):
                views = _load_training_views_from_transforms(
                    {"id": "project-bom"},
                    manifest_path,
                    None,
                    {"train_resolution": 64},
                )

            self.assertEqual(1, len(views))
            self.assertEqual("frame.png", views[0].image_name)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_manifest_loader_falls_back_to_project_input_basename(self) -> None:
        temp_dir = Path("companion_app") / "tests" / f"_tmp_manifest_input_{uuid.uuid4().hex}"
        try:
            input_dir = temp_dir / "input"
            input_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = temp_dir / "transforms_train_subset.json"
            image_path = input_dir / "frame.png"
            image_path.write_bytes(b"fake")
            manifest_path.write_text(
                json.dumps(
                    {
                        "camera_angle_x": 0.7,
                        "frames": [
                            {
                                "file_path": "images/frame.png",
                                "transform_matrix": [
                                    [1.0, 0.0, 0.0, 0.0],
                                    [0.0, 1.0, 0.0, 0.0],
                                    [0.0, 0.0, 1.0, 0.0],
                                    [0.0, 0.0, 0.0, 1.0],
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch("companion_app.gsplat_pipeline.paths.project_root", return_value=temp_dir),
                mock.patch("companion_app.gsplat_pipeline.paths.project_input_dir", return_value=input_dir),
                mock.patch(
                    "companion_app.gsplat_pipeline._load_image_tensor",
                    return_value=(
                        torch.ones((1, 1, 3), dtype=torch.float32),
                        torch.ones((1, 1, 1), dtype=torch.float32),
                        1,
                        1,
                        1.0,
                        True,
                    ),
                ),
            ):
                views = _load_training_views_from_transforms(
                    {"id": "project-input"},
                    manifest_path,
                    None,
                    {"train_resolution": 64},
                )

            self.assertEqual(1, len(views))
            self.assertEqual("frame.png", views[0].image_name)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_bilateral_grid_starts_as_identity_transform(self) -> None:
        params = _create_bilateral_grid_params(1, torch.device("cpu"), {"appearance_grid_size": 4, "appearance_luma_bins": 4})
        view = _make_view("identity.png")
        colors = torch.tensor([[[[0.2, 0.4, 0.6], [0.8, 0.1, 0.3]]]], dtype=torch.float32)

        corrected = _apply_exposure_compensation(colors, view, params, guide_pixels=colors)
        regularization, smoothness = _appearance_regularization(params)

        self.assertTrue(torch.allclose(colors, corrected, atol=1.0e-5))
        self.assertAlmostEqual(0.0, float(regularization.item()), places=6)
        self.assertAlmostEqual(0.0, float(smoothness.item()), places=6)

    def test_bilateral_grid_inverse_recovers_raw_colors(self) -> None:
        params = _create_bilateral_grid_params(1, torch.device("cpu"), {"appearance_grid_size": 4, "appearance_luma_bins": 4})
        view = _make_view("inverse.png")
        colors = torch.tensor([[[[0.2, 0.4, 0.6], [0.8, 0.1, 0.3]]]], dtype=torch.float32)

        with torch.no_grad():
            params["grid"][:, 3].fill_(0.1)
            params["grid"][:, 7].fill_(-0.05)
            params["grid"][:, 11].fill_(0.08)

        corrected = _apply_exposure_compensation(colors, view, params, guide_pixels=colors)
        restored = _invert_exposure_compensation(corrected, colors, view, params)

        self.assertTrue(torch.allclose(restored, colors, atol=1.0e-4))

    def test_manifest_camera_metadata_enables_ut_for_distorted_camera(self) -> None:
        camera_model, radial, tangential, thin_prism, use_ut = _manifest_camera_metadata(
            {"camera_model": "fisheye"},
            {"k1": 0.05, "k2": -0.01},
        )

        self.assertEqual("fisheye", camera_model)
        self.assertIsNotNone(radial)
        self.assertIsNone(tangential)
        self.assertIsNone(thin_prism)
        self.assertTrue(use_ut)

    def test_manifest_camera_metadata_keeps_plain_fisheye_off_ut_path(self) -> None:
        camera_model, radial, tangential, thin_prism, use_ut = _manifest_camera_metadata(
            {"camera_model": "fisheye"},
            {},
        )

        self.assertEqual("fisheye", camera_model)
        self.assertIsNone(radial)
        self.assertIsNone(tangential)
        self.assertIsNone(thin_prism)
        self.assertFalse(use_ut)

    def test_manifest_distorted_fisheye_normalizes_to_trainable_projection(self) -> None:
        K = torch.tensor(
            [
                [300.0, 0.0, 128.0],
                [0.0, 300.0, 128.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=torch.float32,
        )

        spec, source_camera, source_model_name = _manifest_camera_to_training_spec(
            {
                "camera_model": "fisheye",
                "fl_x": 300.0,
                "fl_y": 300.0,
                "cx": 128.0,
                "cy": 128.0,
            },
            {"k1": 0.05, "k2": -0.01},
            K=K,
            width=256,
            height=256,
            normalize_distortion=True,
        )

        self.assertEqual("fisheye", spec.camera_model)
        self.assertFalse(spec.use_unscented_transform)
        self.assertTrue(spec.requires_image_normalization)
        self.assertEqual("FISHEYE", spec.projection_camera_model_name)
        self.assertIsNotNone(source_camera)
        self.assertEqual("OPENCV_FISHEYE", source_model_name)

    def test_auto_training_profile_prefers_las_strategy(self) -> None:
        profile = _resolve_training_profile(
            {
                "strategy_name": "auto",
                "budget_schedule": "staged",
                "quality_preset": "balanced",
                "train_steps": 1200,
                "train_resolution": 640,
            },
            view_count=24,
            initial_points=10_000,
        )

        self.assertEqual("las", profile["strategy_name"])
        self.assertEqual("igs_plus", profile["budget_schedule"])
        self.assertEqual(36, profile["refine_start_iter"])
        self.assertEqual(30, profile["refine_every"])
        self.assertEqual(0, profile["warmup_events"])
        self.assertIn("edge_threshold", profile)
        self.assertIn("candidate_factor", profile)
        self.assertIn("las_primary_shrink", profile)

    def test_auto_training_profile_switches_to_mcmc_for_normalized_soft_fisheye_dataset(self) -> None:
        profile = _resolve_training_profile(
            {
                "strategy_name": "auto",
                "quality_preset": "balanced",
                "train_steps": 4000,
                "train_resolution": 640,
            },
            view_count=22,
            initial_points=30_000,
            dataset_diagnostics={
                "sharpness_mean": 0.0028,
                "quality_score": 69.0,
                "exposure_clipped_mean": 0.14,
            },
            projection_diagnostics={
                "normalized_ratio": 0.95,
                "fisheye_ratio": 0.95,
            },
        )

        self.assertEqual("mcmc", profile["strategy_name"])
        self.assertEqual("normalized_distorted_camera_majority", profile["auto_strategy_reason"])
        self.assertLess(profile["max_gaussians"], 350000)

    def test_auto_training_profile_switches_to_mcmc_for_long_video_with_near_duplicates(self) -> None:
        profile = _resolve_training_profile(
            {
                "strategy_name": "auto",
                "quality_preset": "balanced",
                "train_steps": 6000,
                "train_resolution": 640,
            },
            view_count=114,
            initial_points=110_000,
            dataset_diagnostics={
                "sharpness_mean": 0.013,
                "quality_score": 80.0,
                "duplicate_like_pairs": 26,
                "source_videos": 1,
                "video_selected_frames": 139,
                "selected_overlap_mean": 0.81,
            },
        )

        self.assertEqual("mcmc", profile["strategy_name"])
        self.assertEqual("video_dataset_with_many_near_duplicates", profile["auto_strategy_reason"])

    def test_self_organizing_config_softens_for_normalized_blurry_dataset(self) -> None:
        config = _resolve_self_organizing_config(
            {},
            max_steps=4000,
            refine_every=100,
            refine_start_iter=120,
            dataset_diagnostics={
                "sharpness_mean": 0.0028,
                "quality_score": 69.0,
                "exposure_clipped_mean": 0.14,
            },
            projection_diagnostics={
                "normalized_ratio": 0.95,
            },
        )

        self.assertLess(config.smoothness_weight, 1.0e-3)
        self.assertGreaterEqual(config.start_step, 600)
        self.assertGreaterEqual(config.sort_every, 140)

    def test_short_las_run_keeps_multiple_densify_events(self) -> None:
        profile = _resolve_training_profile(
            {
                "strategy_name": "las",
                "quality_preset": "balanced",
                "train_steps": 250,
                "train_resolution": 384,
            },
            view_count=12,
            initial_points=1400,
        )

        self.assertLessEqual(profile["refine_start_iter"], 50)
        self.assertLessEqual(profile["refine_every"], 50)
        self.assertGreater(profile["refine_stop_iter"], profile["refine_start_iter"])

    def test_laplacian_edge_backbone_highlights_step_edge(self) -> None:
        image = torch.zeros((1, 8, 8, 3), dtype=torch.float32)
        image[:, :, 4:, :] = 1.0

        edges = _laplacian_edge_backbone(image)

        self.assertEqual((1, 8, 8), tuple(edges.shape))
        self.assertGreater(float(edges[0, :, 3:5].mean().item()), 0.2)
        self.assertLess(float(edges[0, :, :2].mean().item()), 0.05)

    def test_depth_reinit_candidate_sanitizer_drops_non_finite_and_far_points(self) -> None:
        reference = torch.tensor(
            [[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.0, 0.1, 0.0], [0.0, 0.0, 0.1]] * 20,
            dtype=torch.float32,
        )
        points = torch.tensor(
            [
                [0.05, 0.02, 0.01],
                [float("nan"), 0.0, 0.0],
                [1000.0, 1000.0, 1000.0],
            ],
            dtype=torch.float32,
        )
        colors = torch.tensor(
            [
                [0.5, 0.5, 0.5],
                [0.1, 0.2, 0.3],
                [1.5, -0.5, 0.2],
            ],
            dtype=torch.float32,
        )

        filtered_points, filtered_colors = _sanitize_depth_reinit_candidates(points, colors, reference)

        self.assertEqual((1, 3), tuple(filtered_points.shape))
        self.assertTrue(torch.allclose(filtered_points[0], torch.tensor([0.05, 0.02, 0.01])))
        self.assertTrue(torch.all((filtered_colors >= 0.0) & (filtered_colors <= 1.0)))

    def test_read_colmap_dense_array_parses_header_and_payload(self) -> None:
        temp_dir = Path("companion_app") / "tests" / f"_tmp_dense_map_{uuid.uuid4().hex}"
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            dense_path = temp_dir / "frame.png.photometric.bin"
            header = b"3&2&1&"
            payload = torch.tensor([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=torch.float32).numpy().tobytes()
            dense_path.write_bytes(header + payload)

            parsed = _read_colmap_dense_array(dense_path)

            self.assertEqual((2, 3), tuple(parsed.shape))
            self.assertAlmostEqual(1.0, float(parsed[0, 0]))
            self.assertAlmostEqual(6.0, float(parsed[1, 2]))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_voxel_downsample_seed_cloud_keeps_one_point_per_cell(self) -> None:
        points = torch.tensor(
            [
                [0.00, 0.00, 0.00],
                [0.01, 0.01, 0.01],
                [0.30, 0.30, 0.30],
                [0.31, 0.30, 0.29],
            ],
            dtype=torch.float32,
        )
        colors = torch.tensor(
            [
                [1.0, 0.0, 0.0],
                [0.9, 0.1, 0.1],
                [0.0, 1.0, 0.0],
                [0.1, 0.9, 0.1],
            ],
            dtype=torch.float32,
        )

        reduced_points, reduced_colors = _voxel_downsample_seed_cloud(
            points,
            colors,
            voxel_size=0.2,
            max_points=8,
        )

        self.assertEqual(2, len(reduced_points))
        self.assertEqual((2, 3), tuple(reduced_colors.shape))

    def test_igs_plus_budget_schedule_grows_by_refine_events(self) -> None:
        initial = 59
        target = 3320

        before_refine = _scheduled_gaussian_budget(
            step=499,
            initial_gaussians=initial,
            target_gaussians=target,
            refine_start_iter=500,
            refine_stop_iter=5100,
            refine_every=500,
            sh_degree_to_use=0,
            sh_degree=3,
            schedule="igs_plus",
        )
        first_event = _scheduled_gaussian_budget(
            step=501,
            initial_gaussians=initial,
            target_gaussians=target,
            refine_start_iter=500,
            refine_stop_iter=5100,
            refine_every=500,
            sh_degree_to_use=0,
            sh_degree=3,
            schedule="igs_plus",
        )
        later_event = _scheduled_gaussian_budget(
            step=1500,
            initial_gaussians=initial,
            target_gaussians=target,
            refine_start_iter=500,
            refine_stop_iter=5100,
            refine_every=500,
            sh_degree_to_use=1,
            sh_degree=3,
            schedule="igs_plus",
        )

        self.assertEqual(initial, before_refine)
        self.assertGreater(first_event, initial)
        self.assertGreater(later_event, first_event)
        self.assertLessEqual(later_event, target)

    def test_igs_plus_budget_progresses_continuously_between_refine_events(self) -> None:
        initial = 1000
        target = 10000

        at_start = _scheduled_gaussian_budget(
            step=120,
            initial_gaussians=initial,
            target_gaussians=target,
            refine_start_iter=120,
            refine_stop_iter=1120,
            refine_every=100,
            sh_degree_to_use=0,
            sh_degree=3,
            schedule="igs_plus",
        )
        between_events = _scheduled_gaussian_budget(
            step=170,
            initial_gaussians=initial,
            target_gaussians=target,
            refine_start_iter=120,
            refine_stop_iter=1120,
            refine_every=100,
            sh_degree_to_use=0,
            sh_degree=3,
            schedule="igs_plus",
        )
        next_event = _scheduled_gaussian_budget(
            step=220,
            initial_gaussians=initial,
            target_gaussians=target,
            refine_start_iter=120,
            refine_stop_iter=1120,
            refine_every=100,
            sh_degree_to_use=0,
            sh_degree=3,
            schedule="igs_plus",
        )

        self.assertEqual(initial, at_start)
        self.assertGreater(between_events, at_start)
        self.assertGreater(next_event, between_events)

    def test_las_projection_fallback_can_still_grow_without_gradients(self) -> None:
        params = torch.nn.ParameterDict(
            {
                "means": torch.nn.Parameter(torch.zeros((8, 3), dtype=torch.float32)),
                "scales": torch.nn.Parameter(torch.full((8, 3), -4.0, dtype=torch.float32)),
                "quats": torch.nn.Parameter(torch.tensor([[1.0, 0.0, 0.0, 0.0]] * 8, dtype=torch.float32)),
                "opacities": torch.nn.Parameter(torch.full((8,), 2.0, dtype=torch.float32)),
                "sh0": torch.nn.Parameter(torch.zeros((8, 1, 3), dtype=torch.float32)),
                "shN": torch.nn.Parameter(torch.zeros((8, 0, 3), dtype=torch.float32)),
            }
        )
        optimizers = {
            key: torch.optim.Adam([parameter], lr=1.0e-3)
            for key, parameter in params.items()
        }
        strategy = _EdgeAwareLASStrategy(
            refine_start_iter=0,
            refine_stop_iter=100,
            refine_every=50,
            reset_every=10_000,
            prune_opa=0.005,
            grow_scale3d=0.05,
            grow_scale2d=0.05,
            prune_scale3d=10.0,
            prune_scale2d=10.0,
        )
        strategy.check_sanity(params, optimizers)
        state = strategy.initialize_state(scene_scale=1.0)
        state["current_budget_cap"] = 16
        info = {
            "means2d": torch.tensor([[[2.0, 2.0]] * 8], dtype=torch.float32),
            "radii": torch.ones((1, 8, 1), dtype=torch.float32),
            "width": 8,
            "height": 8,
            "n_cameras": 1,
            "edge_backbone": torch.ones((1, 8, 8), dtype=torch.float32),
            "error_backbone": torch.ones((1, 8, 8), dtype=torch.float32),
        }

        strategy.step_post_backward(
            params=params,
            optimizers=optimizers,
            state=state,
            step=0,
            info=info,
            packed=False,
            gradientless=True,
        )

        self.assertGreater(len(params["means"]), 8)
        self.assertEqual("projection_fallback", state["densification_mode"])
        self.assertGreaterEqual(int(state["last_refine_stats"]["split"]), 1)

    def test_hybrid_las_split_matches_long_axis_shape_update(self) -> None:
        params = torch.nn.ParameterDict(
            {
                "means": torch.nn.Parameter(torch.zeros((1, 3), dtype=torch.float32)),
                "scales": torch.nn.Parameter(torch.zeros((1, 3), dtype=torch.float32)),
                "quats": torch.nn.Parameter(torch.tensor([[1.0, 0.0, 0.0, 0.0]], dtype=torch.float32)),
                "opacities": torch.nn.Parameter(torch.logit(torch.tensor([0.9], dtype=torch.float32))),
                "sh0": torch.nn.Parameter(torch.zeros((1, 1, 3), dtype=torch.float32)),
                "shN": torch.nn.Parameter(torch.zeros((1, 0, 3), dtype=torch.float32)),
            }
        )
        optimizers = {key: torch.optim.Adam([parameter], lr=1.0e-3) for key, parameter in params.items()}
        state: dict[str, object] = {}

        long_axis, covariance = _hybrid_las_split(
            params,
            optimizers,
            state,
            torch.tensor([True]),
            torch.tensor([True]),
            primary_shrink=2.0,
            secondary_shrink=1.0 / 0.85,
            opacity_factor=0.6,
            offset_scale=0.5,
        )

        self.assertEqual(1, long_axis)
        self.assertEqual(0, covariance)
        self.assertEqual(2, len(params["means"]))
        child_scales = torch.exp(params["scales"].detach())
        child_ratios = child_scales.max(dim=-1).values / child_scales.min(dim=-1).values.clamp_min(1.0e-6)
        self.assertTrue(torch.all(child_ratios >= 1.69).item())
        self.assertTrue(torch.allclose(child_scales[:, 0], torch.full((2,), 0.5), atol=1.0e-5))
        self.assertTrue(torch.allclose(child_scales[:, 1], torch.full((2,), 0.85), atol=1.0e-5))
        self.assertTrue(torch.allclose(child_scales[:, 2], torch.full((2,), 0.85), atol=1.0e-5))
        self.assertTrue(torch.allclose(params["means"][0], torch.tensor([0.5, 0.0, 0.0]), atol=1.0e-5))
        self.assertTrue(torch.allclose(params["means"][1], torch.tensor([-0.5, 0.0, 0.0]), atol=1.0e-5))
        child_opacity = torch.sigmoid(params["opacities"].detach())
        self.assertTrue(torch.allclose(child_opacity, torch.full((2,), 0.54), atol=1.0e-5))

    def test_summarize_splat_shape_reports_spherical_and_hyperelongated_fractions(self) -> None:
        splats = torch.nn.ParameterDict(
            {
                "means": torch.nn.Parameter(torch.zeros((4, 3), dtype=torch.float32)),
                "scales": torch.nn.Parameter(
                    torch.log(
                        torch.tensor(
                            [
                                [1.0, 1.0, 1.0],
                                [1.1, 1.0, 1.0],
                                [2.0, 1.0, 1.0],
                                [12.0, 1.0, 1.0],
                            ],
                            dtype=torch.float32,
                        )
                    )
                ),
                "quats": torch.nn.Parameter(torch.tensor([[1.0, 0.0, 0.0, 0.0]] * 4, dtype=torch.float32)),
                "opacities": torch.nn.Parameter(torch.logit(torch.full((4,), 0.6, dtype=torch.float32))),
                "sh0": torch.nn.Parameter(torch.zeros((4, 1, 3), dtype=torch.float32)),
                "shN": torch.nn.Parameter(torch.zeros((4, 0, 3), dtype=torch.float32)),
            }
        )

        diagnostics = _summarize_splat_shape(splats)

        self.assertGreater(diagnostics["spherical_fraction"], 0.20)
        self.assertGreater(diagnostics["hyperelongated_fraction"], 0.20)
        self.assertGreater(diagnostics["anisotropy_p95"], diagnostics["anisotropy_median"])

    def test_runtime_monitor_relaxes_sogs_when_detail_retention_worsens(self) -> None:
        monitor = _initialize_training_runtime_monitor(
            max_steps=400,
            strategy_name="las",
            self_organizing_config=SelfOrganizingCompressionConfig(enabled=True),
        )
        strategy = _EdgeAwareLASStrategy(
            refine_start_iter=0,
            refine_stop_iter=100,
            refine_every=50,
            reset_every=10_000,
            prune_opa=0.005,
            grow_scale3d=0.05,
            grow_scale2d=0.05,
            prune_scale3d=10.0,
            prune_scale2d=10.0,
        )
        state: dict[str, object] = {"adaptive_long_axis_bias": 0.0}
        shape = {
            "count": 1024,
            "anisotropy_median": 2.2,
            "anisotropy_p90": 5.0,
            "anisotropy_p95": 7.0,
            "spherical_fraction": 0.15,
            "elongated_fraction": 0.60,
            "hyperelongated_fraction": 0.0,
            "low_opacity_fraction": 0.02,
        }

        for step in range(40):
            if step < 28:
                l1_value = 0.10
                ssim_value = 0.72
            else:
                l1_value = 0.13
                ssim_value = 0.68
            _record_training_runtime_observation(
                monitor,
                step=step,
                loss_value=l1_value + (1.0 - ssim_value),
                l1_value=l1_value,
                ssim_value=ssim_value,
                current_budget_cap=2000,
                gaussian_count=1100,
                shape_diagnostics=shape,
                refine_stats=None,
            )

        actions = _apply_training_runtime_adaptation(
            monitor,
            step=39,
            max_steps=400,
            strategy=strategy,
            strategy_state=state,
            self_organizing_config=SelfOrganizingCompressionConfig(enabled=True),
        )

        self.assertTrue(any(action["kind"] == "sogs_relax" for action in actions))
        self.assertLess(float(monitor["sogs_weight_scale"]), 1.0)

    def test_runtime_monitor_tightens_long_axis_split_when_anisotropy_runs_away(self) -> None:
        monitor = _initialize_training_runtime_monitor(
            max_steps=400,
            strategy_name="las",
            self_organizing_config=SelfOrganizingCompressionConfig(enabled=False),
        )
        strategy = _EdgeAwareLASStrategy(
            refine_start_iter=0,
            refine_stop_iter=100,
            refine_every=50,
            reset_every=10_000,
            prune_opa=0.005,
            grow_scale3d=0.05,
            grow_scale2d=0.05,
            prune_scale3d=10.0,
            prune_scale2d=10.0,
            las_offset_scale=0.5,
        )
        state: dict[str, object] = {"adaptive_long_axis_bias": 0.0}
        shape = {
            "count": 4096,
            "anisotropy_median": 3.2,
            "anisotropy_p90": 16.0,
            "anisotropy_p95": 24.0,
            "spherical_fraction": 0.04,
            "elongated_fraction": 0.85,
            "hyperelongated_fraction": 0.08,
            "low_opacity_fraction": 0.01,
        }

        for step in range(40):
            _record_training_runtime_observation(
                monitor,
                step=step,
                loss_value=0.22,
                l1_value=0.12,
                ssim_value=0.73,
                current_budget_cap=5000,
                gaussian_count=4200,
                shape_diagnostics=shape,
                refine_stats={"step": step, "growth_fill": 0.55, "split_ratio": 0.45},
            )

        actions = _apply_training_runtime_adaptation(
            monitor,
            step=39,
            max_steps=400,
            strategy=strategy,
            strategy_state=state,
            self_organizing_config=SelfOrganizingCompressionConfig(enabled=False),
        )

        self.assertTrue(any(action["kind"] == "anisotropy_guard" for action in actions))
        self.assertGreater(float(state["adaptive_long_axis_bias"]), 0.0)
        self.assertLess(strategy.las_offset_scale, 0.5)

    def test_find_colmap_executable_prefers_configured_path(self) -> None:
        temp_dir = Path("companion_app") / "tests" / f"_tmp_colmap_{uuid.uuid4().hex}"
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            executable = temp_dir / "COLMAP.bat"
            executable.write_text("@echo off\r\n", encoding="utf-8")

            resolved = _find_colmap_executable({"depth_bootstrap_colmap_executable": str(executable)})

            self.assertEqual(executable, resolved)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_find_colmap_executable_discovers_versioned_localappdata_layout(self) -> None:
        temp_home = Path("companion_app") / "tests" / f"_tmp_home_{uuid.uuid4().hex}"
        try:
            executable = temp_home / "AppData" / "Local" / "COLMAP" / "4.0.3" / "COLMAP.bat"
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("@echo off\r\n", encoding="utf-8")

            with mock.patch("pathlib.Path.home", return_value=temp_home):
                resolved = _find_colmap_executable({})

            self.assertEqual(executable, resolved)
        finally:
            shutil.rmtree(temp_home, ignore_errors=True)

    def test_colmap_process_command_wraps_batch_file(self) -> None:
        command = _colmap_process_command(Path("C:/COLMAP/COLMAP.bat"), "patch_match_stereo")
        self.assertEqual(["cmd.exe", "/c", "C:\\COLMAP\\COLMAP.bat", "patch_match_stereo"], command)

    def test_dense_stereo_progress_snapshot_counts_depth_map_types(self) -> None:
        temp_dir = Path("companion_app") / "tests" / f"_tmp_dense_progress_{uuid.uuid4().hex}"
        try:
            depth_dir = temp_dir / "stereo" / "depth_maps"
            depth_dir.mkdir(parents=True, exist_ok=True)
            (depth_dir / "a.photometric.bin").write_bytes(b"x")
            (depth_dir / "b.photometric.bin").write_bytes(b"x")
            (depth_dir / "a.geometric.bin").write_bytes(b"x")

            snapshot = _dense_stereo_progress_snapshot(temp_dir)

            self.assertEqual(2, snapshot["photometric"])
            self.assertEqual(1, snapshot["geometric"])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_cuda_dense_stereo_error_detection_matches_colmap_message(self) -> None:
        self.assertTrue(_is_cuda_dense_stereo_error("[mvs.cc:232] Dense stereo reconstruction requires CUDA, which is not available on your system."))
        self.assertFalse(_is_cuda_dense_stereo_error("some unrelated failure"))

    def test_run_patch_match_stereo_auto_reports_missing_cuda_backend_clearly(self) -> None:
        options = pycolmap.PatchMatchOptions()
        options.gpu_index = "0"
        with (
            mock.patch("companion_app.gsplat_pipeline._find_colmap_executable", return_value=None),
            mock.patch(
                "companion_app.gsplat_pipeline.pycolmap.patch_match_stereo",
                side_effect=RuntimeError("[mvs.cc:232] Dense stereo reconstruction requires CUDA, which is not available on your system."),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "Dense stereo requires a CUDA-enabled backend"):
                _run_patch_match_stereo_auto(
                    Path("dense"),
                    settings={},
                    patch_options=options,
                    job={"id": "job", "log_path": str((Path("companion_app") / "tests" / "tmp.log").resolve())},
                )

    def test_export_handoff_logs_final_export_stages_to_job_log(self) -> None:
        temp_dir = Path("companion_app") / "tests" / f"_tmp_export_{uuid.uuid4().hex}"
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            workspace_ply = temp_dir / "scene.ply"
            workspace_ply.write_text("ply\n", encoding="utf-8")
            workspace_gasp = temp_dir / "scene.gasp"
            workspace_gasp.write_text("gasp", encoding="utf-8")
            workspace_compressed = temp_dir / "scene.compressed.ply"
            workspace_compressed.write_text("compressed", encoding="utf-8")
            workspace_spz = temp_dir / "scene.spz"
            workspace_spz.write_text("spz", encoding="utf-8")
            log_path = (temp_dir / "job.log").resolve()
            project = {
                "id": "project-export-test",
                "name": "Export Test",
                "backend": "gsplat",
            }
            job = {
                "id": "job-export-test",
                "log_path": str(log_path),
            }
            training_summary = {
                "_workspace_compressed_ply": str(workspace_compressed),
                "_workspace_spz": str(workspace_spz),
            }
            fake_exports_root = temp_dir / "exports"
            fake_result_manifest = temp_dir / "result_manifest.json"
            fake_training_summary = temp_dir / "training_summary.json"

            with (
                mock.patch("companion_app.gsplat_pipeline.write_gaussian_gasp_from_ply", return_value=workspace_gasp),
                mock.patch("companion_app.gsplat_pipeline.export_ply_from_gaussian_gasp", side_effect=lambda _src, dst: dst.write_text("ply", encoding="utf-8")),
                mock.patch("companion_app.gsplat_pipeline.paths.exports_root", return_value=fake_exports_root),
                mock.patch("companion_app.gsplat_pipeline._training_summary_path", return_value=fake_training_summary),
                mock.patch("companion_app.gsplat_pipeline._result_manifest_path", return_value=fake_result_manifest),
                mock.patch("companion_app.gsplat_pipeline.paths.write_latest_export"),
                mock.patch("companion_app.gsplat_pipeline.store.update_project"),
            ):
                _export_handoff(
                    project,
                    job,
                    workspace_ply,
                    1,
                    {"min": [0, 0, 0], "max": [1, 1, 1]},
                    training_summary,
                )

            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn("Starting final export handoff.", log_text)
            self.assertIn("Writing workspace GASP package.", log_text)
            self.assertIn("Packaging final export bundle", log_text)
            self.assertIn("Finalizing export metadata", log_text)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_hybrid_las_split_keeps_isotropic_split_close_to_covariance_rule(self) -> None:
        torch.manual_seed(0)
        params = torch.nn.ParameterDict(
            {
                "means": torch.nn.Parameter(torch.zeros((1, 3), dtype=torch.float32)),
                "scales": torch.nn.Parameter(torch.zeros((1, 3), dtype=torch.float32)),
                "quats": torch.nn.Parameter(torch.tensor([[1.0, 0.0, 0.0, 0.0]], dtype=torch.float32)),
                "opacities": torch.nn.Parameter(torch.logit(torch.tensor([0.9], dtype=torch.float32))),
                "sh0": torch.nn.Parameter(torch.zeros((1, 1, 3), dtype=torch.float32)),
                "shN": torch.nn.Parameter(torch.zeros((1, 0, 3), dtype=torch.float32)),
            }
        )
        optimizers = {key: torch.optim.Adam([parameter], lr=1.0e-3) for key, parameter in params.items()}
        state: dict[str, object] = {}

        long_axis, covariance = _hybrid_las_split(
            params,
            optimizers,
            state,
            torch.tensor([True]),
            torch.tensor([False]),
            primary_shrink=2.0,
            secondary_shrink=1.0 / 0.85,
            opacity_factor=0.6,
            offset_scale=0.5,
        )

        self.assertEqual(0, long_axis)
        self.assertEqual(1, covariance)
        self.assertEqual(2, len(params["means"]))
        child_scales = torch.exp(params["scales"].detach())
        self.assertTrue(torch.allclose(child_scales, torch.full((2, 3), 1.0 / 1.6), atol=1.0e-5))
        child_ratios = child_scales.max(dim=-1).values / child_scales.min(dim=-1).values.clamp_min(1.0e-6)
        self.assertTrue(torch.allclose(child_ratios, torch.ones_like(child_ratios), atol=1.0e-5))
        child_opacity = torch.sigmoid(params["opacities"].detach())
        self.assertTrue(torch.allclose(child_opacity, torch.full((2,), 0.9), atol=1.0e-5))

    def test_projection_fallback_pruning_is_capped_while_under_budget(self) -> None:
        params = torch.nn.ParameterDict(
            {
                "means": torch.nn.Parameter(torch.zeros((32, 3), dtype=torch.float32)),
                "scales": torch.nn.Parameter(torch.full((32, 3), -4.0, dtype=torch.float32)),
                "quats": torch.nn.Parameter(torch.tensor([[1.0, 0.0, 0.0, 0.0]] * 32, dtype=torch.float32)),
                "opacities": torch.nn.Parameter(torch.full((32,), -10.0, dtype=torch.float32)),
                "sh0": torch.nn.Parameter(torch.zeros((32, 1, 3), dtype=torch.float32)),
                "shN": torch.nn.Parameter(torch.zeros((32, 0, 3), dtype=torch.float32)),
            }
        )
        optimizers = {
            key: torch.optim.Adam([parameter], lr=1.0e-3)
            for key, parameter in params.items()
        }
        strategy = _EdgeAwareLASStrategy(
            refine_start_iter=0,
            refine_stop_iter=100,
            refine_every=25,
            reset_every=1000,
            prune_opa=0.005,
            grow_scale3d=0.05,
            grow_scale2d=0.05,
            prune_scale3d=10.0,
            prune_scale2d=10.0,
        )
        strategy.check_sanity(params, optimizers)
        state = strategy.initialize_state(scene_scale=1.0)
        state["densification_mode"] = "projection_fallback"
        state["current_budget_cap"] = 128
        state["last_target_selected"] = 20

        pruned = strategy._prune_gs(params, optimizers, state, step=50)

        self.assertEqual(14, pruned)
        self.assertTrue(bool(state["last_prune_limited"]))
        self.assertEqual(32, int(state["last_pruned_requested"]))
        self.assertEqual(14, int(state["last_pruned_applied"]))

    def test_reconstruction_stats_reject_low_registered_view_coverage(self) -> None:
        stats = _reconstruction_registration_stats(_FakeReconstruction(registered_images=2, total_images=22), 22)

        self.assertEqual(6, _minimum_registered_view_count(22))
        self.assertEqual(2, stats["registered_images"])
        self.assertEqual(22, stats["total_input_images"])
        self.assertFalse(bool(stats["usable"]))

    def test_reconstruction_stats_accept_reasonable_registered_view_coverage(self) -> None:
        stats = _reconstruction_registration_stats(_FakeReconstruction(registered_images=8, total_images=22), 22)

        self.assertTrue(bool(stats["usable"]))
        self.assertGreaterEqual(int(stats["registered_images"]), int(stats["min_registered_images"]))

    def test_alternate_sfm_match_mode_switches_after_low_coverage_attempt(self) -> None:
        self.assertEqual("sequential", _alternate_sfm_match_mode("exhaustive", 22))
        self.assertEqual("exhaustive", _alternate_sfm_match_mode("sequential", 22))
        self.assertIsNone(_alternate_sfm_match_mode("exhaustive", 6))


if __name__ == "__main__":
    unittest.main()
