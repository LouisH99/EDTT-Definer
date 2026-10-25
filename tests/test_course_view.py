from __future__ import annotations

import math
import base64
from decimal import Decimal

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QColor, QCloseEvent, QImage, QMouseEvent
from PySide6.QtCore import QByteArray, QBuffer, QIODevice

from edtt_definer.course_scene import Point3D, SceneState, build_course_scene, unproject_coordinate
from edtt_definer.course_view import CourseViewPanel
from edtt_definer.live import LiveResult, LiveState, LocationSnapshot
from edtt_definer.project import AltitudeMode, CourseMap, ProjectData, StageMetadata
from edtt_definer.theme import ThemeMode, apply_theme
from edtt_definer.trace import RaceTrace, TracePoint, TraceSegment, TraceState
from edtt_definer.ui import MainWindow


def visual_project() -> ProjectData:
    project = ProjectData.new()
    stages = (
        project.document.start.section,
        project.document.waypoints[0].section,
        project.document.end.section,
    )
    for stage in stages:
        stage.set_value("System", "Demo System")
        stage.set_value("Body", "Demo A 1")
        stage.set_value("BodyRadius", "5411.771")
    stages[0].set_value("Position", "0,0")
    stages[0].set_value("Proximity", "100")
    stages[1].set_value("Position", "0.01,-0.01 to 0.01,0.01")
    stages[2].set_value("Position", "0.02,0")
    stages[2].set_value("Proximity", "80")
    project.stage_metadata[stages[0].node_id] = StageMetadata(
        altitude_mode=AltitudeMode.MAXIMUM,
        maximum_altitude="500",
    )
    stages[0].set_value("Altitude", "250")
    stages[0].set_value("AltitudeProximity", "250")
    project.stage_metadata[stages[1].node_id] = StageMetadata(
        display_name="Canyon Gate",
        altitude_mode=AltitudeMode.TARGET,
    )
    stages[1].set_value("Altitude", "300")
    stages[1].set_value("AltitudeProximity", "50")
    project.stage_metadata[stages[2].node_id] = StageMetadata(
        altitude_mode=AltitudeMode.NONE,
    )
    return project


def live_snapshot(
    latitude: str = "0.005", longitude: str = "0.005", heading: str | None = "90"
) -> LocationSnapshot:
    return LocationSnapshot(
        system="Demo System",
        body="Demo A 1",
        latitude=Decimal(latitude),
        longitude=Decimal(longitude),
        altitude=Decimal("125"),
        heading=Decimal(heading) if heading is not None else None,
        flags=1 << 21,
        flag_has_lat_long=True,
    )


def map_fixture(scene) -> CourseMap:
    image = QImage(80, 80, QImage.Format.Format_RGB32)
    image.fill(QColor("#d02030"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return CourseMap("Demo System", "Demo A 1", base64.b64encode(bytes(data)).decode("ascii"),
                     *scene.reference_coordinate,
                     [(-2000, 2000), (2000, 2000), (2000, -2000), (-2000, -2000)])


def test_map_renders_below_course_and_respects_visibility(qtbot) -> None:
    panel = CourseViewPanel()
    panel.resize(850, 700)
    qtbot.addWidget(panel)
    panel.show()
    panel.set_project(visual_project())
    canvas = panel.canvas
    canvas.fit_view()
    course_map = map_fixture(canvas.scene)
    course_map.locked = True
    course_map.opacity = 1
    canvas.set_course_map(course_map)
    image = canvas.grab().toImage()
    samples = [QPoint(horizontal, vertical)
               for horizontal in range(10, image.width() - 10, 13)
               for vertical in range(10, image.height() - 10, 13)
               if image.pixelColor(horizontal, vertical) == QColor("#d02030")]
    assert len(samples) > 100
    course_map.visible = False
    assert canvas.grab().toImage().pixelColor(samples[0]) != QColor("#d02030")


def test_map_corner_drag_moves_only_one_corner_and_rejects_crossing(qtbot) -> None:
    panel = CourseViewPanel()
    panel.resize(850, 700)
    qtbot.addWidget(panel)
    panel.set_project(visual_project())
    canvas = panel.canvas
    course_map = map_fixture(canvas.scene)
    canvas.set_course_map(course_map)
    canvas.top_view()
    canvas.scale = 0.1
    assert canvas.tilt_degrees == 0
    original = list(course_map.corners)
    canvas._map_drag_index = 0
    canvas._drag_map(QPointF(20, 10))
    assert course_map.corners[0] == pytest.approx((-1800, 1900), abs=1e-5)
    for index in range(1, 4):
        assert course_map.corners[index] == pytest.approx(original[index], abs=1e-5)
    accepted = list(course_map.corners)
    canvas._drag_map(QPointF(1000, 0))
    assert course_map.corners == accepted


def test_map_import_controls_and_project_reload(qtbot, tmp_path, monkeypatch) -> None:
    panel = CourseViewPanel()
    panel.resize(850, 700)
    qtbot.addWidget(panel)
    panel.show()
    project = visual_project()
    panel.set_project(project)
    source = tmp_path / "map.png"
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(QColor("#20a060"))
    assert image.save(str(source))
    changes = []
    panel.map_changed.connect(lambda: changes.append(True))
    panel.import_map(source)
    assert len(project.course_maps) == 1
    assert panel.canvas.map_editing
    assert panel.canvas.tilt_degrees == 45
    assert not panel.follow_button.isEnabled()
    assert panel.map_visible.isChecked()
    panel.map_opacity.setValue(35)
    assert project.course_maps[0].opacity == 0.35
    panel.map_locked.setChecked(True)
    assert not panel.canvas.map_editing
    assert panel.follow_button.isEnabled()
    panel.canvas.orbit_by(30, 45)
    tilt_before_hiding = panel.canvas.tilt_degrees
    panel.map_visible.setChecked(False)
    assert not project.course_maps[0].visible
    assert panel.canvas.tilt_degrees == tilt_before_hiding
    reloaded = ProjectData.from_dict(project.to_dict())
    panel.set_project(reloaded)
    assert panel.canvas.course_map is reloaded.course_maps[0]
    assert panel.map_opacity.value() == 35
    assert not panel.map_visible.isChecked()
    assert panel.map_locked.isChecked()
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    qtbot.mouseClick(panel.map_remove_button, Qt.MouseButton.LeftButton)
    assert not reloaded.course_maps
    assert panel.canvas.course_map is None
    assert not panel.map_opacity.isEnabled()
    assert len(changes) == 5


def test_map_import_rejects_unreadable_image_and_missing_course(qtbot, tmp_path) -> None:
    panel = CourseViewPanel()
    qtbot.addWidget(panel)
    source = tmp_path / "invalid.png"
    source.write_bytes(b"not an image")
    with pytest.raises(ValueError, match="body radius"):
        panel.import_map(source)
    panel.set_project(visual_project())
    with pytest.raises(ValueError, match="readable image"):
        panel.import_map(source)
    assert panel.project.course_maps == []


def test_map_mouse_drag_handles_translation_and_lock(qtbot) -> None:
    panel = CourseViewPanel()
    panel.resize(850, 700)
    qtbot.addWidget(panel)
    panel.show()
    project = visual_project()
    course_map = map_fixture(build_course_scene(project))
    project.course_maps.append(course_map)
    panel.set_project(project)
    canvas = panel.canvas
    canvas.top_view()
    canvas.fit_view()
    canvas.grab()
    notifications = []
    panel.map_changed.connect(lambda: notifications.append(True))
    first = canvas._map_polygon()[0].toPoint()
    original = list(course_map.corners)
    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=first)
    qtbot.mouseMove(canvas, first + QPoint(30, 15))
    qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=first + QPoint(30, 15))
    assert course_map.corners[0] != original[0]
    for index in range(1, 4):
        assert course_map.corners[index] == pytest.approx(original[index], abs=1e-5)
    assert notifications == [True]
    original = list(course_map.corners)
    center = canvas.world_to_screen(Point3D(0, 0, 0)).toPoint()
    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=center)
    qtbot.mouseMove(canvas, center + QPoint(15, 10))
    qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=center + QPoint(15, 10))
    assert all(after != before for after, before in zip(course_map.corners, original))
    assert len(notifications) == 2
    panel.map_locked.setChecked(True)
    original = list(course_map.corners)
    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=center)
    qtbot.mouseMove(canvas, center + QPoint(25, 20))
    qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=center + QPoint(25, 20))
    assert course_map.corners == original
    assert canvas.tilt_degrees > 0
    assert len(notifications) == 3


@pytest.mark.parametrize("locked", [False, True])
def test_map_visibility_options_and_refresh_preserve_camera(qtbot, locked) -> None:
    panel = CourseViewPanel()
    qtbot.addWidget(panel)
    project = visual_project()
    course_map = map_fixture(build_course_scene(project))
    course_map.locked = locked
    course_map.visible = False
    project.course_maps.append(course_map)
    panel.set_project(project)
    canvas = panel.canvas
    canvas.fit_view()
    canvas.orbit_by(73, 18)
    canvas.roll_by(17)
    canvas.pan_by_screen(QPointF(14, 9))
    expected = (canvas.yaw_degrees, canvas.tilt_degrees, canvas.roll_degrees,
                canvas.camera_target, canvas.pan, canvas.scale)
    panel.map_visible.setChecked(True)
    panel.map_opacity.setValue(42)
    panel.map_locked.setChecked(not locked)
    panel.refresh_project(project)
    actual = (canvas.yaw_degrees, canvas.tilt_degrees, canvas.roll_degrees,
              canvas.camera_target, canvas.pan, canvas.scale)
    assert actual == expected


@pytest.mark.parametrize("degrees", [90, -45, 180])
def test_map_rotation_control_preserves_shape_camera_and_saved_alignment(qtbot, degrees) -> None:
    panel = CourseViewPanel()
    qtbot.addWidget(panel)
    project = visual_project()
    course_map = map_fixture(build_course_scene(project))
    course_map.corners = [(-1200, 900), (1500, 900), (1100, -800), (-900, -600)]
    project.course_maps.append(course_map)
    panel.set_project(project)
    canvas = panel.canvas
    canvas.orbit_by(28, 12)
    canvas.roll_by(11)
    camera = (canvas.yaw_degrees, canvas.tilt_degrees, canvas.roll_degrees)
    original = list(course_map.corners)
    center_east = sum(point[0] for point in original) / 4
    center_north = sum(point[1] for point in original) / 4
    notifications = []
    panel.map_changed.connect(lambda: notifications.append(True))
    assert panel.map_rotation.isEnabled()
    panel.map_rotation.setValue(degrees * 10)
    cosine = math.cos(math.radians(degrees))
    sine = math.sin(math.radians(degrees))
    for (east, north), actual in zip(original, course_map.corners):
        expected = (center_east + (east - center_east) * cosine + (north - center_north) * sine,
                    center_north - (east - center_east) * sine + (north - center_north) * cosine)
        assert actual == pytest.approx(expected)
    assert (canvas.yaw_degrees, canvas.tilt_degrees, canvas.roll_degrees) == camera
    assert notifications == [True]
    saved = ProjectData.from_dict(project.to_dict())
    assert saved.course_maps[0].corners == course_map.corners
    panel.set_project(saved)
    expected_angle = (degrees + 180) % 360 - 180
    assert (panel.map_rotation.value() / 10 + 180) % 360 - 180 == pytest.approx(expected_angle)
    panel.map_locked.setChecked(True)
    assert not panel.map_rotation.isEnabled()
    original = list(saved.course_maps[0].corners)
    canvas.rotate_map(30)
    assert saved.course_maps[0].corners == original


@pytest.mark.parametrize("percentage", [10, 50, 150, 400])
def test_map_scale_slider_preserves_center_shape_camera_and_saved_size(qtbot, percentage) -> None:
    panel = CourseViewPanel()
    qtbot.addWidget(panel)
    project = visual_project()
    course_map = map_fixture(build_course_scene(project))
    course_map.corners = [(-1200, 900), (1500, 900), (1100, -800), (-900, -600)]
    project.course_maps.append(course_map)
    panel.set_project(project)
    canvas = panel.canvas
    canvas.fit_view()
    canvas.orbit_by(28, 12)
    canvas.roll_by(11)
    original = list(course_map.corners)
    center_east = sum(point[0] for point in original) / 4
    center_north = sum(point[1] for point in original) / 4
    camera = (canvas.yaw_degrees, canvas.tilt_degrees, canvas.roll_degrees,
              canvas.camera_target, canvas.pan, canvas.scale)
    notifications = []
    panel.map_changed.connect(lambda: notifications.append(True))
    assert panel.map_scale.isEnabled()
    panel.map_scale.setValue(percentage)
    for (east, north), actual in zip(original, course_map.corners):
        expected = (center_east + (east - center_east) * percentage / 100,
                    center_north + (north - center_north) * percentage / 100)
        assert actual == pytest.approx(expected)
    assert course_map.scale_factor == pytest.approx(percentage / 100)
    assert panel.map_scale_label.text() == f"{percentage}%"
    assert notifications == [True]
    assert (canvas.yaw_degrees, canvas.tilt_degrees, canvas.roll_degrees,
            canvas.camera_target, canvas.pan, canvas.scale) == camera
    saved = ProjectData.from_dict(project.to_dict())
    panel.set_project(saved)
    assert panel.map_scale.value() == percentage
    panel.map_scale.setValue(100)
    for actual, expected in zip(saved.course_maps[0].corners, original):
        assert actual == pytest.approx(expected)
    panel.map_locked.setChecked(True)
    assert not panel.map_scale.isEnabled()
    original = list(saved.course_maps[0].corners)
    canvas.scale_map(2)
    assert saved.course_maps[0].corners == original


@pytest.mark.parametrize("factor", [0, -1, float("nan"), float("inf")])
def test_map_scale_rejects_invalid_factors(qtbot, factor) -> None:
    panel = CourseViewPanel()
    qtbot.addWidget(panel)
    project = visual_project()
    course_map = map_fixture(build_course_scene(project))
    project.course_maps.append(course_map)
    panel.set_project(project)
    original = list(course_map.corners)
    panel.canvas.scale_map(factor)
    assert course_map.corners == original
    assert course_map.scale_factor == 1


def test_map_stays_geographically_anchored_after_course_edits(qtbot) -> None:
    panel = CourseViewPanel()
    qtbot.addWidget(panel)
    project = visual_project()
    course_map = map_fixture(build_course_scene(project))
    project.course_maps.append(course_map)
    panel.set_project(project)
    old_reference = panel.canvas.scene.reference_coordinate
    expected = [unproject_coordinate(east, north, *old_reference, panel.current_radius_km)
                for east, north in course_map.corners]
    project.document.end.section.set_value("Position", "0.04,0.03")
    panel.refresh_project(project)
    new_reference = panel.canvas.scene.reference_coordinate
    assert old_reference != new_reference
    for point, geographic in zip(panel.canvas.map_world_corners(), expected):
        actual = unproject_coordinate(point.east_m, point.north_m, *new_reference, panel.current_radius_km)
        assert actual == pytest.approx(geographic, abs=1e-8)


def test_map_is_scoped_to_body_without_marking_switches_dirty(qtbot) -> None:
    panel = CourseViewPanel()
    qtbot.addWidget(panel)
    project = visual_project()
    course_map = map_fixture(build_course_scene(project))
    project.course_maps.append(course_map)
    remote = project.document.add_waypoint("ShipPass")
    for key, value in (("System", "Other"), ("Body", "Other 1"),
                       ("BodyRadius", "1000"), ("Position", "1,2"), ("Proximity", "50")):
        remote.set_value(key, value)
    notifications = []
    panel.map_changed.connect(lambda: notifications.append(True))
    panel.set_project(project)
    assert panel.canvas.course_map is course_map
    panel.body_selector.setCurrentIndex(1)
    assert panel.canvas.course_map is None
    assert not panel.map_locked.isEnabled()
    panel.body_selector.setCurrentIndex(0)
    assert panel.canvas.course_map is course_map
    assert not notifications


def test_map_changes_mark_main_window_dirty_without_changing_txt(qtbot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.project = visual_project()
    window.course_view.set_project(window.project)
    from edtt_definer.project import serialize_project_export
    original = serialize_project_export(window.project)
    window.dirty = False
    window.project.course_maps.append(map_fixture(window.course_view.canvas.scene))
    window.course_view.refresh_project(window.project)
    assert not window.dirty
    window.course_view.map_opacity.setValue(20)
    assert window.dirty
    assert serialize_project_export(window.project) == original
    window.dirty = False


def test_panel_starts_at_fitted_45_degree_overview_and_renders_course(qtbot) -> None:
    panel = CourseViewPanel()
    panel.resize(850, 600)
    qtbot.addWidget(panel)
    panel.show()
    project = visual_project()

    panel.set_project(project)
    panel.canvas.fit_view()
    qtbot.wait(20)
    image = panel.canvas.grab().toImage()

    assert panel.canvas.scene.state is SceneState.READY
    assert panel.canvas.yaw_degrees == 0
    assert panel.canvas.tilt_degrees == 45
    assert panel.canvas.scale > 0
    assert not image.isNull()
    assert image.width() > 250
    assert "altitude scale 1:1" in panel.status_label.text()
    assert (
        panel.course_distance_label.text()
        == "Course distance: 1.889 km · surface route between checkpoint centres"
    )
    assert "surface distance" in panel.course_distance_label.accessibleDescription()
    assert panel.body_selector.count() == 1


def test_project_and_body_replacement_restore_default_overview(qtbot) -> None:
    project = visual_project()
    remote = project.document.add_waypoint("ShipPass")
    remote.set_value("System", "Remote System")
    remote.set_value("Body", "Remote 2")
    remote.set_value("BodyRadius", "1000")
    remote.set_value("Position", "1,2")
    remote.set_value("Proximity", "50")
    project.stage_metadata[remote.node_id] = StageMetadata(
        altitude_mode=AltitudeMode.NONE
    )
    panel = CourseViewPanel()
    panel.resize(850, 600)
    qtbot.addWidget(panel)
    panel.show()
    panel.set_project(project)
    panel.canvas.fit_view()

    panel.canvas.yaw_degrees = 170
    panel.canvas.tilt_degrees = 225
    panel.body_selector.setCurrentIndex(1)
    qtbot.wait(20)
    assert panel.canvas.yaw_degrees == 0
    assert panel.canvas.tilt_degrees == 45

    panel.canvas.yaw_degrees = 200
    panel.canvas.tilt_degrees = 315
    panel.set_project(visual_project())
    qtbot.wait(20)
    assert panel.canvas.yaw_degrees == 0
    assert panel.canvas.tilt_degrees == 45


def test_click_hover_and_camera_controls(qtbot) -> None:
    panel = CourseViewPanel()
    panel.resize(850, 600)
    qtbot.addWidget(panel)
    panel.show()
    project = visual_project()
    waypoint = project.document.waypoints[0].section
    panel.set_project(project)
    panel.canvas.fit_view()
    panel.canvas.grab()
    stage = next(
        item for item in panel.canvas.scene.stages if item.section_id == waypoint.node_id
    )
    center = panel.canvas.world_to_screen(stage.center).toPoint()
    selected: list[str] = []
    panel.stage_selected.connect(selected.append)

    panel.canvas.mouseMoveEvent(
        QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(center),
            QPointF(center),
            QPointF(panel.canvas.mapToGlobal(center)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert "Waypoint 1 — Canyon Gate" in panel.detail_label.text()
    qtbot.mouseClick(panel.canvas, Qt.MouseButton.LeftButton, pos=center)

    assert selected == [waypoint.node_id]
    assert panel.canvas.selected_section_id == waypoint.node_id

    before_scale = panel.canvas.scale
    qtbot.keyClick(panel.canvas, Qt.Key.Key_Plus)
    assert panel.canvas.scale > before_scale
    panel.canvas.yaw_degrees = 43
    panel.canvas.tilt_degrees = 37
    qtbot.mouseClick(panel.top_button, Qt.MouseButton.LeftButton)
    assert panel.canvas.yaw_degrees == 0
    assert panel.canvas.tilt_degrees == 0

    panel.center_height_lock.setChecked(False)
    pan_before = QPoint(panel.canvas.pan.toPoint())
    start = QPoint(100, 100)
    finish = QPoint(140, 125)
    qtbot.mousePress(panel.canvas, Qt.MouseButton.RightButton, pos=start)
    qtbot.mouseMove(panel.canvas, finish)
    qtbot.mouseRelease(panel.canvas, Qt.MouseButton.RightButton, pos=finish)
    assert panel.canvas.pan.toPoint() != pan_before


def test_horizontal_mouse_orbit_matches_v066_direction(qtbot) -> None:
    panel = CourseViewPanel()
    qtbot.addWidget(panel)
    panel.set_project(visual_project())

    panel.canvas.yaw_degrees = 0
    qtbot.mousePress(panel.canvas, Qt.MouseButton.LeftButton, pos=QPoint(120, 100))
    qtbot.mouseMove(panel.canvas, QPoint(160, 100))
    qtbot.mouseRelease(panel.canvas, Qt.MouseButton.LeftButton, pos=QPoint(160, 100))
    right_drag_yaw = panel.canvas.yaw_degrees

    panel.canvas.yaw_degrees = 0
    qtbot.mousePress(panel.canvas, Qt.MouseButton.LeftButton, pos=QPoint(160, 100))
    qtbot.mouseMove(panel.canvas, QPoint(120, 100))
    qtbot.mouseRelease(panel.canvas, Qt.MouseButton.LeftButton, pos=QPoint(120, 100))
    left_drag_yaw = panel.canvas.yaw_degrees

    assert 0 < right_drag_yaw < 180
    assert left_drag_yaw > 180


def test_manual_orbit_reaches_below_course_but_stops_at_vertical_poles(qtbot) -> None:
    panel = CourseViewPanel()
    qtbot.addWidget(panel)
    panel.set_project(visual_project())
    panel.canvas.tilt_degrees = 80

    panel.canvas.orbit_by(450, 20)
    assert panel.canvas.yaw_degrees == pytest.approx(90)
    assert panel.canvas.tilt_degrees == pytest.approx(100)

    panel.canvas.orbit_by(0, 280)
    assert panel.canvas.tilt_degrees == pytest.approx(180)

    panel.canvas.orbit_by(0, -400)
    assert panel.canvas.tilt_degrees == pytest.approx(0)


def test_center_height_lock_defaults_to_ground_and_double_click_centers_stage(
    qtbot,
) -> None:
    panel = CourseViewPanel()
    panel.resize(850, 600)
    qtbot.addWidget(panel)
    panel.show()
    project = visual_project()
    waypoint = project.document.waypoints[0].section
    panel.set_project(project)
    panel.canvas.tilt_degrees = 45
    panel.canvas.fit_view()
    panel.canvas.grab()
    stage = next(
        item for item in panel.canvas.scene.stages if item.section_id == waypoint.node_id
    )
    position = panel.canvas.world_to_screen(stage.center).toPoint()
    selected: list[str] = []
    panel.stage_selected.connect(selected.append)

    panel.canvas.mouseDoubleClickEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonDblClick,
            QPointF(position),
            QPointF(position),
            QPointF(panel.canvas.mapToGlobal(position)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )

    assert panel.center_height_lock.isChecked()
    assert selected == [waypoint.node_id]
    assert panel.canvas.camera_target.east_m == pytest.approx(stage.center.east_m)
    assert panel.canvas.camera_target.north_m == pytest.approx(stage.center.north_m)
    assert panel.canvas.camera_target.altitude_m == 0
    assert panel.canvas.pan == QPointF()

    panel.center_height_lock.setChecked(False)
    assert panel.canvas.camera_target.altitude_m == pytest.approx(stage.center.altitude_m)
    assert panel.canvas.world_to_screen(stage.center).x() == pytest.approx(
        panel.canvas.width() / 2
    )
    assert panel.canvas.world_to_screen(stage.center).y() == pytest.approx(
        panel.canvas.height() / 2
    )

    panel.center_height_lock.setChecked(True)
    target_before = panel.canvas.camera_target
    start = QPoint(100, 100)
    finish = QPoint(140, 160)
    qtbot.mousePress(panel.canvas, Qt.MouseButton.RightButton, pos=start)
    qtbot.mouseMove(panel.canvas, finish)
    qtbot.mouseRelease(panel.canvas, Qt.MouseButton.RightButton, pos=finish)
    assert panel.canvas.camera_target != target_before
    assert panel.canvas.camera_target.altitude_m == 0
    assert panel.canvas.pan == QPointF()

    panel.canvas.roll_by(63)
    rolled_target = panel.canvas.camera_target
    panel.canvas.pan_by_screen(QPointF(30, -45))
    assert panel.canvas.camera_target != rolled_target
    assert panel.canvas.camera_target.altitude_m == 0
    assert panel.canvas.pan == QPointF()


def test_ground_lock_normalizes_camera_even_when_scene_cannot_be_fitted(qtbot) -> None:
    panel = CourseViewPanel()
    qtbot.addWidget(panel)
    panel.set_project(visual_project())
    panel.center_height_lock.setChecked(False)
    panel.canvas.camera_target = Point3D(10.0, 20.0, 750.0)
    panel.canvas.pan = QPointF(15.0, 35.0)
    panel.canvas.set_scene(build_course_scene(ProjectData.new()))

    panel.center_height_lock.setChecked(True)

    assert panel.canvas.camera_target.altitude_m == 0
    assert panel.canvas.pan == QPointF()

    panel.canvas.camera_target = Point3D(10.0, 20.0, 500.0)
    panel.canvas.pan = QPointF(5.0, 25.0)
    panel.canvas.set_center_height_locked(True)
    assert panel.canvas.camera_target.altitude_m == 0
    assert panel.canvas.pan == QPointF()


def test_locked_vertical_pan_slows_toward_side_view(qtbot) -> None:
    panel = CourseViewPanel()
    qtbot.addWidget(panel)
    panel.set_project(visual_project())
    panel.canvas.scale = 2.0

    distances: list[float] = []
    for tilt in (0.0, 45.0, 80.0, 90.0):
        panel.canvas.camera_target = Point3D(0.0, 0.0, 0.0)
        panel.canvas.tilt_degrees = tilt
        panel.canvas.pan_by_screen(QPointF(0.0, 100.0))
        distances.append(
            math.hypot(
                panel.canvas.camera_target.east_m,
                panel.canvas.camera_target.north_m,
            )
        )
        assert panel.canvas.camera_target.altitude_m == 0
        assert panel.canvas.pan == QPointF()

    assert distances[0] == pytest.approx(50.0)
    assert distances[0] > distances[1] > distances[2] > distances[3]
    assert distances[3] == pytest.approx(0.0, abs=1e-10)


def test_locked_fit_uses_ground_target_without_orbit_jump(qtbot) -> None:
    panel = CourseViewPanel()
    panel.resize(850, 600)
    qtbot.addWidget(panel)
    panel.set_project(visual_project())
    panel.canvas.yaw_degrees = 37
    panel.canvas.fit_view()
    target_after_fit = panel.canvas.camera_target

    assert panel.canvas.pan == QPointF()
    assert target_after_fit.altitude_m == 0

    panel.canvas.orbit_by(1, 0)
    assert panel.canvas.pan == QPointF()
    assert panel.canvas.camera_target == target_after_fit


def test_follow_ship_centers_live_marker_and_rotates_with_heading(qtbot) -> None:
    panel = CourseViewPanel()
    panel.resize(850, 600)
    qtbot.addWidget(panel)
    panel.show()
    panel.set_project(visual_project(), live_snapshot=live_snapshot(heading="135"))
    panel.center_height_lock.setChecked(False)
    qtbot.mouseClick(panel.follow_button, Qt.MouseButton.LeftButton)

    assert panel.follow_button.isChecked()
    assert panel.canvas.follow_live
    assert panel.canvas.tilt_degrees == pytest.approx(45)
    assert panel.canvas.yaw_degrees == pytest.approx(135)
    marker = panel.canvas.scene.live_marker
    assert marker is not None
    screen_position = panel.canvas.world_to_screen(marker.position)
    assert screen_position.x() == pytest.approx(panel.canvas.width() / 2)
    assert screen_position.y() == pytest.approx(panel.canvas.height() / 2)

    qtbot.mouseClick(panel.follow_button, Qt.MouseButton.LeftButton)
    assert panel.follow_button.isChecked()
    assert panel.canvas.follow_live
    assert panel.canvas.follow_yaw_offset_degrees == pytest.approx(90)
    assert panel.canvas.yaw_degrees == pytest.approx(225)
    assert "camera rotated 90°" in panel.status_label.text()

    panel.set_live_snapshot(live_snapshot("0.013", "0.01", "40"))
    assert panel.canvas.yaw_degrees == pytest.approx(130)
    marker = panel.canvas.scene.live_marker
    assert marker is not None
    screen_position = panel.canvas.world_to_screen(marker.position)
    assert screen_position.x() == pytest.approx(panel.canvas.width() / 2)
    assert screen_position.y() == pytest.approx(panel.canvas.height() / 2)

    qtbot.mouseClick(panel.follow_button, Qt.MouseButton.LeftButton)
    assert panel.canvas.follow_yaw_offset_degrees == pytest.approx(180)
    assert panel.canvas.yaw_degrees == pytest.approx(220)
    original_scale = panel.canvas.scale

    panel.set_live_snapshot(live_snapshot("0.012", "0.009", "225"))

    assert panel.canvas.yaw_degrees == pytest.approx(45)
    assert panel.canvas.scale == pytest.approx(original_scale)
    marker = panel.canvas.scene.live_marker
    assert marker is not None
    screen_position = panel.canvas.world_to_screen(marker.position)
    assert screen_position.x() == pytest.approx(panel.canvas.width() / 2)
    assert screen_position.y() == pytest.approx(panel.canvas.height() / 2)
    assert "Following ship at 45°" in panel.status_label.text()

    qtbot.keyClick(panel.canvas, Qt.Key.Key_Plus)
    assert panel.canvas.scale > original_scale
    marker = panel.canvas.scene.live_marker
    assert marker is not None
    screen_position = panel.canvas.world_to_screen(marker.position)
    assert screen_position.x() == pytest.approx(panel.canvas.width() / 2)
    assert screen_position.y() == pytest.approx(panel.canvas.height() / 2)


def test_ground_lock_keeps_follow_camera_centered_below_live_marker(qtbot) -> None:
    panel = CourseViewPanel()
    panel.resize(850, 600)
    qtbot.addWidget(panel)
    panel.show()
    panel.set_project(visual_project(), live_snapshot=live_snapshot(heading="135"))

    qtbot.mouseClick(panel.follow_button, Qt.MouseButton.LeftButton)

    marker = panel.canvas.scene.live_marker
    assert marker is not None
    ground = Point3D(marker.position.east_m, marker.position.north_m, 0.0)
    ground_position = panel.canvas.world_to_screen(ground)
    marker_position = panel.canvas.world_to_screen(marker.position)
    assert panel.center_height_lock.isChecked()
    assert panel.canvas.pan.y() == 0
    assert ground_position.x() == pytest.approx(panel.canvas.width() / 2)
    assert ground_position.y() == pytest.approx(panel.canvas.height() / 2)
    assert marker_position.y() < ground_position.y()


def test_side_views_keep_altitude_above_ground_and_explicit_roll_inverts(qtbot) -> None:
    panel = CourseViewPanel()
    panel.resize(850, 600)
    qtbot.addWidget(panel)
    panel.show()
    panel.set_project(visual_project())
    panel.canvas.yaw_degrees = 0
    panel.canvas.tilt_degrees = 90
    ground = Point3D(0.0, 0.0, 0.0)
    elevated = Point3D(0.0, 0.0, 950.0)
    assert panel.canvas.roll_degrees == 0
    assert panel.canvas.world_to_screen(elevated).y() < panel.canvas.world_to_screen(ground).y()

    panel.canvas.yaw_degrees = 180
    assert panel.canvas.world_to_screen(elevated).y() < panel.canvas.world_to_screen(ground).y()
    depth_before_roll = panel.canvas.camera_orientation.depth

    qtbot.keyClick(
        panel.canvas,
        Qt.Key.Key_R,
        modifier=Qt.KeyboardModifier.ShiftModifier,
    )

    assert panel.canvas.roll_degrees == 180
    assert panel.canvas.camera_orientation.depth == depth_before_roll
    assert panel.canvas.world_to_screen(elevated).y() > panel.canvas.world_to_screen(ground).y()

    panel.canvas.roll_by(37)
    qtbot.keyClick(panel.canvas, Qt.Key.Key_R)
    assert panel.canvas.roll_degrees == 0
    assert panel.canvas.world_to_screen(elevated).y() < panel.canvas.world_to_screen(ground).y()


def test_follow_keeps_orbit_angle_but_panning_stops_it(qtbot) -> None:
    panel = CourseViewPanel()
    panel.resize(850, 600)
    qtbot.addWidget(panel)
    panel.show()
    panel.set_project(visual_project())
    qtbot.mouseClick(panel.follow_button, Qt.MouseButton.LeftButton)

    assert panel.follow_button.isChecked()
    assert "waiting for matching live coordinates" in panel.status_label.text()

    panel.set_live_snapshot(live_snapshot(heading="40"))
    assert panel.canvas.tilt_degrees == pytest.approx(45)
    assert panel.canvas.yaw_degrees == pytest.approx(40)

    start = QPoint(100, 100)
    finish = QPoint(150, 130)
    qtbot.mousePress(panel.canvas, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(panel.canvas, finish)
    qtbot.mouseRelease(panel.canvas, Qt.MouseButton.LeftButton, pos=finish)
    assert panel.follow_button.isChecked()
    assert panel.canvas.follow_live
    assert panel.canvas.follow_yaw_offset_degrees == pytest.approx(22.5)
    assert panel.canvas.yaw_degrees == pytest.approx(62.5)
    assert panel.canvas.follow_tilt_degrees == pytest.approx(55.5)
    assert panel.canvas.tilt_degrees == pytest.approx(55.5)
    assert "Following ship at 55.5°" in panel.status_label.text()
    assert "camera rotated 22.5°" in panel.status_label.text()

    panel.canvas.roll_follow_by(35)
    panel.set_live_snapshot(live_snapshot("0.013", "0.01", "80"))
    assert panel.canvas.follow_live
    assert panel.canvas.yaw_degrees == pytest.approx(102.5)
    assert panel.canvas.tilt_degrees == pytest.approx(55.5)
    assert panel.canvas.roll_degrees == pytest.approx(35)
    marker = panel.canvas.scene.live_marker
    assert marker is not None
    ground_or_ship = Point3D(
        marker.position.east_m,
        marker.position.north_m,
        0.0 if panel.canvas.center_height_locked else marker.position.altitude_m,
    )
    center = panel.canvas.world_to_screen(ground_or_ship)
    assert center.x() == pytest.approx(panel.canvas.width() / 2)
    assert center.y() == pytest.approx(panel.canvas.height() / 2)

    qtbot.mousePress(panel.canvas, Qt.MouseButton.RightButton, pos=start)
    qtbot.mouseMove(panel.canvas, finish)
    qtbot.mouseRelease(panel.canvas, Qt.MouseButton.RightButton, pos=finish)
    assert not panel.follow_button.isChecked()
    assert not panel.canvas.follow_live

    qtbot.mouseClick(panel.follow_button, Qt.MouseButton.LeftButton)
    assert panel.canvas.follow_yaw_offset_degrees == 0
    assert panel.canvas.follow_tilt_degrees == pytest.approx(45)
    assert panel.canvas.roll_degrees == pytest.approx(0)
    qtbot.mouseClick(panel.top_button, Qt.MouseButton.LeftButton)
    assert not panel.follow_button.isChecked()
    assert panel.canvas.tilt_degrees == 0

    qtbot.mouseClick(panel.follow_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(panel.fit_button, Qt.MouseButton.LeftButton)
    assert not panel.follow_button.isChecked()


def test_map_retains_last_known_marker_until_coordinates_are_authoritatively_cleared(
    qtbot,
) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    window.live_stale_timer.stop()
    qtbot.addWidget(window)
    window._set_project(visual_project(), None, dirty=False)
    snapshot = live_snapshot()

    window._display_live_result(LiveResult(LiveState.LIVE, snapshot, "Live"))
    assert window.course_view.canvas.scene.live_marker is not None

    window._display_live_result(
        LiveResult(LiveState.INVALID, snapshot, "Status.json is being replaced")
    )
    assert window.course_view.canvas.scene.live_marker is not None

    window._display_live_result(LiveResult(LiveState.LIVE, snapshot, "Live"))
    window._mark_live_stale()
    assert window.live_result.state is LiveState.STALE
    assert window.course_view.canvas.scene.live_marker is not None

    window._display_live_result(
        LiveResult(LiveState.MISSING, snapshot, "Status.json is temporarily missing")
    )
    assert window.course_view.canvas.scene.live_marker is not None

    no_coordinates = LocationSnapshot(system=snapshot.system, body=snapshot.body)
    window._display_live_result(
        LiveResult(
            LiveState.NO_COORDINATES,
            no_coordinates,
            "No planetary coordinates",
        )
    )
    assert window.course_view.canvas.scene.live_marker is None


def test_parseable_stale_snapshot_is_available_as_last_known_map_position(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    window.live_stale_timer.stop()
    qtbot.addWidget(window)
    window._set_project(visual_project(), None, dirty=False)

    window._display_live_result(
        LiveResult(LiveState.STALE, live_snapshot(), "Status.json is old")
    )

    assert window.course_view.canvas.scene.live_marker is not None
    assert not window.capture_button.isEnabled()
    assert not window.capture_add_button.isEnabled()
    assert not window.stage_form.capture_a_button.isEnabled()
    assert not window.stage_form.capture_b_button.isEnabled()


def test_editor_selection_changes_body_and_live_marker_is_filtered(qtbot) -> None:
    project = visual_project()
    remote = project.document.add_waypoint("ShipPass")
    remote.set_value("System", "Remote System")
    remote.set_value("Body", "Remote 2")
    remote.set_value("BodyRadius", "1000")
    remote.set_value("Position", "1,2")
    remote.set_value("Proximity", "50")
    project.stage_metadata[remote.node_id] = StageMetadata(
        altitude_mode=AltitudeMode.NONE
    )
    panel = CourseViewPanel()
    qtbot.addWidget(panel)
    panel.set_project(project)

    assert panel.body_selector.count() == 2
    panel.set_selected_section(remote.node_id)
    assert panel.canvas.scene.body_group is not None
    assert panel.canvas.scene.body_group.body_name == "Remote 2"

    panel.set_live_snapshot(
        LocationSnapshot(
            system="Demo System",
            body="Demo A 1",
            latitude=Decimal("0.005"),
            longitude=Decimal("0.005"),
            heading=Decimal("90"),
            flags=1 << 21,
            flag_has_lat_long=True,
        )
    )
    assert panel.canvas.scene.live_marker is None

    start = project.document.start.section
    panel.set_selected_section(start.node_id)
    assert panel.canvas.scene.body_group is not None
    assert panel.canvas.scene.body_group.body_name == "Demo A 1"
    assert panel.canvas.scene.live_marker is not None


def test_main_window_course_view_popout_redock_and_selection_are_not_dirty(
    qtbot, tmp_path
) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings)
    window.poll_timer.stop()
    window.update_timer.stop()
    qtbot.addWidget(window)
    window._set_project(visual_project(), None, dirty=False)
    waypoint = window.project.document.waypoints[0].section

    assert window.right_tabs.count() == 3
    assert window.right_tabs.tabText(2) == "Course view"
    assert not window._course_detached
    window.course_view.stage_selected.emit(waypoint.node_id)
    assert window.current_section_id == waypoint.node_id
    assert not window.dirty
    window.course_view.center_height_lock.setChecked(False)
    assert window.course_view.canvas.center_on_stage(waypoint.node_id)
    assert not window.dirty

    window._pop_out_course_view()
    assert window._course_detached
    assert window.course_window is not None
    assert window.course_window.isVisible()
    assert window.course_view.parentWidget() is window.course_window
    assert not window.dirty

    window.course_window.close()
    qtbot.wait(10)
    assert not window._course_detached
    assert not window.course_window.isVisible()
    assert window.course_view.parentWidget() is window.course_page
    assert not window.dirty

    window.course_view.set_live_snapshot(live_snapshot())
    qtbot.mouseClick(window.course_view.follow_button, Qt.MouseButton.LeftButton)
    assert window.course_view.follow_button.isChecked()
    assert not window.dirty

    window._pop_out_course_view()
    assert window.course_window.isVisible()
    assert window.close()
    qtbot.wait(10)
    assert not window._course_detached
    assert not window.course_window.isVisible()


def test_project_refresh_updates_geometry_without_resetting_camera(qtbot) -> None:
    project = visual_project()
    panel = CourseViewPanel()
    panel.resize(800, 500)
    qtbot.addWidget(panel)
    panel.show()
    panel.set_project(project)
    panel.canvas.fit_view()
    panel.canvas.yaw_degrees = 31
    panel.canvas.tilt_degrees = 42
    panel.canvas.orbit_by(17, -23)
    old_scale = panel.canvas.scale
    old_basis = (
        panel.canvas.camera_orientation.right,
        panel.canvas.camera_orientation.up,
        panel.canvas.camera_orientation.depth,
    )
    waypoint = project.document.waypoints[0].section

    waypoint.set_value("Position", "0.01,-0.02 to 0.01,0.02")
    panel.refresh_project(project)

    assert panel.canvas.yaw_degrees == 48
    assert panel.canvas.tilt_degrees == 19
    assert panel.canvas.camera_orientation.right == old_basis[0]
    assert panel.canvas.camera_orientation.up == old_basis[1]
    assert panel.canvas.camera_orientation.depth == old_basis[2]
    assert panel.canvas.scale == pytest.approx(old_scale)
    gate = next(
        stage for stage in panel.canvas.scene.stages if stage.section_id == waypoint.node_id
    )
    assert gate.endpoint_b is not None
    assert gate.endpoint_b.east_m - gate.endpoint_a.east_m > 3000


def test_course_controls_have_accessible_help(qtbot) -> None:
    panel = CourseViewPanel()
    qtbot.addWidget(panel)

    for widget in (
        panel.body_selector,
        panel.follow_button,
        panel.center_height_lock,
        panel.top_button,
        panel.fit_button,
        panel.pop_out_button,
        panel.status_label,
        panel.canvas,
    ):
        assert widget.accessibleDescription()
        assert widget.toolTip()


def test_course_colors_follow_the_application_theme(qtbot) -> None:
    panel = CourseViewPanel()
    qtbot.addWidget(panel)
    try:
        apply_theme(ThemeMode.DARK)
        panel.refresh_theme()
        dark_start = panel.canvas._colors()["start"].name()
        apply_theme(ThemeMode.LIGHT)
        panel.refresh_theme()
        light_start = panel.canvas._colors()["start"].name()
    finally:
        apply_theme(ThemeMode.SYSTEM)

    assert dark_start != light_start


def recorded_trace() -> RaceTrace:
    return RaceTrace(
        system="Demo System",
        body="Demo A 1",
        radius_km=Decimal("5411.771"),
        segments=[
            TraceSegment(
                points=[
                    TracePoint(
                        timestamp_utc="2026-09-05T12:00:00Z",
                        unix_time=Decimal("1"),
                        elapsed_ms=0,
                        latitude=Decimal("0"),
                        longitude=Decimal("0"),
                        altitude_m=Decimal("100"),
                        heading_degrees=Decimal("0"),
                        radius_km=Decimal("5411.771"),
                    ),
                    TracePoint(
                        timestamp_utc="2026-09-05T12:00:01Z",
                        unix_time=Decimal("2"),
                        elapsed_ms=1000,
                        latitude=Decimal("0.005"),
                        longitude=Decimal("0.005"),
                        altitude_m=Decimal("150"),
                        heading_degrees=Decimal("45"),
                        radius_km=Decimal("5411.771"),
                        speed_mps=Decimal("250"),
                    ),
                ]
            )
        ],
    )


def test_panel_displays_trace_and_emits_recording_actions(qtbot) -> None:
    panel = CourseViewPanel()
    panel.resize(850, 600)
    qtbot.addWidget(panel)
    panel.show()
    panel.set_project(visual_project())
    panel.set_trace(recorded_trace())
    emitted: list[str] = []
    panel.trace_record_requested.connect(lambda: emitted.append("record"))
    panel.trace_pause_requested.connect(lambda: emitted.append("pause"))
    panel.set_trace_status(TraceState.IDLE, "Loaded 2 points.", False)

    assert len(panel.canvas.scene.trace_segments) == 1
    assert "2 trace points shown" in panel.status_label.text()
    qtbot.mouseClick(panel.trace_record_button, Qt.MouseButton.LeftButton)
    assert emitted == ["record"]

    panel.set_trace_status(TraceState.RECORDING, "Recording · 2 points.", True)
    qtbot.mouseClick(panel.trace_pause_button, Qt.MouseButton.LeftButton)
    assert emitted[-1] == "pause"
    assert "unsaved" in panel.trace_status_label.text()


def test_main_window_records_pauses_saves_and_reopens_trace(qtbot, tmp_path) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window._set_project(visual_project(), None, dirty=False)
    window.live_result = LiveResult(LiveState.LIVE, live_snapshot(), "Live")

    window.start_trace_recording()
    window.poll_timer.stop()
    assert window.trace_recorder.state is TraceState.RECORDING
    assert window.trace_recorder.trace.point_count == 1
    assert window.poll_timer.isSingleShot()
    assert window.poll_timer.interval() == 40

    window.toggle_trace_pause()
    assert window.trace_recorder.state is TraceState.PAUSED
    assert window.poll_timer.interval() == 40
    window.toggle_trace_pause()
    window.poll_timer.stop()
    assert window.trace_recorder.state is TraceState.RECORDING

    path = tmp_path / "recording.edtttrace.json"
    assert window.save_trace(str(path))
    assert not window.trace_recorder.dirty
    window.trace_recorder.clear()
    window.course_view.set_trace(window.trace_recorder.trace)
    window.open_trace(str(path))
    assert window.trace_recorder.trace.point_count == 1
    assert window.trace_recorder.state is TraceState.IDLE
    window.dirty = False


def test_project_replacement_respects_unsaved_trace_cancel(qtbot, monkeypatch) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window.trace_recorder.replace(recorded_trace(), dirty=True)
    original_project = window.project
    monkeypatch.setattr(window, "_confirm_trace_discard", lambda: False)

    window.new_project()

    assert window.project is original_project
    assert window.trace_recorder.trace.point_count == 2
    window.trace_recorder.dirty = False
    window.dirty = False


def test_close_respects_unsaved_trace_cancel(qtbot, monkeypatch) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window.trace_recorder.replace(recorded_trace(), dirty=True)
    monkeypatch.setattr(window, "_confirm_discard", lambda: True)
    monkeypatch.setattr(window, "_confirm_trace_discard", lambda: False)
    event = QCloseEvent()

    window.closeEvent(event)

    assert not event.isAccepted()
    window.trace_recorder.dirty = False
    window.dirty = False
