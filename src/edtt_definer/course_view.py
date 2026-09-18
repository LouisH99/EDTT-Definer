from __future__ import annotations

import math
from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .camera import CameraOrientation
from .course_scene import (
    BodyGroup,
    BodyKey,
    CourseScene,
    Point3D,
    SceneStage,
    SceneState,
    body_groups,
    body_key_for_section,
    build_course_scene,
)
from .help_text import set_help
from .live import LocationSnapshot
from .model import SectionType
from .project import ProjectData
from .radius import BodyRadiusCache
from .theme import is_dark_theme, semantic_color
from .trace import RaceTrace, TraceState


DEFAULT_OVERVIEW_YAW_DEGREES = 0.0
DEFAULT_OVERVIEW_TILT_DEGREES = 45.0


def _with_alpha(color: QColor, alpha: int) -> QColor:
    result = QColor(color)
    result.setAlpha(alpha)
    return result


def _nice_distance(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    fraction = value / (10**exponent)
    if fraction < 1.5:
        nice = 1.0
    elif fraction < 3.5:
        nice = 2.0
    elif fraction < 7.5:
        nice = 5.0
    else:
        nice = 10.0
    return nice * (10**exponent)


def _distance_to_segment(point: QPointF, start: QPointF, end: QPointF) -> float:
    dx = end.x() - start.x()
    dy = end.y() - start.y()
    length_squared = dx * dx + dy * dy
    if length_squared <= 0:
        return math.hypot(point.x() - start.x(), point.y() - start.y())
    amount = max(
        0.0,
        min(
            1.0,
            ((point.x() - start.x()) * dx + (point.y() - start.y()) * dy)
            / length_squared,
        ),
    )
    nearest_x = start.x() + amount * dx
    nearest_y = start.y() + amount * dy
    return math.hypot(point.x() - nearest_x, point.y() - nearest_y)


def _trace_color(speed: float | None, maximum_speed: float) -> QColor:
    """RaceViz-inspired cyan → peach → red speed gradient."""
    amount = 0.0 if speed is None or maximum_speed <= 0 else speed / maximum_speed
    amount = max(0.0, min(1.0, amount))
    stops = (
        (0.0, QColor("#47cbff")),
        (0.5, QColor("#ffc273")),
        (1.0, QColor("#ff5c5c")),
    )
    first_at, first = stops[0]
    for second_at, second in stops[1:]:
        if amount <= second_at:
            local = (amount - first_at) / (second_at - first_at)
            return QColor(
                round(first.red() + (second.red() - first.red()) * local),
                round(first.green() + (second.green() - first.green()) * local),
                round(first.blue() + (second.blue() - first.blue()) * local),
            )
        first_at, first = second_at, second
    return QColor(stops[-1][1])


class CourseCanvas(QWidget):
    stage_selected = Signal(str)
    hovered_stage_changed = Signal(object)
    follow_live_changed = Signal(bool)
    follow_view_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.scene = build_course_scene(ProjectData.new())
        self.selected_section_id: str | None = None
        self.hovered_section_id: str | None = None
        self._yaw_degrees = DEFAULT_OVERVIEW_YAW_DEGREES
        self._tilt_degrees = DEFAULT_OVERVIEW_TILT_DEGREES
        self._roll_degrees = 0.0
        self.camera_orientation = CameraOrientation.from_turntable(
            self._yaw_degrees,
            self._tilt_degrees,
            self._roll_degrees,
        )
        self.scale = 1.0
        self.pan = QPointF()
        self.center_height_locked = True
        self.camera_target = Point3D(0.0, 0.0, 0.0)
        self._centered_section_id: str | None = None
        self.follow_live = False
        self.follow_yaw_offset_degrees = 0.0
        self.follow_tilt_degrees = DEFAULT_OVERVIEW_TILT_DEGREES
        self._follow_initialized = False
        self._needs_fit = True
        self._press_position: QPointF | None = None
        self._last_mouse_position: QPointF | None = None
        self._pressed_button = Qt.MouseButton.NoButton
        self._dragged = False
        self._hit_centers: dict[str, QPointF] = {}
        self._hit_gate_lines: dict[str, tuple[QPointF, QPointF]] = {}
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self.fit_view)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(260, 220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Interactive 3D course view")
        set_help(self, "course_canvas")

    def set_scene(self, scene: CourseScene, *, reset_view: bool = False) -> None:
        self.scene = scene
        self._enforce_center_height_lock()
        if reset_view:
            self._reset_overview_orientation()
        available = {stage.section_id for stage in scene.stages}
        if self.hovered_section_id not in available:
            self.hovered_section_id = None
            self.hovered_stage_changed.emit(None)
        if self.follow_live and self._apply_live_follow(
            initialize=not self._follow_initialized
        ):
            self._fit_timer.stop()
        elif self._centered_section_id is not None and not reset_view:
            self.center_on_stage(self._centered_section_id)
        elif reset_view or self._needs_fit:
            self._needs_fit = True
            self._fit_timer.start(0)
        self.update()

    def _reset_overview_orientation(self) -> None:
        self.set_follow_live(False)
        self._set_camera_angles(
            DEFAULT_OVERVIEW_YAW_DEGREES,
            DEFAULT_OVERVIEW_TILT_DEGREES,
            reset_roll=True,
        )
        self._centered_section_id = None

    @property
    def yaw_degrees(self) -> float:
        return self._yaw_degrees

    @yaw_degrees.setter
    def yaw_degrees(self, value: float) -> None:
        self._set_camera_angles(value, self._tilt_degrees)

    @property
    def tilt_degrees(self) -> float:
        return self._tilt_degrees

    @tilt_degrees.setter
    def tilt_degrees(self, value: float) -> None:
        self._set_camera_angles(self._yaw_degrees, value)

    @property
    def roll_degrees(self) -> float:
        return self._roll_degrees

    def _set_camera_angles(
        self,
        yaw_degrees: float,
        tilt_degrees: float,
        *,
        reset_roll: bool = False,
    ) -> None:
        self._yaw_degrees = yaw_degrees % 360.0
        self._tilt_degrees = max(0.0, min(180.0, float(tilt_degrees)))
        if reset_roll:
            self._roll_degrees = 0.0
        self._rebuild_camera_orientation()

    def _rebuild_camera_orientation(self) -> None:
        self.camera_orientation = CameraOrientation.from_turntable(
            self._yaw_degrees,
            self._tilt_degrees,
            self._roll_degrees,
        )

    def set_selected_section(self, section_id: str | None) -> None:
        self.selected_section_id = section_id
        self.update()

    def set_follow_live(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.follow_live:
            return
        self.follow_live = enabled
        self._follow_initialized = False
        if enabled:
            self.follow_yaw_offset_degrees = 0.0
            self.follow_tilt_degrees = DEFAULT_OVERVIEW_TILT_DEGREES
            self._roll_degrees = 0.0
            self._rebuild_camera_orientation()
            self._fit_timer.stop()
            self._apply_live_follow(initialize=True)
        self.follow_live_changed.emit(enabled)
        self.update()

    def rotate_follow_view(self, degrees: float = 90.0) -> None:
        if not self.follow_live:
            self.set_follow_live(True)
            return
        self.follow_yaw_offset_degrees = (
            self.follow_yaw_offset_degrees + degrees
        ) % 360.0
        self._apply_live_follow(initialize=False)
        self.update()

    def _apply_live_follow(self, *, initialize: bool) -> bool:
        marker = self.scene.live_marker
        if self.scene.state is not SceneState.READY or marker is None:
            return False
        follow_yaw = (
            (marker.heading_degrees or 0.0) + self.follow_yaw_offset_degrees
        ) % 360.0
        self._set_camera_angles(follow_yaw, self.follow_tilt_degrees)
        if initialize:
            self.fit_view()
            self._follow_initialized = True
        self._centered_section_id = None
        self.camera_target = Point3D(
            marker.position.east_m,
            marker.position.north_m,
            0.0 if self.center_height_locked else marker.position.altitude_m,
        )
        if self.center_height_locked:
            self.pan = QPointF()
        else:
            x, y, _depth = self._camera_coordinates(marker.position)
            self.pan = QPointF(-x * self.scale, -y * self.scale)
        self._needs_fit = False
        self.update()
        return True

    def top_view(self) -> None:
        self.set_follow_live(False)
        self._set_camera_angles(0.0, 0.0, reset_roll=True)
        self.fit_view()

    def fit_view(self) -> None:
        if self.width() < 2 or self.height() < 2 or self.scene.state is not SceneState.READY:
            self._needs_fit = True
            self.update()
            return
        points = self._extent_points()
        if not points:
            self.scale = 1.0
            self.pan = QPointF()
            self.camera_target = Point3D(0.0, 0.0, 0.0)
            self._centered_section_id = None
            self._needs_fit = False
            self.update()
            return
        self._centered_section_id = None
        self.camera_target = Point3D(
            (min(point.east_m for point in points) + max(point.east_m for point in points))
            / 2.0,
            (
                min(point.north_m for point in points)
                + max(point.north_m for point in points)
            )
            / 2.0,
            0.0
            if self.center_height_locked
            else (
                min(point.altitude_m for point in points)
                + max(point.altitude_m for point in points)
            )
            / 2.0,
        )
        projected = [self._camera_coordinates(point) for point in points]
        min_x = min(point[0] for point in projected)
        max_x = max(point[0] for point in projected)
        min_y = min(point[1] for point in projected)
        max_y = max(point[1] for point in projected)
        span_x = max(max_x - min_x, 1.0)
        span_y = max(max_y - min_y, 1.0)
        vertical_span = (
            2.0 * max(abs(min_y), abs(max_y), 0.5)
            if self.center_height_locked
            else span_y
        )
        self.scale = max(
            1e-7,
            min(
                1e4,
                min(
                    (self.width() - 70) / span_x,
                    (self.height() - 70) / vertical_span,
                ),
            ),
        )
        horizontal_offset = -((min_x + max_x) / 2.0) * self.scale
        if self.center_height_locked:
            self.pan = QPointF()
            self._move_ground_target_for_screen_delta(
                QPointF(horizontal_offset, 0.0)
            )
        else:
            self.pan = QPointF(
                horizontal_offset,
                -((min_y + max_y) / 2.0) * self.scale,
            )
        self._needs_fit = False
        if self.follow_live:
            self._apply_live_follow(initialize=False)
        self.update()

    def _extent_points(self) -> list[Point3D]:
        points: list[Point3D] = []
        for stage in self.scene.stages:
            bounds = stage.altitude_bounds
            altitudes = (
                (bounds.minimum_m, bounds.maximum_m) if bounds is not None else (0.0,)
            )
            if stage.is_gate and stage.endpoint_b is not None:
                for altitude in altitudes:
                    points.append(replace(stage.endpoint_a, altitude_m=altitude))
                    points.append(replace(stage.endpoint_b, altitude_m=altitude))
            else:
                radius = stage.proximity_m or 0.0
                for altitude in altitudes:
                    points.extend(
                        (
                            Point3D(stage.center.east_m - radius, stage.center.north_m, altitude),
                            Point3D(stage.center.east_m + radius, stage.center.north_m, altitude),
                            Point3D(stage.center.east_m, stage.center.north_m - radius, altitude),
                            Point3D(stage.center.east_m, stage.center.north_m + radius, altitude),
                        )
                    )
            points.append(stage.center)
        if self.scene.live_marker is not None:
            points.append(self.scene.live_marker.position)
        for segment in self.scene.trace_segments:
            points.extend(point.position for point in segment.points)
        if not points:
            return points
        min_east = min(point.east_m for point in points)
        max_east = max(point.east_m for point in points)
        min_north = min(point.north_m for point in points)
        max_north = max(point.north_m for point in points)
        horizontal_span = max(max_east - min_east, max_north - min_north)
        if horizontal_span < 100.0:
            center_east = (min_east + max_east) / 2.0
            center_north = (min_north + max_north) / 2.0
            points.extend(
                (
                    Point3D(center_east - 50.0, center_north - 50.0, 0.0),
                    Point3D(center_east + 50.0, center_north + 50.0, 0.0),
                )
            )
        return points

    def _camera_coordinates(self, point: Point3D) -> tuple[float, float, float]:
        east = point.east_m - self.camera_target.east_m
        north_offset = point.north_m - self.camera_target.north_m
        altitude = point.altitude_m - self.camera_target.altitude_m
        return self.camera_orientation.project(east, north_offset, altitude)

    def world_to_screen(self, point: Point3D) -> QPointF:
        x, y, _depth = self._camera_coordinates(point)
        return QPointF(
            self.width() / 2.0 + self.pan.x() + x * self.scale,
            self.height() / 2.0 + self.pan.y() + y * self.scale,
        )

    def orbit_by(self, horizontal_degrees: float, vertical_degrees: float) -> None:
        """Orbit upright around the target without crossing a vertical pole."""
        self.set_follow_live(False)
        self._yaw_degrees = (self._yaw_degrees + horizontal_degrees) % 360.0
        self._tilt_degrees = max(
            0.0,
            min(180.0, self._tilt_degrees + vertical_degrees),
        )
        self._rebuild_camera_orientation()
        self._enforce_center_height_lock()
        self._needs_fit = False
        self.update()

    def orbit_follow_by(
        self, horizontal_degrees: float, vertical_degrees: float
    ) -> None:
        """Adjust the heading-relative follow view without releasing its target."""
        if not self.follow_live:
            self.orbit_by(horizontal_degrees, vertical_degrees)
            return
        self.follow_yaw_offset_degrees = (
            self.follow_yaw_offset_degrees + horizontal_degrees
        ) % 360.0
        self.follow_tilt_degrees = max(
            0.0,
            min(180.0, self.follow_tilt_degrees + vertical_degrees),
        )
        if not self._apply_live_follow(initialize=False):
            self._set_camera_angles(
                self._yaw_degrees + horizontal_degrees,
                self.follow_tilt_degrees,
            )
            self._needs_fit = False
            self.update()
        self.follow_view_changed.emit()

    def set_roll(self, degrees: float) -> None:
        """Set an intentional camera roll without changing the viewing direction."""
        self.set_follow_live(False)
        self._roll_degrees = degrees % 360.0
        self._rebuild_camera_orientation()
        self._enforce_center_height_lock()
        self._needs_fit = False
        self.update()
        self.follow_view_changed.emit()

    def roll_by(self, degrees: float) -> None:
        """Roll intentionally without changing the orbital viewing direction."""
        self.set_roll(self._roll_degrees + degrees)

    def roll_follow_by(self, degrees: float) -> None:
        """Roll the follow view while keeping live tracking active."""
        if not self.follow_live:
            self.roll_by(degrees)
            return
        self._roll_degrees = (self._roll_degrees + degrees) % 360.0
        self._rebuild_camera_orientation()
        self._enforce_center_height_lock()
        self._needs_fit = False
        self.update()

    def _enforce_center_height_lock(self) -> None:
        if not self.center_height_locked:
            return
        if self.camera_target.altitude_m != 0.0:
            self.camera_target = replace(self.camera_target, altitude_m=0.0)
        self.pan = QPointF()

    def _move_ground_target_for_screen_delta(self, delta: QPointF) -> None:
        if self.scale <= 0:
            return
        right = self.camera_orientation.right
        up = self.camera_orientation.up
        desired_x = delta.x() / self.scale
        desired_y = delta.y() / self.scale
        m00 = -right.east
        m01 = -right.north
        m10 = up.east
        m11 = up.north
        # Use the transpose of the ground-to-screen projection rather than its
        # inverse. An inverse must move a very large distance across the surface
        # to reproduce a vertical mouse drag when the ground is almost edge-on.
        # The transpose naturally attenuates that component: full sensitivity
        # from above, progressively less toward a side view, and zero in a
        # direction where the ground has no visible screen-space extent.
        east_delta = m00 * desired_x + m10 * desired_y
        north_delta = m01 * desired_x + m11 * desired_y
        self.camera_target = Point3D(
            self.camera_target.east_m + east_delta,
            self.camera_target.north_m + north_delta,
            0.0,
        )

    def pan_by_screen(self, delta: QPointF) -> None:
        """Pan in screen space while keeping a locked pivot on the ground plane."""
        self.set_follow_live(False)
        self._centered_section_id = None
        if not self.center_height_locked:
            self.pan += delta
        elif self.scale > 0:
            # Keep screen-space pan out of the altitude axis. Instead, move the
            # world-space camera target across the local ground plane and leave
            # its altitude and projected screen position fixed at zero/centre.
            self._move_ground_target_for_screen_delta(delta)
            self.pan = QPointF()
        self._needs_fit = False
        self.update()

    def set_center_height_locked(self, locked: bool) -> None:
        locked = bool(locked)
        unchanged = self.center_height_locked == locked
        self.center_height_locked = locked
        if locked:
            self._enforce_center_height_lock()
        if unchanged:
            self.update()
            return
        if self.follow_live and self._apply_live_follow(initialize=False):
            return
        if self._centered_section_id is not None:
            self.center_on_stage(self._centered_section_id)
            return
        self.fit_view()

    def center_on_stage(self, section_id: str) -> bool:
        stage = next(
            (item for item in self.scene.stages if item.section_id == section_id),
            None,
        )
        if stage is None:
            self._centered_section_id = None
            return False
        self.set_follow_live(False)
        self._centered_section_id = section_id
        self.camera_target = Point3D(
            stage.center.east_m,
            stage.center.north_m,
            0.0 if self.center_height_locked else stage.center.altitude_m,
        )
        self.pan = QPointF()
        self._needs_fit = False
        self.update()
        return True

    def _colors(self) -> dict[str, QColor]:
        # The canvas deliberately keeps RaceViz's high-contrast dark presentation
        # in both application themes. Surrounding Qt controls still follow the
        # selected application theme.
        dark = is_dark_theme(self)
        return {
            "background": QColor("#333333"),
            "foreground": QColor("#f2f2f2"),
            "muted": QColor("#b8b8b8"),
            "grid": QColor("#555c62"),
            "route": QColor("#899198"),
            "start": QColor("#00ff00" if dark else "#00d600"),
            "waypoint": QColor("#ffff00" if dark else "#e5e500"),
            "end": QColor("#0080ff" if dark else "#168cff"),
            "selected": QColor("#80bfff" if dark else "#9acbff"),
            "invalid": QColor("#ff4d4d" if dark else "#ff6666"),
            "validation": QColor("#ffb347" if dark else "#ffc266"),
            "live": QColor("#e28cff" if dark else "#eea4ff"),
        }

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        if self._needs_fit and self.scene.state is SceneState.READY:
            self.fit_view()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        colors = self._colors()
        painter.fillRect(self.rect(), colors["background"])
        self._hit_centers.clear()
        self._hit_gate_lines.clear()
        if self.scene.state is not SceneState.READY:
            painter.setPen(colors["muted"])
            painter.drawText(
                self.rect().adjusted(24, 24, -24, -24),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self.scene.message,
            )
            painter.end()
            return

        self._draw_grid(painter, colors)
        self._draw_trace(painter)
        route_pen = QPen(colors["route"], 2.0, Qt.PenStyle.DashLine)
        route_pen.setCosmetic(True)
        painter.setPen(route_pen)
        for segment in self.scene.route_segments:
            painter.drawLine(self.world_to_screen(segment.start), self.world_to_screen(segment.end))

        ordered = sorted(
            self.scene.stages,
            key=lambda stage: self._camera_coordinates(stage.center)[2],
            reverse=True,
        )
        for stage in ordered:
            self._draw_stage(painter, stage, colors)
        if self.scene.live_marker is not None:
            self._draw_live_marker(painter, colors)
        self._draw_scale(painter, colors)
        self._draw_trace_legend(painter, colors)
        painter.end()

    def _draw_trace(self, painter: QPainter) -> None:
        maximum_speed = max(
            (
                point.speed_mps or 0.0
                for segment in self.scene.trace_segments
                for point in segment.points
            ),
            default=0.0,
        )
        for segment in self.scene.trace_segments:
            if len(segment.points) == 1:
                point = segment.points[0]
                painter.setPen(QPen(_trace_color(point.speed_mps, maximum_speed), 3.0))
                painter.drawPoint(self.world_to_screen(point.position))
                continue
            for first, second in zip(segment.points, segment.points[1:]):
                pen = QPen(_trace_color(second.speed_mps, maximum_speed), 3.2)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawLine(
                    self.world_to_screen(first.position),
                    self.world_to_screen(second.position),
                )

    def _draw_grid(self, painter: QPainter, colors: dict[str, QColor]) -> None:
        points = self._extent_points()
        if not points:
            return
        min_x = min(point.east_m for point in points)
        max_x = max(point.east_m for point in points)
        min_y = min(point.north_m for point in points)
        max_y = max(point.north_m for point in points)
        span = max(max_x - min_x, max_y - min_y, 100.0)
        padding = span * 0.2
        min_x -= padding
        max_x += padding
        min_y -= padding
        max_y += padding
        interval = _nice_distance(span / 8.0)
        min_x = math.floor(min_x / interval) * interval
        max_x = math.ceil(max_x / interval) * interval
        min_y = math.floor(min_y / interval) * interval
        max_y = math.ceil(max_y / interval) * interval
        grid_pen = QPen(_with_alpha(colors["grid"], 150), 1.0)
        grid_pen.setCosmetic(True)
        painter.setPen(grid_pen)
        x = min_x
        count = 0
        while x <= max_x + interval / 2 and count < 40:
            painter.drawLine(
                self.world_to_screen(Point3D(x, min_y, 0.0)),
                self.world_to_screen(Point3D(x, max_y, 0.0)),
            )
            x += interval
            count += 1
        y = min_y
        count = 0
        while y <= max_y + interval / 2 and count < 40:
            painter.drawLine(
                self.world_to_screen(Point3D(min_x, y, 0.0)),
                self.world_to_screen(Point3D(max_x, y, 0.0)),
            )
            y += interval
            count += 1

        painter.setPen(QPen(colors["foreground"], 1.4))
        north = self.world_to_screen(Point3D(min_x, max_y, 0.0))
        east = self.world_to_screen(Point3D(max_x, min_y, 0.0))
        painter.drawText(north + QPointF(5, 14), "N")
        painter.drawText(east + QPointF(-16, -5), "E")

    def _stage_color(self, stage: SceneStage, colors: dict[str, QColor]) -> QColor:
        if stage.section_id in {self.selected_section_id, self.hovered_section_id}:
            return colors["selected"]
        if stage.has_error:
            return colors["invalid"]
        if stage.section_type is SectionType.START:
            return colors["start"]
        if stage.section_type is SectionType.END:
            return colors["end"]
        return colors["waypoint"]

    def _draw_stage(
        self, painter: QPainter, stage: SceneStage, colors: dict[str, QColor]
    ) -> None:
        color = self._stage_color(stage, colors)
        pen = QPen(color, 2.2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        bounds = stage.altitude_bounds
        altitudes = (bounds.minimum_m, bounds.maximum_m) if bounds is not None else (0.0,)

        if stage.is_gate and stage.endpoint_b is not None:
            bottom_a = replace(stage.endpoint_a, altitude_m=altitudes[0])
            bottom_b = replace(stage.endpoint_b, altitude_m=altitudes[0])
            screen_bottom_a = self.world_to_screen(bottom_a)
            screen_bottom_b = self.world_to_screen(bottom_b)
            if bounds is not None:
                top_a = replace(stage.endpoint_a, altitude_m=altitudes[-1])
                top_b = replace(stage.endpoint_b, altitude_m=altitudes[-1])
                polygon = QPolygonF(
                    [
                        screen_bottom_a,
                        screen_bottom_b,
                        self.world_to_screen(top_b),
                        self.world_to_screen(top_a),
                    ]
                )
                painter.setBrush(_with_alpha(color, 48))
                painter.drawPolygon(polygon)
                painter.setBrush(Qt.BrushStyle.NoBrush)
            else:
                painter.drawLine(screen_bottom_a, screen_bottom_b)
            self._hit_gate_lines[stage.section_id] = (screen_bottom_a, screen_bottom_b)
        else:
            radius = stage.proximity_m or 0.0
            if radius > 0:
                rings: list[QPolygonF] = []
                for altitude in altitudes:
                    rings.append(
                        QPolygonF(
                            [
                                self.world_to_screen(
                                    Point3D(
                                        stage.center.east_m
                                        + math.sin(index * math.tau / 32) * radius,
                                        stage.center.north_m
                                        + math.cos(index * math.tau / 32) * radius,
                                        altitude,
                                    )
                                )
                                for index in range(33)
                            ]
                        )
                    )
                if bounds is not None and len(rings) == 2:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(_with_alpha(color, 28))
                    for index in range(32):
                        painter.drawPolygon(
                            QPolygonF(
                                [
                                    rings[0][index],
                                    rings[0][index + 1],
                                    rings[1][index + 1],
                                    rings[1][index],
                                ]
                            )
                        )
                    painter.setBrush(_with_alpha(color, 20))
                    painter.drawPolygon(rings[1])
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(pen)
                for ring in rings:
                    painter.drawPolyline(ring)
                if bounds is not None:
                    for angle in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2):
                        x = stage.center.east_m + math.sin(angle) * radius
                        y = stage.center.north_m + math.cos(angle) * radius
                        painter.drawLine(
                            self.world_to_screen(Point3D(x, y, bounds.minimum_m)),
                            self.world_to_screen(Point3D(x, y, bounds.maximum_m)),
                        )

        center = self.world_to_screen(stage.center)
        self._hit_centers[stage.section_id] = center
        painter.setBrush(color)
        marker_size = 6.0 if stage.section_type is SectionType.WAYPOINT else 8.0
        if stage.section_type is SectionType.START:
            marker = QPolygonF(
                [
                    center + QPointF(0, -marker_size),
                    center + QPointF(marker_size, marker_size),
                    center + QPointF(-marker_size, marker_size),
                ]
            )
            painter.drawPolygon(marker)
        elif stage.section_type is SectionType.END:
            marker = QPolygonF(
                [
                    center + QPointF(0, -marker_size),
                    center + QPointF(marker_size, 0),
                    center + QPointF(0, marker_size),
                    center + QPointF(-marker_size, 0),
                ]
            )
            painter.drawPolygon(marker)
        else:
            painter.drawEllipse(center, marker_size, marker_size)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if stage.validation_notes:
            badge_color = colors["invalid"] if stage.has_error else colors["validation"]
            badge_center = center + QPointF(-marker_size - 5, -marker_size - 5)
            painter.setPen(QPen(badge_color, 1.8))
            if stage.has_error:
                painter.drawLine(badge_center + QPointF(-3, -3), badge_center + QPointF(3, 3))
                painter.drawLine(badge_center + QPointF(-3, 3), badge_center + QPointF(3, -3))
            else:
                painter.drawEllipse(badge_center, 4, 4)
                painter.drawPoint(badge_center)

        should_label = (
            stage.section_type in {SectionType.START, SectionType.END}
            or stage.section_id in {self.selected_section_id, self.hovered_section_id}
        )
        if should_label:
            self._draw_label(painter, center + QPointF(9, -9), stage.label, colors)

    def _draw_label(
        self,
        painter: QPainter,
        position: QPointF,
        text: str,
        colors: dict[str, QColor],
    ) -> None:
        metrics = painter.fontMetrics()
        bounds = QRectF(metrics.boundingRect(text)).adjusted(-4, -2, 4, 2)
        bounds.moveBottomLeft(position)
        painter.fillRect(bounds, _with_alpha(colors["background"], 220))
        painter.setPen(colors["foreground"])
        painter.drawText(bounds.adjusted(4, 2, -4, -2), Qt.AlignmentFlag.AlignLeft, text)

    def _draw_live_marker(self, painter: QPainter, colors: dict[str, QColor]) -> None:
        marker = self.scene.live_marker
        if marker is None:
            return
        color = colors["live"]
        center = self.world_to_screen(marker.position)
        painter.setPen(QPen(color, 2.2))
        painter.setBrush(_with_alpha(color, 80))
        painter.drawEllipse(center, 7.0, 7.0)
        if marker.heading_degrees is not None:
            length_m = 26.0 / max(self.scale, 1e-9)
            heading = math.radians(marker.heading_degrees)
            end = Point3D(
                marker.position.east_m + math.sin(heading) * length_m,
                marker.position.north_m + math.cos(heading) * length_m,
                marker.position.altitude_m,
            )
            painter.drawLine(center, self.world_to_screen(end))
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_scale(self, painter: QPainter, colors: dict[str, QColor]) -> None:
        if self.scale <= 0:
            return
        distance_m = _nice_distance(110.0 / self.scale)
        pixels = distance_m * self.scale
        start = QPointF(18.0, self.height() - 24.0)
        end = QPointF(start.x() + pixels, start.y())
        painter.setPen(QPen(colors["foreground"], 2.0))
        painter.drawLine(start, end)
        painter.drawLine(start + QPointF(0, -4), start + QPointF(0, 4))
        painter.drawLine(end + QPointF(0, -4), end + QPointF(0, 4))
        label = (
            f"{distance_m / 1000:g} km" if distance_m >= 1000 else f"{distance_m:g} m"
        )
        painter.drawText(QPointF(start.x(), start.y() - 7), label)

    def _draw_trace_legend(
        self, painter: QPainter, colors: dict[str, QColor]
    ) -> None:
        maximum_speed = max(
            (
                point.speed_mps or 0.0
                for segment in self.scene.trace_segments
                for point in segment.points
            ),
            default=0.0,
        )
        if not self.scene.trace_segments:
            return
        width = 120
        right = self.width() - 18
        left = right - width
        y = self.height() - 24
        for offset in range(width):
            speed = maximum_speed * offset / max(1, width - 1)
            painter.setPen(QPen(_trace_color(speed, maximum_speed), 3.0))
            painter.drawLine(QPointF(left + offset, y), QPointF(left + offset, y + 5))
        painter.setPen(colors["foreground"])
        painter.drawText(QPointF(left, y - 7), "Speed")
        painter.drawText(QPointF(left, y + 19), "0")
        maximum_label = f"{maximum_speed:g} m/s"
        label_width = painter.fontMetrics().horizontalAdvance(maximum_label)
        painter.drawText(QPointF(right - label_width, y + 19), maximum_label)

    def _stage_at(self, position: QPointF) -> str | None:
        nearest: tuple[float, str] | None = None
        for section_id, center in self._hit_centers.items():
            distance = math.hypot(position.x() - center.x(), position.y() - center.y())
            if distance <= 16 and (nearest is None or distance < nearest[0]):
                nearest = (distance, section_id)
        for section_id, (start, end) in self._hit_gate_lines.items():
            distance = _distance_to_segment(position, start, end)
            if distance <= 9 and (nearest is None or distance < nearest[0]):
                nearest = (distance, section_id)
        return nearest[1] if nearest else None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() in {
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.RightButton,
            Qt.MouseButton.MiddleButton,
        }:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._press_position = event.position()
            self._last_mouse_position = event.position()
            self._pressed_button = event.button()
            self._dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        position = event.position()
        if self._last_mouse_position is not None and self._pressed_button:
            delta = position - self._last_mouse_position
            if self._press_position is not None and (
                position - self._press_position
            ).manhattanLength() > 3:
                if (
                    not self._dragged
                    and self._pressed_button
                    in {Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton}
                ):
                    self.set_follow_live(False)
                self._dragged = True
            if self._pressed_button == Qt.MouseButton.LeftButton:
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    if self.follow_live:
                        self.roll_follow_by(delta.x() * 0.45)
                    else:
                        self.roll_by(delta.x() * 0.45)
                else:
                    if self.follow_live:
                        self.orbit_follow_by(
                            delta.x() * 0.45,
                            delta.y() * 0.35,
                        )
                    else:
                        self.orbit_by(delta.x() * 0.45, delta.y() * 0.35)
            elif self.follow_live:
                self._last_mouse_position = position
                event.accept()
                return
            else:
                self.pan_by_screen(delta)
            self._last_mouse_position = position
            self.update()
            event.accept()
            return
        hovered = self._stage_at(position)
        if hovered != self.hovered_section_id:
            self.hovered_section_id = hovered
            self.hovered_stage_changed.emit(hovered)
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == self._pressed_button:
            if event.button() == Qt.MouseButton.LeftButton and not self._dragged:
                section_id = self._stage_at(event.position())
                if section_id:
                    self.set_selected_section(section_id)
                    self.stage_selected.emit(section_id)
            self._press_position = None
            self._last_mouse_position = None
            self._pressed_button = Qt.MouseButton.NoButton
            self._dragged = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            section_id = self._stage_at(event.position())
            if section_id and self.center_on_stage(section_id):
                self.set_selected_section(section_id)
                self.stage_selected.emit(section_id)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if (
            self.hovered_section_id is not None
            and self._pressed_button == Qt.MouseButton.NoButton
        ):
            self.hovered_section_id = None
            self.hovered_stage_changed.emit(None)
            self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        factor = math.pow(1.0015, event.angleDelta().y())
        old_scale = self.scale
        self.scale = max(1e-7, min(1e4, self.scale * factor))
        if old_scale > 0:
            center = QPointF(self.width() / 2.0, self.height() / 2.0)
            cursor_offset = event.position() - center - self.pan
            self.pan -= cursor_offset * (self.scale / old_scale - 1.0)
            if self.center_height_locked:
                self._enforce_center_height_lock()
        if self.follow_live:
            self._apply_live_follow(initialize=False)
        self._needs_fit = False
        self.update()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in {Qt.Key.Key_Plus, Qt.Key.Key_Equal}:
            self.scale = min(1e4, self.scale * 1.2)
        elif event.key() == Qt.Key.Key_Minus:
            self.scale = max(1e-7, self.scale / 1.2)
        elif event.key() == Qt.Key.Key_Left:
            self.pan_by_screen(QPointF(20, 0))
        elif event.key() == Qt.Key.Key_Right:
            self.pan_by_screen(QPointF(-20, 0))
        elif event.key() == Qt.Key.Key_Up:
            self.pan_by_screen(QPointF(0, 20))
        elif event.key() == Qt.Key.Key_Down:
            self.pan_by_screen(QPointF(0, -20))
        elif event.key() == Qt.Key.Key_R:
            if self.follow_live:
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.roll_follow_by(180.0)
                else:
                    self._roll_degrees = 0.0
                    self._rebuild_camera_orientation()
                    self.update()
                    self.follow_view_changed.emit()
            elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.roll_by(180.0)
            else:
                self.set_roll(0.0)
            event.accept()
            return
        elif event.key() == Qt.Key.Key_T:
            self.top_view()
            event.accept()
            return
        elif event.key() == Qt.Key.Key_F:
            self.set_follow_live(False)
            self.fit_view()
            event.accept()
            return
        else:
            super().keyPressEvent(event)
            return
        self._needs_fit = False
        if self.follow_live:
            self._apply_live_follow(initialize=False)
        self.update()
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._needs_fit:
            self._fit_timer.start(0)


class CourseViewPanel(QWidget):
    stage_selected = Signal(str)
    pop_out_requested = Signal()
    trace_record_requested = Signal()
    trace_pause_requested = Signal()
    trace_stop_requested = Signal()
    trace_clear_requested = Signal()
    trace_open_requested = Signal()
    trace_save_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project = ProjectData.new()
        self.live_snapshot: LocationSnapshot | None = None
        self.radius_cache: BodyRadiusCache | None = None
        self.selected_section_id: str | None = None
        self.trace = RaceTrace()
        self._groups: dict[str, BodyGroup] = {}
        self._current_body_key: BodyKey | None = None
        self._updating_bodies = False

        self.body_selector = QComboBox()
        self.body_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.body_selector.setMinimumContentsLength(18)
        self.top_button = QPushButton("Top")
        self.fit_button = QPushButton("Fit")
        self.follow_button = QPushButton("Follow ship")
        self.follow_button.setCheckable(True)
        self.center_height_lock = QCheckBox("Lock center at 0 m")
        self.center_height_lock.setChecked(True)
        self.pop_out_button = QPushButton("Pop out")
        self.trace_record_button = QPushButton("Record")
        self.trace_pause_button = QPushButton("Pause")
        self.trace_stop_button = QPushButton("Stop")
        self.trace_clear_button = QPushButton("Clear")
        self.trace_open_button = QPushButton("Open trace…")
        self.trace_save_button = QPushButton("Save trace…")
        self.trace_status_label = QLabel("No trace loaded.")
        self.trace_status_label.setWordWrap(True)
        self.status_label = QLabel("Course view is waiting for project data.")
        self.status_label.setWordWrap(True)
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)
        self.warning_label.setVisible(False)
        self.detail_label = QLabel("Hover over a checkpoint for details.")
        self.detail_label.setWordWrap(True)
        self.canvas = CourseCanvas()

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(QLabel("Body"))
        controls.addWidget(self.body_selector, 1)
        controls.addWidget(self.pop_out_button)
        camera_controls = QHBoxLayout()
        camera_controls.setContentsMargins(0, 0, 0, 0)
        camera_controls.addWidget(QLabel("Camera"))
        camera_controls.addWidget(self.follow_button)
        camera_controls.addWidget(self.center_height_lock)
        camera_controls.addStretch(1)
        camera_controls.addWidget(self.top_button)
        camera_controls.addWidget(self.fit_button)
        trace_controls = QHBoxLayout()
        trace_controls.setContentsMargins(0, 0, 0, 0)
        trace_controls.addWidget(QLabel("Race line"))
        trace_controls.addWidget(self.trace_record_button)
        trace_controls.addWidget(self.trace_pause_button)
        trace_controls.addWidget(self.trace_stop_button)
        trace_controls.addWidget(self.trace_clear_button)
        trace_controls.addStretch(1)
        trace_controls.addWidget(self.trace_open_button)
        trace_controls.addWidget(self.trace_save_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(controls)
        layout.addLayout(camera_controls)
        layout.addLayout(trace_controls)
        layout.addWidget(self.trace_status_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.warning_label)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.detail_label)

        self.body_selector.currentIndexChanged.connect(self._body_changed)
        self.top_button.clicked.connect(self.canvas.top_view)
        self.fit_button.clicked.connect(self._fit_overview)
        self.follow_button.clicked.connect(self._follow_button_clicked)
        self.center_height_lock.toggled.connect(self._center_height_lock_changed)
        self.pop_out_button.clicked.connect(self.pop_out_requested)
        self.trace_record_button.clicked.connect(self.trace_record_requested)
        self.trace_pause_button.clicked.connect(self.trace_pause_requested)
        self.trace_stop_button.clicked.connect(self.trace_stop_requested)
        self.trace_clear_button.clicked.connect(self.trace_clear_requested)
        self.trace_open_button.clicked.connect(self.trace_open_requested)
        self.trace_save_button.clicked.connect(self.trace_save_requested)
        self.canvas.stage_selected.connect(self.stage_selected)
        self.canvas.hovered_stage_changed.connect(self._hovered_stage_changed)
        self.canvas.follow_live_changed.connect(self._follow_live_changed)
        self.canvas.follow_view_changed.connect(
            lambda: self._update_status(self.canvas.scene)
        )
        for widget, help_key in (
            (self.body_selector, "course_body"),
            (self.follow_button, "course_follow"),
            (self.center_height_lock, "course_center_height_lock"),
            (self.top_button, "course_top"),
            (self.fit_button, "course_fit"),
            (self.pop_out_button, "course_popout"),
            (self.status_label, "course_status"),
            (self.canvas, "course_canvas"),
            (self.trace_record_button, "trace_record"),
            (self.trace_pause_button, "trace_pause"),
            (self.trace_stop_button, "trace_stop"),
            (self.trace_clear_button, "trace_clear"),
            (self.trace_open_button, "trace_open"),
            (self.trace_save_button, "trace_save"),
            (self.trace_status_label, "trace_status"),
        ):
            set_help(widget, help_key)
        self.setAccessibleName("Course view")
        self.set_trace_status(TraceState.IDLE, "No trace loaded.", False)

    def _fit_overview(self) -> None:
        self.canvas.set_follow_live(False)
        self.canvas.fit_view()

    def _follow_button_clicked(self, _checked: bool) -> None:
        if not self.canvas.follow_live:
            self.canvas.set_follow_live(True)
            return
        self.canvas.rotate_follow_view(90.0)
        self.follow_button.setChecked(True)
        self._update_status(self.canvas.scene)

    def _follow_live_changed(self, enabled: bool) -> None:
        if self.follow_button.isChecked() != enabled:
            self.follow_button.setChecked(enabled)
        self._update_status(self.canvas.scene)

    def _center_height_lock_changed(self, locked: bool) -> None:
        self.canvas.set_center_height_locked(locked)
        self._update_status(self.canvas.scene)

    @staticmethod
    def _key_id(key: BodyKey) -> str:
        return f"{key.system}\x1f{key.body}"

    def set_project(
        self,
        project: ProjectData,
        *,
        selected_section_id: str | None = None,
        live_snapshot: LocationSnapshot | None = None,
        radius_cache: BodyRadiusCache | None = None,
    ) -> None:
        self.project = project
        self.live_snapshot = live_snapshot
        self.radius_cache = radius_cache
        self.selected_section_id = selected_section_id
        self._populate_bodies(prefer_selected=True, reset_view=True)

    @property
    def current_body_group(self) -> BodyGroup | None:
        if self._current_body_key is None:
            return None
        return next(
            (group for group in self._groups.values() if group.key == self._current_body_key),
            None,
        )

    @property
    def current_radius_km(self):
        return self.canvas.scene.radius_km

    def set_trace(self, trace: RaceTrace) -> None:
        self.trace = trace
        if trace.system or trace.body:
            trace_key = BodyKey.from_names(trace.system, trace.body)
            index = self.body_selector.findData(self._key_id(trace_key))
            if index >= 0 and index != self.body_selector.currentIndex():
                self.body_selector.setCurrentIndex(index)
                return
        self._rebuild_scene(reset_view=False)

    def set_trace_status(self, state: TraceState, text: str, dirty: bool) -> None:
        point_count = self.trace.point_count
        suffix = " · unsaved" if dirty and point_count else ""
        self.trace_status_label.setText(f"{text}{suffix}")
        self.trace_record_button.setEnabled(state is TraceState.IDLE)
        self.trace_pause_button.setEnabled(state is not TraceState.IDLE)
        self.trace_pause_button.setText("Resume" if state is TraceState.PAUSED else "Pause")
        self.trace_stop_button.setEnabled(state is not TraceState.IDLE)
        self.trace_clear_button.setEnabled(point_count > 0 or state is not TraceState.IDLE)
        self.trace_save_button.setEnabled(point_count > 0)

    def refresh_project(
        self, project: ProjectData, *, selected_section_id: str | None = None
    ) -> None:
        self.project = project
        if selected_section_id is not None:
            self.selected_section_id = selected_section_id
        self._populate_bodies(prefer_selected=True, reset_view=False)

    def set_live_snapshot(self, snapshot: LocationSnapshot | None) -> None:
        self.live_snapshot = snapshot
        self._rebuild_scene(reset_view=False)

    def set_selected_section(self, section_id: str | None) -> None:
        self.selected_section_id = section_id
        selected_key = body_key_for_section(self.project, section_id)
        if selected_key is not None and selected_key != self._current_body_key:
            index = self.body_selector.findData(self._key_id(selected_key))
            if index >= 0:
                self.body_selector.setCurrentIndex(index)
                return
        self.canvas.set_selected_section(section_id)
        self._hovered_stage_changed(self.canvas.hovered_section_id)

    def set_detached(self, detached: bool) -> None:
        self.pop_out_button.setText("Dock back" if detached else "Pop out")
        set_help(self.pop_out_button, "course_dock" if detached else "course_popout")

    def refresh_theme(self) -> None:
        self._apply_status_style()
        self.canvas.update()

    def _populate_bodies(self, *, prefer_selected: bool, reset_view: bool) -> None:
        groups = body_groups(self.project)
        previous = self._current_body_key
        preferred = body_key_for_section(self.project, self.selected_section_id) if prefer_selected else None
        available = {group.key for group in groups}
        chosen = (
            preferred
            if preferred in available
            else previous
            if previous in available
            else groups[0].key
            if groups
            else None
        )
        self._updating_bodies = True
        self.body_selector.clear()
        self._groups = {}
        for group in groups:
            key_id = self._key_id(group.key)
            self._groups[key_id] = group
            self.body_selector.addItem(group.label, key_id)
        if chosen is not None:
            index = self.body_selector.findData(self._key_id(chosen))
            self.body_selector.setCurrentIndex(max(index, 0))
        self.body_selector.setEnabled(len(groups) > 1)
        self._updating_bodies = False
        body_changed = chosen != previous
        self._current_body_key = chosen
        self._rebuild_scene(reset_view=reset_view or body_changed)

    def _body_changed(self, _index: int) -> None:
        if self._updating_bodies:
            return
        group = self._groups.get(str(self.body_selector.currentData() or ""))
        self._current_body_key = group.key if group else None
        self._rebuild_scene(reset_view=True)

    def _rebuild_scene(self, *, reset_view: bool) -> None:
        scene = build_course_scene(
            self.project,
            self._current_body_key,
            live_snapshot=self.live_snapshot,
            radius_cache=self.radius_cache,
            trace=self.trace,
        )
        self.canvas.set_scene(scene, reset_view=reset_view)
        self.canvas.set_selected_section(self.selected_section_id)
        self._update_status(scene)

    def _update_status(self, scene: CourseScene) -> None:
        text = scene.message
        if scene.radius_km is not None:
            text += (
                f" · Body radius {format(scene.radius_km, 'f')} km"
                + (f" ({scene.radius_source})" if scene.radius_source else "")
                + " · altitude scale 1:1"
            )
        if scene.trace_message:
            text += f" · {scene.trace_message}"
        if self.canvas.follow_live:
            if scene.live_marker is None:
                text += " · Follow ship is waiting for matching live coordinates"
            elif scene.live_marker.heading_degrees is None:
                text += (
                    f" · Following ship at {self.canvas.follow_tilt_degrees:g}°; "
                    "heading unavailable"
                )
            else:
                text += f" · Following ship at {self.canvas.follow_tilt_degrees:g}°"
            if self.canvas.follow_yaw_offset_degrees:
                text += (
                    f" · camera rotated {self.canvas.follow_yaw_offset_degrees:g}°"
                )
        text += (
            " · camera center altitude locked to 0 m"
            if self.canvas.center_height_locked
            else " · camera center uses checkpoint/course altitude"
        )
        self.status_label.setText(text)
        warnings: list[str] = []
        if scene.radius_warning:
            warnings.append(scene.radius_warning)
        if scene.skipped:
            details = "; ".join(f"{item.label}: {item.reason}" for item in scene.skipped[:3])
            if len(scene.skipped) > 3:
                details += f"; and {len(scene.skipped) - 3} more"
            warnings.append(f"Skipped stages — {details}")
        noted = [stage for stage in scene.stages if stage.validation_notes]
        if noted:
            warnings.append(
                f"{len(noted)} visible checkpoint{'s have' if len(noted) != 1 else ' has'} "
                "validation notes; select or hover for details."
            )
        self.warning_label.setText("\n".join(warnings))
        self.warning_label.setVisible(bool(warnings))
        self._apply_status_style()
        self._hovered_stage_changed(self.canvas.hovered_section_id)

    def _apply_status_style(self) -> None:
        self.warning_label.setStyleSheet(
            f"color: {semantic_color('warning', self).name()}; font-weight: 600;"
        )

    def _hovered_stage_changed(self, section_id: str | None) -> None:
        section_id = section_id or self.selected_section_id
        stage = next(
            (item for item in self.canvas.scene.stages if item.section_id == section_id),
            None,
        )
        if stage is None:
            self.detail_label.setText("Hover over a checkpoint for details.")
            return
        shape = "Gate" if stage.is_gate else "Point"
        if stage.altitude_bounds is None:
            altitude = "no altitude restriction"
        else:
            altitude = (
                f"altitude {stage.altitude_bounds.minimum_m:g}–"
                f"{stage.altitude_bounds.maximum_m:g} m"
            )
        proximity = (
            f", proximity {stage.proximity_m:g} m"
            if not stage.is_gate and stage.proximity_m is not None
            else ""
        )
        notes = ""
        if stage.validation_notes:
            first = stage.validation_notes[0]
            notes = f" · {first.severity.value.upper()}: {first.message}"
            if len(stage.validation_notes) > 1:
                notes += f" (+{len(stage.validation_notes) - 1} more)"
        self.detail_label.setText(
            f"{stage.label} · {shape} · {altitude}{proximity} · "
            f"{stage.latitude}, {stage.longitude} · {stage.player_state or 'no PlayerState'}"
            f"{notes}"
        )
