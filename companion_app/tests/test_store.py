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


if __name__ == "__main__":
    unittest.main()
