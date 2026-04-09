from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run repeatable Gaussian Points training benchmarks.")
    parser.add_argument("sources", nargs="+", help="Image/video/archive/folder sources to benchmark.")
    parser.add_argument("--steps", type=int, default=200, help="Training steps per dataset.")
    parser.add_argument("--train-resolution", type=int, default=640, help="Training image resolution.")
    parser.add_argument("--sfm-max-image-size", type=int, default=1600, help="COLMAP feature extraction max image size.")
    parser.add_argument(
        "--preset",
        choices=("compact", "balanced", "high"),
        default="balanced",
        help="Training quality preset to benchmark.",
    )
    parser.add_argument(
        "--strategy",
        choices=("auto", "default", "mcmc"),
        default="auto",
        help="Training strategy to benchmark.",
    )
    parser.add_argument(
        "--all-presets",
        action="store_true",
        help="Benchmark compact, balanced, and high presets for each source.",
    )
    parser.add_argument(
        "--data-root",
        default="",
        help="Optional companion data root. Defaults to repo-local _tmp_benchmarks.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON output path. Defaults to <data-root>/benchmark_report.json.",
    )
    parser.add_argument(
        "--keep-data",
        action="store_true",
        help="Keep the benchmark data root instead of recreating it.",
    )
    parser.add_argument("--min-val-psnr", type=float, default=0.0, help="Fail if validation PSNR falls below this value.")
    parser.add_argument("--min-val-ssim", type=float, default=0.0, help="Fail if validation SSIM falls below this value.")
    parser.add_argument("--max-gaussians", type=int, default=0, help="Fail if final gaussian count exceeds this value.")
    parser.add_argument(
        "--baseline-file",
        default="",
        help="Optional JSON baseline file. If provided, the benchmark will report deltas and apply baseline gates when present.",
    )
    parser.add_argument(
        "--baseline-key",
        default="",
        help="Optional explicit baseline key for single-dataset comparisons.",
    )
    return parser.parse_args()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_baseline_key(
    repo_root: Path,
    source: Path,
    preset: str,
    strategy: str,
    steps: int,
    train_resolution: int,
) -> str:
    try:
        source_key = source.resolve().relative_to(repo_root.resolve()).as_posix()
    except Exception:
        source_key = source.name
    return f"{source_key}|{preset}|{strategy}|{steps}|{train_resolution}"


def _load_baselines(path: Path | None) -> dict[str, object]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("entries") or {}


def _default_baseline_file(repo_root: Path) -> Path | None:
    candidate = repo_root / "benchmarks" / "quality_baselines.json"
    if candidate.exists():
        return candidate
    return None


def _coerce_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _effective_gates(args: argparse.Namespace, baseline_entry: dict[str, object] | None) -> dict[str, float | int]:
    baseline_gates = baseline_entry.get("gates") if isinstance(baseline_entry, dict) else {}
    if not isinstance(baseline_gates, dict):
        baseline_gates = {}

    min_val_psnr = args.min_val_psnr if args.min_val_psnr > 0.0 else _coerce_float(baseline_gates.get("min_val_psnr")) or 0.0
    min_val_ssim = args.min_val_ssim if args.min_val_ssim > 0.0 else _coerce_float(baseline_gates.get("min_val_ssim")) or 0.0
    max_gaussians = args.max_gaussians if args.max_gaussians > 0 else _coerce_int(baseline_gates.get("max_gaussians")) or 0

    return {
        "min_val_psnr": round(float(min_val_psnr), 5),
        "min_val_ssim": round(float(min_val_ssim), 5),
        "max_gaussians": int(max_gaussians),
    }


def _comparison_payload(
    result: dict[str, object],
    baseline_key: str,
    baseline_entry: dict[str, object] | None,
    gates: dict[str, float | int],
) -> dict[str, object] | None:
    if not isinstance(baseline_entry, dict):
        return None

    baseline_metrics = baseline_entry.get("metrics")
    if not isinstance(baseline_metrics, dict):
        baseline_metrics = {}

    training_summary = result.get("training_summary") if isinstance(result, dict) else {}
    if not isinstance(training_summary, dict):
        training_summary = {}
    train_metrics = training_summary.get("metrics")
    if not isinstance(train_metrics, dict):
        train_metrics = {}
    validation_metrics = training_summary.get("validation_metrics")
    if not isinstance(validation_metrics, dict):
        validation_metrics = {}

    current_metrics = {
        "train_psnr": _coerce_float(train_metrics.get("psnr")),
        "train_ssim": _coerce_float(train_metrics.get("ssim")),
        "val_psnr": _coerce_float(validation_metrics.get("psnr")),
        "val_ssim": _coerce_float(validation_metrics.get("ssim")),
        "final_gaussians": _coerce_int(training_summary.get("final_gaussians")),
    }

    deltas: dict[str, float | int | None] = {}
    for key, current_value in current_metrics.items():
        baseline_value = baseline_metrics.get(key)
        if current_value is None or baseline_value is None:
            deltas[f"{key}_delta"] = None
            continue
        if key == "final_gaussians":
            deltas[f"{key}_delta"] = int(current_value) - int(baseline_value)
        else:
            deltas[f"{key}_delta"] = round(float(current_value) - float(baseline_value), 5)

    return {
        "key": baseline_key,
        "label": baseline_entry.get("label") or baseline_key,
        "compare_enabled": bool(baseline_entry.get("compare_enabled", True)),
        "metrics_source": baseline_entry.get("metrics_source"),
        "baseline_metrics": baseline_metrics,
        "current_metrics": current_metrics,
        "deltas": deltas,
        "gates": gates,
    }


def _preferred_worker_python(repo_root: Path) -> Path | None:
    candidates = [
        repo_root / ".gstrain310" / "Scripts" / "python.exe",
        repo_root / ".gstrain310" / "bin" / "python",
        repo_root / ".gstrain311" / "Scripts" / "python.exe",
        repo_root / ".gstrain311" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _ensure_worker_runtime(repo_root: Path) -> None:
    if os.environ.get("GAUSSIAN_POINTS_BENCHMARK_RUNTIME") == "worker":
        return

    worker_python = _preferred_worker_python(repo_root)
    if not worker_python:
        return

    try:
        current_python = Path(sys.executable).resolve()
        target_python = worker_python.resolve()
    except OSError:
        return
    if current_python == target_python:
        return

    env = os.environ.copy()
    env["GAUSSIAN_POINTS_BENCHMARK_RUNTIME"] = "worker"
    completed = subprocess.run([str(target_python), *sys.argv], env=env, cwd=repo_root)
    raise SystemExit(completed.returncode)


def main() -> int:
    args = _parse_args()
    repo_root = _repo_root()
    _ensure_worker_runtime(repo_root)
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    data_root = Path(args.data_root) if args.data_root else (repo_root / "_tmp_benchmarks")
    output_path = Path(args.output) if args.output else (data_root / "benchmark_report.json")
    baseline_file = Path(args.baseline_file) if args.baseline_file else _default_baseline_file(repo_root)
    baselines = _load_baselines(baseline_file)

    if data_root.exists() and not args.keep_data:
        try:
            shutil.rmtree(data_root)
        except PermissionError:
            data_root = data_root.parent / f"{data_root.name}_{int(time.time())}"
            output_path = Path(args.output) if args.output else (data_root / "benchmark_report.json")
    data_root.mkdir(parents=True, exist_ok=True)

    os.environ["GAUSSIAN_POINTS_COMPANION_HOME"] = str(data_root)

    from companion_app import store
    from companion_app.pipeline import ingest_media_sources, run_job

    store.init_db()
    results: list[dict[str, object]] = []
    failures: list[str] = []
    presets = ["compact", "balanced", "high"] if args.all_presets else [args.preset]

    for raw_source in args.sources:
        source = Path(raw_source).resolve()
        for preset in presets:
            project = store.create_project(name=f"{source.stem or source.name}_{preset}")
            ingest_started = time.perf_counter()
            ingest_summary = ingest_media_sources(project["id"], [str(source)])
            ingest_seconds = time.perf_counter() - ingest_started

            settings = store.default_job_settings(force_restart=True)
            settings["quality_preset"] = preset
            settings["strategy_name"] = args.strategy
            settings["train_steps"] = int(args.steps)
            settings["train_resolution"] = int(args.train_resolution)
            settings["sfm_max_image_size"] = int(args.sfm_max_image_size)
            settings["densify_stop_iter"] = min(int(settings["densify_stop_iter"]), max(int(args.steps) - 1, 1))
            settings["refine_scale2d_stop_iter"] = int(args.steps)

            job = store.create_job(project["id"], settings)
            started = time.perf_counter()
            exit_code = run_job(job["id"])
            elapsed = time.perf_counter() - started
            final_job = store.get_job(job["id"]) or {}
            final_project = store.get_project(project["id"]) or {}
            training_summary = final_project.get("last_training_summary") or {}
            scene_path = final_project.get("last_result_ply")
            if scene_path and Path(scene_path).exists():
                ply_size_bytes = int(Path(scene_path).stat().st_size)
            else:
                ply_size_bytes = None

            result = {
                "source": str(source),
                "preset": preset,
                "strategy": args.strategy,
                "project_id": project["id"],
                "job_id": job["id"],
                "exit_code": exit_code,
                "elapsed_seconds": round(elapsed, 2),
                "ingest_seconds": round(ingest_seconds, 2),
                "status": final_job.get("status"),
                "stage": final_job.get("stage"),
                "message": final_job.get("message"),
                "error_text": final_job.get("error_text"),
                "last_result_ply": final_project.get("last_result_ply"),
                "last_manifest_path": final_project.get("last_manifest_path"),
                "training_summary": training_summary,
                "ply_size_bytes": ply_size_bytes,
                "ingest": ingest_summary,
                "log_path": final_job.get("log_path"),
            }

            metrics = training_summary.get("metrics", {})
            validation_metrics = training_summary.get("validation_metrics", {})
            final_gaussians = int(training_summary.get("final_gaussians") or 0)
            val_psnr = float(validation_metrics.get("psnr") or 0.0)
            val_ssim = float(validation_metrics.get("ssim") or 0.0)
            baseline_key = args.baseline_key or _default_baseline_key(
                repo_root=repo_root,
                source=source,
                preset=preset,
                strategy=args.strategy,
                steps=int(args.steps),
                train_resolution=int(args.train_resolution),
            )
            baseline_entry = baselines.get(baseline_key)
            effective_gates = _effective_gates(args, baseline_entry if isinstance(baseline_entry, dict) else None)
            comparison = _comparison_payload(
                result=result,
                baseline_key=baseline_key,
                baseline_entry=baseline_entry if isinstance(baseline_entry, dict) else None,
                gates=effective_gates,
            )
            if comparison is not None:
                result["baseline_comparison"] = comparison
            results.append(result)

            if effective_gates["min_val_psnr"] > 0.0 and val_psnr < float(effective_gates["min_val_psnr"]):
                failures.append(
                    f"{source.name} [{preset}] val_psnr {val_psnr:.3f} < {float(effective_gates['min_val_psnr']):.3f}"
                )
            if effective_gates["min_val_ssim"] > 0.0 and val_ssim < float(effective_gates["min_val_ssim"]):
                failures.append(
                    f"{source.name} [{preset}] val_ssim {val_ssim:.4f} < {float(effective_gates['min_val_ssim']):.4f}"
                )
            if effective_gates["max_gaussians"] > 0 and final_gaussians > int(effective_gates["max_gaussians"]):
                failures.append(
                    f"{source.name} [{preset}] gaussians {final_gaussians} > {int(effective_gates['max_gaussians'])}"
                )

            compare_suffix = ""
            if comparison is not None:
                deltas = comparison.get("deltas") or {}
                val_psnr_delta = deltas.get("val_psnr_delta")
                val_ssim_delta = deltas.get("val_ssim_delta")
                gaussians_delta = deltas.get("final_gaussians_delta")
                compare_suffix = (
                    f" baseline={comparison['label']} "
                    f"d_val_psnr={val_psnr_delta if val_psnr_delta is not None else 'n/a'} "
                    f"d_val_ssim={val_ssim_delta if val_ssim_delta is not None else 'n/a'} "
                    f"d_splats={gaussians_delta if gaussians_delta is not None else 'n/a'}"
                )
            print(
                f"{source.name} [{preset}]: status={result['status']} "
                f"elapsed={result['elapsed_seconds']}s splats={training_summary.get('final_gaussians', 'n/a')} "
                f"train_psnr={metrics.get('psnr', 'n/a')} val_psnr={validation_metrics.get('psnr', 'n/a')} "
                f"val_ssim={validation_metrics.get('ssim', 'n/a')}{compare_suffix}"
            )

    report = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "data_root": str(data_root),
        "steps": int(args.steps),
        "train_resolution": int(args.train_resolution),
        "sfm_max_image_size": int(args.sfm_max_image_size),
        "presets": presets,
        "strategy": args.strategy,
        "baseline_file": str(baseline_file) if baseline_file else None,
        "results": results,
        "failures": failures,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved benchmark report to {output_path}")
    if failures:
        print("\nBenchmark gates failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
