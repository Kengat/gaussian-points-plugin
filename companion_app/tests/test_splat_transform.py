from __future__ import annotations

import math

from companion_app.splat_transform import (
    normalize_snapshot,
    rotated_snapshot,
    snapshot_from_bounds,
    snapshot_from_payload,
    snapshot_to_payload,
    snapshots_equal,
    translated_snapshot,
    uniformly_scaled_snapshot,
)


def test_snapshot_payload_round_trip_preserves_transform() -> None:
    snapshot = {
        "center": (12.0, -4.0, 8.0),
        "half_extents": {"x": 3.5, "y": 2.25, "z": 1.75},
        "axes": {
            "x": (0.0, 1.0, 0.0),
            "y": (-1.0, 0.0, 0.0),
            "z": (0.0, 0.0, 1.0),
        },
    }
    payload = snapshot_to_payload(snapshot, scene_path="C:/tmp/example.gasp")
    restored = snapshot_from_payload(payload)
    assert restored is not None
    assert payload["scenePath"].endswith("example.gasp")
    assert snapshots_equal(normalize_snapshot(snapshot), restored)


def test_rotated_snapshot_turns_y_into_z_for_x_axis_rotation() -> None:
    snapshot = snapshot_from_bounds((0.0, 0.0, 0.0), (2.0, 3.0, 4.0))
    rotated = rotated_snapshot(snapshot, "x", math.pi * 0.5)
    assert rotated["axes"]["x"] == snapshot["axes"]["x"]
    assert abs(rotated["axes"]["y"][2] - 1.0) < 1.0e-6
    assert abs(rotated["axes"]["z"][1] + 1.0) < 1.0e-6


def test_translated_and_scaled_snapshot_updates_center_and_extents() -> None:
    snapshot = snapshot_from_bounds((1.0, 2.0, 3.0), (4.0, 5.0, 6.0))
    translated = translated_snapshot(snapshot, (10.0, -2.0, 1.5))
    scaled = uniformly_scaled_snapshot(translated, 2.0)
    assert translated["center"] == (11.0, 0.0, 4.5)
    assert scaled["half_extents"]["x"] > translated["half_extents"]["x"]
    assert scaled["half_extents"]["y"] > translated["half_extents"]["y"]
    assert scaled["half_extents"]["z"] > translated["half_extents"]["z"]
