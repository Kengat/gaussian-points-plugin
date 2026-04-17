from __future__ import annotations

import math
from typing import Any

from PySide6 import QtCore, QtGui, QtWidgets

from .splat_transform import (
    axis_vector,
    box_center,
    half_extent,
    normalize_angle,
    normalize_snapshot,
    offset_point,
    rotated_snapshot,
    scaled_direction,
    snapshot_to_payload,
    translated_snapshot,
    uniformly_scaled_snapshot,
    vec_add,
    vec_cross,
    vec_dot,
    vec_length,
    vec_normalize,
    vec_scale,
    vec_sub,
)


HANDLE_NONE = 0
HANDLE_MOVE_X = 1
HANDLE_MOVE_Y = 2
HANDLE_MOVE_Z = 3
HANDLE_ROTATE_X = 4
HANDLE_ROTATE_Y = 5
HANDLE_ROTATE_Z = 6
HANDLE_MOVE_CENTER = 7
HANDLE_SCALE_UNIFORM = 8

MOVE_HANDLE_IDS = (HANDLE_MOVE_X, HANDLE_MOVE_Y, HANDLE_MOVE_Z)
ROTATE_HANDLE_IDS = (HANDLE_ROTATE_X, HANDLE_ROTATE_Y, HANDLE_ROTATE_Z)

MOVE_GAP_PIXELS = 22.0
MOVE_LENGTH_PIXELS = 64.0
ROTATE_RADIUS_PIXELS = 96.0
ROTATE_SEGMENTS = 48
MOVE_PICK_THRESHOLD = 12.0
CENTER_PICK_THRESHOLD = 16.0
ROTATE_PICK_THRESHOLD = 16.0

TAN_HALF_FOV = math.tan(math.radians(45.0) * 0.5)
AXIS_COLORS = {
    "x": QtGui.QColor("#FF5C6C"),
    "y": QtGui.QColor("#3FD56D"),
    "z": QtGui.QColor("#3FA7FF"),
}
RING_AXIS_NAMES = {
    HANDLE_ROTATE_X: "x",
    HANDLE_ROTATE_Y: "y",
    HANDLE_ROTATE_Z: "z",
}
MOVE_AXIS_NAMES = {
    HANDLE_MOVE_X: "x",
    HANDLE_MOVE_Y: "y",
    HANDLE_MOVE_Z: "z",
}
ROTATE_PLANE_AXES = {
    "x": ("y", "z"),
    "y": ("z", "x"),
    "z": ("x", "y"),
}


class TransformGizmoOverlay(QtWidgets.QWidget):
    def __init__(self, host: QtWidgets.QWidget, controller: QtCore.QObject, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._host = host
        self._controller = controller
        self._detail: dict[str, Any] | None = None
        self._active_tool = "projects"
        self._snapshot: dict[str, Any] | None = None
        self._camera: dict[str, Any] | None = None
        self._hovered_handle = HANDLE_NONE
        self._active_handle = HANDLE_NONE
        self._drag: dict[str, Any] | None = None
        self._camera_drag: dict[str, Any] | None = None
        self._project_id: str | None = None
        self._scene_path: str | None = None
        self._camera_poll = QtCore.QTimer(self)
        self._camera_poll.setInterval(45)
        self._camera_poll.timeout.connect(self._refresh_camera_state)

        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.hide()

    def set_active_tool(self, tool_name: str) -> None:
        self._active_tool = (tool_name or "").strip().lower() or "projects"
        if self._active_tool in {"move", "transform"}:
            self._refresh_camera_state()
        self._sync_visibility()
        self.update()

    def set_detail(self, detail: dict[str, Any] | None) -> None:
        preview = (detail or {}).get("preview") or {}
        self._detail = detail or {}
        self._project_id = str(preview.get("projectId") or "") or None
        self._scene_path = str(preview.get("path") or "") or None
        if not self._drag:
            self._snapshot = getattr(self._host, "current_snapshot", None)
        self._refresh_camera_state()
        self._sync_visibility()
        self.update()

    def drag_matches(self, project_id: str | None, scene_path: str | None) -> bool:
        if not self._drag:
            return False
        return self._project_id == (project_id or None) and self._scene_path == (scene_path or None)

    @property
    def drag_active(self) -> bool:
        return self._drag is not None

    def _sync_visibility(self) -> None:
        preview = (self._detail or {}).get("preview") or {}
        can_show = (
            bool(preview.get("hasScene"))
            and bool(getattr(self._host, "last_load_succeeded", False))
            and self._active_tool in {"move", "transform"}
            and self._snapshot is not None
            and self._camera is not None
        )
        self.setVisible(can_show)
        if can_show:
            self._camera_poll.start()
        else:
            self._camera_poll.stop()
            self._hovered_handle = HANDLE_NONE
            self._active_handle = HANDLE_NONE
            self._drag = None
            self._camera_drag = None

    def _refresh_camera_state(self) -> None:
        self._camera = self._host.camera_state() if self.isVisible() else None
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        if not self.isVisible() or not self._snapshot or not self._camera:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
        self._draw_box_outline(painter)
        if self._active_tool == "move":
            self._draw_move_gizmo(painter)
        elif self._active_tool == "transform":
            self._draw_transform_gizmo(painter)
        painter.end()
        super().paintEvent(event)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if not self.isVisible() or not self._snapshot or not self._camera:
            event.ignore()
            return
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            handle_id = self._pick_handle(event.position())
            if handle_id != HANDLE_NONE:
                self._start_transform_drag(handle_id, event.position())
                event.accept()
                return
        if event.button() in {
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.MouseButton.MiddleButton,
            QtCore.Qt.MouseButton.RightButton,
        }:
            self._start_camera_drag(event)
            event.accept()
            return
        event.ignore()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag:
            self._apply_transform_drag(event.position())
            event.accept()
            return
        if self._camera_drag:
            self._apply_camera_drag(event.position())
            event.accept()
            return
        handle_id = self._pick_handle(event.position()) if self.isVisible() else HANDLE_NONE
        if self._hovered_handle != handle_id:
            self._hovered_handle = handle_id
            self.update()
        event.accept() if self.isVisible() else event.ignore()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._finish_transform_drag()
            event.accept()
            return
        if self._camera_drag and event.button() == self._camera_drag.get("button"):
            self._camera_drag = None
            event.accept()
            return
        event.ignore()

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if self.isVisible() and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._host.fit_view()
            self._refresh_camera_state()
            event.accept()
            return
        event.ignore()

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if not self.isVisible():
            event.ignore()
            return
        angle_delta = event.angleDelta().y()
        if angle_delta:
            self._host.zoom_camera(angle_delta / 120.0)
            self._refresh_camera_state()
        event.accept()

    def _draw_box_outline(self, painter: QtGui.QPainter) -> None:
        points = self._world_corners()
        if not points:
            return
        pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 80))
        pen.setWidthF(1.35)
        painter.setPen(pen)
        for start, end in (
            (0, 1), (1, 2), (2, 3), (3, 0),
            (4, 5), (5, 6), (6, 7), (7, 4),
            (0, 4), (1, 5), (2, 6), (3, 7),
        ):
            start_point = self._project_point(points[start])
            end_point = self._project_point(points[end])
            if start_point is None or end_point is None:
                continue
            painter.drawLine(start_point, end_point)

    def _draw_move_gizmo(self, painter: QtGui.QPainter) -> None:
        for handle_id in MOVE_HANDLE_IDS:
            axis_name = MOVE_AXIS_NAMES[handle_id]
            segment = self._move_handle_segment(axis_name)
            if not segment:
                continue
            start_point = self._project_point(segment[0])
            end_point = self._project_point(segment[1])
            if start_point is None or end_point is None:
                continue
            highlighted = handle_id in {self._hovered_handle, self._active_handle}
            color = self._display_color(axis_name, highlighted)
            pen = QtGui.QPen(color)
            pen.setWidthF(3.4 if highlighted else 2.25)
            painter.setPen(pen)
            painter.drawLine(start_point, end_point)
            painter.setBrush(color)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawEllipse(end_point, 6.5 if highlighted else 5.0, 6.5 if highlighted else 5.0)

        center_point = self._project_point(box_center(self._snapshot))
        if center_point is not None:
            highlighted = HANDLE_MOVE_CENTER in {self._hovered_handle, self._active_handle}
            color = self._display_color("z", highlighted, alpha=220)
            painter.setBrush(QtGui.QBrush(color))
            pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 180 if highlighted else 110))
            pen.setWidthF(1.2)
            painter.setPen(pen)
            radius = 7.5 if highlighted else 6.0
            painter.drawEllipse(center_point, radius, radius)

    def _draw_transform_gizmo(self, painter: QtGui.QPainter) -> None:
        for handle_id in ROTATE_HANDLE_IDS:
            axis_name = RING_AXIS_NAMES[handle_id]
            polyline = self._rotation_ring(axis_name)
            if len(polyline) < 2:
                continue
            screen_points = [self._project_point(point) for point in polyline]
            if any(point is None for point in screen_points):
                continue
            highlighted = handle_id in {self._hovered_handle, self._active_handle}
            color = self._display_color(axis_name, highlighted, alpha=230)
            pen = QtGui.QPen(color)
            pen.setWidthF(3.0 if highlighted else 2.1)
            painter.setPen(pen)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            path = QtGui.QPainterPath(screen_points[0])
            for point in screen_points[1:]:
                path.lineTo(point)
            painter.drawPath(path)

        center_point = self._project_point(box_center(self._snapshot))
        if center_point is None:
            return
        highlighted = HANDLE_SCALE_UNIFORM in {self._hovered_handle, self._active_handle}
        size = 13.0 if highlighted else 11.0
        rect = QtCore.QRectF(center_point.x() - size * 0.5, center_point.y() - size * 0.5, size, size)
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#FF8E3C" if highlighted else "#FF5400")))
        pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 180 if highlighted else 120))
        pen.setWidthF(1.2)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 3.5, 3.5)

    def _pick_handle(self, position: QtCore.QPointF) -> int:
        if not self._snapshot or not self._camera:
            return HANDLE_NONE
        x = float(position.x())
        y = float(position.y())
        center = self._project_point(box_center(self._snapshot))
        if self._active_tool == "move":
            if center is not None and self._distance_to_point(x, y, center) <= CENTER_PICK_THRESHOLD:
                return HANDLE_MOVE_CENTER
            best_handle = HANDLE_NONE
            best_distance = MOVE_PICK_THRESHOLD
            for handle_id in MOVE_HANDLE_IDS:
                axis_name = MOVE_AXIS_NAMES[handle_id]
                segment = self._move_handle_segment(axis_name)
                if not segment:
                    continue
                start = self._project_point(segment[0])
                end = self._project_point(segment[1])
                if start is None or end is None:
                    continue
                distance = self._distance_to_segment(x, y, start, end)
                if distance < best_distance:
                    best_distance = distance
                    best_handle = handle_id
            return best_handle
        if self._active_tool == "transform":
            if center is not None and self._distance_to_point(x, y, center) <= CENTER_PICK_THRESHOLD:
                return HANDLE_SCALE_UNIFORM
            best_handle = HANDLE_NONE
            best_distance = ROTATE_PICK_THRESHOLD
            for handle_id in ROTATE_HANDLE_IDS:
                polyline = [self._project_point(point) for point in self._rotation_ring(RING_AXIS_NAMES[handle_id])]
                if len(polyline) < 2 or any(point is None for point in polyline):
                    continue
                distance = self._distance_to_polyline(x, y, [point for point in polyline if point is not None])
                if distance < best_distance:
                    best_distance = distance
                    best_handle = handle_id
            return best_handle
        return HANDLE_NONE

    def _start_transform_drag(self, handle_id: int, position: QtCore.QPointF) -> None:
        if not self._snapshot or not self._camera:
            return
        snapshot = normalize_snapshot(self._snapshot)
        center = box_center(snapshot)
        camera_direction = self._camera_forward()
        drag: dict[str, Any] | None = None
        if handle_id in MOVE_HANDLE_IDS:
            axis_name = MOVE_AXIS_NAMES[handle_id]
            axis = axis_vector(snapshot, axis_name)
            plane_normal = self._plane_normal_for_axis(axis, camera_direction)
            hit_point = self._intersect_mouse_with_plane(position, center, plane_normal) or center
            drag = {
                "handle_id": handle_id,
                "kind": "move_axis",
                "axis": axis,
                "plane_origin": center,
                "plane_normal": plane_normal,
                "start_scalar": vec_dot(vec_sub(hit_point, center), axis),
                "start_snapshot": snapshot,
                "changed": False,
            }
        elif handle_id == HANDLE_MOVE_CENTER:
            plane_normal = camera_direction
            hit_point = self._intersect_mouse_with_plane(position, center, plane_normal) or center
            drag = {
                "handle_id": handle_id,
                "kind": "move_center",
                "plane_origin": center,
                "plane_normal": plane_normal,
                "start_hit": hit_point,
                "start_snapshot": snapshot,
                "changed": False,
            }
        elif handle_id in ROTATE_HANDLE_IDS:
            axis_name = RING_AXIS_NAMES[handle_id]
            axis = axis_vector(snapshot, axis_name)
            axis_a_name, axis_b_name = ROTATE_PLANE_AXES[axis_name]
            axis_a = axis_vector(snapshot, axis_a_name)
            axis_b = axis_vector(snapshot, axis_b_name)
            hit_point = self._intersect_mouse_with_plane(position, center, axis) or offset_point(center, axis_a, 1.0)
            drag = {
                "handle_id": handle_id,
                "kind": "rotate",
                "axis_name": axis_name,
                "plane_origin": center,
                "plane_normal": axis,
                "axis_a": axis_a,
                "axis_b": axis_b,
                "start_angle": self._angle_on_plane(hit_point, center, axis_a, axis_b),
                "start_snapshot": snapshot,
                "changed": False,
            }
        elif handle_id == HANDLE_SCALE_UNIFORM:
            drag = {
                "handle_id": handle_id,
                "kind": "scale_uniform",
                "center": center,
                "start_screen": (float(position.x()), float(position.y())),
                "start_snapshot": snapshot,
                "changed": False,
            }
        if drag is None:
            return
        self._drag = drag
        self._active_handle = handle_id
        self._hovered_handle = handle_id
        self.update()

    def _apply_transform_drag(self, position: QtCore.QPointF) -> None:
        if not self._drag or not self._snapshot:
            return
        start_snapshot = self._drag["start_snapshot"]
        next_snapshot: dict[str, Any] | None = None
        if self._drag["kind"] == "move_axis":
            hit_point = self._intersect_mouse_with_plane(position, self._drag["plane_origin"], self._drag["plane_normal"])
            if hit_point is None:
                return
            scalar = vec_dot(vec_sub(hit_point, self._drag["plane_origin"]), self._drag["axis"])
            delta = scalar - float(self._drag["start_scalar"])
            translation = scaled_direction(self._drag["axis"], delta, fallback=self._drag["axis"])
            next_snapshot = translated_snapshot(start_snapshot, translation)
        elif self._drag["kind"] == "move_center":
            hit_point = self._intersect_mouse_with_plane(position, self._drag["plane_origin"], self._drag["plane_normal"])
            if hit_point is None:
                return
            next_snapshot = translated_snapshot(start_snapshot, vec_sub(hit_point, self._drag["start_hit"]))
        elif self._drag["kind"] == "rotate":
            hit_point = self._intersect_mouse_with_plane(position, self._drag["plane_origin"], self._drag["plane_normal"])
            if hit_point is None:
                return
            angle = self._angle_on_plane(hit_point, self._drag["plane_origin"], self._drag["axis_a"], self._drag["axis_b"])
            delta_angle = normalize_angle(angle - float(self._drag["start_angle"]))
            next_snapshot = rotated_snapshot(start_snapshot, self._drag["axis_name"], delta_angle)
        elif self._drag["kind"] == "scale_uniform":
            delta_pixels = self._signed_uniform_scale_pixels(position)
            delta_world = self._world_units_for_pixels(abs(delta_pixels), self._drag["center"])
            if delta_pixels < 0.0:
                delta_world *= -1.0
            next_snapshot = uniformly_scaled_snapshot(start_snapshot, delta_world)
        if next_snapshot is None:
            return
        normalized = normalize_snapshot(next_snapshot)
        if not self._host.set_transform(normalized, persist_pending=False):
            return
        self._snapshot = normalized
        self._drag["changed"] = True
        self.update()

    def _finish_transform_drag(self) -> None:
        drag = self._drag
        self._drag = None
        self._active_handle = HANDLE_NONE
        if drag and drag.get("changed") and self._snapshot and self._project_id and self._scene_path:
            self._controller.save_preview_transform(
                self._project_id,
                self._scene_path,
                snapshot_to_payload(self._snapshot),
            )
        self.update()

    def _start_camera_drag(self, event: QtGui.QMouseEvent) -> None:
        mode = "pan"
        if event.button() == QtCore.Qt.MouseButton.LeftButton and not (
            event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier
        ):
            mode = "orbit"
        self._camera_drag = {
            "mode": mode,
            "button": event.button(),
            "last_pos": QtCore.QPointF(event.position()),
        }

    def _apply_camera_drag(self, position: QtCore.QPointF) -> None:
        if not self._camera_drag:
            return
        last_pos = self._camera_drag["last_pos"]
        delta_x = float(position.x() - last_pos.x()) * self._device_scale()
        delta_y = float(position.y() - last_pos.y()) * self._device_scale()
        self._camera_drag["last_pos"] = QtCore.QPointF(position)
        if self._camera_drag["mode"] == "orbit":
            self._host.orbit_camera(delta_x, delta_y)
        else:
            self._host.pan_camera(delta_x, delta_y)
        self._refresh_camera_state()

    def _move_handle_segment(self, axis_name: str) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        if not self._snapshot:
            return None
        center = box_center(self._snapshot)
        axis_dir = axis_vector(self._snapshot, axis_name)
        face_center = offset_point(center, axis_dir, half_extent(self._snapshot, axis_name))
        gap = self._world_units_for_pixels(MOVE_GAP_PIXELS, face_center)
        length = self._world_units_for_pixels(MOVE_LENGTH_PIXELS, face_center)
        return (
            offset_point(face_center, axis_dir, gap),
            offset_point(face_center, axis_dir, gap + length),
        )

    def _rotation_ring(self, axis_name: str) -> list[tuple[float, float, float]]:
        if not self._snapshot:
            return []
        center = box_center(self._snapshot)
        axis_a_name, axis_b_name = ROTATE_PLANE_AXES[axis_name]
        axis_a = axis_vector(self._snapshot, axis_a_name)
        axis_b = axis_vector(self._snapshot, axis_b_name)
        radius = self._world_units_for_pixels(ROTATE_RADIUS_PIXELS, center)
        points: list[tuple[float, float, float]] = []
        for index in range(ROTATE_SEGMENTS + 1):
            angle = (index / ROTATE_SEGMENTS) * math.tau
            point = offset_point(center, axis_a, math.cos(angle) * radius)
            points.append(offset_point(point, axis_b, math.sin(angle) * radius))
        return points

    def _world_corners(self) -> list[tuple[float, float, float]]:
        if not self._snapshot:
            return []
        center = box_center(self._snapshot)
        axis_x = axis_vector(self._snapshot, "x")
        axis_y = axis_vector(self._snapshot, "y")
        axis_z = axis_vector(self._snapshot, "z")
        hx = half_extent(self._snapshot, "x")
        hy = half_extent(self._snapshot, "y")
        hz = half_extent(self._snapshot, "z")
        corners: list[tuple[float, float, float]] = []
        for sx, sy, sz in (
            (-hx, -hy, -hz),
            (hx, -hy, -hz),
            (hx, hy, -hz),
            (-hx, hy, -hz),
            (-hx, -hy, hz),
            (hx, -hy, hz),
            (hx, hy, hz),
            (-hx, hy, hz),
        ):
            corner = center
            corner = vec_add(corner, vec_scale(axis_x, sx))
            corner = vec_add(corner, vec_scale(axis_y, sy))
            corner = vec_add(corner, vec_scale(axis_z, sz))
            corners.append(corner)
        return corners

    def _project_point(self, world_point: tuple[float, float, float]) -> QtCore.QPointF | None:
        if not self._camera:
            return None
        view = self._camera.get("view_matrix") or []
        projection = self._camera.get("projection_matrix") or []
        width = float(self._camera.get("viewport_width") or 0)
        height = float(self._camera.get("viewport_height") or 0)
        if len(view) != 16 or len(projection) != 16 or width <= 0.0 or height <= 0.0:
            return None
        view_position = self._mul_mat4_vec4(view, (world_point[0], world_point[1], world_point[2], 1.0))
        clip_position = self._mul_mat4_vec4(projection, view_position)
        w = clip_position[3]
        if abs(w) <= 1.0e-6:
            return None
        ndc_x = clip_position[0] / w
        ndc_y = clip_position[1] / w
        if not (math.isfinite(ndc_x) and math.isfinite(ndc_y)):
            return None
        scale = self._device_scale()
        screen_x = ((ndc_x * 0.5) + 0.5) * width / scale
        screen_y = (1.0 - ((ndc_y * 0.5) + 0.5)) * height / scale
        return QtCore.QPointF(screen_x, screen_y)

    def _mouse_ray(self, position: QtCore.QPointF) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        if not self._camera:
            return None
        width = float(self._camera.get("viewport_width") or 0)
        height = float(self._camera.get("viewport_height") or 0)
        if width <= 0.0 or height <= 0.0:
            return None
        camera_position = tuple(float(value) for value in (self._camera.get("camera_position") or (0.0, 0.0, 1.0)))
        target = tuple(float(value) for value in (self._camera.get("camera_target") or (0.0, 0.0, 0.0)))
        camera_up = tuple(float(value) for value in (self._camera.get("camera_up") or (0.0, 0.0, 1.0)))
        forward = vec_normalize(vec_sub(target, camera_position), (0.0, 0.0, -1.0))
        right = vec_normalize(vec_cross(forward, camera_up), (1.0, 0.0, 0.0))
        up = vec_normalize(vec_cross(right, forward), (0.0, 0.0, 1.0))
        aspect = width / height
        pixel_x = float(position.x()) * self._device_scale()
        pixel_y = float(position.y()) * self._device_scale()
        ndc_x = (2.0 * pixel_x / width) - 1.0
        ndc_y = 1.0 - (2.0 * pixel_y / height)
        direction = vec_add(
            forward,
            vec_add(
                vec_scale(right, ndc_x * aspect * TAN_HALF_FOV),
                vec_scale(up, ndc_y * TAN_HALF_FOV),
            ),
        )
        return camera_position, vec_normalize(direction, forward)

    def _intersect_mouse_with_plane(
        self,
        position: QtCore.QPointF,
        plane_origin: tuple[float, float, float],
        plane_normal: tuple[float, float, float],
    ) -> tuple[float, float, float] | None:
        ray = self._mouse_ray(position)
        if ray is None:
            return None
        ray_origin, ray_direction = ray
        normal = vec_normalize(plane_normal, (0.0, 0.0, 1.0))
        denominator = vec_dot(ray_direction, normal)
        if abs(denominator) <= 1.0e-6:
            return None
        distance = vec_dot(vec_sub(plane_origin, ray_origin), normal) / denominator
        return None if distance < 0.0 else vec_add(ray_origin, vec_scale(ray_direction, distance))

    def _camera_forward(self) -> tuple[float, float, float]:
        if not self._camera:
            return (0.0, 0.0, -1.0)
        position = tuple(float(value) for value in (self._camera.get("camera_position") or (0.0, 0.0, 1.0)))
        target = tuple(float(value) for value in (self._camera.get("camera_target") or (0.0, 0.0, 0.0)))
        return vec_normalize(vec_sub(target, position), (0.0, 0.0, -1.0))

    def _plane_normal_for_axis(
        self,
        axis: tuple[float, float, float],
        camera_direction: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        tangent = vec_cross(camera_direction, axis)
        normal = vec_cross(axis, tangent)
        if vec_length(normal) > 1.0e-3:
            return vec_normalize(normal, (0.0, 0.0, 1.0))
        fallback = vec_cross((0.0, 0.0, 1.0), axis)
        normal = vec_cross(axis, fallback)
        if vec_length(normal) > 1.0e-3:
            return vec_normalize(normal, (0.0, 1.0, 0.0))
        return (0.0, 1.0, 0.0)

    def _angle_on_plane(
        self,
        point: tuple[float, float, float],
        origin: tuple[float, float, float],
        axis_a: tuple[float, float, float],
        axis_b: tuple[float, float, float],
    ) -> float:
        vector = vec_sub(point, origin)
        return math.atan2(vec_dot(vector, axis_b), vec_dot(vector, axis_a))

    def _world_units_for_pixels(self, logical_pixels: float, world_point: tuple[float, float, float]) -> float:
        if not self._camera:
            return logical_pixels
        height = float(self._camera.get("viewport_height") or 0)
        if height <= 0.0:
            return logical_pixels
        camera_position = tuple(float(value) for value in (self._camera.get("camera_position") or (0.0, 0.0, 1.0)))
        depth = max(vec_dot(vec_sub(world_point, camera_position), self._camera_forward()), 1.0)
        pixel_count = float(logical_pixels) * self._device_scale()
        return pixel_count * ((2.0 * TAN_HALF_FOV * depth) / height)

    def _signed_uniform_scale_pixels(self, position: QtCore.QPointF) -> float:
        if not self._drag:
            return 0.0
        start_x, start_y = self._drag["start_screen"]
        delta_x = float(position.x()) - float(start_x)
        delta_y = float(position.y()) - float(start_y)
        diagonal = math.sqrt(0.5)
        return (delta_x * diagonal) + (-delta_y * diagonal)

    def _distance_to_point(self, x: float, y: float, point: QtCore.QPointF) -> float:
        return math.hypot(x - point.x(), y - point.y())

    def _distance_to_segment(self, x: float, y: float, start: QtCore.QPointF, end: QtCore.QPointF) -> float:
        vx = end.x() - start.x()
        vy = end.y() - start.y()
        wx = x - start.x()
        wy = y - start.y()
        vv = (vx * vx) + (vy * vy)
        if vv <= 1.0e-4:
            return math.hypot(wx, wy)
        t = ((wx * vx) + (wy * vy)) / vv
        t = max(0.0, min(1.0, t))
        closest_x = start.x() + (vx * t)
        closest_y = start.y() + (vy * t)
        return math.hypot(x - closest_x, y - closest_y)

    def _distance_to_polyline(self, x: float, y: float, points: list[QtCore.QPointF]) -> float:
        if len(points) < 2:
            return float("inf")
        return min(
            self._distance_to_segment(x, y, points[index], points[index + 1])
            for index in range(len(points) - 1)
        )

    def _device_scale(self) -> float:
        return max(float(self._host.devicePixelRatioF()), 1.0)

    def _display_color(self, axis_name: str, highlighted: bool, *, alpha: int = 255) -> QtGui.QColor:
        color = QtGui.QColor(AXIS_COLORS[axis_name])
        color.setAlpha(alpha)
        if highlighted:
            color = color.lighter(135)
            color.setAlpha(min(255, alpha))
        return color

    def _mul_mat4_vec4(self, matrix: list[float], vector: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        return (
            matrix[0] * vector[0] + matrix[1] * vector[1] + matrix[2] * vector[2] + matrix[3] * vector[3],
            matrix[4] * vector[0] + matrix[5] * vector[1] + matrix[6] * vector[2] + matrix[7] * vector[3],
            matrix[8] * vector[0] + matrix[9] * vector[1] + matrix[10] * vector[2] + matrix[11] * vector[3],
            matrix[12] * vector[0] + matrix[13] * vector[1] + matrix[14] * vector[2] + matrix[15] * vector[3],
        )
