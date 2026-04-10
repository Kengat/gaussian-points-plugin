from __future__ import annotations

import os
import shutil
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from companion_app import paths
from companion_app.qt_state import QtStateController


class QtLiveMonitorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_home = os.environ.get("GAUSSIAN_POINTS_COMPANION_HOME")
        self._temp_dir = str((paths.repo_root() / "companion_app" / "tests" / f"_tmp_qt_live_{uuid.uuid4().hex}").resolve())
        os.environ["GAUSSIAN_POINTS_COMPANION_HOME"] = self._temp_dir
        paths._DATA_ROOT = None
        self.controller = QtStateController()

    def tearDown(self) -> None:
        if self._previous_home is None:
            os.environ.pop("GAUSSIAN_POINTS_COMPANION_HOME", None)
        else:
            os.environ["GAUSSIAN_POINTS_COMPANION_HOME"] = self._previous_home
        paths._DATA_ROOT = None
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def test_qml_state_uses_rest_before_and_after_jobs(self) -> None:
        self.assertEqual("REST", self.controller._live_monitor_payload(None, "")["label"])
        completed = self.controller._live_monitor_payload({"status": "completed"}, "")
        stopped = self.controller._live_monitor_payload({"status": "stopped"}, "")

        self.assertEqual(("idle", "REST"), (completed["state"], completed["label"]))
        self.assertEqual(("idle", "REST"), (stopped["state"], stopped["label"]))

    def test_qml_state_switches_to_quiet_and_check(self) -> None:
        now = datetime.now(timezone.utc)
        quiet = self.controller._live_monitor_payload(
            {
                "status": "running",
                "monitor_last_activity_at": (now - timedelta(seconds=130)).isoformat(),
                "monitor_last_activity_kind": "output",
            },
            "",
        )
        check = self.controller._live_monitor_payload(
            {
                "status": "running",
                "monitor_last_activity_at": (now - timedelta(minutes=21)).isoformat(),
                "monitor_last_activity_kind": "output",
            },
            "",
        )

        self.assertEqual(("stale", "QUIET", False), (quiet["state"], quiet["label"], quiet["showStopPrompt"]))
        self.assertEqual(("silent", "CHECK", True), (check["state"], check["label"], check["showStopPrompt"]))

    def test_detects_cached_colmap_reconstruction(self) -> None:
        project_id = uuid.uuid4().hex
        reconstruction_dir = paths.project_colmap_scratch_dir(project_id) / "sparse" / "0"
        reconstruction_dir.mkdir(parents=True, exist_ok=True)
        for name in ("cameras.bin", "images.bin", "points3D.bin"):
            (reconstruction_dir / name).write_bytes(b"ok")

        self.assertTrue(self.controller._has_cached_colmap_reconstruction(project_id))


if __name__ == "__main__":
    unittest.main()
