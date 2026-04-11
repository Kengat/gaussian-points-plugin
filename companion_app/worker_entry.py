from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from . import paths, store
from .pipeline import run_job


_HEARTBEAT_SECONDS = 60.0
_MONITOR_INTERVAL_SECONDS = 5.0


def _thread_limit_from_job(job: dict) -> int:
    settings = job.get("settings") if isinstance(job.get("settings"), dict) else {}
    try:
        requested = int(settings.get("sfm_num_threads", 6))
    except (TypeError, ValueError):
        requested = 6
    return max(1, min(requested, 6))


def _append_log(log_path: str | Path, message: str) -> None:
    target = Path(log_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%H:%M:%S")
    with target.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def _format_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes, seconds_part = divmod(total_seconds, 60)
    hours, minutes_part = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes_part}m {seconds_part}s"
    if minutes > 0:
        return f"{minutes}m {seconds_part}s"
    return f"{seconds_part}s"


def _mark_monitor_activity(job_id: str, kind: str) -> None:
    now = store.utc_now()
    if kind == "heartbeat":
        store.update_job(job_id, monitor_last_heartbeat_at=now)
        return

    updates = {
        "monitor_last_activity_at": now,
        "monitor_last_activity_kind": kind,
    }
    if kind == "output":
        updates["monitor_last_output_at"] = now
    elif kind == "stage":
        updates["monitor_last_stage_at"] = now
    store.update_job(job_id, **updates)


def _supervise_worker(job_id: str) -> int:
    store.init_db()
    job = store.get_job(job_id)
    if not job:
        raise RuntimeError(f"Unknown job id: {job_id}")

    log_path = Path(job["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")

    preferred_python = paths.preferred_worker_python()
    python_executable = str(preferred_python or Path(sys.executable))
    repo_root = str(paths.repo_root())
    env = os.environ.copy()
    env["GAUSSIAN_POINTS_WORKER_CHILD"] = "1"
    env["GAUSSIAN_POINTS_WORKER_SUPERVISED"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    thread_limit = _thread_limit_from_job(job)
    env["OMP_NUM_THREADS"] = str(thread_limit)
    env["MKL_NUM_THREADS"] = str(thread_limit)
    env["OPENBLAS_NUM_THREADS"] = str(thread_limit)
    env["NUMEXPR_NUM_THREADS"] = str(thread_limit)
    env["COLMAP_NUM_THREADS"] = str(thread_limit)
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = repo_root if not pythonpath else f"{repo_root}{os.pathsep}{pythonpath}"

    _append_log(log_path, f"Supervisor launching worker with runtime: {python_executable}")
    _append_log(log_path, f"Worker CPU thread limit: {thread_limit}")
    if preferred_python:
        _append_log(log_path, f"Preferred worker runtime resolved to: {preferred_python}")
    else:
        _append_log(log_path, "Preferred worker runtime was not found; falling back to the current Python executable.")

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with log_path.open("a", encoding="utf-8") as output_handle:
        child = subprocess.Popen(
            [python_executable, "-m", "companion_app.worker_entry", job_id],
            cwd=repo_root,
            env=env,
            creationflags=creationflags,
            stdout=output_handle,
            stderr=subprocess.STDOUT,
        )

    started = time.monotonic()
    last_output_at = started
    last_heartbeat_at = started
    last_stage_activity_at = started
    last_log_size = log_path.stat().st_size if log_path.exists() else 0
    last_stage_signature = None
    initial_monitor_time = store.utc_now()
    store.update_job(
        job_id,
        monitor_child_pid=child.pid,
        monitor_started_at=initial_monitor_time,
        monitor_last_activity_at=initial_monitor_time,
        monitor_last_activity_kind="start",
        monitor_last_output_at=initial_monitor_time,
        monitor_last_stage_at=initial_monitor_time,
        monitor_last_heartbeat_at=None,
    )

    while True:
        child_code = child.poll()
        current_job = store.get_job(job_id) or {}
        now = time.monotonic()
        stage_signature = (
            str(current_job.get("status") or ""),
            str(current_job.get("stage") or ""),
            round(float(current_job.get("progress") or 0.0), 4),
        )
        if stage_signature != last_stage_signature:
            last_stage_signature = stage_signature
            last_stage_activity_at = now
            _mark_monitor_activity(job_id, "stage")

        if current_job.get("stop_requested") and child_code is None:
            _append_log(log_path, "Supervisor noticed stop request. Terminating worker process.")
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _append_log(log_path, "Worker did not exit after terminate(); forcing kill.")
                child.kill()
                child.wait(timeout=10)
            child_code = child.returncode

        if log_path.exists():
            current_size = log_path.stat().st_size
            if current_size != last_log_size:
                last_log_size = current_size
                last_output_at = time.monotonic()
                _mark_monitor_activity(job_id, "output")

        if child_code is None:
            if (now - last_stage_activity_at) >= _HEARTBEAT_SECONDS and (now - last_heartbeat_at) >= _HEARTBEAT_SECONDS:
                stage = str(current_job.get("stage") or "Running")
                progress = float(current_job.get("progress") or 0.0)
                message = str(current_job.get("message") or "Still working.")
                elapsed_text = _format_elapsed(now - started)
                silence_text = _format_elapsed(now - last_output_at)
                stage_silence_text = _format_elapsed(now - last_stage_activity_at)
                heartbeat = (
                    f"Worker heartbeat: stage={stage}, progress={progress * 100.0:.0f}%, "
                    f"elapsed={elapsed_text}, no new stage update for {stage_silence_text}, "
                    f"no new output for {silence_text}."
                )
                _append_log(log_path, heartbeat)
                last_log_size = log_path.stat().st_size if log_path.exists() else last_log_size
                if current_job.get("status") == "running":
                    store.update_job(job_id, message=f"{message} Still working ({elapsed_text}).")
                _mark_monitor_activity(job_id, "heartbeat")
                last_heartbeat_at = now
            time.sleep(_MONITOR_INTERVAL_SECONDS)
            continue

        if current_job.get("status") in {"completed", "failed", "stopped"}:
            return int(child_code)

        if current_job.get("stop_requested"):
            _append_log(log_path, "Worker stopped before writing a final status; supervisor marked the job as stopped.")
            store.update_job(
                job_id,
                status="stopped",
                stage="Stopped",
                message="Stopped by user.",
                finished_at=store.utc_now(),
                error_text=None,
            )
            return int(child_code)

        exit_code = int(child_code)
        exit_hex = f"0x{exit_code & 0xFFFFFFFF:08X}"
        failure_message = f"Worker process exited unexpectedly with code {exit_code} ({exit_hex})."
        if exit_code & 0xFFFFFFFF == 0xC0000409:
            failure_message += " This matches a Windows stack-buffer-overrun crash in the previous runtime."
        _append_log(log_path, failure_message)
        store.update_job(
            job_id,
            status="failed",
            stage="Failed",
            message=failure_message,
            error_text=failure_message,
            finished_at=store.utc_now(),
        )
        return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    args = parser.parse_args(argv)
    if os.environ.get("GAUSSIAN_POINTS_WORKER_CHILD") == "1":
        store.init_db()
        return run_job(args.job_id)
    return _supervise_worker(args.job_id)


if __name__ == "__main__":
    sys.exit(main())
