from __future__ import annotations

import math
from typing import Any


EPSILON = 1.0e-6
MIN_HALF_EXTENT = 1.0e-3
AXES = ("x", "y", "z")


def vec3(x: float, y: float, z: float) -> tuple[float, float, float]:
    return (float(x), float(y), float(z))


def vec_add(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (left[0] + right[0], left[1] + right[1], left[2] + right[2])


def vec_sub(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def vec_scale(vector: tuple[float, float, float], scalar: float) -> tuple[float, float, float]:
    return (vector[0] * scalar, vector[1] * scalar, vector[2] * scalar)


def vec_dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return (left[0] * right[0]) + (left[1] * right[1]) + (left[2] * right[2])


def vec_cross(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        (left[1] * right[2]) - (left[2] * right[1]),
        (left[2] * right[0]) - (left[0] * right[2]),
        (left[0] * right[1]) - (left[1] * right[0]),
    )


def vec_length(vector: tuple[float, float, float]) -> float:
    return math.sqrt(vec_dot(vector, vector))


def vec_normalize(
    vector: tuple[float, float, float] | list[float] | None,
    fallback: tuple[float, float, float],
) -> tuple[float, float, float]:
    source = tuple(float(value) for value in (vector or fallback))
    length = vec_length(source)
    if length <= EPSILON:
        return fallback
    return (source[0] / length, source[1] / length, source[2] / length)


def vec_distance(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return vec_length(vec_sub(left, right))


def scaled_direction(
    vector: tuple[float, float, float],
    length: float,
    *,
    fallback: tuple[float, float, float] = (1.0, 0.0, 0.0),
) -> tuple[float, float, float]:
    direction = vec_normalize(vector, fallback)
    return vec_scale(direction, float(length))


def project_vector(
    vector: tuple[float, float, float],
    onto: tuple[float, float, float],
) -> tuple[float, float, float]:
    return vec_scale(onto, vec_dot(vector, onto))


def offset_point(
    point: tuple[float, float, float],
    direction: tuple[float, float, float],
    distance: float,
) -> tuple[float, float, float]:
    return vec_add(point, scaled_direction(direction, distance))


def default_axes() -> dict[str, tuple[float, float, float]]:
    return {
        "x": (1.0, 0.0, 0.0),
        "y": (0.0, 1.0, 0.0),
        "z": (0.0, 0.0, 1.0),
    }


def snapshot_from_bounds(
    center_xyz: tuple[float, float, float] | list[float],
    half_extents_xyz: tuple[float, float, float] | list[float],
) -> dict[str, Any]:
    center = tuple(float(value) for value in center_xyz[:3])
    half_values = [max(float(value), MIN_HALF_EXTENT) for value in half_extents_xyz[:3]]
    return {
        "center": center,
        "half_extents": {"x": half_values[0], "y": half_values[1], "z": half_values[2]},
        "axes": default_axes(),
    }


def clone_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "center": tuple(float(value) for value in snapshot["center"]),
        "half_extents": {
            axis: float(snapshot["half_extents"][axis])
            for axis in AXES
        },
        "axes": {
            axis: tuple(float(value) for value in snapshot["axes"][axis])
            for axis in AXES
        },
    }


def normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    center = tuple(float(value) for value in snapshot["center"])
    axes = snapshot.get("axes") or {}
    x_axis = vec_normalize(axes.get("x"), (1.0, 0.0, 0.0))
    y_seed = vec_normalize(axes.get("y"), (0.0, 1.0, 0.0))
    y_axis = vec_sub(y_seed, project_vector(y_seed, x_axis))
    y_axis = vec_normalize(y_axis, (0.0, 1.0, 0.0))
    z_axis = vec_normalize(vec_cross(x_axis, y_axis), (0.0, 0.0, 1.0))
    original_z = axes.get("z")
    if original_z is not None and vec_dot(z_axis, vec_normalize(original_z, (0.0, 0.0, 1.0))) < 0.0:
        z_axis = vec_scale(z_axis, -1.0)
    y_axis = vec_normalize(vec_cross(z_axis, x_axis), (0.0, 1.0, 0.0))
    half_extents = {
        axis: max(float((snapshot.get("half_extents") or {}).get(axis, MIN_HALF_EXTENT)), MIN_HALF_EXTENT)
        for axis in AXES
    }
    return {
        "center": center,
        "half_extents": half_extents,
        "axes": {"x": x_axis, "y": y_axis, "z": z_axis},
    }


def snapshot_to_payload(snapshot: dict[str, Any], *, scene_path: str | None = None) -> dict[str, Any]:
    normalized = normalize_snapshot(snapshot)
    payload = {
        "center": [float(value) for value in normalized["center"]],
        "half_extents": {
            axis: float(normalized["half_extents"][axis])
            for axis in AXES
        },
        "axes": {
            axis: [float(value) for value in normalized["axes"][axis]]
            for axis in AXES
        },
    }
    if scene_path:
        payload["scenePath"] = str(scene_path)
    return payload


def snapshot_from_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    center = payload.get("center")
    half_extents = payload.get("half_extents")
    axes = payload.get("axes")
    if not (isinstance(center, (list, tuple)) and len(center) >= 3):
        return None
    if not isinstance(half_extents, dict):
        return None
    if not isinstance(axes, dict):
        return None
    try:
        snapshot = {
            "center": tuple(float(value) for value in center[:3]),
            "half_extents": {
                axis: max(float(half_extents[axis]), MIN_HALF_EXTENT)
                for axis in AXES
            },
            "axes": {
                axis: tuple(float(value) for value in axes[axis][:3])
                for axis in AXES
            },
        }
    except (KeyError, TypeError, ValueError):
        return None
    return normalize_snapshot(snapshot)


def snapshots_equal(left: dict[str, Any] | None, right: dict[str, Any] | None, *, epsilon: float = EPSILON) -> bool:
    if not left or not right:
        return False
    if vec_distance(tuple(left["center"]), tuple(right["center"])) > epsilon:
        return False
    for axis in AXES:
        if abs(float(left["half_extents"][axis]) - float(right["half_extents"][axis])) > epsilon:
            return False
        left_axis = tuple(left["axes"][axis])
        right_axis = tuple(right["axes"][axis])
        if vec_distance(left_axis, right_axis) > epsilon:
            return False
    return True


def box_center(snapshot: dict[str, Any]) -> tuple[float, float, float]:
    return tuple(snapshot["center"])


def axis_vector(snapshot: dict[str, Any], axis: str) -> tuple[float, float, float]:
    fallback = default_axes()[axis]
    return vec_normalize((snapshot.get("axes") or {}).get(axis), fallback)


def half_extent(snapshot: dict[str, Any], axis: str) -> float:
    return max(float((snapshot.get("half_extents") or {}).get(axis, MIN_HALF_EXTENT)), MIN_HALF_EXTENT)


def translated_snapshot(snapshot: dict[str, Any], translation: tuple[float, float, float]) -> dict[str, Any]:
    next_snapshot = clone_snapshot(snapshot)
    next_snapshot["center"] = vec_add(tuple(snapshot["center"]), translation)
    return next_snapshot


def uniformly_scaled_snapshot(snapshot: dict[str, Any], delta: float) -> dict[str, Any]:
    values = [max(float(snapshot["half_extents"][axis]), MIN_HALF_EXTENT) for axis in AXES]
    reference = max(values)
    if reference <= EPSILON:
        factor = 1.0
    else:
        factor = max((reference + float(delta)) / reference, MIN_HALF_EXTENT / reference)
    next_snapshot = clone_snapshot(snapshot)
    next_snapshot["half_extents"] = {
        axis: max(float(snapshot["half_extents"][axis]) * factor, MIN_HALF_EXTENT)
        for axis in AXES
    }
    return next_snapshot


def rotated_snapshot(snapshot: dict[str, Any], axis: str, angle: float) -> dict[str, Any]:
    direction = axis_vector(snapshot, axis)
    next_snapshot = clone_snapshot(snapshot)
    next_snapshot["axes"] = {
        axis_name: rotate_vector_around_axis(tuple(snapshot["axes"][axis_name]), direction, angle)
        for axis_name in AXES
    }
    return normalize_snapshot(next_snapshot)


def rotate_vector_around_axis(
    vector: tuple[float, float, float],
    axis: tuple[float, float, float],
    angle: float,
) -> tuple[float, float, float]:
    unit_axis = vec_normalize(axis, (0.0, 0.0, 1.0))
    cos_angle = math.cos(angle)
    sin_angle = math.sin(angle)
    term_a = vec_scale(vector, cos_angle)
    term_b = vec_scale(vec_cross(unit_axis, vector), sin_angle)
    term_c = vec_scale(unit_axis, vec_dot(unit_axis, vector) * (1.0 - cos_angle))
    return vec_add(vec_add(term_a, term_b), term_c)


def normalize_angle(angle: float) -> float:
    value = float(angle)
    while value > math.pi:
        value -= math.pi * 2.0
    while value < -math.pi:
        value += math.pi * 2.0
    return value
