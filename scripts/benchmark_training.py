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
    return parser.parse_args()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


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
            results.append(result)

            metrics = training_summary.get("metrics", {})
            print(
                f"{source.name} [{preset}]: status={result['status']} "
                f"elapsed={result['elapsed_seconds']}s splats={training_summary.get('final_gaussians', 'n/a')} "
                f"psnr={metrics.get('psnr', 'n/a')}"
            )

    report = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "data_root": str(data_root),
        "steps": int(args.steps),
        "train_resolution": int(args.train_resolution),
        "sfm_max_image_size": int(args.sfm_max_image_size),
        "presets": presets,
        "strategy": args.strategy,
        "results": results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved benchmark report to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
