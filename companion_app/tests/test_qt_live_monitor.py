from __future__ import annotations

import os
import shutil
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from companion_app import paths, store
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

    def test_export_panel_uses_reference_copy_when_exports_exist(self) -> None:
        project = store.create_project("Export Copy")
        manifest_path = paths.project_result_dir(project["id"]) / "result_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text("{}", encoding="utf-8")
        store.update_project(project["id"], last_manifest_path=str(manifest_path))

        detail = self.controller._build_detail(project["id"])

        self.assertIsNotNone(detail)
        self.assertEqual(
            "Trained splats are compiled and ready. Choose a destination format to export the geometry.",
            detail["exportPanel"]["body"],
        )

    def test_properties_panel_exposes_grouped_sections_for_qml(self) -> None:
        project = store.create_project("Advanced Properties")

        detail = self.controller._build_detail(project["id"])

        self.assertIsNotNone(detail)
        properties_panel = detail["propertiesPanel"]
        self.assertIn("basicItems", properties_panel)
        self.assertIn("summaryCards", properties_panel)
        self.assertIn("sections", properties_panel)
        self.assertEqual(["Source Directory", "Image Count", "Resolution"], [item["label"] for item in properties_panel["basicItems"]])
        self.assertEqual("Capture", properties_panel["sections"][0]["title"])
        self.assertEqual("Camera Setup", properties_panel["sections"][0]["rows"][0]["label"])

    def test_loss_chart_payload_parses_real_log_lines(self) -> None:
        payload = self.controller._loss_chart_payload(
            "\n".join(
                [
                    "[12:00:00] iter=10 loss=0.380, lr=0.001",
                    "[12:00:05] iter=20 loss=0.125, lr=0.001",
                    "[12:00:10] iter=30 loss=0.002, lr=0.001",
                ]
            )
        )

        self.assertEqual(3, len(payload["points"]))
        self.assertEqual("0.002", payload["minValue"])
        self.assertEqual("12:00:10", payload["points"][-1]["time"])


if __name__ == "__main__":
    unittest.main()
