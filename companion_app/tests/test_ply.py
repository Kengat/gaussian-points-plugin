from __future__ import annotations

import unittest
from pathlib import Path
import shutil
import uuid

from companion_app.gaussian_gasp import (
    export_ply_from_gaussian_gasp,
    read_gaussian_gasp_metadata,
    safe_export_stem,
    write_gaussian_gasp_from_ply,
)
from companion_app.ply import read_preview_points, write_gaussian_ply


class PlyRoundTripTest(unittest.TestCase):
    def test_round_trip_preview(self) -> None:
        temp_dir = Path("companion_app") / "tests" / f"_tmp_{uuid.uuid4().hex}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            path = temp_dir / "scene.ply"
            write_gaussian_ply(
                [
                    {"position": (0.0, 0.0, 0.0), "color": (1.0, 0.5, 0.25), "alpha": 0.7, "scale": -3.0},
                    {"position": (1.0, -1.0, 0.5), "color": (0.2, 0.8, 0.9), "alpha": 0.85, "scale": -2.8},
                ],
                path,
            )
            points, stats = read_preview_points(path, sample_limit=16)
            self.assertEqual(2, stats["point_count"])
            self.assertEqual(2, len(points))
            self.assertAlmostEqual(1.0, points[0].r, delta=0.02)
            self.assertAlmostEqual(0.8, points[1].g, delta=0.02)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_gaussian_gasp_exports_exact_ply_payload(self) -> None:
        temp_dir = Path("companion_app") / "tests" / f"_tmp_{uuid.uuid4().hex}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            source_ply = temp_dir / "source.ply"
            gasp_path = temp_dir / "Sample Project.gasp"
            exported_ply = temp_dir / "Sample Project.ply"
            write_gaussian_ply(
                [
                    {"position": (0.0, 0.0, 0.0), "color": (1.0, 0.5, 0.25), "alpha": 0.7, "scale": -3.0},
                    {"position": (1.0, -1.0, 0.5), "color": (0.2, 0.8, 0.9), "alpha": 0.85, "scale": -2.8},
                ],
                source_ply,
            )
            write_gaussian_gasp_from_ply(source_ply, gasp_path, project={"id": "project-id", "name": "Sample Project"})
            metadata = read_gaussian_gasp_metadata(gasp_path)
            self.assertEqual(2, metadata["vertex_count"])
            self.assertEqual(252, metadata["point_stride_bytes"])
            export_ply_from_gaussian_gasp(gasp_path, exported_ply)
            self.assertEqual(source_ply.read_bytes(), exported_ply.read_bytes())
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_safe_export_stem_keeps_project_name_but_removes_windows_invalid_chars(self) -> None:
        self.assertEqual("OldHouse video test 1", safe_export_stem("OldHouse video test 1"))
        self.assertEqual("room_test_name", safe_export_stem("room<test>:name"))


if __name__ == "__main__":
    unittest.main()
