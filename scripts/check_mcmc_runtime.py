from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose MCMC runtime readiness for Gaussian Points.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full diagnostic report as JSON.",
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
    if os.environ.get("GAUSSIAN_POINTS_MCMC_DIAG_RUNTIME") == "worker":
        return

    worker_python = _preferred_worker_python(repo_root)
    if worker_python is None:
        return

    try:
        current_python = Path(sys.executable).resolve()
        target_python = worker_python.resolve()
    except OSError:
        return
    if current_python == target_python:
        return

    env = os.environ.copy()
    env["GAUSSIAN_POINTS_MCMC_DIAG_RUNTIME"] = "worker"
    completed = subprocess.run([str(target_python), *sys.argv], env=env, cwd=repo_root)
    raise SystemExit(completed.returncode)


def _check_torch_extensions_dir() -> dict[str, object]:
    from companion_app import paths

    configured = os.environ.get("TORCH_EXTENSIONS_DIR")
    extension_dir = Path(configured) if configured else (paths.data_root() / "torch_extensions")
    extension_dir.mkdir(parents=True, exist_ok=True)
    probe_path = extension_dir / ".write_probe"
    probe_path.write_text("ok", encoding="utf-8")
    probe_path.unlink(missing_ok=True)
    os.environ["TORCH_EXTENSIONS_DIR"] = str(extension_dir)
    return {
        "path": str(extension_dir),
        "writable": True,
    }


def _native_relocation_status() -> dict[str, object]:
    import torch
    import gsplat.relocation as relocation_module

    if not torch.cuda.is_available():
        return {
            "available": False,
            "mode": "unavailable",
            "error": "CUDA is not available in the active worker runtime.",
        }

    device = torch.device("cuda")
    try:
        opacities = torch.tensor([0.5], dtype=torch.float32, device=device)
        scales = torch.ones((1, 3), dtype=torch.float32, device=device)
        ratios = torch.ones((1,), dtype=torch.int32, device=device)
        binoms = torch.ones((2, 2), dtype=torch.float32, device=device)
        relocation_module.compute_relocation(opacities, scales, ratios, binoms)
        return {
            "available": True,
            "mode": "native",
        }
    except Exception as error:
        return {
            "available": False,
            "mode": "fallback_required",
            "error": str(error),
        }


def _collect_report() -> dict[str, object]:
    repo_root = _repo_root()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from companion_app import paths

    torch_extensions = _check_torch_extensions_dir()

    torch_report: dict[str, object]
    gsplat_report: dict[str, object]
    mcmc_report: dict[str, object]

    try:
        import torch

        device_count = torch.cuda.device_count() if torch.cuda.is_available() else 0
        devices = []
        for index in range(device_count):
            devices.append(
                {
                    "index": index,
                    "name": torch.cuda.get_device_name(index),
                    "capability": ".".join(str(part) for part in torch.cuda.get_device_capability(index)),
                }
            )
        torch_report = {
            "version": torch.__version__,
            "cuda_version": torch.version.cuda,
            "cuda_available": bool(torch.cuda.is_available()),
            "device_count": device_count,
            "devices": devices,
        }
    except Exception as error:
        torch_report = {
            "error": str(error),
        }

    try:
        import gsplat
        from gsplat.strategy import MCMCStrategy

        gsplat_report = {
            "version": getattr(gsplat, "__version__", "unknown"),
            "mcmc_strategy_import": True,
            "mcmc_strategy_class": MCMCStrategy.__name__,
        }
    except Exception as error:
        gsplat_report = {
            "mcmc_strategy_import": False,
            "error": str(error),
        }

    try:
        mcmc_report = _native_relocation_status()
    except Exception as error:
        mcmc_report = {
            "available": False,
            "mode": "fallback_required",
            "error": str(error),
        }

    cl_path = shutil.which("cl")
    nvcc_path = shutil.which("nvcc")
    notes = []
    if mcmc_report.get("mode") == "native":
        notes.append("Native gsplat relocation is ready. MCMCStrategy can run without the PyTorch fallback.")
    else:
        notes.append("Native relocation is unavailable, so the app will use the PyTorch relocation fallback.")
        if not cl_path:
            notes.append("MSVC cl.exe is not on PATH. Install Visual Studio Build Tools if you want native JIT builds on this machine.")
        if not nvcc_path:
            notes.append("nvcc is not on PATH. Install a matching CUDA toolkit if native JIT builds are required.")

    return {
        "python": {
            "executable": sys.executable,
            "version": sys.version.splitlines()[0],
            "preferred_worker_python": str(paths.preferred_worker_python()) if paths.preferred_worker_python() else None,
        },
        "paths": {
            "repo_root": str(repo_root),
            "data_root": str(paths.data_root()),
            "torch_extensions": torch_extensions,
        },
        "toolchain": {
            "cl_path": cl_path,
            "nvcc_path": nvcc_path,
        },
        "torch": torch_report,
        "gsplat": gsplat_report,
        "mcmc_runtime": mcmc_report,
        "notes": notes,
    }


def _print_summary(report: dict[str, object]) -> None:
    python_report = report["python"]
    torch_report = report["torch"]
    gsplat_report = report["gsplat"]
    runtime_report = report["mcmc_runtime"]
    toolchain = report["toolchain"]
    paths_report = report["paths"]

    print(f"Python: {python_report['executable']}")
    print(f"Worker Python: {python_report['preferred_worker_python']}")
    print(f"Data root: {paths_report['data_root']}")
    print(f"TORCH_EXTENSIONS_DIR: {paths_report['torch_extensions']['path']}")
    print(
        "Torch: "
        f"{torch_report.get('version', 'unavailable')} "
        f"(CUDA {torch_report.get('cuda_version', 'n/a')}, available={torch_report.get('cuda_available', False)})"
    )
    print(f"gsplat: {gsplat_report.get('version', 'unavailable')}")
    print(f"MCMC native relocation: {runtime_report.get('mode', 'unknown')}")
    if runtime_report.get("error"):
        print(f"Relocation error: {runtime_report['error']}")
    print(f"cl.exe: {toolchain.get('cl_path')}")
    print(f"nvcc: {toolchain.get('nvcc_path')}")
    for note in report.get("notes", []):
        print(f"- {note}")


def main() -> int:
    args = _parse_args()
    repo_root = _repo_root()
    _ensure_worker_runtime(repo_root)
    report = _collect_report()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
