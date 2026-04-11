from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid
from pathlib import Path

from companion_app import paths, pipeline


class PipelineManifestRepairTest(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_home = os.environ.get("GAUSSIAN_POINTS_COMPANION_HOME")
        self._temp_dir = str((paths.repo_root() / "companion_app" / "tests" / f"_tmp_pipeline_{uuid.uuid4().hex}").resolve())
        os.environ["GAUSSIAN_POINTS_COMPANION_HOME"] = self._temp_dir
        paths._DATA_ROOT = None
        self.project_id = "project-manifest"
        paths.ensure_project_dirs(self.project_id)

    def tearDown(self) -> None:
        if self._previous_home is None:
            os.environ.pop("GAUSSIAN_POINTS_COMPANION_HOME", None)
        else:
            os.environ["GAUSSIAN_POINTS_COMPANION_HOME"] = self._previous_home
        paths._DATA_ROOT = None
        shutil.rmtree(self._temp_dir, ignore_errors=True)

    def test_bundled_manifest_replaces_truncated_project_manifest(self) -> None:
        input_dir = paths.project_input_dir(self.project_id)
        for name in ("r_0.png", "r_8.png", "r_16.png", "r_24.png", "r_32.png", "r_40.png", "r_48.png", "r_56.png", "r_64.png", "r_72.png", "r_80.png", "r_88.png"):
            (input_dir / f"000_{name}").write_bytes(b"")

        truncated_manifest = {
            "camera_angle_x": 0.6911112070083618,
            "frames": [
                {
                    "file_path": "input/000_r_88.png",
                    "transform_matrix": [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]],
                }
            ],
        }
        manifest_path = paths.project_root(self.project_id) / "transforms_train_subset.json"
        manifest_path.write_text(json.dumps(truncated_manifest, indent=2), encoding="utf-8")

        status = pipeline.ensure_project_camera_manifests(self.project_id)

        self.assertEqual("manifest", status["mode"])
        self.assertEqual(12, int(status["usable_views"]))

        repaired_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(12, len(repaired_payload["frames"]))
        self.assertEqual("input/000_r_0.png", repaired_payload["frames"][0]["file_path"])
        self.assertEqual("input/000_r_88.png", repaired_payload["frames"][-1]["file_path"])


if __name__ == "__main__":
    unittest.main()
