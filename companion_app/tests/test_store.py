from __future__ import annotations

import os
import shutil
import unittest
import uuid
from unittest import mock

from companion_app import paths, store


class StoreStaleJobTest(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_home = os.environ.get("GAUSSIAN_POINTS_COMPANION_HOME")
        self._temp_dir = str((paths.repo_root() / "companion_app" / "tests" / f"_tmp_store_{uuid.uuid4().hex}").resolve())
        os.environ["GAUSSIAN_POINTS_COMPANION_HOME"] = self._temp_dir
        paths._DATA_ROOT = None
        store.init_db()

    def tearDown(self) -> None:
        if self._previous_home is None:
            os.environ.pop("GAUSSIAN_POINTS_COMPANION_HOME", None)
        else:
            os.environ["GAUSSIAN_POINTS_COMPANION_HOME"] = self._previous_home
        paths._DATA_ROOT = None
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def test_latest_job_marks_missing_running_pid_as_failed(self) -> None:
        project = store.create_project("Stale Job")
        job = store.create_job(project["id"], store.default_job_settings())
        with mock.patch("companion_app.store._pid_exists", return_value=True):
            store.update_job(job["id"], status="running", stage="COLMAP", pid=41000)

        with mock.patch("companion_app.store._pid_exists", return_value=False):
            latest = store.latest_job(project["id"])

        self.assertIsNotNone(latest)
        self.assertEqual("failed", latest["status"])
        self.assertEqual("Failed", latest["stage"])
        self.assertIn("no longer running", latest["message"])

    def test_request_stop_marks_missing_running_pid_as_stopped(self) -> None:
        project = store.create_project("Stop Stale Job")
        job = store.create_job(project["id"], store.default_job_settings())
        with mock.patch("companion_app.store._pid_exists", return_value=True):
            store.update_job(job["id"], status="running", stage="COLMAP", pid=41000)

        with mock.patch("companion_app.store._pid_exists", return_value=False):
            store.request_job_stop(job["id"])
            updated = store.get_job(job["id"])

        self.assertIsNotNone(updated)
        self.assertEqual("stopped", updated["status"])
        self.assertEqual("Stopped", updated["stage"])
        self.assertIn("Stopped after the worker process exited", updated["message"])

    def test_save_project_training_settings_persists_known_fields(self) -> None:
        project = store.create_project("Saved Settings")
        settings = store.project_training_settings(project["id"], force_restart=False)
        settings["train_steps"] = 4200
        settings["max_gaussians"] = 900000
        settings["quality_preset"] = "high"
        settings["train_resolution"] = 960
        settings["sfm_match_mode"] = "sequential"
        settings["force_restart"] = True

        store.save_project_training_settings(project["id"], settings)
        restored = store.project_training_settings(project["id"], force_restart=False)

        self.assertEqual(4200, restored["train_steps"])
        self.assertEqual(900000, restored["max_gaussians"])
        self.assertEqual("high", restored["quality_preset"])
        self.assertEqual(960, restored["train_resolution"])
        self.assertEqual("sequential", restored["sfm_match_mode"])
        self.assertFalse(bool(restored["force_restart"]))

    def test_default_job_settings_restore_room_scale_sfm_defaults(self) -> None:
        settings = store.default_job_settings()

        self.assertEqual(1600, settings["sfm_max_image_size"])
        self.assertEqual(6, settings["sfm_num_threads"])
        self.assertEqual("auto", settings["strategy_name"])

    def test_worker_thread_limit_matches_restored_sfm_defaults(self) -> None:
        from companion_app.worker_entry import _thread_limit_from_job

        self.assertEqual(6, _thread_limit_from_job({"settings": {"sfm_num_threads": 12}}))
        self.assertEqual(2, _thread_limit_from_job({"settings": {"sfm_num_threads": 2}}))

    def test_preserve_sfm_cache_is_runtime_only(self) -> None:
        project = store.create_project("Preserve SfM")
        settings = store.project_training_settings(project["id"], force_restart=True)
        settings["preserve_sfm_cache"] = True

        store.save_project_training_settings(project["id"], settings)
        restored = store.project_training_settings(project["id"], force_restart=False)

        self.assertFalse(bool(restored["preserve_sfm_cache"]))
        self.assertFalse(bool(restored["force_restart"]))

    def test_worker_heartbeat_does_not_refresh_live_activity(self) -> None:
        from companion_app.worker_entry import _mark_monitor_activity

        project = store.create_project("Heartbeat")
        job = store.create_job(project["id"], store.default_job_settings())
        original_activity = "2026-04-10T00:00:00+00:00"
        store.update_job(
            job["id"],
            monitor_last_activity_at=original_activity,
            monitor_last_activity_kind="output",
        )

        _mark_monitor_activity(job["id"], "heartbeat")
        heartbeat_job = store.get_job(job["id"])

        self.assertIsNotNone(heartbeat_job)
        self.assertEqual(original_activity, heartbeat_job["monitor_last_activity_at"])
        self.assertEqual("output", heartbeat_job["monitor_last_activity_kind"])
        self.assertIsNotNone(heartbeat_job.get("monitor_last_heartbeat_at"))


if __name__ == "__main__":
    unittest.main()
