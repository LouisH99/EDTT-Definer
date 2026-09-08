from __future__ import annotations

from decimal import Decimal

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QCloseEvent, QMouseEvent

from edtt_definer.course_scene import SceneState
from edtt_definer.course_view import CourseViewPanel
from edtt_definer.live import LiveResult, LiveState, LocationSnapshot
from edtt_definer.project import AltitudeMode, ProjectData, StageMetadata
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


def test_panel_starts_top_down_and_renders_course(qtbot) -> None:
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
    assert panel.canvas.tilt_degrees == 0
    assert panel.canvas.scale > 0
    assert not image.isNull()
    assert image.width() > 250
    assert "altitude scale 1:1" in panel.status_label.text()
    assert panel.body_selector.count() == 1


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

    pan_before = QPoint(panel.canvas.pan.toPoint())
    start = QPoint(100, 100)
    finish = QPoint(140, 125)
    qtbot.mousePress(panel.canvas, Qt.MouseButton.RightButton, pos=start)
    qtbot.mouseMove(panel.canvas, finish)
    qtbot.mouseRelease(panel.canvas, Qt.MouseButton.RightButton, pos=finish)
    assert panel.canvas.pan.toPoint() != pan_before


def test_manual_orbit_wraps_freely_through_full_vertical_axis(qtbot) -> None:
    panel = CourseViewPanel()
    qtbot.addWidget(panel)
    panel.set_project(visual_project())
    panel.canvas.tilt_degrees = 80

    panel.canvas.orbit_by(450, 20)
    assert panel.canvas.yaw_degrees == pytest.approx(90)
    assert panel.canvas.tilt_degrees == pytest.approx(100)

    panel.canvas.orbit_by(0, 280)
    assert panel.canvas.tilt_degrees == pytest.approx(20)

    panel.canvas.orbit_by(0, -40)
    assert panel.canvas.tilt_degrees == pytest.approx(340)


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


def test_follow_ship_centers_live_marker_and_rotates_with_heading(qtbot) -> None:
    panel = CourseViewPanel()
    panel.resize(850, 600)
    qtbot.addWidget(panel)
    panel.show()
    panel.set_project(visual_project(), live_snapshot=live_snapshot(heading="135"))
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


def test_follow_waits_for_live_data_and_manual_navigation_stops_it(qtbot) -> None:
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
    assert not panel.follow_button.isChecked()
    assert not panel.canvas.follow_live

    qtbot.mouseClick(panel.follow_button, Qt.MouseButton.LeftButton)
    assert panel.canvas.follow_yaw_offset_degrees == 0
    qtbot.mouseClick(panel.top_button, Qt.MouseButton.LeftButton)
    assert not panel.follow_button.isChecked()
    assert panel.canvas.tilt_degrees == 0

    qtbot.mouseClick(panel.follow_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(panel.fit_button, Qt.MouseButton.LeftButton)
    assert not panel.follow_button.isChecked()


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
    old_scale = panel.canvas.scale
    waypoint = project.document.waypoints[0].section

    waypoint.set_value("Position", "0.01,-0.02 to 0.01,0.02")
    panel.refresh_project(project)

    assert panel.canvas.yaw_degrees == 31
    assert panel.canvas.tilt_degrees == 42
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
