from __future__ import annotations

from decimal import Decimal

import pytest
from PySide6.QtCore import QEvent, QObject, QSettings, Qt, Signal
from PySide6.QtGui import QCloseEvent, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QSizePolicy,
)

from edtt_definer.geometry import calculate_gate, great_circle_distance_m
from edtt_definer.live import LiveResult, LiveState, LocationSnapshot
from edtt_definer.model import SectionType
from edtt_definer.parser import parse_text
from edtt_definer.project import AltitudeMode, ProjectData, StageMetadata, save_project
from edtt_definer.theme import ThemeMode
from edtt_definer.conversion import RaceVehicle, build_vehicle_conversion
from edtt_definer.ui import (
    APP_DISPLAY_NAME,
    VALIDATION_GROUP_ROLE,
    MainWindow,
    RaceConversionDialog,
)
from edtt_definer.updates import ReleaseInfo

from .test_parser import SAMPLE, WRAPPED_SAMPLE


class FakeEdsmClient(QObject):
    succeeded = Signal(str, str, str)
    failed = Signal(str, str, str)

    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, str]] = []

    def lookup(self, system: str, body: str) -> bool:
        self.calls.append((system, body))
        return True


class FakeUpdateClient(QObject):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, starts: bool = True):
        super().__init__()
        self.starts = starts
        self.calls = 0

    def check(self) -> bool:
        self.calls += 1
        return self.starts


def test_patch_release_version_is_visible() -> None:
    assert APP_DISPLAY_NAME == "EDTT Definer v0.6.7"


def test_new_course_uses_authoring_defaults(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)

    assert window.project.document.event_info.get("MaxShipSize") == "N/A"
    assert window.event_form.max_ship_size.currentText() == "N/A"
    assert len(window.project.document.waypoints) == 1

    waypoint = window.project.document.waypoints[0].section
    window.refresh_navigation(waypoint.node_id)
    assert window.stage_form.current_altitude_mode() is AltitudeMode.MAXIMUM
    assert window.stage_form.maximum_altitude.text() == ""
    assert window.stage_form.altitude_locked.isChecked()

    added = window.add_waypoint()
    assert window.project.stage_metadata[added.node_id].altitude_mode is AltitudeMode.MAXIMUM
    assert window.project.stage_metadata[added.node_id].altitude_locked
    window.dirty = False


def test_new_waypoints_inherit_previous_maximum_height(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    first = window.project.document.waypoints[0].section
    window.refresh_navigation(first.node_id)
    window.stage_form.maximum_altitude.setText("500")

    second = window.add_waypoint()
    second_metadata = window.project.stage_metadata[second.node_id]
    assert second_metadata.altitude_mode is AltitudeMode.MAXIMUM
    assert second_metadata.maximum_altitude == "500"
    assert second_metadata.altitude_locked
    assert second.value("Altitude") == "250"
    assert second.value("AltitudeProximity") == "250"

    window.stage_form.maximum_altitude.setText("750")
    third = window.add_waypoint()
    assert window.project.stage_metadata[third.node_id].maximum_altitude == "750"
    assert third.value("Altitude") == "375"
    assert third.value("AltitudeProximity") == "375"

    target_index = window.stage_form.altitude_mode.findData(AltitudeMode.TARGET.value)
    window.stage_form.altitude_mode.setCurrentIndex(target_index)
    fourth = window.add_waypoint()
    assert window.project.stage_metadata[fourth.node_id].altitude_mode is AltitudeMode.MAXIMUM
    assert window.project.stage_metadata[fourth.node_id].maximum_altitude == ""
    assert fourth.value("Altitude") == ""
    assert fourth.value("AltitudeProximity") == ""
    window.dirty = False


def test_capture_and_add_inherits_previous_maximum_height(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    first = window.project.document.waypoints[0].section
    window.refresh_navigation(first.node_id)
    window.stage_form.maximum_altitude.setText("500")
    window.live_result = LiveResult(
        LiveState.LIVE,
        LocationSnapshot(
            system="Sol",
            body="Earth",
            latitude=Decimal("1.25"),
            longitude=Decimal("2.5"),
            altitude=Decimal("999"),
            flags=1 << 21,
            flag_has_lat_long=True,
        ),
        "ready",
    )

    window.capture_and_add()
    added = window.project.document.waypoints[-1].section

    assert added.value("Position") == "1.25,2.5"
    assert added.value("Altitude") == "250"
    assert added.value("AltitudeProximity") == "250"
    assert window.project.stage_metadata[added.node_id].maximum_altitude == "500"
    assert window.project.stage_metadata[added.node_id].altitude_locked
    window.dirty = False


def test_new_waypoint_inherits_maximum_inferred_from_import(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    document = parse_text(SAMPLE)
    previous = document.waypoints[0].section
    previous.set_value("Altitude", "125")
    previous.set_value("AltitudeProximity", "125")
    window._set_project(ProjectData(document=document), None, dirty=False)

    added = window.add_waypoint()

    assert window.project.stage_metadata[added.node_id].maximum_altitude == "250"
    assert added.value("Altitude") == "125"
    assert added.value("AltitudeProximity") == "125"
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


def test_final_waypoint_can_be_deleted_and_added_again(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    project = ProjectData(document=parse_text(SAMPLE))
    waypoint = project.document.waypoints[0].section
    project.stage_metadata[waypoint.node_id] = StageMetadata(display_name="Only gate")
    project.capture_metadata[waypoint.node_id] = {"heading": "90"}
    window._set_project(project, None, dirty=False)
    window.refresh_navigation(waypoint.node_id)

    window.delete_waypoint()

    assert not window.project.document.waypoints
    assert waypoint.node_id not in window.project.stage_metadata
    assert waypoint.node_id not in window.project.capture_metadata
    assert window.project.document.end is not None
    assert window.current_section_id == window.project.document.end.section.node_id
    assert window.navigation.currentItem().text() == "End"
    assert "[waypoint]" not in window.preview.toPlainText().lower()

    added = window.add_waypoint()
    assert len(window.project.document.waypoints) == 1
    assert window.project.document.waypoints[0].section is added
    assert window.navigation.currentItem().text() == "Waypoint 1"
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


def test_multi_select_waypoints_duplicate_move_and_delete_as_group(qtbot, monkeypatch) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window._set_project(ProjectData(document=parse_text(SAMPLE)), None, dirty=False)
    assert window.navigation.selectionMode() is QAbstractItemView.SelectionMode.ExtendedSelection
    first = window.project.document.waypoints[0].section
    second = window.add_waypoint()
    third = window.add_waypoint()
    first.set_value("NextInstruction", "First")
    second.set_value("NextInstruction", "Second")
    third.set_value("NextInstruction", "Third")
    window.project.stage_metadata[first.node_id] = StageMetadata(
        display_name="Alpha", separator_before="Lap 1"
    )
    window.project.stage_metadata[second.node_id] = StageMetadata(display_name="Bravo")
    window.project.capture_metadata[first.node_id] = {"heading": "10"}
    window.project.capture_metadata[second.node_id] = {"heading": "20"}
    window.refresh_navigation(first.node_id)

    waypoint_items = [
        window.navigation.item(row)
        for row in range(window.navigation.count())
        if (
            window.project.document.section_by_id(
                window.navigation.item(row).data(Qt.ItemDataRole.UserRole)
            ) is not None
            and window.project.document.section_by_id(
                window.navigation.item(row).data(Qt.ItemDataRole.UserRole)
            ).section_type is SectionType.WAYPOINT
        )
    ]
    window.navigation.setCurrentItem(waypoint_items[0])
    waypoint_items[1].setSelected(True)

    window.duplicate_waypoint()

    assert [
        waypoint.get("NextInstruction") for waypoint in window.project.document.waypoints
    ] == ["First", "Second", "First", "Second", "Third"]
    selected_duplicates = window._selected_waypoints()
    assert len(selected_duplicates) == 2
    assert [
        window.project.stage_metadata[section.node_id].display_name
        for section in selected_duplicates
    ] == ["Alpha (copy)", "Bravo (copy)"]
    assert [
        window.project.stage_metadata[section.node_id].separator_before
        for section in selected_duplicates
    ] == ["Lap 2", ""]
    assert [
        window.project.capture_metadata[section.node_id]["heading"]
        for section in selected_duplicates
    ] == ["10", "20"]

    window.move_waypoint(1)
    assert [
        waypoint.get("NextInstruction") for waypoint in window.project.document.waypoints
    ] == ["First", "Second", "Third", "First", "Second"]
    assert [
        section.node_id for section in window._selected_waypoints()
    ] == [section.node_id for section in selected_duplicates]

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Cancel,
    )
    window.delete_waypoint()
    assert len(window.project.document.waypoints) == 5

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    window.delete_waypoint()
    assert [
        waypoint.get("NextInstruction") for waypoint in window.project.document.waypoints
    ] == ["First", "Second", "Third"]
    assert all(
        section.node_id not in window.project.stage_metadata
        for section in selected_duplicates
    )
    assert all(
        section.node_id not in window.project.capture_metadata
        for section in selected_duplicates
    )
    window.dirty = False


def test_new_waypoint_is_inserted_after_active_waypoint_and_inherits_it(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    first = window.project.document.waypoints[0].section
    second = window.add_waypoint()
    window.stage_form.maximum_altitude.setText("400")
    third = window.add_waypoint()
    window.stage_form.maximum_altitude.setText("900")

    window.refresh_navigation(second.node_id)
    inserted = window.add_waypoint()

    assert [
        waypoint.section.node_id
        for waypoint in window.project.document.waypoints
    ] == [first.node_id, second.node_id, inserted.node_id, third.node_id]
    assert window.project.metadata_for(inserted.node_id).maximum_altitude == "400"
    assert inserted.value("Altitude") == "200"
    assert inserted.value("AltitudeProximity") == "200"
    assert window.current_section_id == inserted.node_id
    window.dirty = False


def test_editor_only_section_divider_can_be_added_renamed_and_removed(
    qtbot, monkeypatch,
) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window._set_project(ProjectData(document=parse_text(SAMPLE)), None, dirty=False)
    waypoint = window.project.document.waypoints[0].section
    original_export = window.preview.toPlainText()
    window.refresh_navigation(waypoint.node_id)
    answers = iter((("Lap 1", True), ("Canyon section", True)))
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        lambda *_args, **_kwargs: next(answers),
    )

    window.edit_section_separator()

    assert window.project.metadata_for(waypoint.node_id).separator_before == "Lap 1"
    divider = next(
        window.navigation.item(row)
        for row in range(window.navigation.count())
        if window.navigation.item(row).data(Qt.ItemDataRole.UserRole) is None
    )
    assert divider.text() == "▾ ─── Lap 1 ───"
    assert divider.flags() & Qt.ItemFlag.ItemIsSelectable
    assert divider.font().bold()
    assert window.navigation.currentItem() is divider
    assert window.waypoint_buttons["Delete"].text() == "Delete section"
    assert window.preview.toPlainText() == original_export

    window.edit_section_separator()
    assert window.project.metadata_for(waypoint.node_id).separator_before == "Canyon section"
    window.stage_form.waypoint_name.setText("Gate A")
    assert window.project.metadata_for(waypoint.node_id).separator_before == "Canyon section"
    assert "Canyon section" in window.navigation.currentItem().text()

    waypoint_count = len(window.project.document.waypoints)
    window.delete_waypoint()
    assert window.project.metadata_for(waypoint.node_id).separator_before == ""
    assert len(window.project.document.waypoints) == waypoint_count
    assert all(
        window.navigation.item(row).data(Qt.ItemDataRole.UserRole) is not None
        for row in range(window.navigation.count())
    )
    assert window.preview.toPlainText() == original_export
    window.dirty = False


def test_section_dividers_collapse_expand_and_reveal_external_selection(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window._set_project(ProjectData(document=parse_text(SAMPLE)), None, dirty=False)
    first = window.project.document.waypoints[0].section
    second = window.add_waypoint()
    third = window.add_waypoint()
    fourth = window.add_waypoint()
    window.project.metadata_for(first.node_id).separator_before = "Lap 1"
    window.project.metadata_for(third.node_id).separator_before = "Lap 2"
    window.refresh_navigation(first.node_id, select_divider_for=first.node_id)
    window.dirty = False

    first_divider = window.navigation.currentItem()
    assert first_divider is not None
    assert "Lap 1" in first_divider.text()
    window._navigation_item_double_clicked(first_divider)

    waypoint_items = {
        window.navigation.item(row).data(Qt.ItemDataRole.UserRole):
        window.navigation.item(row)
        for row in range(window.navigation.count())
        if window.navigation.item(row).data(Qt.ItemDataRole.UserRole)
    }
    assert window.navigation.currentItem().text().startswith("▸")
    assert waypoint_items[first.node_id].isHidden()
    assert waypoint_items[second.node_id].isHidden()
    assert not waypoint_items[third.node_id].isHidden()
    assert not waypoint_items[fourth.node_id].isHidden()
    assert not window.dirty

    window._select_section_from_course(second.node_id)

    waypoint_items = {
        window.navigation.item(row).data(Qt.ItemDataRole.UserRole):
        window.navigation.item(row)
        for row in range(window.navigation.count())
        if window.navigation.item(row).data(Qt.ItemDataRole.UserRole)
    }
    assert window.current_section_id == second.node_id
    assert first.node_id not in window._collapsed_route_sections
    assert not waypoint_items[first.node_id].isHidden()
    assert not waypoint_items[second.node_id].isHidden()
    assert not window.dirty


def test_selected_divider_duplicates_its_complete_section(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window._set_project(ProjectData(document=parse_text(SAMPLE)), None, dirty=False)
    first = window.project.document.waypoints[0].section
    second = window.add_waypoint()
    third = window.add_waypoint()
    fourth = window.add_waypoint()
    for section, instruction, name, separator in zip(
        (first, second, third, fourth),
        ("First", "Second", "Third", "Fourth"),
        ("Alpha", "Bravo", "Charlie", "Delta"),
        ("Lap 1", "", "Lap 2", ""),
        strict=True,
    ):
        section.set_value("NextInstruction", instruction)
        window.project.stage_metadata[section.node_id] = StageMetadata(
            display_name=name,
            separator_before=separator,
        )
        window.project.capture_metadata[section.node_id] = {
            "heading": instruction,
        }
    window.refresh_navigation(first.node_id, select_divider_for=first.node_id)

    assert window.waypoint_buttons["Duplicate"].text() == "Duplicate section"
    assert window.waypoint_buttons["Duplicate"].isEnabled()
    window.duplicate_waypoint()

    route = [waypoint.section for waypoint in window.project.document.waypoints]
    assert [section.value("NextInstruction") for section in route] == [
        "First", "Second", "First", "Second", "Third", "Fourth",
    ]
    duplicate_first, duplicate_second = route[2:4]
    assert window.project.metadata_for(duplicate_first.node_id).separator_before == "Lap 3"
    assert window.project.metadata_for(duplicate_first.node_id).display_name == "Alpha (copy)"
    assert window.project.metadata_for(duplicate_second.node_id).display_name == "Bravo (copy)"
    assert window.project.capture_metadata[duplicate_first.node_id]["heading"] == "First"
    assert window.project.capture_metadata[duplicate_second.node_id]["heading"] == "Second"
    assert [section.node_id for section in window._selected_waypoints()] == [
        duplicate_first.node_id,
        duplicate_second.node_id,
    ]
    assert "Lap 1" not in window.preview.toPlainText()
    assert "Lap 3" not in window.preview.toPlainText()
    window.dirty = False


def test_reverse_selected_waypoints_preserves_data_and_divider_positions(
    qtbot, monkeypatch,
) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window._set_project(ProjectData(document=parse_text(SAMPLE)), None, dirty=False)
    first = window.project.document.waypoints[0].section
    second = window.add_waypoint()
    third = window.add_waypoint()
    for section, instruction, name, separator in zip(
        (first, second, third),
        ("First", "Second", "Third"),
        ("Alpha", "Bravo", "Charlie"),
        ("Lap 1", "", "Technical section"),
        strict=True,
    ):
        section.set_value("NextInstruction", instruction)
        window.project.stage_metadata[section.node_id] = StageMetadata(
            display_name=name,
            maximum_altitude=f"{len(name)}00",
            separator_before=separator,
        )
        window.project.capture_metadata[section.node_id] = {"name": name}
    window.refresh_navigation(first.node_id)
    waypoint_items = [
        window.navigation.item(row)
        for row in range(window.navigation.count())
        if (
            window.project.document.section_by_id(
                window.navigation.item(row).data(Qt.ItemDataRole.UserRole)
            ) is not None
            and window.project.document.section_by_id(
                window.navigation.item(row).data(Qt.ItemDataRole.UserRole)
            ).section_type is SectionType.WAYPOINT
        )
    ]
    window.navigation.setCurrentItem(waypoint_items[0])
    for item in waypoint_items[1:]:
        item.setSelected(True)
    confirmations = []
    monkeypatch.setattr(
        window,
        "_confirm_reverse_waypoints",
        lambda sections: confirmations.append([section.node_id for section in sections]) or True,
    )

    window.reverse_selected_waypoints()

    reversed_sections = [
        waypoint.section for waypoint in window.project.document.waypoints
    ]
    assert [section.value("NextInstruction") for section in reversed_sections] == [
        "Third", "Second", "First"
    ]
    assert [
        window.project.metadata_for(section.node_id).display_name
        for section in reversed_sections
    ] == ["Charlie", "Bravo", "Alpha"]
    assert [
        window.project.metadata_for(section.node_id).separator_before
        for section in reversed_sections
    ] == ["Lap 1", "", "Technical section"]
    assert [
        window.project.capture_metadata[section.node_id]["name"]
        for section in reversed_sections
    ] == ["Charlie", "Bravo", "Alpha"]
    assert confirmations == [[first.node_id, second.node_id, third.node_id]]
    assert len(window._selected_waypoints()) == 3
    assert "Lap 1" not in window.preview.toPlainText()
    assert "Technical section" not in window.preview.toPlainText()
    window.dirty = False


def test_reverse_rejects_non_contiguous_waypoint_selection(qtbot, monkeypatch) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window._set_project(ProjectData(document=parse_text(SAMPLE)), None, dirty=False)
    first = window.project.document.waypoints[0].section
    second = window.add_waypoint()
    third = window.add_waypoint()
    window.refresh_navigation(first.node_id)
    waypoint_items = [
        window.navigation.item(row)
        for row in range(window.navigation.count())
        if (
            window.project.document.section_by_id(
                window.navigation.item(row).data(Qt.ItemDataRole.UserRole)
            ) is not None
            and window.project.document.section_by_id(
                window.navigation.item(row).data(Qt.ItemDataRole.UserRole)
            ).section_type is SectionType.WAYPOINT
        )
    ]
    window.navigation.setCurrentItem(waypoint_items[0])
    waypoint_items[2].setSelected(True)
    messages = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda *_args, **_kwargs: messages.append(_args[-1]),
    )
    monkeypatch.setattr(
        window,
        "_confirm_reverse_waypoints",
        lambda _sections: pytest.fail("confirmation must not open for a scattered selection"),
    )

    window.reverse_selected_waypoints()

    assert [
        waypoint.section.node_id for waypoint in window.project.document.waypoints
    ] == [first.node_id, second.node_id, third.node_id]
    assert messages and "consecutive" in messages[0]
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


def test_stale_last_known_marker_cannot_be_captured(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    window.live_stale_timer.stop()
    qtbot.addWidget(window)
    waypoint = window.project.document.waypoints[0].section
    waypoint.set_value("BodyRadius", "6371")
    window.refresh_navigation(waypoint.node_id)
    snapshot = LocationSnapshot(
        system="Sol",
        body="Earth",
        latitude=Decimal("12.3"),
        longitude=Decimal("45.6"),
        flags=1 << 21,
        flag_has_lat_long=True,
    )
    window._display_live_result(LiveResult(LiveState.LIVE, snapshot, "Live"))
    window._mark_live_stale()
    original_position = waypoint.value("Position")

    assert window.live_result.state is LiveState.STALE
    assert not window.capture_button.isEnabled()
    assert not window.capture_add_button.isEnabled()
    assert not window.stage_form.capture_a_button.isEnabled()
    assert not window.stage_form.capture_b_button.isEnabled()

    window.capture_selected(None)
    window.capture_selected("a")
    window.capture_selected("b")
    assert waypoint.value("Position") == original_position


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


def test_gate_endpoint_b_can_be_captured_before_a(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window.live_result = LiveResult(
        LiveState.LIVE,
        LocationSnapshot(
            latitude=Decimal("10.1"),
            longitude=Decimal("20.2"),
            flags=1 << 21,
            flag_has_lat_long=True,
        ),
        "ready",
    )
    waypoint = window.project.document.waypoints[0].section
    window.current_section_id = waypoint.node_id
    window.stage_form.position_mode.setCurrentText("Gate")

    window.capture_selected("b")

    assert waypoint.value("Position") == ", to 10.1,20.2"
    assert window.stage_form.lat_a.text() == ""
    assert window.stage_form.lon_a.text() == ""
    assert window.stage_form.lat_b.text() == "10.1"
    assert window.stage_form.lon_b.text() == "20.2"
    assert any(
        issue.section_id == waypoint.node_id and issue.field == "Position"
        for issue in window.validation_issues
    )

    window.live_result.snapshot.latitude = Decimal("10.2")
    window.live_result.snapshot.longitude = Decimal("20.3")
    window.capture_selected("a")

    assert waypoint.value("Position") == "10.2,20.3 to 10.1,20.2"
    assert not any(
        issue.section_id == waypoint.node_id and issue.field == "Position"
        for issue in window.validation_issues
    )
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


def test_manual_gate_distance_updates_from_endpoints_and_radius(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    waypoint = window.project.document.waypoints[0].section
    window.refresh_navigation(waypoint.node_id)
    gate = calculate_gate("0", "0", "0", "2000", "5411.771")

    window.stage_form.position_mode.setCurrentText("Gate")
    window.stage_form.body_radius.setText("5411.771")
    window.stage_form.lat_a.setText(gate.latitude_a)
    window.stage_form.lon_a.setText(gate.longitude_a)
    window.stage_form.lat_b.setText(gate.latitude_b)
    window.stage_form.lon_b.setText(gate.longitude_b)

    assert "2.000 km" in window.stage_form.gate_distance_label.text()
    assert "2,000.0 m" in window.stage_form.gate_distance_label.text()
    window.stage_form.body_radius.setText("")
    assert window.stage_form.gate_distance_label.text().endswith("—")
    window.dirty = False


def test_local_radius_autofill_preserves_existing_values(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    waypoint = window.project.document.waypoints[0].section
    window.refresh_navigation(waypoint.node_id)
    snapshot = LocationSnapshot(
        system="Radius System",
        body="Radius Body",
        body_radius_km=Decimal("5411.771234"),
        body_radius_source="Journal",
        latitude=Decimal("1"),
        longitude=Decimal("2"),
        flags=1 << 21,
        flag_has_lat_long=True,
    )

    window._consider_radius_for_selected(snapshot)

    assert waypoint.value("BodyRadius") == "5411.771234"
    assert window.stage_form.body_radius_source.text() == "Source: Journal"
    assert window.project.capture_metadata[waypoint.node_id]["body_radius_source"] == "Journal"

    waypoint.set_value("BodyRadius", "999")
    window.refresh_navigation(waypoint.node_id)
    snapshot.body_radius_km = Decimal("1234")
    window._consider_radius_for_selected(snapshot)
    assert waypoint.value("BodyRadius") == "999"
    window.dirty = False


def test_edsm_fallback_asks_permission_and_applies_async_result(
    qtbot, monkeypatch, tmp_path
) -> None:
    fake = FakeEdsmClient()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("edsm_radius_permission", "ask")
    window = MainWindow(edsm_client=fake, settings=settings)
    window.poll_timer.stop()
    qtbot.addWidget(window)
    waypoint = window.project.document.waypoints[0].section
    waypoint.set_value("System", "Fallback System")
    waypoint.set_value("Body", "Fallback System 3 a")
    window.refresh_navigation(waypoint.node_id)
    monkeypatch.setattr(window, "_ask_edsm_permission", lambda *_args: "once")

    window.find_radius_for_selected()

    assert fake.calls == [("Fallback System", "Fallback System 3 a")]
    assert waypoint.value("BodyRadius") == ""

    fake.succeeded.emit("Fallback System", "Fallback System 3 a", "3123.456")

    assert waypoint.value("BodyRadius") == "3123.456"
    assert window.stage_form.body_radius_source.text() == "Source: EDSM"
    assert window.project.capture_metadata[waypoint.node_id]["body_radius_source"] == "EDSM"
    cached = window.live_reader.radius_cache.get(
        system="Fallback System", body="Fallback System 3 a"
    )
    assert cached is not None and cached.origin == "edsm"
    window.dirty = False


def test_edsm_not_now_is_not_reprompted_automatically(qtbot, monkeypatch, tmp_path) -> None:
    fake = FakeEdsmClient()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(edsm_client=fake, settings=settings)
    window.poll_timer.stop()
    qtbot.addWidget(window)
    waypoint = window.project.document.waypoints[0].section
    window.refresh_navigation(waypoint.node_id)
    prompts = []
    monkeypatch.setattr(
        window,
        "_ask_edsm_permission",
        lambda system, body: prompts.append((system, body)) or "not_now",
    )
    snapshot = LocationSnapshot(
        system="Prompt System",
        body="Prompt Body",
        latitude=Decimal("1"),
        longitude=Decimal("2"),
        flags=1 << 21,
        flag_has_lat_long=True,
    )

    window._consider_radius_for_selected(snapshot)
    window._consider_radius_for_selected(snapshot)

    assert prompts == [("Prompt System", "Prompt Body")]
    assert fake.calls == []
    assert waypoint.value("BodyRadius") == ""
    window.dirty = False


def test_edsm_permission_prompt_blocks_reentrant_live_updates(
    qtbot, monkeypatch, tmp_path,
) -> None:
    fake = FakeEdsmClient()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(edsm_client=fake, settings=settings)
    window.poll_timer.stop()
    qtbot.addWidget(window)
    waypoint = window.project.document.waypoints[0].section
    window.refresh_navigation(waypoint.node_id)
    snapshot = LocationSnapshot(
        system="Reentrant System",
        body="Reentrant Body",
        latitude=Decimal("1"),
        longitude=Decimal("2"),
        flags=1 << 21,
        flag_has_lat_long=True,
    )
    prompts = []

    def ask_permission(system: str, body: str) -> str:
        prompts.append((system, body))
        window._consider_radius_for_selected(snapshot)
        window._consider_radius_for_selected(snapshot)
        return "not_now"

    monkeypatch.setattr(window, "_ask_edsm_permission", ask_permission)

    window._consider_radius_for_selected(snapshot)

    assert prompts == [("Reentrant System", "Reentrant Body")]
    assert fake.calls == []
    assert not window._edsm_permission_prompt_active
    window.dirty = False


def test_edsm_always_permission_is_remembered(qtbot, monkeypatch, tmp_path) -> None:
    fake = FakeEdsmClient()
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(edsm_client=fake, settings=settings)
    window.poll_timer.stop()
    qtbot.addWidget(window)
    waypoint = window.project.document.waypoints[0].section
    waypoint.set_value("System", "Remember System")
    waypoint.set_value("Body", "Remember Body")
    window.refresh_navigation(waypoint.node_id)
    monkeypatch.setattr(window, "_ask_edsm_permission", lambda *_args: "always")

    window.find_radius_for_selected()

    assert fake.calls == [("Remember System", "Remember Body")]
    assert settings.value("edsm_radius_permission", "", str) == "always"
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


def test_instruction_editor_is_one_free_form_multiline_box(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window._set_project(ProjectData(document=parse_text(SAMPLE)), None, dirty=False)
    waypoint = window.project.document.waypoints[0].section

    window._show_section(waypoint.node_id)
    editor = window.stage_form.next_instructions

    assert editor.layout().count() == 2
    assert (
        editor.layout().itemAt(0).layout().itemAt(0).widget()
        is editor.terse_button
    )
    assert editor.layout().itemAt(1).widget() is editor.edit
    assert editor.terse_button.text() == "Insert Terse:"
    assert editor.edit.toPlainText() == "Terse:Turn left."

    editor.edit.setPlainText(
        "Climb above the ridge.\n\nTerse:Turn right at the next gate."
    )

    assert waypoint.values("NextInstruction") == [
        "Climb above the ridge.",
        "Terse:Turn right at the next gate.",
    ]
    assert "NextInstruction=Climb above the ridge." in window.preview.toPlainText()
    assert (
        "NextInstruction=Terse:Turn right at the next gate."
        in window.preview.toPlainText()
    )
    window.dirty = False


def test_instruction_editor_terse_button_handles_line_selection_and_empty_text(
    qtbot,
) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window._set_project(ProjectData(document=parse_text(SAMPLE)), None, dirty=False)
    waypoint = window.project.document.waypoints[0].section
    window._show_section(waypoint.node_id)
    editor = window.stage_form.next_instructions

    editor.edit.setPlainText("First instruction\nSecond instruction\nTerse:Already short")
    cursor = editor.edit.textCursor()
    cursor.setPosition(editor.edit.toPlainText().index("Second") + 3)
    editor.edit.setTextCursor(cursor)
    qtbot.mouseClick(editor.terse_button, Qt.MouseButton.LeftButton)
    assert editor.edit.toPlainText() == (
        "First instruction\nTerse:Second instruction\nTerse:Already short"
    )

    cursor = editor.edit.textCursor()
    cursor.select(QTextCursor.SelectionType.Document)
    editor.edit.setTextCursor(cursor)
    qtbot.mouseClick(editor.terse_button, Qt.MouseButton.LeftButton)
    assert editor.edit.toPlainText() == (
        "Terse:First instruction\nTerse:Second instruction\nTerse:Already short"
    )
    assert waypoint.values("NextInstruction") == [
        "Terse:First instruction",
        "Terse:Second instruction",
        "Terse:Already short",
    ]

    editor.edit.clear()
    qtbot.mouseClick(editor.terse_button, Qt.MouseButton.LeftButton)
    assert editor.edit.toPlainText() == "Terse:"
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


def test_vehicle_mismatch_requires_confirmation_before_export(
    qtbot, monkeypatch, tmp_path
) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    window.update_timer.stop()
    qtbot.addWidget(window)
    window._set_project(ProjectData(document=parse_text(SAMPLE)), None, dirty=False)
    window.project.document.event_info.set("RaceType", "FIGHTER")
    confirmations = []
    picker_calls = []
    monkeypatch.setattr(
        window,
        "_confirm_vehicle_mismatch_export",
        lambda mismatch: confirmations.append(mismatch) or False,
    )
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: picker_calls.append(True) or (str(tmp_path / "race"), ""),
    )

    window.export_file()

    assert len(confirmations) == 1
    assert confirmations[0].race_type == "FIGHTER"
    assert confirmations[0].player_state == "Ship"
    assert picker_calls == []
    assert not (tmp_path / "race.export.txt").exists()
    window.dirty = False


def test_confirmed_vehicle_mismatch_can_be_exported(qtbot, monkeypatch, tmp_path) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    window.update_timer.stop()
    qtbot.addWidget(window)
    window._set_project(ProjectData(document=parse_text(SAMPLE)), None, dirty=False)
    window.project.document.event_info.set("RaceType", "FIGHTER")
    output = tmp_path / "confirmed.export.txt"
    monkeypatch.setattr(window, "_confirm_vehicle_mismatch_export", lambda _mismatch: True)
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(output), "EDCoPilot import file (*.export.txt)"),
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: None)

    window.export_file()

    assert output.is_file()
    assert "RaceType=FIGHTER" in output.read_text(encoding="utf-8")
    window.dirty = False


def test_flying_start_choices_only_appear_for_start_and_first_waypoint(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    window.update_timer.stop()
    qtbot.addWidget(window)
    document = window.project.document

    def choices() -> list[str]:
        combo = window.stage_form.player_state
        return [combo.itemText(index) for index in range(combo.count())]

    window.refresh_navigation(document.start.section.node_id)
    assert "ShipFlyingStart" in choices()
    assert "FighterFlyingStart" in choices()

    first = document.waypoints[0].section
    window.refresh_navigation(first.node_id)
    assert "ShipFlyingStart" in choices()
    assert "FighterFlyingStart" in choices()

    second = window.add_waypoint()
    assert "ShipFlyingStart" not in choices()
    assert "FighterFlyingStart" not in choices()

    window.refresh_navigation(document.end.section.node_id)
    assert "ShipFlyingStart" not in choices()
    assert "FighterFlyingStart" not in choices()

    second.set_value("PlayerState", "FighterFlyingStart")
    window.refresh_navigation(second.node_id)
    assert window.stage_form.player_state.currentText() == "FighterFlyingStart"
    assert "FighterFlyingStart" not in choices()
    window.dirty = False


def test_gate_can_be_converted_to_centered_point_without_changing_stage_data(
    qtbot, monkeypatch
) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    waypoint = window.project.document.waypoints[0].section
    waypoint.set_value("System", "Midpoint System")
    waypoint.set_value("Body", "Midpoint Body")
    waypoint.set_value("Position", "0,-1 to 0,1")
    waypoint.set_value("Proximity", "125")
    waypoint.set_value("Altitude", "250")
    waypoint.set_value("AltitudeProximity", "250")
    waypoint.set_values("NextInstruction", ["Keep going"])
    window.project.stage_metadata[waypoint.node_id] = StageMetadata(
        display_name="Center Gate",
        altitude_mode=AltitudeMode.MAXIMUM,
        maximum_altitude="500",
    )
    window.refresh_navigation(waypoint.node_id)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    window.convert_gate_to_point()

    assert waypoint.value("Position") == "0.000000,0.000000"
    assert waypoint.value("Proximity") == "125"
    assert waypoint.value("Altitude") == "250"
    assert waypoint.value("AltitudeProximity") == "250"
    assert waypoint.values("NextInstruction") == ["Keep going"]
    assert window.project.stage_metadata[waypoint.node_id].display_name == "Center Gate"
    assert len(window.project.document.waypoints) == 1
    assert window.stage_form.position_mode.currentText() == "Point"
    conversion = window.project.capture_metadata[waypoint.node_id]["gate_midpoint_conversion"]
    assert conversion["source_position"] == "0,-1 to 0,1"
    assert conversion["latitude"] == "0.000000"
    assert conversion["longitude"] == "0.000000"
    assert "Position=0.000000,0.000000" in window.preview.toPlainText()
    window.dirty = False


def test_gate_conversion_requires_complete_non_antipodal_endpoints(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    waypoint = window.project.document.waypoints[0].section
    window.refresh_navigation(waypoint.node_id)
    window.stage_form.position_mode.setCurrentText("Gate")
    window.stage_form.lat_a.setText("0")
    window.stage_form.lon_a.setText("0")
    window.stage_form.lat_b.setText("0")

    assert not window.stage_form.convert_gate_button.isEnabled()

    window.stage_form.lon_b.setText("1")
    assert window.stage_form.convert_gate_button.isEnabled()

    window.stage_form.lon_b.setText("180")
    assert not window.stage_form.convert_gate_button.isEnabled()
    window.dirty = False


def test_gate_conversion_button_is_compact_and_next_to_mode_selector(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    layout = window.stage_form.position_box.layout()
    index = layout.indexOf(window.stage_form.convert_gate_button)

    assert layout.getItemPosition(index) == (0, 3, 1, 2)
    assert (
        window.stage_form.convert_gate_button.sizePolicy().horizontalPolicy()
        is QSizePolicy.Policy.Maximum
    )
    assert window.stage_form.convert_gate_button.text() == "Gate → center point"
    window.dirty = False


def test_collapsible_sections_have_defaults_counts_and_persist(qtbot, tmp_path) -> None:
    settings_path = tmp_path / "settings.ini"
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings)
    window.poll_timer.stop()
    qtbot.addWidget(window)

    assert not window.event_form.advanced_section.expanded
    assert not window.stage_form.speed_box.expanded
    assert window.stage_form.travel_box.expanded
    assert window.stage_form.conditions_box.expanded

    waypoint = window.project.document.waypoints[0].section
    window.refresh_navigation(waypoint.node_id)
    window.stage_form.max_speed.setText("100")
    assert "(1)" in window.stage_form.speed_box.header.text()
    window.stage_form.speed_box.set_expanded(True)
    settings.sync()

    restored_settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    restored = MainWindow(settings=restored_settings)
    restored.poll_timer.stop()
    qtbot.addWidget(restored)
    assert restored.stage_form.speed_box.expanded
    window.dirty = False
    restored.dirty = False


def test_collapsed_section_marks_and_reveals_validation_error(qtbot, tmp_path) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings)
    window.poll_timer.stop()
    qtbot.addWidget(window)
    waypoint = window.project.document.waypoints[0].section
    window.refresh_navigation(waypoint.node_id)
    window.stage_form.speed_box.set_expanded(False)
    window.stage_form.max_speed.setText("not-a-number")

    assert window.stage_form.speed_box.header.property("hasValidationError") is True
    matching_item = next(
        window.validation_list.item(row)
        for row in range(window.validation_list.count())
        if "MaxSpeed must be numeric" in window.validation_list.item(row).text()
    )
    window._jump_to_issue(matching_item)

    assert window.stage_form.speed_box.expanded
    assert window.stage_form.speed_box.header.property("hasValidationError") is False
    window.dirty = False


def test_repeated_validation_results_expand_to_named_waypoints(qtbot) -> None:
    document = parse_text(SAMPLE)
    first = document.waypoints[0].section
    assert document.duplicate_waypoint(first.node_id) is not None
    assert document.duplicate_waypoint(first.node_id) is not None
    project = ProjectData(document=document)
    route = [waypoint.section for waypoint in document.waypoints]
    for index, section in enumerate(route, start=1):
        section.set_value("System", "")
        section.set_value("Proximity", "100")
        project.stage_metadata[section.node_id] = StageMetadata(
            display_name=f"Gate {index}",
        )

    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    window._set_project(project, None, dirty=False)

    warning_group = next(
        window.validation_list.item(row)
        for row in range(window.validation_list.count())
        if "Gate waypoints normally omit Proximity"
        in window.validation_list.item(row).text()
    )
    error_group = next(
        window.validation_list.item(row)
        for row in range(window.validation_list.count())
        if "Surface coordinates require both System and Body"
        in window.validation_list.item(row).text()
    )
    assert warning_group.text().startswith("▸ WARNING: 3 Waypoints")
    assert error_group.text().startswith("▸ ERROR: 3 Waypoints")
    assert warning_group.data(VALIDATION_GROUP_ROLE)
    assert not window.dirty

    window._jump_to_issue(warning_group)

    warning_group = next(
        window.validation_list.item(row)
        for row in range(window.validation_list.count())
        if (
            window.validation_list.item(row).data(VALIDATION_GROUP_ROLE)
            and "Gate waypoints normally omit Proximity"
            in window.validation_list.item(row).text()
        )
    )
    warning_children = [
        window.validation_list.item(row)
        for row in range(window.validation_list.count())
        if (
            not window.validation_list.item(row).data(VALIDATION_GROUP_ROLE)
            and getattr(
                window.validation_list.item(row).data(Qt.ItemDataRole.UserRole),
                "message",
                "",
            )
            == "Gate waypoints normally omit Proximity."
        )
    ]
    assert warning_group.text().startswith("▾ WARNING: 3 Waypoints")
    assert [item.text().strip() for item in warning_children] == [
        "↳ Waypoint 1 — Gate 1",
        "↳ Waypoint 2 — Gate 2",
        "↳ Waypoint 3 — Gate 3",
    ]
    assert all(not item.isHidden() for item in warning_children)
    assert not window.dirty

    selected_issue = warning_children[1].data(Qt.ItemDataRole.UserRole)
    window._jump_to_issue(warning_children[1])

    assert window.current_section_id == selected_issue.section_id
    assert window.stage_form.title.text() == "Waypoint 2 — Gate 2"
    assert not window.dirty


def test_theme_choice_is_applied_and_persisted(qtbot, tmp_path) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(settings=settings)
    window.poll_timer.stop()
    qtbot.addWidget(window)

    window.set_theme(ThemeMode.DARK)
    assert settings.value("appearance/theme", "", str) == "dark"
    assert QApplication.instance().property("edttThemeMode") == "dark"
    assert window.theme_actions[ThemeMode.DARK].isChecked()

    window.set_theme(ThemeMode.LIGHT)
    assert settings.value("appearance/theme", "", str) == "light"
    assert QApplication.instance().property("edttThemeMode") == "light"
    assert window.theme_actions[ThemeMode.LIGHT].isChecked()

    window.set_theme(ThemeMode.SYSTEM)
    window.dirty = False


def test_explanations_use_delayed_hover_and_accessible_descriptions(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)
    waypoint = window.project.document.waypoints[0].section
    window.refresh_navigation(waypoint.node_id)
    window.show()
    target = window.stage_form.player_state
    assert "in-game state" in target.accessibleDescription()
    assert target.toolTip() == ""
    window.tooltip_filter.delay_ms = 10

    with qtbot.waitSignal(window.tooltip_filter.shown, timeout=1000) as blocker:
        QApplication.sendEvent(target, QEvent(QEvent.Type.Enter))

    assert blocker.args[0] is target
    assert "Flying starts" in blocker.args[1]
    window.dirty = False


def test_about_dialog_has_clickable_project_links_and_disclosure(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    qtbot.addWidget(window)

    window.show_about()
    qtbot.addWidget(window.about_dialog)
    links = window.about_dialog.findChild(QLabel, "aboutLinks")

    assert links is not None
    assert links.openExternalLinks()
    assert "https://github.com/LouisH99/EDTT-Definer" in links.text()
    assert "/issues" in links.text()
    assert "/releases" in links.text()
    assert "vibe-coded" in " ".join(
        label.text() for label in window.about_dialog.findChildren(QLabel)
    )
    window.about_dialog.close()
    window.dirty = False


def test_vehicle_conversion_dialog_previews_changes_and_defaults_to_cancel(qtbot) -> None:
    document = ProjectData.new().document
    dialog = RaceConversionDialog(document)
    qtbot.addWidget(dialog)
    dialog.target.setCurrentIndex(dialog.target.findData(RaceVehicle.SLF.value))

    assert dialog.cancel_button.isDefault()
    assert not dialog.apply_button.isDefault()
    assert dialog.apply_button.isEnabled()
    assert "Event Info: RaceType  SHIP  →  FIGHTER" in dialog.preview.toPlainText()
    assert "Waypoint 1: PlayerState  ShipPass  →  FighterFlyingStart" in dialog.preview.toPlainText()


def test_confirmed_whole_race_conversion_preserves_unrelated_data(
    qtbot, monkeypatch
) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    window.update_timer.stop()
    qtbot.addWidget(window)
    waypoint = window.project.document.waypoints[0].section
    waypoint.set_value("Position", "1.25,2.5")
    waypoint.set_value("Condition", "HeadingLimit=(90,180)")
    window.project.stage_metadata[waypoint.node_id] = StageMetadata(
        display_name="Canyon Gate",
        altitude_mode=AltitudeMode.MAXIMUM,
        maximum_altitude="500",
        altitude_locked=True,
    )
    max_ship_size = window.project.document.event_info.get("MaxShipSize")
    plan = build_vehicle_conversion(window.project.document, RaceVehicle.SLF)
    monkeypatch.setattr(window, "_choose_vehicle_conversion", lambda: plan)

    window.event_form.convert_race_button.click()

    assert window.project.document.event_info.get("RaceType") == "FIGHTER"
    assert window.project.document.event_info.get("MaxShipSize") == max_ship_size
    assert window.project.document.start.get("PlayerState") == "FighterFlyingStart"
    assert waypoint.value("PlayerState") == "FighterFlyingStart"
    assert window.project.document.end.get("PlayerState") == "Fighter"
    assert waypoint.value("Position") == "1.25,2.5"
    assert waypoint.value("Condition") == "HeadingLimit=(90,180)"
    assert window.project.stage_metadata[waypoint.node_id].display_name == "Canyon Gate"
    assert window.dirty
    assert "RaceType=FIGHTER" in window.preview.toPlainText()
    window.dirty = False


def test_cancelled_whole_race_conversion_changes_nothing(qtbot, monkeypatch) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    window.update_timer.stop()
    qtbot.addWidget(window)
    before = window.preview.toPlainText()
    monkeypatch.setattr(window, "_choose_vehicle_conversion", lambda: None)

    window.convert_whole_race()

    assert window.preview.toPlainText() == before
    assert not window.dirty


def test_automatic_update_preference_and_startup_check(qtbot, tmp_path) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    fake = FakeUpdateClient()
    window = MainWindow(update_client=fake, settings=settings)
    window.poll_timer.stop()
    window.update_timer.stop()
    qtbot.addWidget(window)

    assert window.automatic_update_action.isChecked()
    window._automatic_update_check()
    assert fake.calls == 1

    window.automatic_update_action.setChecked(False)
    assert settings.value("updates/check_at_startup", True, bool) is False
    window._automatic_update_check()
    assert fake.calls == 1
    window.dirty = False


def test_update_can_be_skipped_and_manual_check_ignores_skip(
    qtbot, monkeypatch, tmp_path
) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("updates/check_at_startup", False)
    fake = FakeUpdateClient()
    window = MainWindow(update_client=fake, settings=settings)
    window.poll_timer.stop()
    window.update_timer.stop()
    qtbot.addWidget(window)
    release = ReleaseInfo(
        tag_name="v9.9",
        version=(9, 9, 0),
        html_url="https://github.com/LouisH99/EDTT-Definer/releases/tag/v9.9",
        title="EDTT Definer v9.9",
    )
    shown = []
    monkeypatch.setattr(
        window,
        "_show_update_available",
        lambda value: shown.append(value) or "skip",
    )

    window._update_check_is_manual = False
    window._update_check_succeeded(release)
    assert len(shown) == 1
    assert settings.value("updates/skipped_version", "", str) == "9.9.0"

    window._update_check_is_manual = False
    window._update_check_succeeded(release)
    assert len(shown) == 1

    window._update_check_is_manual = True
    window._update_check_succeeded(release)
    assert len(shown) == 2
    window.dirty = False


def test_startup_update_failure_is_silent_but_manual_failure_is_reported(
    qtbot, monkeypatch, tmp_path
) -> None:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("updates/check_at_startup", False)
    window = MainWindow(update_client=FakeUpdateClient(), settings=settings)
    window.poll_timer.stop()
    qtbot.addWidget(window)
    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args, **_kwargs: warnings.append((_args, _kwargs)),
    )

    window._update_check_is_manual = False
    window._update_check_failed("offline")
    assert warnings == []
    window._update_check_is_manual = True
    window._update_check_failed("offline")
    assert len(warnings) == 1
    assert "offline" in warnings[0][0][-1]
    window.dirty = False


def test_detailed_help_covers_player_states_and_major_controls(qtbot) -> None:
    window = MainWindow()
    window.poll_timer.stop()
    window.update_timer.stop()
    qtbot.addWidget(window)
    waypoint = window.project.document.waypoints[0].section
    window.refresh_navigation(waypoint.node_id)

    player_help = window.stage_form.player_state.accessibleDescription()
    assert "Ship means the ship must be landed" in player_help
    assert "ShipPass means a ship flies through" in player_help
    assert "Start and Waypoint 1" in player_help
    fighter_index = window.stage_form.player_state.findText("Fighter")
    assert "ship-launched fighter" in window.stage_form.player_state.itemData(
        fighter_index, Qt.ItemDataRole.ToolTipRole
    )
    for widget in (
        window.event_form.event_ref,
        window.event_form.convert_race_button,
        window.stage_form.position_mode,
        window.stage_form.maximum_altitude,
        window.stage_form.max_speed,
        window.navigation,
        window.waypoint_buttons["Section…"],
        window.waypoint_buttons["Reverse"],
        window.capture_button,
        window.preview,
    ):
        assert widget.accessibleDescription()
        assert widget.property("edttHelpText")
        assert widget.toolTip() == ""
    window.dirty = False
