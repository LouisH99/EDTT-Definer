from __future__ import annotations

from decimal import Decimal

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QFileDialog, QMessageBox

from edtt_definer.geometry import great_circle_distance_m
from edtt_definer.live import LiveResult, LiveState, LocationSnapshot
from edtt_definer.model import SectionType
from edtt_definer.parser import parse_text
from edtt_definer.project import AltitudeMode, ProjectData, StageMetadata, save_project
from edtt_definer.ui import MainWindow

from .test_parser import SAMPLE, WRAPPED_SAMPLE


def test_new_course_uses_authoring_defaults(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)

    assert window.project.document.event_info.get("MaxShipSize") == "N/A"
    assert window.event_form.max_ship_size.currentText() == "N/A"

    waypoint = window.project.document.waypoints[0].section
    window.refresh_navigation(waypoint.node_id)
    assert window.stage_form.current_altitude_mode() is AltitudeMode.MAXIMUM
    assert window.stage_form.maximum_altitude.text() == ""
    assert window.stage_form.altitude_locked.isChecked()

    added = window.add_waypoint()
    assert window.project.stage_metadata[added.node_id].altitude_mode is AltitudeMode.MAXIMUM
    assert window.project.stage_metadata[added.node_id].altitude_locked
    window.dirty = False


def test_add_duplicate_move_and_delete_waypoints(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    original_count = len(window.project.document.waypoints)
    added = window.add_waypoint()
    assert len(window.project.document.waypoints) == original_count + 1
    window.current_section_id = added.node_id
    window.duplicate_waypoint()
    assert len(window.project.document.waypoints) == original_count + 2
    window.move_waypoint(-1)
    window.delete_waypoint()
    assert len(window.project.document.waypoints) == original_count + 1
    window.dirty = False


def test_waypoint_names_follow_duplicate_move_and_delete(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window._set_project(ProjectData(document=parse_text(SAMPLE)), None, dirty=False)
    original = window.project.document.waypoints[0].section
    window.refresh_navigation(original.node_id)

    window.stage_form.waypoint_name.setText("Canyon Gate")

    assert window.navigation.currentItem().text() == "Waypoint 1 — Canyon Gate"
    assert window.project.stage_metadata[original.node_id].display_name == "Canyon Gate"
    assert "Canyon Gate" not in window.preview.toPlainText()

    window.duplicate_waypoint()
    duplicate = window._current_waypoint()
    assert duplicate is not None and duplicate.node_id != original.node_id
    assert window.project.stage_metadata[duplicate.node_id].display_name == "Canyon Gate (copy)"
    assert window.navigation.currentItem().text() == "Waypoint 2 — Canyon Gate (copy)"

    window.move_waypoint(-1)
    assert window.navigation.currentItem().text() == "Waypoint 1 — Canyon Gate (copy)"
    window.delete_waypoint()
    assert duplicate.node_id not in window.project.stage_metadata
    assert window.project.stage_metadata[original.node_id].display_name == "Canyon Gate"
    window.dirty = False


def test_altitude_modes_detect_and_generate_edcopilot_pair(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    document = parse_text(SAMPLE)
    waypoint = document.waypoints[0].section
    waypoint.set_value("Altitude", "250")
    waypoint.set_value("AltitudeProximity", "250")
    window._set_project(ProjectData(document=document), None, dirty=False)
    window.refresh_navigation(waypoint.node_id)

    assert window.stage_form.current_altitude_mode() is AltitudeMode.MAXIMUM
    assert window.stage_form.maximum_altitude.text() == "500"
    assert window.stage_form.altitude_locked.isChecked()

    window.stage_form.maximum_altitude.setText("501")
    assert waypoint.value("Altitude") == "250.5"
    assert waypoint.value("AltitudeProximity") == "250.5"

    target_index = window.stage_form.altitude_mode.findData(AltitudeMode.TARGET.value)
    window.stage_form.altitude_mode.setCurrentIndex(target_index)
    assert waypoint.value("Altitude") == "250.5"
    assert waypoint.value("AltitudeProximity") == "250.5"
    assert window.project.stage_metadata[waypoint.node_id].altitude_mode is AltitudeMode.TARGET

    window.stage_form.altitude.setText("400")
    window.stage_form.altitude_proximity.setText("100")
    maximum_index = window.stage_form.altitude_mode.findData(AltitudeMode.MAXIMUM.value)
    window.stage_form.altitude_mode.setCurrentIndex(maximum_index)
    assert window.stage_form.maximum_altitude.text() == "500"
    assert waypoint.value("Altitude") == "250"
    assert waypoint.value("AltitudeProximity") == "250"

    none_index = window.stage_form.altitude_mode.findData(AltitudeMode.NONE.value)
    window.stage_form.altitude_mode.setCurrentIndex(none_index)
    assert waypoint.value("Altitude") == ""
    assert waypoint.value("AltitudeProximity") == ""
    window.dirty = False


def test_capture_selected_and_capture_add(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    snapshot = LocationSnapshot(
        timestamp="2026-01-01T00:00:00Z",
        system="Sol",
        body="Earth",
        latitude=Decimal("1.234567"),
        longitude=Decimal("2.345678"),
        altitude=Decimal("120.5"),
        heading=Decimal("90"),
        flags=1 << 21,
        flag_has_lat_long=True,
    )
    window.live_result = LiveResult(LiveState.LIVE, snapshot, "ready")
    waypoint = window.project.document.waypoints[0].section
    window.current_section_id = waypoint.node_id
    window.capture_selected(None)
    assert waypoint.value("Position") == "1.234567,2.345678"
    assert waypoint.value("System") == "Sol"
    before = len(window.project.document.waypoints)
    window.capture_and_add()
    assert len(window.project.document.waypoints) == before + 1
    assert window.project.document.waypoints[-1].get("Position") == "1.234567,2.345678"
    window.dirty = False


def test_default_altitude_lock_protects_all_capture_paths(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    document = parse_text(SAMPLE)
    waypoint = document.waypoints[0].section
    waypoint.set_value("BodyRadius", "5411.771")
    waypoint.set_value("Altitude", "100")
    waypoint.set_value("AltitudeProximity", "20")
    window._set_project(ProjectData(document=document), None, dirty=False)
    window.refresh_navigation(waypoint.node_id)
    window.stage_form.position_mode.setCurrentText("Gate")
    snapshot = LocationSnapshot(
        system="Sol",
        body="Earth",
        latitude=Decimal("10.1"),
        longitude=Decimal("20.2"),
        altitude=Decimal("999"),
        heading=Decimal("90"),
        flags=1 << 21,
        flag_has_lat_long=True,
    )
    window.live_result = LiveResult(LiveState.LIVE, snapshot, "ready")

    window.capture_selected("a")
    snapshot.latitude = Decimal("10.2")
    snapshot.longitude = Decimal("20.3")
    window.capture_selected("b")
    window.calculate_gate_from_live()

    assert waypoint.value("Altitude") == "100"
    assert waypoint.value("AltitudeProximity") == "20"
    assert window.project.metadata_for(waypoint.node_id).altitude_locked

    before = len(window.project.document.waypoints)
    window.capture_and_add()
    added = window.project.document.waypoints[-1].section
    assert len(window.project.document.waypoints) == before + 1
    assert added.value("Altitude") == ""
    assert window.project.metadata_for(added.node_id).altitude_locked
    window.dirty = False


def test_unlocked_live_altitude_updates_target_but_not_maximum(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    document = parse_text(SAMPLE)
    waypoint = document.waypoints[0].section
    project = ProjectData(document=document)
    project.stage_metadata[waypoint.node_id] = StageMetadata(
        altitude_mode=AltitudeMode.NONE,
        altitude_locked=False,
    )
    window._set_project(project, None, dirty=False)
    window.refresh_navigation(waypoint.node_id)
    window.live_result = LiveResult(
        LiveState.LIVE,
        LocationSnapshot(
            latitude=Decimal("1.5"),
            longitude=Decimal("2.5"),
            altitude=Decimal("333"),
            flags=1 << 21,
            flag_has_lat_long=True,
        ),
        "ready",
    )

    window.capture_selected(None)
    assert waypoint.value("Altitude") == "333"
    assert waypoint.value("AltitudeProximity") == ""
    assert window.project.stage_metadata[waypoint.node_id].altitude_mode is AltitudeMode.TARGET

    waypoint.set_value("AltitudeProximity", "25")
    window.refresh_navigation(waypoint.node_id)
    window.live_result.snapshot.altitude = Decimal("444")
    window.capture_selected(None)
    assert waypoint.value("Altitude") == "444"
    assert waypoint.value("AltitudeProximity") == "25"

    waypoint.set_value("Altitude", "250")
    waypoint.set_value("AltitudeProximity", "250")
    window.project.stage_metadata[waypoint.node_id] = StageMetadata(
        altitude_mode=AltitudeMode.MAXIMUM,
        maximum_altitude="500",
        altitude_locked=False,
    )
    window.refresh_navigation(waypoint.node_id)
    window.live_result.snapshot.altitude = Decimal("900")
    window.capture_selected(None)

    assert waypoint.value("Altitude") == "250"
    assert waypoint.value("AltitudeProximity") == "250"
    window.dirty = False


def test_gate_endpoint_capture(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window.live_result = LiveResult(
        LiveState.LIVE,
        LocationSnapshot(
            latitude=Decimal("10.1"), longitude=Decimal("20.2"),
            flags=1 << 21, flag_has_lat_long=True,
        ),
        "ready",
    )
    waypoint = window.project.document.waypoints[0].section
    window.current_section_id = waypoint.node_id
    window.stage_form.position_mode.setCurrentText("Gate")
    window.capture_selected("a")
    assert " to " in waypoint.value("Position")
    window.live_result.snapshot.latitude = Decimal("10.2")
    window.live_result.snapshot.longitude = Decimal("20.3")
    window.capture_selected("b")
    assert waypoint.value("Position") == "10.1,20.2 to 10.2,20.3"
    window.dirty = False


def test_create_gate_from_live_heading(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    snapshot = LocationSnapshot(
        timestamp="2026-01-01T00:00:00Z",
        system="Gate System",
        body="Gate Body",
        latitude=Decimal("0"),
        longitude=Decimal("0"),
        heading=Decimal("0"),
        flags=1 << 21,
        flag_has_lat_long=True,
    )
    window.live_result = LiveResult(LiveState.LIVE, snapshot, "ready")
    waypoint = window.project.document.waypoints[0].section
    waypoint.set_value("BodyRadius", "5411.771")
    window.refresh_navigation(waypoint.node_id)
    window.stage_form.gate_width.setText("2000")
    window.stage_form.gate_forward_offset.setText("0")
    window._update_capture_buttons()

    assert window.stage_form.calculate_gate_button.isEnabled()
    window.calculate_gate_from_live()

    position = waypoint.value("Position")
    point_a, point_b = position.split(" to ")
    latitude_a, longitude_a = point_a.split(",")
    latitude_b, longitude_b = point_b.split(",")
    assert great_circle_distance_m(
        latitude_a,
        longitude_a,
        latitude_b,
        longitude_b,
        "5411.771",
    ) == pytest.approx(2000, abs=0.2)
    metadata = window.project.capture_metadata[waypoint.node_id]["calculated_gate"]
    assert metadata["center_latitude"] == "0.000000"
    assert metadata["center_longitude"] == "0.000000"
    assert metadata["heading"] == "0"
    assert metadata["width_m"] == "2000"
    assert metadata["forward_offset_m"] == "0"
    assert waypoint.value("System") == "Gate System"
    assert waypoint.value("Body") == "Gate Body"
    window.dirty = False


def test_discard_allows_opening_another_project(qtbot, monkeypatch, tmp_path) -> None:
    target = ProjectData()
    target.document.event_info.set("Name", "Opened project")
    project_path = tmp_path / "opened.edttc.json"
    save_project(target, project_path)

    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window.dirty = True
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard,
    )

    window.open_file(str(project_path))

    assert window.project_path == project_path
    assert window.project.document.event_info.get("Name") == "Opened project"
    assert not window.dirty


def test_open_action_shows_file_picker_after_discard(qtbot, monkeypatch) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window.dirty = True
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard,
    )
    picker_calls = []

    def show_picker(*args, **kwargs):
        picker_calls.append((args, kwargs))
        return "", ""

    monkeypatch.setattr(QFileDialog, "getOpenFileName", show_picker)

    window.open_action.trigger()

    assert len(picker_calls) == 1
    window.dirty = False


def test_discard_allows_window_to_close(qtbot, monkeypatch) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window.dirty = True
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard,
    )
    event = QCloseEvent()

    window.closeEvent(event)

    assert event.isAccepted()
    window.dirty = False


def test_open_wrapped_edcopilot_export(qtbot, monkeypatch, tmp_path) -> None:
    export_path = tmp_path / "legacy.export.txt"
    export_path.write_text(WRAPPED_SAMPLE, encoding="utf-8", newline="")
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window.dirty = True
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Discard,
    )

    window.open_file(str(export_path))

    assert window.project.document.event_info.get("Name") == "Legacy Race"
    assert [section.name for section in window.project.document.sections()] == ["eventinfo", "start", "end"]
    assert window.navigation.item(0).text() == "Event Info"
    assert not window.dirty


def test_end_editor_writes_end_instruction_and_extra_stage_fields(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window._set_project(ProjectData(document=parse_text(SAMPLE)), None, dirty=False)
    end = window.project.document.sections(SectionType.END)[0]

    window._show_section(end.node_id)
    assert window.stage_form.end_instructions_box.isVisibleTo(window.stage_form)
    assert not window.stage_form.next_instructions_box.isVisibleTo(window.stage_form)
    window.stage_form.end_instructions.set_values(["You made it."])
    window.stage_form.last_leg_message.setPlainText("Final leg")
    window.stage_form.max_speed.setText("100")
    window.stage_form.max_speed_warning_distance.setText("500,250")
    window.stage_form.max_speed_warning_instruction.setPlainText("Slow down")
    window.stage_form.apply()

    assert end.values("EndInstruction") == ["You made it."]
    assert end.values("NextInstruction") == []
    assert end.value("LastLegMessage") == "Final leg"
    assert end.value("MaxSpeed") == "100"
    assert end.value("MaxSpeedWarningDistance") == "500,250"
    assert end.value("MaxSpeedWarningInstruction") == "Slow down"
    window.dirty = False


def test_create_edcopilot_txt_action_writes_text_not_json(qtbot, monkeypatch, tmp_path) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window._set_project(ProjectData(document=parse_text(SAMPLE)), None, dirty=False)
    chosen_path = tmp_path / "race"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(chosen_path), "EDCoPilot import file (*.export.txt)"),
    )
    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *_args, **_kwargs: messages.append((_args, _kwargs)),
    )

    window.export_action.trigger()

    exported = tmp_path / "race.export.txt"
    content = exported.read_text(encoding="utf-8")
    assert exported.is_file()
    assert not content.lstrip().startswith("{")
    assert "[eventinfo]" in content
    assert "EndInstruction=Finished." in content
    assert messages
    window.dirty = False
