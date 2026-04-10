from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from companion_app.web_desktop_app import CompanionApi


class WebLiveMonitorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.api = CompanionApi()

    def test_idle_and_finished_jobs_show_rest(self) -> None:
        self.assertEqual("REST", self.api._live_monitor_payload(None, "")["label"])
        completed = self.api._live_monitor_payload({"status": "completed"}, "")
        stopped = self.api._live_monitor_payload({"status": "stopped"}, "")

        self.assertEqual({"state": "idle", "label": "REST"}, {key: completed[key] for key in ("state", "label")})
        self.assertEqual({"state": "idle", "label": "REST"}, {key: stopped[key] for key in ("state", "label")})

    def test_running_jobs_change_live_state_by_real_activity_age(self) -> None:
        now = datetime.now(timezone.utc)
        live = self.api._live_monitor_payload(
            {
                "status": "running",
                "monitor_last_activity_at": now.isoformat(),
                "monitor_last_activity_kind": "output",
            },
            "",
        )
        quiet = self.api._live_monitor_payload(
            {
                "status": "running",
                "monitor_last_activity_at": (now - timedelta(seconds=130)).isoformat(),
                "monitor_last_activity_kind": "output",
            },
            "",
        )
        check = self.api._live_monitor_payload(
            {
                "status": "running",
                "monitor_last_activity_at": (now - timedelta(minutes=21)).isoformat(),
                "monitor_last_activity_kind": "output",
            },
            "",
        )

        self.assertEqual(("live", "LIVE", False), (live["state"], live["label"], live["showStopPrompt"]))
        self.assertEqual(("stale", "QUIET", False), (quiet["state"], quiet["label"], quiet["showStopPrompt"]))
        self.assertEqual(("silent", "CHECK", True), (check["state"], check["label"], check["showStopPrompt"]))


if __name__ == "__main__":
    unittest.main()
