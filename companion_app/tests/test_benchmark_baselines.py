from __future__ import annotations

import argparse
import importlib.util
import unittest
from pathlib import Path


def _load_benchmark_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "scripts" / "benchmark_training.py"
    spec = importlib.util.spec_from_file_location("benchmark_training", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load benchmark_training module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


benchmark_training = _load_benchmark_module()


class BenchmarkBaselineTest(unittest.TestCase):
    def test_default_baseline_key_uses_repo_relative_path(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        source = repo_root / "sample_datasets" / "nerf_synthetic_lego_12" / "images"
        key = benchmark_training._default_baseline_key(
            repo_root=repo_root,
            source=source,
            preset="compact",
            strategy="auto",
            steps=25,
            train_resolution=640,
        )
        self.assertEqual("sample_datasets/nerf_synthetic_lego_12/images|compact|auto|25|640", key)

    def test_effective_gates_fall_back_to_baseline_values(self) -> None:
        args = argparse.Namespace(min_val_psnr=0.0, min_val_ssim=0.0, max_gaussians=0)
        gates = benchmark_training._effective_gates(
            args,
            {
                "gates": {
                    "min_val_psnr": 14.0,
                    "min_val_ssim": 0.64,
                    "max_gaussians": 2000,
                }
            },
        )
        self.assertEqual(14.0, gates["min_val_psnr"])
        self.assertEqual(0.64, gates["min_val_ssim"])
        self.assertEqual(2000, gates["max_gaussians"])

    def test_comparison_payload_computes_metric_deltas(self) -> None:
        comparison = benchmark_training._comparison_payload(
            result={
                "training_summary": {
                    "final_gaussians": 1500,
                    "metrics": {"psnr": 14.5, "ssim": 0.65},
                    "validation_metrics": {"psnr": 14.25, "ssim": 0.645},
                }
            },
            baseline_key="demo",
            baseline_entry={
                "label": "Demo baseline",
                "compare_enabled": True,
                "metrics_source": "train_and_validation",
                "metrics": {
                    "train_psnr": 14.0,
                    "train_ssim": 0.64,
                    "val_psnr": 14.0,
                    "val_ssim": 0.64,
                    "final_gaussians": 1400,
                },
            },
            gates={"min_val_psnr": 14.0, "min_val_ssim": 0.64, "max_gaussians": 2000},
        )
        self.assertIsNotNone(comparison)
        self.assertEqual(0.5, comparison["deltas"]["train_psnr_delta"])
        self.assertEqual(0.01, comparison["deltas"]["train_ssim_delta"])
        self.assertEqual(0.25, comparison["deltas"]["val_psnr_delta"])
        self.assertEqual(0.005, comparison["deltas"]["val_ssim_delta"])
        self.assertEqual(100, comparison["deltas"]["final_gaussians_delta"])


if __name__ == "__main__":
    unittest.main()
