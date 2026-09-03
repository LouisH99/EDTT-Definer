from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSettings, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .edsm import EdsmRadiusClient
from .geometry import calculate_gate, great_circle_distance_m, spherical_midpoint
from .live import LiveLocationReader, LiveResult, LiveState, LocationSnapshot
from .model import (
    EntryKind,
    EventDocument,
    Position,
    SectionNode,
    SectionType,
    Stage,
)
from .parser import _entry_from_line, load_document, save_document, serialize_document
from .project import (
    AltitudeMode,
    ProjectData,
    StageMetadata,
    apply_maximum_altitude,
    decimal_value,
    inferred_altitude_mode,
    inferred_maximum_altitude,
    load_project,
    save_project,
)
from .radius import BodyRadiusCache, normalize_name
from .theme import ThemeMode, apply_theme, semantic_color
from .validator import KNOWN_FIELDS, Severity, ValidationIssue, has_errors, validate_project
from .widgets import CollapsibleSection, DelayedTooltipFilter


APP_NAME = "EDTT Definer"
SETTINGS_APP_NAME = "Elite Dangerous Time Trial Creator"
_version_parts = __version__.split(".")
DISPLAY_VERSION = ".".join(_version_parts[:2] if _version_parts[2:] == ["0"] else _version_parts)
APP_DISPLAY_NAME = f"{APP_NAME} v{DISPLAY_VERSION}"
PLAYER_STATES = [
    "Ship", "ShipFlyingStart", "ShipPass", "FighterFlyingStart", "Fighter", "SRV", "OnFoot",
]

REPOSITORY_URL = "https://github.com/LouisH99/EDTT-Definer"
ISSUES_URL = f"{REPOSITORY_URL}/issues"
RELEASES_URL = f"{REPOSITORY_URL}/releases"


def _editable_combo(values: list[str]) -> QComboBox:
    combo = QComboBox()
    combo.setEditable(True)
    combo.addItems(values)
    return combo


class SimpleListEditor(QWidget):
    changed = Signal()

    def __init__(self, placeholder: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        add = QPushButton("Add / update")
        remove = QPushButton("Remove")
        add.clicked.connect(self._add_or_update)
        remove.clicked.connect(self._remove)
        self.edit.returnPressed.connect(self._add_or_update)
        self.list.currentItemChanged.connect(self._selected)
        row = QHBoxLayout()
        row.addWidget(self.edit, 1)
        row.addWidget(add)
        row.addWidget(remove)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list)
        layout.addLayout(row)

    def values(self) -> list[str]:
        return [self.list.item(index).text() for index in range(self.list.count())]

    def set_values(self, values: list[str]) -> None:
        blocker = QSignalBlocker(self.list)
        self.list.clear()
        self.list.addItems(values)
        del blocker
        self.edit.clear()

    def _selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current:
            self.edit.setText(current.text())

    def _add_or_update(self) -> None:
        value = self.edit.text().strip()
        if not value:
            return
        item = self.list.currentItem()
        if item:
            item.setText(value)
        else:
            self.list.addItem(value)
        self.list.clearSelection()
        self.edit.clear()
        self.changed.emit()

    def _remove(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)
            self.edit.clear()
            self.changed.emit()


class InstructionEditor(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.list = QListWidget()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Instruction spoken after this stage")
        self.terse = QCheckBox("Terse")
        self.terse.setToolTip(
            "Prefix the instruction with Terse: so EDCoPilot uses the shorter speech behavior."
        )
        add = QPushButton("Add / update")
        remove = QPushButton("Remove")
        add.clicked.connect(self._add_or_update)
        remove.clicked.connect(self._remove)
        self.edit.returnPressed.connect(self._add_or_update)
        self.list.currentItemChanged.connect(self._selected)
        row = QHBoxLayout()
        row.addWidget(self.terse)
        row.addWidget(self.edit, 1)
        row.addWidget(add)
        row.addWidget(remove)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list)
        layout.addLayout(row)

    def values(self) -> list[str]:
        return [self.list.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.list.count())]

    def set_values(self, values: list[str]) -> None:
        self.list.clear()
        for value in values:
            self._append(value)
        self.edit.clear()
        self.terse.setChecked(False)

    def _append(self, value: str) -> None:
        terse = value.startswith("Terse:")
        text = value[6:] if terse else value
        item = QListWidgetItem(("[Terse] " if terse else "") + text)
        item.setData(Qt.ItemDataRole.UserRole, value)
        self.list.addItem(item)

    def _selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if not current:
            return
        value = current.data(Qt.ItemDataRole.UserRole)
        terse = value.startswith("Terse:")
        self.terse.setChecked(terse)
        self.edit.setText(value[6:] if terse else value)

    def _add_or_update(self) -> None:
        text = self.edit.text().strip()
        if not text:
            return
        value = f"Terse:{text}" if self.terse.isChecked() else text
        item = self.list.currentItem()
        if item:
            item.setText(("[Terse] " if self.terse.isChecked() else "") + text)
            item.setData(Qt.ItemDataRole.UserRole, value)
        else:
            self._append(value)
        self.list.clearSelection()
        self.edit.clear()
        self.terse.setChecked(False)
        self.changed.emit()

    def _remove(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)
            self.changed.emit()


class ConditionEditor(QWidget):
    changed = Signal()

    PARAMETERLESS = {
        "OnlyStateMaxAltitudeOnMiss",
        "NoAltitudeSpikeFilter",
        "NoCrossWaypointInstruction",
        "HandBrakeOn",
        "Salute",
        "InsideBuilding",
        "OnBuilding",
    }

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.list = QListWidget()
        self.kind = QComboBox()
        self.kind.addItems([
            "Custom", "DirectionLimit", "HeadingLimit", "SupercruiseDestinationDrop",
            *sorted(self.PARAMETERLESS),
        ])
        self.value = QLineEdit()
        self.value.setPlaceholderText("Condition value")
        add = QPushButton("Add / update")
        remove = QPushButton("Remove")
        self.kind.currentTextChanged.connect(self._kind_changed)
        self.list.currentItemChanged.connect(self._selected)
        add.clicked.connect(self._add_or_update)
        remove.clicked.connect(self._remove)
        row = QHBoxLayout()
        row.addWidget(self.kind)
        row.addWidget(self.value, 1)
        row.addWidget(add)
        row.addWidget(remove)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list)
        layout.addLayout(row)
        self._kind_changed("Custom")

    def values(self) -> list[str]:
        return [self.list.item(index).text() for index in range(self.list.count())]

    def set_values(self, values: list[str]) -> None:
        self.list.clear()
        self.list.addItems(values)
        self.value.clear()
        self.kind.setCurrentText("Custom")

    def _kind_changed(self, kind: str) -> None:
        self.value.setEnabled(kind not in self.PARAMETERLESS)
        placeholders = {
            "DirectionLimit": "Example: (90,180)",
            "HeadingLimit": "Example: (90,180)",
            "SupercruiseDestinationDrop": "Journal type:description",
            "Custom": "Complete condition value",
        }
        self.value.setPlaceholderText(placeholders.get(kind, "No value required"))

    def _selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if not current:
            return
        condition = current.text()
        if condition in self.PARAMETERLESS:
            self.kind.setCurrentText(condition)
            self.value.clear()
            return
        for prefix in ("DirectionLimit", "HeadingLimit", "SupercruiseDestinationDrop"):
            marker = f"{prefix}=" if prefix in {"DirectionLimit", "HeadingLimit"} else f"{prefix}:"
            if condition.startswith(marker):
                self.kind.setCurrentText(prefix)
                self.value.setText(condition[len(marker):])
                return
        self.kind.setCurrentText("Custom")
        self.value.setText(condition)

    def _condition_value(self) -> str:
        kind = self.kind.currentText()
        value = self.value.text().strip()
        if kind in self.PARAMETERLESS:
            return kind
        if kind in {"DirectionLimit", "HeadingLimit"}:
            return f"{kind}={value}" if value else ""
        if kind == "SupercruiseDestinationDrop":
            return f"{kind}:{value}" if value else ""
        return value

    def _add_or_update(self) -> None:
        condition = self._condition_value()
        if not condition:
            return
        item = self.list.currentItem()
        if item:
            item.setText(condition)
        else:
            self.list.addItem(condition)
        self.list.clearSelection()
        self.value.clear()
        self.kind.setCurrentText("Custom")
        self.changed.emit()

    def _remove(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.list.takeItem(row)
            self.changed.emit()


class AdvancedEditor(QWidget):
    changed = Signal()
    count_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.section: SectionNode | None = None
        self.known: set[str] = set()
        self.entries: list = []
        self.list = QListWidget()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Raw line or FutureField=value")
        update = QPushButton("Add / update")
        remove = QPushButton("Remove")
        self.list.currentItemChanged.connect(self._selected)
        update.clicked.connect(self._update)
        remove.clicked.connect(self._remove)
        row = QHBoxLayout()
        row.addWidget(self.edit, 1)
        row.addWidget(update)
        row.addWidget(remove)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list)
        layout.addLayout(row)

    def set_section(self, section: SectionNode, known: set[str]) -> None:
        self.section = section
        self.known = known
        self._reload()

    def _reload(self) -> None:
        self.entries = []
        self.list.clear()
        if self.section is None:
            return
        for entry in self.section.entries:
            unknown_key = entry.kind is EntryKind.KEY_VALUE and entry.key and entry.key.casefold() not in self.known
            if entry.kind is EntryKind.RAW or unknown_key:
                self.entries.append(entry)
                self.list.addItem(entry.raw)
        self.count_changed.emit(len(self.entries))

    def _selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current:
            self.edit.setText(current.text())

    def _update(self) -> None:
        if self.section is None:
            return
        raw = self.edit.text()
        if not raw.strip():
            return
        row = self.list.currentRow()
        replacement = _entry_from_line(raw)
        if row >= 0:
            old = self.entries[row]
            index = self.section.entries.index(old)
            self.section.entries[index] = replacement
        else:
            self.section.entries.append(replacement)
        self.edit.clear()
        self._reload()
        self.changed.emit()

    def _remove(self) -> None:
        if self.section is None:
            return
        row = self.list.currentRow()
        if row >= 0:
            self.section.entries.remove(self.entries[row])
            self._reload()
            self.changed.emit()


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(520)

        title = QLabel(APP_DISPLAY_NAME)
        title.setObjectName("sectionTitle")
        description = QLabel(
            "Create, edit, validate, and export EDCoPilot time-trial definitions.<br>"
            "This is an independent community tool for Elite Dangerous and EDCoPilot."
        )
        description.setWordWrap(True)

        links = QLabel(
            f'<a href="{REPOSITORY_URL}">GitHub repository</a> &nbsp;•&nbsp; '
            f'<a href="{ISSUES_URL}">Report a bug or request a feature</a><br>'
            f'<a href="{RELEASES_URL}">Releases</a> &nbsp;•&nbsp; '
            '<a href="https://www.edsm.net/">EDSM</a>'
        )
        links.setObjectName("aboutLinks")
        links.setOpenExternalLinks(True)
        links.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)

        disclosure = QLabel(
            "Licensed under the MIT License.\n\n"
            "AI development disclosure: This project was “vibe-coded” with OpenAI Codex. "
            "The project owner directed the requirements, supplied examples, tested the application, "
            "and made the release decisions; Codex generated and revised most of the code, tests, "
            "and documentation."
        )
        disclosure.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.clicked.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addSpacing(8)
        layout.addWidget(links)
        layout.addSpacing(8)
        layout.addWidget(disclosure)
        layout.addWidget(buttons)


class EventInfoForm(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.section: SectionNode | None = None
        self.loading = False
        title = QLabel("Event information")
        title.setObjectName("sectionTitle")
        self.event_ref = QLineEdit()
        self.event_state = _editable_combo(["AUTHOR", "ALPHA", "PROD", "DELETE"])
        self.name = QLineEdit()
        self.short_desc = QTextEdit()
        self.short_desc.setMaximumHeight(80)
        self.max_ship_size = _editable_combo(["N/A", "S", "M", "L"])
        self.game_type = _editable_combo(["BOTH", "ODYSSEY", "HORIZONS"])
        self.race_type = _editable_combo(["SHIP", "SRV", "FIGHTER", "ONFOOT"])
        self.time_penalty = QLineEdit()
        self.max_penalties = QLineEdit()
        self.pause_resume = QCheckBox("Allow PauseResume")
        self.restrictions = SimpleListEditor("Restriction, e.g. NoHullRepair")
        self.restrictions.setMinimumHeight(130)
        self.advanced = AdvancedEditor()
        self.advanced_section = CollapsibleSection(
            "Advanced / unrecognized entries", expanded=False
        )
        self.advanced_section.add_widget(self.advanced)
        self.advanced.count_changed.connect(self.advanced_section.set_item_count)

        form = QFormLayout()
        form.addRow("Event reference*", self.event_ref)
        form.addRow("State*", self.event_state)
        form.addRow("Name*", self.name)
        form.addRow("Short description*", self.short_desc)
        form.addRow("Maximum ship size", self.max_ship_size)
        form.addRow("Game type*", self.game_type)
        form.addRow("Race type*", self.race_type)
        form.addRow("Time penalty", self.time_penalty)
        form.addRow("Maximum penalties", self.max_penalties)
        form.addRow("Event condition", self.pause_resume)
        restrictions_group = QGroupBox("Restrictions")
        restrictions_layout = QVBoxLayout(restrictions_group)
        restrictions_layout.addWidget(self.restrictions)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(form)
        layout.addWidget(restrictions_group)
        layout.addWidget(self.advanced_section)
        layout.addStretch(1)

        for widget in (self.event_ref, self.name, self.time_penalty, self.max_penalties):
            widget.textChanged.connect(self._emit_changed)
        for combo in (self.event_state, self.max_ship_size, self.game_type, self.race_type):
            combo.currentTextChanged.connect(self._emit_changed)
        self.short_desc.textChanged.connect(self._emit_changed)
        self.pause_resume.toggled.connect(self._emit_changed)
        self.restrictions.changed.connect(self._emit_changed)
        self.advanced.changed.connect(self._emit_changed)

        self.event_ref.setToolTip(
            "A stable identifier used by EDCoPilot. Use only letters, numbers, spaces, hyphens, and underscores."
        )
        self.event_state.setToolTip(
            "AUTHOR is for editing, ALPHA for testing, PROD for published events, and DELETE requests removal."
        )
        self.max_ship_size.setToolTip(
            "Largest allowed landing-pad class: N/A, Small, Medium, or Large."
        )
        self.game_type.setToolTip(
            "Whether the event supports Horizons, Odyssey, or both game modes."
        )
        self.race_type.setToolTip(
            "The vehicle or movement type used for the race: ship, fighter, SRV, or on foot."
        )
        self.time_penalty.setToolTip("Optional time added for each recorded penalty.")
        self.max_penalties.setToolTip("Optional number of penalties allowed by the event.")
        self.pause_resume.setToolTip(
            "Adds Condition=PauseResume so the race can be paused and resumed."
        )

    def set_section(self, section: SectionNode) -> None:
        self.loading = True
        self.section = section
        self.event_ref.setText(section.value("EventRef"))
        self.event_state.setCurrentText(section.value("EventState"))
        self.name.setText(section.value("Name"))
        self.short_desc.setPlainText(section.value("ShortDesc"))
        self.max_ship_size.setCurrentText(section.value("MaxShipSize"))
        self.game_type.setCurrentText(section.value("GameType"))
        self.race_type.setCurrentText(section.value("RaceType"))
        self.time_penalty.setText(section.value("TimePenalty"))
        self.max_penalties.setText(section.value("MaxPenalties"))
        self.pause_resume.setChecked("PauseResume" in section.values("Condition"))
        self.restrictions.set_values(section.values("Restriction"))
        self.advanced.set_section(section, KNOWN_FIELDS["eventinfo"])
        self.loading = False

    def apply(self) -> None:
        if self.section is None:
            return
        values = {
            "EventRef": self.event_ref.text(),
            "EventState": self.event_state.currentText(),
            "Name": self.name.text(),
            "ShortDesc": self.short_desc.toPlainText().replace("\n", " "),
            "MaxShipSize": self.max_ship_size.currentText(),
            "GameType": self.game_type.currentText(),
            "RaceType": self.race_type.currentText(),
            "TimePenalty": self.time_penalty.text(),
            "MaxPenalties": self.max_penalties.text(),
        }
        for key, value in values.items():
            self.section.set_value(key, value)
        self.section.set_values("Restriction", self.restrictions.values())
        other_conditions = [value for value in self.section.values("Condition") if value != "PauseResume"]
        if self.pause_resume.isChecked():
            other_conditions.append("PauseResume")
        self.section.set_values("Condition", other_conditions)

    def _emit_changed(self, *_args) -> None:
        if not self.loading:
            self.apply()
            self.changed.emit()


class StageForm(QWidget):
    changed = Signal()
    metadata_changed = Signal()
    capture_a_requested = Signal()
    capture_b_requested = Signal()
    calculate_gate_requested = Signal()
    convert_gate_requested = Signal()
    gate_parameters_changed = Signal()
    find_radius_requested = Signal()
    body_radius_manually_edited = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.section: SectionNode | None = None
        self.metadata = StageMetadata()
        self.loading = False
        self.position_touched = False
        self.title = QLabel("Stage")
        self.title.setObjectName("sectionTitle")
        self.waypoint_name = QLineEdit()
        self.waypoint_name.setPlaceholderText("Optional name used only inside EDTT Definer")
        self.player_state = _editable_combo(PLAYER_STATES)
        self.system = QLineEdit()
        self.body = QLineEdit()
        self.body_radius = QLineEdit()
        self.find_radius_button = QPushButton("Find radius")
        self.body_radius_source = QLabel("Source: —")
        self.body_radius_source.setWordWrap(True)
        self.station = QLineEdit()
        self.position_mode = QComboBox()
        self.position_mode.addItems(["Point", "Gate"])
        self.lat_a = QLineEdit()
        self.lon_a = QLineEdit()
        self.lat_b = QLineEdit()
        self.lon_b = QLineEdit()
        self.gate_width = QLineEdit("2000")
        self.gate_forward_offset = QLineEdit("0")
        self.proximity = QLineEdit()
        self.altitude = QLineEdit()
        self.altitude_proximity = QLineEdit()
        self.altitude_mode = QComboBox()
        self.altitude_mode.addItem("No altitude restriction", AltitudeMode.NONE.value)
        self.altitude_mode.addItem("Maximum height", AltitudeMode.MAXIMUM.value)
        self.altitude_mode.addItem("Target altitude ± tolerance", AltitudeMode.TARGET.value)
        self.maximum_altitude = QLineEdit()
        self.maximum_altitude.setPlaceholderText("Maximum height above the surface")
        self.altitude_locked = QCheckBox("Lock altitude during live capture")
        self.altitude_locked.setChecked(True)
        self.start_orientation_instructions = InstructionEditor()
        self.start_instructions = InstructionEditor()
        self.next_instructions = InstructionEditor()
        self.end_instructions = InstructionEditor()
        # Kept as an alias for callers written against the original form.
        self.instructions = self.next_instructions
        self.enter_system_instruction = QTextEdit()
        self.enter_system_instruction.setMaximumHeight(70)
        self.approach_body_instruction = QTextEdit()
        self.approach_body_instruction.setMaximumHeight(70)
        self.last_leg_message = QTextEdit()
        self.last_leg_message.setMaximumHeight(70)
        self.max_speed = QLineEdit()
        self.max_speed_warning_distance = QLineEdit()
        self.max_speed_warning_instruction = QTextEdit()
        self.max_speed_warning_instruction.setMaximumHeight(70)
        self.max_speed_exceeded_instruction = QTextEdit()
        self.max_speed_exceeded_instruction.setMaximumHeight(70)
        self.max_speed_warning_trigger = QLineEdit()
        self.conditions = ConditionEditor()
        self.advanced = AdvancedEditor()

        capture_a = QPushButton("Capture A")
        capture_b = QPushButton("Capture B")
        self.calculate_gate_button = QPushButton("Create gate from live heading")
        self.convert_gate_button = QPushButton("Gate → center point")
        self.convert_gate_button.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        capture_a.clicked.connect(self.capture_a_requested)
        capture_b.clicked.connect(self.capture_b_requested)
        self.calculate_gate_button.clicked.connect(self.calculate_gate_requested)
        self.convert_gate_button.clicked.connect(self.convert_gate_requested)
        self.position_box = QGroupBox("Position")
        position_grid = QGridLayout(self.position_box)
        position_grid.addWidget(QLabel("Mode"), 0, 0)
        position_grid.addWidget(self.position_mode, 0, 1)
        position_grid.addWidget(
            self.convert_gate_button,
            0,
            3,
            1,
            2,
            Qt.AlignmentFlag.AlignRight,
        )
        position_grid.addWidget(QLabel("Latitude A"), 1, 0)
        position_grid.addWidget(self.lat_a, 1, 1)
        position_grid.addWidget(QLabel("Longitude A"), 1, 2)
        position_grid.addWidget(self.lon_a, 1, 3)
        position_grid.addWidget(capture_a, 1, 4)
        position_grid.addWidget(QLabel("Latitude B"), 2, 0)
        position_grid.addWidget(self.lat_b, 2, 1)
        position_grid.addWidget(QLabel("Longitude B"), 2, 2)
        position_grid.addWidget(self.lon_b, 2, 3)
        position_grid.addWidget(capture_b, 2, 4)
        position_grid.addWidget(QLabel("Gate width (m)"), 3, 0)
        position_grid.addWidget(self.gate_width, 3, 1)
        position_grid.addWidget(QLabel("Distance ahead (m)"), 3, 2)
        position_grid.addWidget(self.gate_forward_offset, 3, 3)
        position_grid.addWidget(self.calculate_gate_button, 3, 4)
        self.gate_distance_label = QLabel("Measured A–B distance: —")
        self.gate_distance_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        position_grid.addWidget(self.gate_distance_label, 4, 0, 1, 5)
        self.gate_width.setToolTip("Total gate width. The center mode places half of this distance on each side.")
        self.gate_forward_offset.setToolTip("0 centers the gate on the live position; a positive value places it ahead along the live heading.")
        self.convert_gate_button.setToolTip(
            "Replace this gate with one point at the spherical midpoint of endpoints A and B. "
            "All other stage settings are kept."
        )

        form = QFormLayout()
        self.waypoint_name_label = QLabel("Waypoint name (editor only)")
        form.addRow(self.waypoint_name_label, self.waypoint_name)
        form.addRow("Player state*", self.player_state)
        form.addRow("System", self.system)
        form.addRow("Body", self.body)
        radius_editor = QWidget()
        radius_editor_layout = QVBoxLayout(radius_editor)
        radius_editor_layout.setContentsMargins(0, 0, 0, 0)
        radius_row = QHBoxLayout()
        radius_row.setContentsMargins(0, 0, 0, 0)
        radius_row.addWidget(self.body_radius, 1)
        radius_row.addWidget(self.find_radius_button)
        radius_editor_layout.addLayout(radius_row)
        radius_editor_layout.addWidget(self.body_radius_source)
        form.addRow("Body radius (km)", radius_editor)
        form.addRow("Station", self.station)
        form.addRow("Proximity (m)", self.proximity)
        self.altitude_box = QGroupBox("Altitude rule")
        altitude_form = QFormLayout(self.altitude_box)
        altitude_form.addRow("Mode", self.altitude_mode)
        self.maximum_altitude_label = QLabel("Maximum height (m)")
        altitude_form.addRow(self.maximum_altitude_label, self.maximum_altitude)
        self.altitude_label = QLabel("Target altitude (m)")
        altitude_form.addRow(self.altitude_label, self.altitude)
        self.altitude_proximity_label = QLabel("Tolerance (m)")
        altitude_form.addRow(self.altitude_proximity_label, self.altitude_proximity)
        altitude_form.addRow(self.altitude_locked)
        self.altitude_locked.setToolTip(
            "When enabled, live position and gate captures keep the stage's existing altitude rule."
        )
        self.maximum_altitude.setToolTip(
            "EDCoPilot receives half this value as Altitude and half as AltitudeProximity."
        )
        navigation_content = QWidget()
        navigation_form = QFormLayout(navigation_content)
        navigation_form.addRow("Enter system", self.enter_system_instruction)
        navigation_form.addRow("Approach body", self.approach_body_instruction)
        self.last_leg_label = QLabel("Last-leg message")
        navigation_form.addRow(self.last_leg_label, self.last_leg_message)
        self.travel_box = CollapsibleSection("Travel instructions", expanded=True)
        self.travel_box.add_widget(navigation_content)

        self.start_orientation_box = QGroupBox("Start orientation instructions")
        QVBoxLayout(self.start_orientation_box).addWidget(self.start_orientation_instructions)
        self.start_instructions_box = QGroupBox("Start instructions")
        QVBoxLayout(self.start_instructions_box).addWidget(self.start_instructions)
        self.next_instructions_box = QGroupBox("Next instructions")
        QVBoxLayout(self.next_instructions_box).addWidget(self.next_instructions)
        self.end_instructions_box = QGroupBox("End instructions")
        QVBoxLayout(self.end_instructions_box).addWidget(self.end_instructions)

        speed_content = QWidget()
        speed_form = QFormLayout(speed_content)
        speed_form.addRow("Maximum speed", self.max_speed)
        speed_form.addRow("Warning distance(s)", self.max_speed_warning_distance)
        speed_form.addRow("Warning trigger speed(s)", self.max_speed_warning_trigger)
        speed_form.addRow("Warning instruction", self.max_speed_warning_instruction)
        speed_form.addRow("Exceeded instruction", self.max_speed_exceeded_instruction)
        self.max_speed_warning_distance.setPlaceholderText("One value or comma-separated values")
        self.max_speed_warning_trigger.setPlaceholderText("One value or comma-separated values")
        self.speed_box = CollapsibleSection("Maximum-speed rule", expanded=False)
        self.speed_box.add_widget(speed_content)
        self.conditions_box = CollapsibleSection("Conditions", expanded=True)
        self.conditions_box.add_widget(self.conditions)
        self.advanced_section = CollapsibleSection(
            "Advanced / unrecognized entries", expanded=False
        )
        self.advanced_section.add_widget(self.advanced)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addLayout(form)
        layout.addWidget(self.position_box)
        layout.addWidget(self.altitude_box)
        layout.addWidget(self.travel_box)
        layout.addWidget(self.start_orientation_box)
        layout.addWidget(self.start_instructions_box)
        layout.addWidget(self.next_instructions_box)
        layout.addWidget(self.end_instructions_box)
        layout.addWidget(self.speed_box)
        layout.addWidget(self.conditions_box)
        layout.addWidget(self.advanced_section)
        layout.addStretch(1)

        for widget in (
            self.system, self.body, self.body_radius, self.station, self.proximity,
            self.altitude, self.altitude_proximity, self.maximum_altitude, self.max_speed,
            self.max_speed_warning_distance, self.max_speed_warning_trigger,
        ):
            widget.textChanged.connect(self._emit_changed)
        self.waypoint_name.textChanged.connect(self._emit_metadata_changed)
        self.altitude_locked.toggled.connect(self._emit_metadata_changed)
        self.altitude_mode.currentIndexChanged.connect(self._altitude_mode_changed)
        self.player_state.currentTextChanged.connect(self._emit_changed)
        for editor in (
            self.enter_system_instruction, self.approach_body_instruction,
            self.last_leg_message, self.max_speed_warning_instruction,
            self.max_speed_exceeded_instruction,
        ):
            editor.textChanged.connect(self._emit_changed)
        for editor in (
            self.start_orientation_instructions, self.start_instructions,
            self.next_instructions, self.end_instructions,
        ):
            editor.changed.connect(self._emit_changed)
        self.conditions.changed.connect(self._emit_changed)
        self.advanced.changed.connect(self._emit_changed)
        self.advanced.count_changed.connect(self.advanced_section.set_item_count)
        for widget in (self.lat_a, self.lon_a, self.lat_b, self.lon_b):
            widget.textChanged.connect(self._position_changed)
        self.position_mode.currentTextChanged.connect(self._position_changed)
        self.position_mode.currentTextChanged.connect(self._update_gate_fields)
        self.gate_width.textChanged.connect(self.gate_parameters_changed)
        self.gate_forward_offset.textChanged.connect(self.gate_parameters_changed)
        self.find_radius_button.clicked.connect(self.find_radius_requested)
        self.body_radius.textEdited.connect(self.body_radius_manually_edited)
        for widget in (self.lat_a, self.lon_a, self.lat_b, self.lon_b, self.body_radius):
            widget.textChanged.connect(self._update_gate_distance)
        self.position_mode.currentTextChanged.connect(self._update_gate_distance)
        for editor in (
            self.enter_system_instruction,
            self.approach_body_instruction,
            self.last_leg_message,
            self.max_speed_warning_instruction,
            self.max_speed_exceeded_instruction,
        ):
            editor.textChanged.connect(self._update_collapsible_counts)
        for widget in (
            self.max_speed,
            self.max_speed_warning_distance,
            self.max_speed_warning_trigger,
        ):
            widget.textChanged.connect(self._update_collapsible_counts)
        self.conditions.changed.connect(self._update_collapsible_counts)

        self.player_state.setToolTip(
            "The in-game state EDCoPilot expects when this stage is crossed. Flying starts have special first-waypoint rules."
        )
        self.position_mode.setToolTip(
            "Point uses one coordinate and a Proximity radius. Gate uses a line between endpoints A and B."
        )
        self.body_radius.setToolTip(
            "Planet radius in kilometres. It is used only for gate geometry and distance calculations."
        )
        self.find_radius_button.setToolTip(
            "Look for a matching radius in local journal data and the local cache, then optionally EDSM."
        )
        self.proximity.setToolTip(
            "How close the player must come to a point checkpoint. EDTT Definer never chooses this tolerance for you."
        )
        self.altitude_mode.setToolTip(
            "Choose no restriction, a maximum height above the surface, or a target altitude with a tolerance."
        )
        self.max_speed.setToolTip("Optional maximum speed enforced for this stage by EDCoPilot.")
        self.max_speed_warning_distance.setToolTip(
            "Distance before the stage at which EDCoPilot gives speed warnings; accepts comma-separated values."
        )
        self.max_speed_warning_trigger.setToolTip(
            "Optional speed thresholds paired with the warning distances; accepts comma-separated values."
        )
        self.conditions.kind.setToolTip(
            "Choose a guided EDCoPilot condition or Custom to preserve a condition not listed here."
        )

    def set_section(self, section: SectionNode, label: str, metadata: StageMetadata) -> None:
        self.loading = True
        self.position_touched = False
        self.section = section
        self.metadata = metadata
        self.title.setText(label)
        self.waypoint_name.setText(metadata.display_name)
        self.player_state.setCurrentText(section.value("PlayerState"))
        self.system.setText(section.value("System"))
        self.body.setText(section.value("Body"))
        self.body_radius.setText(section.value("BodyRadius"))
        self.station.setText(section.value("Station"))
        position = Position.parse(section.value("Position"))
        self.position_mode.setCurrentText("Gate" if position.is_gate else "Point")
        self.lat_a.setText(position.latitude_a)
        self.lon_a.setText(position.longitude_a)
        self.lat_b.setText(position.latitude_b)
        self.lon_b.setText(position.longitude_b)
        self.proximity.setText(section.value("Proximity"))
        self.altitude.setText(section.value("Altitude"))
        self.altitude_proximity.setText(section.value("AltitudeProximity"))
        mode = inferred_altitude_mode(section, metadata)
        mode_index = self.altitude_mode.findData(mode.value)
        self.altitude_mode.setCurrentIndex(max(mode_index, 0))
        self._displayed_altitude_mode = mode
        self.maximum_altitude.setText(
            inferred_maximum_altitude(section, metadata) if mode is AltitudeMode.MAXIMUM else metadata.maximum_altitude
        )
        self.altitude_locked.setChecked(metadata.altitude_locked)
        self.enter_system_instruction.setPlainText(section.value("EnterSystemInstruction"))
        self.approach_body_instruction.setPlainText(section.value("ApproachBodyInstruction"))
        self.last_leg_message.setPlainText(section.value("LastLegMessage"))
        self.start_orientation_instructions.set_values(section.values("StartOrientationInstruction"))
        self.start_instructions.set_values(section.values("StartInstruction"))
        self.next_instructions.set_values(section.values("NextInstruction"))
        self.end_instructions.set_values(section.values("EndInstruction"))
        self.max_speed.setText(section.value("MaxSpeed"))
        self.max_speed_warning_distance.setText(section.value("MaxSpeedWarningDistance"))
        self.max_speed_warning_trigger.setText(section.value("MaxSpeedWarningTrigger"))
        self.max_speed_warning_instruction.setPlainText(section.value("MaxSpeedWarningInstruction"))
        self.max_speed_exceeded_instruction.setPlainText(section.value("MaxSpeedExceededInstruction"))
        self.conditions.set_values(section.values("Condition"))
        is_start = section.section_type is SectionType.START
        is_waypoint = section.section_type is SectionType.WAYPOINT
        is_end = section.section_type is SectionType.END
        self.waypoint_name.setVisible(is_waypoint)
        self.waypoint_name_label.setVisible(is_waypoint)
        self.start_orientation_box.setVisible(is_start)
        self.start_instructions_box.setVisible(is_start)
        self.next_instructions_box.setVisible(is_start or is_waypoint)
        self.end_instructions_box.setVisible(is_end)
        self.last_leg_message.setVisible(is_end)
        self.last_leg_label.setVisible(is_end)
        self.speed_box.setVisible(is_waypoint or is_end)
        known = KNOWN_FIELDS.get(section.name.casefold(), set())
        self.advanced.set_section(section, known)
        self._update_gate_fields(self.position_mode.currentText())
        self._update_altitude_fields(mode)
        self._update_gate_distance()
        self._update_collapsible_counts()
        self.position_touched = False
        self.loading = False

    def apply(self) -> None:
        if self.section is None:
            return
        values = {
            "PlayerState": self.player_state.currentText(),
            "System": self.system.text(),
            "Body": self.body.text(),
            "BodyRadius": self.body_radius.text(),
            "Station": self.station.text(),
            "Proximity": self.proximity.text(),
            "EnterSystemInstruction": self.enter_system_instruction.toPlainText().replace("\n", " "),
            "ApproachBodyInstruction": self.approach_body_instruction.toPlainText().replace("\n", " "),
        }
        for key, value in values.items():
            self.section.set_value(key, value)
        mode = self.current_altitude_mode()
        if mode is AltitudeMode.NONE:
            self.section.set_value("Altitude", "")
            self.section.set_value("AltitudeProximity", "")
        elif mode is AltitudeMode.MAXIMUM:
            apply_maximum_altitude(self.section, self.maximum_altitude.text())
        else:
            self.section.set_value("Altitude", self.altitude.text())
            self.section.set_value("AltitudeProximity", self.altitude_proximity.text())
        if self.position_touched:
            self.section.set_value(
                "Position",
                Position(
                    self.lat_a.text().strip(), self.lon_a.text().strip(),
                    self.lat_b.text().strip(), self.lon_b.text().strip(),
                    self.position_mode.currentText() == "Gate",
                ).format(),
            )
        if self.section.section_type is SectionType.START:
            self.section.set_values("StartOrientationInstruction", self.start_orientation_instructions.values())
            self.section.set_values("StartInstruction", self.start_instructions.values())
            self.section.set_values("NextInstruction", self.next_instructions.values())
        elif self.section.section_type is SectionType.WAYPOINT:
            self.section.set_values("NextInstruction", self.next_instructions.values())
            self._apply_speed_fields()
        elif self.section.section_type is SectionType.END:
            self.section.set_values("EndInstruction", self.end_instructions.values())
            self.section.set_value("LastLegMessage", self.last_leg_message.toPlainText().replace("\n", " "))
            self._apply_speed_fields()
        self.section.set_values("Condition", self.conditions.values())

    def _apply_speed_fields(self) -> None:
        if self.section is None:
            return
        values = {
            "MaxSpeed": self.max_speed.text(),
            "MaxSpeedWarningDistance": self.max_speed_warning_distance.text(),
            "MaxSpeedWarningTrigger": self.max_speed_warning_trigger.text(),
            "MaxSpeedWarningInstruction": self.max_speed_warning_instruction.toPlainText().replace("\n", " "),
            "MaxSpeedExceededInstruction": self.max_speed_exceeded_instruction.toPlainText().replace("\n", " "),
        }
        for key, value in values.items():
            self.section.set_value(key, value)

    def set_captured_point(self, latitude: str, longitude: str, endpoint: str = "a") -> None:
        if endpoint == "b":
            self.position_mode.setCurrentText("Gate")
            self.lat_b.setText(latitude)
            self.lon_b.setText(longitude)
        else:
            self.lat_a.setText(latitude)
            self.lon_a.setText(longitude)

    def set_gate_parameters(self, width_m: str, forward_offset_m: str) -> None:
        width_blocker = QSignalBlocker(self.gate_width)
        offset_blocker = QSignalBlocker(self.gate_forward_offset)
        self.gate_width.setText(width_m)
        self.gate_forward_offset.setText(forward_offset_m)
        del width_blocker, offset_blocker

    def set_body_radius_value(self, value: str, source: str) -> None:
        blocker = QSignalBlocker(self.body_radius)
        self.body_radius.setText(value)
        del blocker
        self.set_body_radius_source(source)
        self._update_gate_distance()

    def set_body_radius_source(self, source: str | None) -> None:
        if not self.body_radius.text().strip():
            self.body_radius_source.setText("Source: —")
        else:
            self.body_radius_source.setText(f"Source: {source or 'Manual / imported'}")

    def _update_gate_distance(self, *_args) -> None:
        is_gate = self.position_mode.currentText() == "Gate"
        self.gate_distance_label.setVisible(is_gate)
        self.convert_gate_button.setVisible(is_gate)
        midpoint_values = (
            self.lat_a.text().strip(),
            self.lon_a.text().strip(),
            self.lat_b.text().strip(),
            self.lon_b.text().strip(),
        )
        midpoint_available = False
        if is_gate and all(midpoint_values):
            try:
                spherical_midpoint(*midpoint_values)
                midpoint_available = True
            except ValueError:
                pass
        self.convert_gate_button.setEnabled(midpoint_available)
        if not is_gate:
            return
        values = (
            self.lat_a.text().strip(),
            self.lon_a.text().strip(),
            self.lat_b.text().strip(),
            self.lon_b.text().strip(),
            self.body_radius.text().strip(),
        )
        if not all(values):
            self.gate_distance_label.setText("Measured A–B distance: —")
            self.gate_distance_label.setToolTip(
                "Enter or capture both gate endpoints and a body radius to measure the gate."
            )
            return
        try:
            distance_m = great_circle_distance_m(*values)
        except ValueError as exc:
            self.gate_distance_label.setText("Measured A–B distance: unavailable")
            self.gate_distance_label.setToolTip(str(exc))
            return
        if distance_m >= 1000:
            text = f"{distance_m / 1000:,.3f} km ({distance_m:,.1f} m)"
        else:
            text = f"{distance_m:,.1f} m"
        self.gate_distance_label.setText(f"Measured A–B distance: {text}")
        self.gate_distance_label.setToolTip(
            "Great-circle surface distance calculated from endpoints A and B and Body radius."
        )

    def _update_collapsible_counts(self, *_args) -> None:
        travel_values = (
            self.enter_system_instruction.toPlainText().strip(),
            self.approach_body_instruction.toPlainText().strip(),
            self.last_leg_message.toPlainText().strip()
            if self.section and self.section.section_type is SectionType.END
            else "",
        )
        speed_values = (
            self.max_speed.text().strip(),
            self.max_speed_warning_distance.text().strip(),
            self.max_speed_warning_trigger.text().strip(),
            self.max_speed_warning_instruction.toPlainText().strip(),
            self.max_speed_exceeded_instruction.toPlainText().strip(),
        )
        self.travel_box.set_item_count(sum(bool(value) for value in travel_values))
        self.speed_box.set_item_count(sum(bool(value) for value in speed_values))
        self.conditions_box.set_item_count(len(self.conditions.values()))

    def current_altitude_mode(self) -> AltitudeMode:
        data = self.altitude_mode.currentData()
        try:
            return AltitudeMode(str(data))
        except ValueError:
            return AltitudeMode.NONE

    def metadata_snapshot(self) -> StageMetadata:
        return StageMetadata(
            display_name=self.waypoint_name.text().strip()
            if self.section and self.section.section_type is SectionType.WAYPOINT
            else "",
            altitude_mode=self.current_altitude_mode(),
            maximum_altitude=self.maximum_altitude.text().strip(),
            altitude_locked=self.altitude_locked.isChecked(),
        )

    def _update_altitude_fields(self, mode: AltitudeMode) -> None:
        maximum = mode is AltitudeMode.MAXIMUM
        target = mode is AltitudeMode.TARGET
        self.maximum_altitude_label.setVisible(maximum)
        self.maximum_altitude.setVisible(maximum)
        self.altitude_label.setVisible(target)
        self.altitude.setVisible(target)
        self.altitude_proximity_label.setVisible(target)
        self.altitude_proximity.setVisible(target)

    def _altitude_mode_changed(self, *_args) -> None:
        mode = self.current_altitude_mode()
        if self.loading:
            self._update_altitude_fields(mode)
            self._displayed_altitude_mode = mode
            return
        previous = getattr(self, "_displayed_altitude_mode", AltitudeMode.NONE)
        if mode is AltitudeMode.MAXIMUM and previous is AltitudeMode.TARGET:
            altitude = decimal_value(self.altitude.text())
            tolerance = decimal_value(self.altitude_proximity.text())
            maximum = format(altitude + tolerance, "f") if altitude is not None and tolerance is not None else ""
            blocker = QSignalBlocker(self.maximum_altitude)
            self.maximum_altitude.setText(maximum)
            del blocker
        elif mode is AltitudeMode.TARGET and previous is AltitudeMode.MAXIMUM:
            maximum = decimal_value(self.maximum_altitude.text())
            half = format(maximum / Decimal("2"), "f") if maximum is not None and maximum > 0 else ""
            altitude_blocker = QSignalBlocker(self.altitude)
            tolerance_blocker = QSignalBlocker(self.altitude_proximity)
            self.altitude.setText(half)
            self.altitude_proximity.setText(half)
            del altitude_blocker, tolerance_blocker
        self._displayed_altitude_mode = mode
        self._update_altitude_fields(mode)
        self._emit_changed()

    def _emit_metadata_changed(self, *_args) -> None:
        if not self.loading:
            self.metadata_changed.emit()

    def _position_changed(self, *_args) -> None:
        if not self.loading:
            self.position_touched = True
            self.apply()
            self.changed.emit()

    def _update_gate_fields(self, mode: str) -> None:
        enabled = mode == "Gate"
        self.lat_b.setEnabled(enabled)
        self.lon_b.setEnabled(enabled)
        self._update_gate_distance()

    def _emit_changed(self, *_args) -> None:
        if not self.loading:
            self.apply()
            self.changed.emit()


class MainWindow(QMainWindow):
    def __init__(
        self,
        edsm_client: EdsmRadiusClient | None = None,
        settings: QSettings | None = None,
    ):
        super().__init__()
        # Keep the pre-release settings key so existing testers retain their
        # window layout, recent files, and journal-directory override.
        self.settings = settings if settings is not None else QSettings("EDTT Creator", SETTINGS_APP_NAME)
        self.theme_mode = ThemeMode.from_setting(
            self.settings.value("appearance/theme", ThemeMode.SYSTEM.value, str)
        )
        apply_theme(self.theme_mode)
        configured_dir = self.settings.value("journal_directory", "", str)
        radius_cache = BodyRadiusCache.from_json(
            self.settings.value("body_radius_cache", "", str)
        )
        self.live_reader = LiveLocationReader(configured_dir or None, radius_cache=radius_cache)
        self.edsm_client = edsm_client or EdsmRadiusClient(self)
        self.edsm_client.succeeded.connect(self._edsm_radius_succeeded)
        self.edsm_client.failed.connect(self._edsm_radius_failed)
        self._radius_lookup_targets: dict[tuple[str, str], set[str]] = {}
        self._radius_explicit_requests: set[tuple[str, str]] = set()
        self._radius_prompted: set[tuple[str, str]] = set()
        self._radius_lookup_attempted: set[tuple[str, str]] = set()
        self.live_result = LiveResult(LiveState.MISSING, None, "Waiting for game data.")
        self.project = ProjectData.new()
        self.project_path: Path | None = None
        self.dirty = False
        self.current_section_id: str | None = None
        self.recent_menu = None
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.resize(1480, 880)
        self._build_actions()
        self._build_ui()
        self._bind_collapsible_settings()
        self.tooltip_filter = DelayedTooltipFilter(self, delay_ms=1800)
        self.tooltip_filter.register_existing(self)
        QApplication.instance().styleHints().colorSchemeChanged.connect(
            self._system_color_scheme_changed
        )
        self._restore_settings()
        self._set_project(ProjectData.new(), None, dirty=False)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(750)
        self.poll_timer.timeout.connect(self._poll_live)
        self.poll_timer.start()

    def _build_actions(self) -> None:
        self.new_action = QAction("&New", self, shortcut="Ctrl+N", triggered=self.new_project)
        # QAction.triggered emits a checked boolean. Do not pass that value to
        # open_file(), where it would be mistaken for an explicit path and
        # prevent the file picker from opening.
        self.open_action = QAction("&Open…", self, shortcut="Ctrl+O", triggered=lambda: self.open_file())
        self.save_action = QAction("Save Editable Project (.json)", self, shortcut="Ctrl+S", triggered=self.save_project)
        self.save_as_action = QAction("Save Editable Project &As…", self, shortcut="Ctrl+Shift+S", triggered=self.save_project_as)
        self.export_action = QAction("&Create EDCoPilot TXT…", self, shortcut="Ctrl+E", triggered=self.export_file)
        self.exit_action = QAction("E&xit", self, triggered=self.close)
        self.about_action = QAction(f"About v{DISPLAY_VERSION}", self, triggered=self.show_about)
        self.reset_edsm_action = QAction(
            "Reset EDSM radius permission", self, triggered=self.reset_edsm_permission
        )
        menu = self.menuBar().addMenu("&File")
        for action in (self.new_action, self.open_action):
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(self.export_action)
        menu.addSeparator()
        menu.addAction(self.save_action)
        menu.addAction(self.save_as_action)
        self.recent_menu = menu.addMenu("Recent Files")
        menu.addSeparator()
        menu.addAction(self.exit_action)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction(self.reset_edsm_action)
        help_menu.addSeparator()
        help_menu.addAction(self.about_action)
        appearance_menu = self.menuBar().addMenu("&Appearance")
        self.theme_action_group = QActionGroup(self)
        self.theme_action_group.setExclusive(True)
        self.theme_actions: dict[ThemeMode, QAction] = {}
        for mode, label in (
            (ThemeMode.SYSTEM, "Follow Windows"),
            (ThemeMode.LIGHT, "Light"),
            (ThemeMode.DARK, "Dark"),
        ):
            action = QAction(label, self, checkable=True)
            action.setChecked(mode is self.theme_mode)
            action.triggered.connect(lambda _checked=False, selected=mode: self.set_theme(selected))
            self.theme_action_group.addAction(action)
            appearance_menu.addAction(action)
            self.theme_actions[mode] = action
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        for action in (self.new_action, self.open_action, self.export_action):
            toolbar.addAction(action)
        self.addToolBar(toolbar)
        self._rebuild_recent_menu()

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Race structure"))
        self.navigation = QListWidget()
        self.navigation.currentItemChanged.connect(self._navigation_changed)
        left_layout.addWidget(self.navigation, 1)
        waypoint_row = QGridLayout()
        buttons = [
            ("Add", self.add_waypoint), ("Capture + add", self.capture_and_add),
            ("Duplicate", self.duplicate_waypoint), ("Delete", self.delete_waypoint),
            ("Move up", lambda: self.move_waypoint(-1)), ("Move down", lambda: self.move_waypoint(1)),
        ]
        for index, (text, callback) in enumerate(buttons):
            button = QPushButton(text)
            button.clicked.connect(callback)
            descriptions = {
                "Add": "Add a new waypoint after the existing waypoints.",
                "Capture + add": "Add a waypoint and fill it from the current live surface coordinates.",
                "Duplicate": "Duplicate the selected waypoint, including editor-only metadata.",
                "Delete": "Delete the selected waypoint.",
                "Move up": "Move the selected waypoint one place earlier in the route.",
                "Move down": "Move the selected waypoint one place later in the route.",
            }
            button.setToolTip(descriptions[text])
            waypoint_row.addWidget(button, index // 2, index % 2)
        left_layout.addLayout(waypoint_row)

        self.form_stack = QStackedWidget()
        self.event_form = EventInfoForm()
        self.stage_form = StageForm()
        self.event_form.changed.connect(self._document_changed)
        self.stage_form.changed.connect(self._document_changed)
        self.stage_form.metadata_changed.connect(self._stage_metadata_changed)
        self.stage_form.capture_a_requested.connect(lambda: self.capture_selected("a"))
        self.stage_form.capture_b_requested.connect(lambda: self.capture_selected("b"))
        self.stage_form.calculate_gate_requested.connect(self.calculate_gate_from_live)
        self.stage_form.convert_gate_requested.connect(self.convert_gate_to_point)
        self.stage_form.gate_parameters_changed.connect(self._update_capture_buttons)
        self.stage_form.find_radius_requested.connect(self.find_radius_for_selected)
        self.stage_form.body_radius_manually_edited.connect(self._body_radius_manually_edited)
        self.form_stack.addWidget(self.event_form)
        self.stage_scroll = QScrollArea()
        self.stage_scroll.setWidgetResizable(True)
        self.stage_scroll.setWidget(self.stage_form)
        self.form_stack.addWidget(self.stage_scroll)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        live_box = QGroupBox("Live game location")
        live_layout = QVBoxLayout(live_box)
        self.live_status = QLabel("Waiting for game data")
        self.live_status.setWordWrap(True)
        live_layout.addWidget(self.live_status)
        live_form = QFormLayout()
        self.live_labels: dict[str, QLabel] = {}
        for key, label in (
            ("system", "System"), ("body", "Body"), ("station", "Station"),
            ("body_radius_km", "Body radius"),
            ("latitude", "Latitude"), ("longitude", "Longitude"),
            ("altitude", "Altitude"), ("heading", "Heading"),
        ):
            value = QLabel("—")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.live_labels[key] = value
            live_form.addRow(label, value)
        live_layout.addLayout(live_form)
        live_buttons = QHBoxLayout()
        self.capture_button = QPushButton("Capture into selected")
        self.capture_add_button = QPushButton("Capture + add waypoint")
        folder_button = QPushButton("Journal folder…")
        self.capture_button.clicked.connect(lambda: self.capture_selected(None))
        self.capture_add_button.clicked.connect(self.capture_and_add)
        folder_button.clicked.connect(self.choose_journal_directory)
        self.capture_button.setToolTip(
            "Fill the selected Start, Waypoint, or End from valid live surface coordinates."
        )
        self.capture_add_button.setToolTip(
            "Create a new waypoint and fill it from valid live surface coordinates."
        )
        folder_button.setToolTip(
            "Override the automatically detected Elite Dangerous Saved Games journal folder."
        )
        live_buttons.addWidget(self.capture_button)
        live_buttons.addWidget(self.capture_add_button)
        live_buttons.addWidget(folder_button)
        live_layout.addLayout(live_buttons)
        right_layout.addWidget(live_box)

        tabs = QTabWidget()
        self.validation_list = QListWidget()
        self.validation_list.itemDoubleClicked.connect(self._jump_to_issue)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("Consolas", 9))
        tabs.addTab(self.validation_list, "Validation")
        tabs.addTab(self.preview, "File preview")
        right_layout.addWidget(tabs, 1)

        splitter.addWidget(left)
        splitter.addWidget(self.form_stack)
        splitter.addWidget(right)
        splitter.setSizes([260, 690, 520])
        self.setCentralWidget(splitter)
        self.splitter = splitter
        self._apply_theme_styles()

    def _bind_collapsible_settings(self) -> None:
        sections = (
            (self.event_form.advanced_section, "event_advanced", False),
            (self.stage_form.travel_box, "stage_travel", True),
            (self.stage_form.speed_box, "stage_speed", False),
            (self.stage_form.conditions_box, "stage_conditions", True),
            (self.stage_form.advanced_section, "stage_advanced", False),
        )
        for section, key, default in sections:
            expanded = self.settings.value(
                f"editor_sections/{key}", default, bool
            )
            section.set_expanded(expanded)
            section.toggled.connect(
                lambda open_state, setting_key=key: self.settings.setValue(
                    f"editor_sections/{setting_key}", open_state
                )
            )

    def set_theme(self, mode: ThemeMode) -> None:
        self.theme_mode = ThemeMode(mode)
        self.settings.setValue("appearance/theme", self.theme_mode.value)
        apply_theme(self.theme_mode)
        action = self.theme_actions.get(self.theme_mode)
        if action is not None:
            action.setChecked(True)
        self._apply_theme_styles()
        self.refresh_validation_and_preview()
        self._refresh_live_status_style()

    def _system_color_scheme_changed(self, *_args) -> None:
        if self.theme_mode is ThemeMode.SYSTEM:
            self._apply_theme_styles()
            self.refresh_validation_and_preview()
            self._refresh_live_status_style()

    def _apply_theme_styles(self) -> None:
        error = semantic_color("error", self).name()
        self.setStyleSheet(
            "QLabel#sectionTitle { font-size: 18px; font-weight: 600; margin-bottom: 8px; }"
            "QGroupBox { font-weight: 600; margin-top: 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
            "QToolButton#collapsibleHeader { font-weight: 600; text-align: left; "
            "padding: 5px 4px; border-bottom: 1px solid palette(mid); }"
            f"QToolButton#collapsibleHeader[hasValidationError=\"true\"] {{ "
            f"color: {error}; border: 1px solid {error}; }}"
        )

    def _restore_settings(self) -> None:
        geometry = self.settings.value("window_geometry")
        if geometry:
            self.restoreGeometry(geometry)
        state = self.settings.value("window_state")
        if state:
            self.restoreState(state)

    def _set_project(self, project: ProjectData, path: Path | None, *, dirty: bool) -> None:
        self.project = project
        self.project_path = path
        self.dirty = dirty
        self.refresh_navigation()
        self.refresh_validation_and_preview()
        self._update_title()

    def refresh_navigation(self, select_id: str | None = None) -> None:
        select_id = select_id or self.current_section_id
        blocker = QSignalBlocker(self.navigation)
        self.navigation.clear()
        waypoint_number = 0
        selected_row = 0
        for section in self.project.document.sections():
            kind = section.section_type
            if kind is SectionType.EVENTINFO:
                label = "Event Info"
            elif kind is SectionType.START:
                label = "Start"
            elif kind is SectionType.END:
                label = "End"
            elif kind is SectionType.WAYPOINT:
                waypoint_number += 1
                name = self.project.stage_metadata.get(section.node_id, StageMetadata()).display_name.strip()
                label = f"Waypoint {waypoint_number}"
                if name:
                    label += f" — {name}"
            else:
                label = f"Unknown: {section.name}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, section.node_id)
            self.navigation.addItem(item)
            if section.node_id == select_id:
                selected_row = self.navigation.count() - 1
        del blocker
        if self.navigation.count():
            self.navigation.setCurrentRow(selected_row)
            self._show_section(self.navigation.item(selected_row).data(Qt.ItemDataRole.UserRole))

    def _navigation_changed(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current:
            self._show_section(current.data(Qt.ItemDataRole.UserRole))

    def _show_section(self, node_id: str) -> None:
        section = self.project.document.section_by_id(node_id)
        if section is None:
            return
        self.current_section_id = node_id
        if section.section_type is SectionType.EVENTINFO:
            self.event_form.set_section(section)
            self.form_stack.setCurrentIndex(0)
        else:
            label = self.navigation.currentItem().text() if self.navigation.currentItem() else section.name.title()
            self.stage_form.set_section(section, label, self.project.metadata_for(section.node_id))
            gate_metadata = self.project.capture_metadata.get(section.node_id, {}).get("calculated_gate")
            if isinstance(gate_metadata, dict):
                self.stage_form.set_gate_parameters(
                    str(gate_metadata.get("width_m", "2000")),
                    str(gate_metadata.get("forward_offset_m", "0")),
                )
            radius_metadata = self.project.capture_metadata.get(section.node_id, {})
            source = radius_metadata.get("body_radius_source")
            self.stage_form.set_body_radius_source(
                str(source) if source else "Manual / imported"
            )
            self.form_stack.setCurrentIndex(1)
        self._update_capture_buttons()
        self._update_collapsible_validation(
            getattr(self, "validation_issues", validate_project(self.project))
        )

    def _document_changed(self) -> None:
        self._sync_current_stage_metadata()
        self.dirty = True
        self.refresh_validation_and_preview()
        self._update_title()
        self._update_capture_buttons()

    def _sync_current_stage_metadata(self) -> None:
        section = self.project.document.section_by_id(self.current_section_id or "")
        if section is None or section.section_type is SectionType.EVENTINFO:
            return
        if self.stage_form.section is not section:
            return
        self.project.stage_metadata[section.node_id] = self.stage_form.metadata_snapshot()

    def _stage_metadata_changed(self) -> None:
        self._sync_current_stage_metadata()
        section = self.project.document.section_by_id(self.current_section_id or "")
        if section and section.section_type is SectionType.WAYPOINT:
            item = self.navigation.currentItem()
            if item:
                waypoint_number = sum(
                    1
                    for row in range(self.navigation.currentRow() + 1)
                    if self.project.document.section_by_id(
                        self.navigation.item(row).data(Qt.ItemDataRole.UserRole)
                    )
                    and self.project.document.section_by_id(
                        self.navigation.item(row).data(Qt.ItemDataRole.UserRole)
                    ).section_type is SectionType.WAYPOINT
                )
                name = self.project.stage_metadata[section.node_id].display_name.strip()
                label = f"Waypoint {waypoint_number}" + (f" — {name}" if name else "")
                item.setText(label)
                self.stage_form.title.setText(label)
        self.dirty = True
        self.refresh_validation_and_preview()
        self._update_title()

    def refresh_validation_and_preview(self) -> None:
        issues = validate_project(self.project)
        self.validation_issues = issues
        self.validation_list.clear()
        errors = 0
        warnings = 0
        for issue in issues:
            prefix = "ERROR" if issue.severity is Severity.ERROR else "WARNING"
            item = QListWidgetItem(f"{prefix}: {issue.message}")
            item.setData(Qt.ItemDataRole.UserRole, issue)
            if issue.severity is Severity.ERROR:
                errors += 1
                item.setForeground(semantic_color("error", self))
            else:
                warnings += 1
                item.setForeground(semantic_color("warning", self))
            self.validation_list.addItem(item)
        self.preview.setPlainText(serialize_document(self.project.document))
        self.statusBar().showMessage(f"{errors} error(s), {warnings} warning(s)")
        self._update_collapsible_validation(issues)

    def _collapsible_for_issue(self, issue: ValidationIssue) -> CollapsibleSection | None:
        section = self.project.document.section_by_id(issue.section_id or "")
        if section is None:
            return None
        field = (issue.field or "").casefold()
        known = KNOWN_FIELDS.get(section.name.casefold(), set())
        if issue.field and field not in known and field != "maximumaltitude":
            return (
                self.event_form.advanced_section
                if section.section_type is SectionType.EVENTINFO
                else self.stage_form.advanced_section
            )
        if section.section_type is SectionType.EVENTINFO:
            return None
        if field in {
            "maxspeed",
            "maxspeedwarningdistance",
            "maxspeedwarningtrigger",
            "maxspeedwarninginstruction",
            "maxspeedexceededinstruction",
        }:
            return self.stage_form.speed_box
        if field == "condition":
            return self.stage_form.conditions_box
        if field in {
            "entersysteminstruction",
            "approachbodyinstruction",
            "lastlegmessage",
        }:
            return self.stage_form.travel_box
        message = issue.message.casefold()
        if not issue.field and ("unrecognized" in message or "unknown" in message):
            return (
                self.event_form.advanced_section
                if section.section_type is SectionType.EVENTINFO
                else self.stage_form.advanced_section
            )
        return None

    def _update_collapsible_validation(self, issues: list[ValidationIssue]) -> None:
        collapsible = (
            self.event_form.advanced_section,
            self.stage_form.travel_box,
            self.stage_form.speed_box,
            self.stage_form.conditions_box,
            self.stage_form.advanced_section,
        )
        counts = {section: 0 for section in collapsible}
        for issue in issues:
            if issue.severity is not Severity.ERROR or issue.section_id != self.current_section_id:
                continue
            target = self._collapsible_for_issue(issue)
            if target is not None:
                counts[target] += 1
        for section, count in counts.items():
            section.set_validation_error_count(count)

    def _jump_to_issue(self, item: QListWidgetItem) -> None:
        issue: ValidationIssue = item.data(Qt.ItemDataRole.UserRole)
        if issue.section_id:
            for row in range(self.navigation.count()):
                nav_item = self.navigation.item(row)
                if nav_item.data(Qt.ItemDataRole.UserRole) == issue.section_id:
                    self.navigation.setCurrentRow(row)
                    break
        target = self._collapsible_for_issue(issue)
        if target is not None:
            target.set_expanded(True)
            if self.form_stack.currentIndex() == 1:
                self.stage_scroll.ensureWidgetVisible(target)

    def _update_title(self) -> None:
        name = self.project_path.name if self.project_path else "Untitled"
        marker = "*" if self.dirty else ""
        self.setWindowTitle(f"{marker}{name} — {APP_DISPLAY_NAME}")

    def show_about(self) -> None:
        existing = getattr(self, "about_dialog", None)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        self.about_dialog = AboutDialog(self)
        self.about_dialog.show()

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        result = QMessageBox.question(
            self, "Unsaved changes", "Discard the unsaved project changes?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return result == QMessageBox.StandardButton.Discard

    def new_project(self) -> None:
        if self._confirm_discard():
            self._set_project(ProjectData.new(), None, dirty=False)

    def open_file(self, path: str | None = None) -> None:
        if not self._confirm_discard():
            return
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "Open time trial", "",
                "Time trial projects (*.edttc.json);;EDCoPilot exports (*.export.txt *.txt);;All files (*)",
            )
        if not path:
            return
        source = Path(path)
        try:
            if source.name.lower().endswith(".edttc.json"):
                project = load_project(source)
                project_path = source
            else:
                project = ProjectData(document=load_document(source), last_export_path=str(source))
                project_path = None
            self._set_project(project, project_path, dirty=False)
            self._add_recent(source)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", f"Could not open the file:\n{exc}")

    def save_project(self) -> bool:
        if self.project_path is None:
            return self.save_project_as()
        try:
            save_project(self.project, self.project_path)
            self.dirty = False
            self._update_title()
            self._add_recent(self.project_path)
            self.statusBar().showMessage(
                "Editable JSON project saved. Use 'Create EDCoPilot TXT' for the file imported by EDCoPilot.",
                10000,
            )
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", f"Could not save the project:\n{exc}")
            return False

    def save_project_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save editable project — not an EDCoPilot import file",
            "",
            "Editor project (*.edttc.json)",
        )
        if not path:
            return False
        if not path.lower().endswith(".edttc.json"):
            path += ".edttc.json"
        self.project_path = Path(path)
        return self.save_project()

    def export_file(self) -> None:
        issues = validate_project(self.project)
        if has_errors(issues):
            count = sum(issue.severity is Severity.ERROR for issue in issues)
            QMessageBox.warning(
                self, "Export blocked",
                f"Fix the {count} validation error(s) shown in the Validation tab before exporting.",
            )
            return
        initial = self.project.last_export_path or ""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Create EDCoPilot import TXT",
            initial,
            "EDCoPilot import file (*.export.txt)",
        )
        if not path:
            return
        if not path.lower().endswith(".export.txt"):
            path += ".export.txt"
        try:
            save_document(self.project.document, path)
            self.project.last_export_path = path
            self.dirty = True
            self._update_title()
            self.statusBar().showMessage(f"Exported {path}", 8000)
            QMessageBox.information(
                self,
                "EDCoPilot TXT created",
                f"Import this file in EDCoPilot:\n{path}\n\n"
                "The editable *.edttc.json project file is not an EDCoPilot import file.",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", f"Could not export the file:\n{exc}")

    def _default_waypoint_state(self) -> str:
        event = self.project.document.event_info
        race_type = event.get("RaceType") if event else "SHIP"
        mapping = {"SHIP": "ShipPass", "FIGHTER": "Fighter", "SRV": "SRV", "ONFOOT": "OnFoot"}
        if not self.project.document.waypoints and self.project.document.start:
            start_state = self.project.document.start.get("PlayerState")
            if start_state in {"ShipFlyingStart", "FighterFlyingStart"}:
                return start_state
        return mapping.get(race_type, "ShipPass")

    def add_waypoint(self) -> SectionNode:
        previous = self.project.document.waypoints[-1].section if self.project.document.waypoints else None
        section = self.project.document.add_waypoint(self._default_waypoint_state())
        metadata = StageMetadata(
            altitude_mode=AltitudeMode.MAXIMUM,
        )
        if previous is not None:
            previous_metadata = self.project.stage_metadata.get(previous.node_id, StageMetadata())
            if inferred_altitude_mode(previous, previous_metadata) is AltitudeMode.MAXIMUM:
                metadata.maximum_altitude = inferred_maximum_altitude(previous, previous_metadata)
                apply_maximum_altitude(section, metadata.maximum_altitude)
        self.project.stage_metadata[section.node_id] = metadata
        self.dirty = True
        self.refresh_navigation(section.node_id)
        self.refresh_validation_and_preview()
        self._update_title()
        return section

    def _current_waypoint(self) -> SectionNode | None:
        section = self.project.document.section_by_id(self.current_section_id or "")
        return section if section and section.section_type is SectionType.WAYPOINT else None

    def duplicate_waypoint(self) -> None:
        section = self._current_waypoint()
        if section:
            duplicate = self.project.document.duplicate_waypoint(section.node_id)
            if duplicate:
                source_metadata = deepcopy(self.project.metadata_for(section.node_id))
                if source_metadata.display_name.strip():
                    source_metadata.display_name = f"{source_metadata.display_name.strip()} (copy)"
                self.project.stage_metadata[duplicate.node_id] = source_metadata
                if section.node_id in self.project.capture_metadata:
                    self.project.capture_metadata[duplicate.node_id] = deepcopy(
                        self.project.capture_metadata[section.node_id]
                    )
                self.dirty = True
                self.refresh_navigation(duplicate.node_id)
                self.refresh_validation_and_preview()
                self._update_title()

    def delete_waypoint(self) -> None:
        section = self._current_waypoint()
        if not section:
            return
        if len(self.project.document.waypoints) <= 1:
            QMessageBox.information(self, "Waypoint required", "A time trial must contain at least one waypoint.")
            return
        if self.project.document.remove_waypoint(section.node_id):
            self.project.stage_metadata.pop(section.node_id, None)
            self.project.capture_metadata.pop(section.node_id, None)
            self.dirty = True
            self.current_section_id = None
            self.refresh_navigation()
            self.refresh_validation_and_preview()
            self._update_title()

    def move_waypoint(self, direction: int) -> None:
        section = self._current_waypoint()
        if section and self.project.document.move_waypoint(section.node_id, direction):
            self.dirty = True
            self.refresh_navigation(section.node_id)
            self.refresh_validation_and_preview()
            self._update_title()

    def _poll_live(self) -> None:
        self.live_result = self.live_reader.poll()
        result = self.live_result
        self.live_status.setText(result.message)
        self._refresh_live_status_style()
        snapshot = result.snapshot
        for key, label in self.live_labels.items():
            value = getattr(snapshot, key, None) if snapshot else None
            if isinstance(value, Decimal):
                value = format(value, "f")
            if key == "body_radius_km" and value not in (None, "") and snapshot:
                value = f"{value} km ({snapshot.body_radius_source or 'local'})"
            label.setText(str(value) if value not in (None, "") else "—")
        self._persist_radius_cache()
        if snapshot is not None:
            self._consider_radius_for_selected(snapshot)
        self._update_capture_buttons()

    def _refresh_live_status_style(self) -> None:
        roles = {
            LiveState.LIVE: "success",
            LiveState.NO_COORDINATES: "warning",
            LiveState.STALE: "muted",
            LiveState.MISSING: "error",
            LiveState.INVALID: "warning",
        }
        color = semantic_color(roles[self.live_result.state], self).name()
        self.live_status.setStyleSheet(f"font-weight: 600; color: {color};")

    def _persist_radius_cache(self) -> None:
        cache = self.live_reader.radius_cache
        if cache.dirty:
            self.settings.setValue("body_radius_cache", cache.to_json())
            cache.mark_clean()

    def _selected_stage_section(self) -> SectionNode | None:
        section = self.project.document.section_by_id(self.current_section_id or "")
        if section is None or section.section_type is SectionType.EVENTINFO:
            return None
        return section

    def _body_radius_manually_edited(self) -> None:
        section = self._selected_stage_section()
        if section is None:
            return
        metadata = dict(self.project.capture_metadata.get(section.node_id, {}))
        value = self.stage_form.body_radius.text().strip()
        if value:
            metadata["body_radius_km"] = value
            metadata["body_radius_source"] = "Manual"
            self.stage_form.set_body_radius_source("Manual")
        else:
            metadata.pop("body_radius_km", None)
            metadata.pop("body_radius_source", None)
            self.stage_form.set_body_radius_source(None)
        if metadata:
            self.project.capture_metadata[section.node_id] = metadata
        else:
            self.project.capture_metadata.pop(section.node_id, None)

    def _radius_context(
        self,
        section: SectionNode,
        snapshot: LocationSnapshot | None,
    ) -> tuple[str, str] | None:
        stage_system = section.value("System").strip()
        stage_body = section.value("Body").strip()
        if stage_system and stage_body:
            return stage_system, stage_body
        if snapshot is None or not snapshot.system or not snapshot.body:
            return None
        if stage_system and normalize_name(stage_system) != normalize_name(snapshot.system):
            return None
        if stage_body and normalize_name(stage_body) != normalize_name(snapshot.body):
            return None
        return stage_system or snapshot.system, stage_body or snapshot.body

    def _consider_radius_for_selected(self, snapshot: LocationSnapshot) -> None:
        section = self._selected_stage_section()
        if (
            section is None
            or section.value("BodyRadius").strip()
            or not snapshot.capture_ready
        ):
            return
        context = self._radius_context(section, snapshot)
        if context is None:
            return
        if snapshot.body_radius_km is not None:
            self._apply_radius_to_section(
                section,
                format(snapshot.body_radius_km, "f"),
                snapshot.body_radius_source or "Journal",
            )
            return
        self._request_edsm_radius(section, *context, automatic=True)

    def find_radius_for_selected(self) -> None:
        section = self._selected_stage_section()
        if section is None:
            return
        if section.value("BodyRadius").strip():
            QMessageBox.information(
                self,
                "Body radius preserved",
                "This stage already has a Body radius. Clear it first if you want to replace it; "
                "EDTT Definer never overwrites an imported or manually entered value automatically.",
            )
            return
        snapshot = self.live_result.snapshot
        context = self._radius_context(section, snapshot)
        if context is None:
            QMessageBox.warning(
                self,
                "Cannot find body radius",
                "Enter both System and Body, or wait until the live game location provides them.",
            )
            return
        system, body = context
        snapshot_matches = (
            snapshot is not None
            and normalize_name(snapshot.system) == normalize_name(system)
            and normalize_name(snapshot.body) == normalize_name(body)
        )
        if snapshot_matches and snapshot and snapshot.body_radius_km is not None:
            self._apply_radius_to_section(
                section,
                format(snapshot.body_radius_km, "f"),
                snapshot.body_radius_source or "Journal",
            )
            return
        record = self.live_reader.radius_cache.get(
            system=system,
            body=body,
            system_address=snapshot.system_address if snapshot_matches and snapshot else None,
            body_id=snapshot.body_id if snapshot_matches and snapshot else None,
        )
        if record is not None:
            source = "EDSM cache" if record.origin == "edsm" else "Local cache"
            self._apply_radius_to_section(section, format(record.radius_km, "f"), source)
            return
        self._request_edsm_radius(section, system, body, automatic=False)

    def _apply_radius_to_section(
        self,
        section: SectionNode,
        radius_km: str,
        source: str,
    ) -> bool:
        if section.value("BodyRadius").strip():
            return False
        section.set_value("BodyRadius", radius_km)
        metadata = dict(self.project.capture_metadata.get(section.node_id, {}))
        metadata["body_radius_km"] = radius_km
        metadata["body_radius_source"] = source
        self.project.capture_metadata[section.node_id] = metadata
        if section.node_id == self.current_section_id and self.stage_form.section is section:
            self.stage_form.set_body_radius_value(radius_km, source)
        self.dirty = True
        self.refresh_validation_and_preview()
        self._update_title()
        self._update_capture_buttons()
        self.statusBar().showMessage(
            f"Body radius set to {radius_km} km from {source}.", 10000
        )
        return True

    def _request_edsm_radius(
        self,
        section: SectionNode,
        system: str,
        body: str,
        *,
        automatic: bool,
    ) -> None:
        key = (normalize_name(system), normalize_name(body))
        if not all(key):
            return
        if automatic and key in self._radius_lookup_attempted:
            return
        permission = self.settings.value("edsm_radius_permission", "ask", str)
        if permission != "always":
            if automatic and key in self._radius_prompted:
                return
            decision = self._ask_edsm_permission(system, body)
            self._radius_prompted.add(key)
            if decision == "always":
                self.settings.setValue("edsm_radius_permission", "always")
            elif decision != "once":
                return
        self._radius_lookup_attempted.add(key)
        self._radius_lookup_targets.setdefault(key, set()).add(section.node_id)
        if not automatic:
            self._radius_explicit_requests.add(key)
        if self.edsm_client.lookup(system, body):
            self.statusBar().showMessage(
                f"Looking up {body} in {system} on EDSM…", 10000
            )

    def _ask_edsm_permission(self, system: str, body: str) -> str:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Allow EDSM radius lookup?")
        box.setText(
            f"No local radius was found for {body} in {system}.\n\n"
            "EDTT Definer can ask EDSM for the system's body list. This sends the "
            "system name to edsm.net; the body match is performed locally."
        )
        once = box.addButton("Allow once", QMessageBox.ButtonRole.AcceptRole)
        always = box.addButton("Always allow", QMessageBox.ButtonRole.YesRole)
        not_now = box.addButton("Not now", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(once)
        box.exec()
        clicked = box.clickedButton()
        if clicked is always:
            return "always"
        if clicked is once:
            return "once"
        assert clicked is not_now or clicked is None
        return "not_now"

    def _edsm_radius_succeeded(self, system: str, body: str, radius_km: str) -> None:
        key = (normalize_name(system), normalize_name(body))
        snapshot = self.live_result.snapshot
        snapshot_matches = (
            snapshot is not None
            and normalize_name(snapshot.system) == key[0]
            and normalize_name(snapshot.body) == key[1]
        )
        self.live_reader.remember_radius(
            radius_km,
            origin="edsm",
            system=system,
            body=body,
            system_address=snapshot.system_address if snapshot_matches and snapshot else None,
            body_id=snapshot.body_id if snapshot_matches and snapshot else None,
        )
        self._persist_radius_cache()
        targets = self._radius_lookup_targets.pop(key, set())
        self._radius_explicit_requests.discard(key)
        applied = 0
        for node_id in targets:
            section = self.project.document.section_by_id(node_id)
            if section is None or section.section_type is SectionType.EVENTINFO:
                continue
            stage_system = section.value("System").strip()
            stage_body = section.value("Body").strip()
            if stage_system and normalize_name(stage_system) != key[0]:
                continue
            if stage_body and normalize_name(stage_body) != key[1]:
                continue
            applied += int(self._apply_radius_to_section(section, radius_km, "EDSM"))
        if not applied:
            self.statusBar().showMessage(
                f"EDSM returned {radius_km} km for {body}; no empty matching stage remained.",
                10000,
            )

    def _edsm_radius_failed(self, system: str, body: str, message: str) -> None:
        key = (normalize_name(system), normalize_name(body))
        self._radius_lookup_targets.pop(key, None)
        explicit = key in self._radius_explicit_requests
        self._radius_explicit_requests.discard(key)
        self.statusBar().showMessage(message, 12000)
        if explicit:
            QMessageBox.warning(self, "Body radius lookup failed", message)

    def reset_edsm_permission(self) -> None:
        self.settings.setValue("edsm_radius_permission", "ask")
        self._radius_prompted.clear()
        self._radius_lookup_attempted.clear()
        QMessageBox.information(
            self,
            "EDSM permission reset",
            "EDTT Definer will ask again before its next EDSM body-radius lookup.",
        )

    def _update_capture_buttons(self) -> None:
        ready = self.live_result.state is LiveState.LIVE and self.live_result.snapshot is not None
        selected = self.project.document.section_by_id(self.current_section_id or "")
        stage_selected = selected is not None and selected.section_type is not SectionType.EVENTINFO
        self.capture_button.setEnabled(ready and stage_selected)
        self.capture_add_button.setEnabled(ready)
        snapshot = self.live_result.snapshot
        gate_ready = (
            ready
            and stage_selected
            and snapshot is not None
            and snapshot.capture_ready
            and snapshot.heading is not None
            and bool(self.stage_form.body_radius.text().strip())
            and bool(self.stage_form.gate_width.text().strip())
        )
        self.stage_form.calculate_gate_button.setEnabled(gate_ready)

    def _previous_context(self, section: SectionNode) -> tuple[str, str, str]:
        stages = [stage.section for stage in ([self.project.document.start] if self.project.document.start else []) + self.project.document.waypoints + ([self.project.document.end] if self.project.document.end else [])]
        try:
            index = stages.index(section)
        except ValueError:
            return "", "", ""
        for previous in reversed(stages[:index]):
            system = previous.value("System")
            body = previous.value("Body")
            station = previous.value("Station")
            if system or body or station:
                return system, body, station
        return "", "", ""

    def capture_selected(self, endpoint: str | None) -> None:
        section = self.project.document.section_by_id(self.current_section_id or "")
        if section is None or section.section_type is SectionType.EVENTINFO:
            return
        self._capture_to(section, endpoint)

    def capture_and_add(self) -> None:
        if self.live_result.state is not LiveState.LIVE:
            return
        section = self.add_waypoint()
        self._capture_to(section, None)

    def calculate_gate_from_live(self) -> None:
        section = self.project.document.section_by_id(self.current_section_id or "")
        snapshot = self.live_result.snapshot
        if (
            section is None
            or section.section_type is SectionType.EVENTINFO
            or self.live_result.state is not LiveState.LIVE
            or snapshot is None
            or not snapshot.capture_ready
        ):
            QMessageBox.warning(self, "Cannot create gate", "Live planetary coordinates are required.")
            return
        if snapshot.heading is None:
            QMessageBox.warning(self, "Cannot create gate", "The live game data does not contain a heading.")
            return

        stage = Stage(section)
        try:
            gate = calculate_gate(
                snapshot.latitude,
                snapshot.longitude,
                snapshot.heading,
                self.stage_form.gate_width.text(),
                stage.get("BodyRadius"),
                self.stage_form.gate_forward_offset.text().strip() or "0",
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot create gate", str(exc))
            return

        stage.position = Position(
            gate.latitude_a,
            gate.longitude_a,
            gate.latitude_b,
            gate.longitude_b,
            True,
        )
        inherited_system, inherited_body, inherited_station = self._previous_context(section)
        stage.set("System", snapshot.system or stage.get("System") or inherited_system)
        stage.set("Body", snapshot.body or stage.get("Body") or inherited_body)
        stage.set("Station", snapshot.station or stage.get("Station") or inherited_station)
        if snapshot.body_radius_km is not None:
            self._apply_radius_to_section(
                section,
                format(snapshot.body_radius_km, "f"),
                snapshot.body_radius_source or "Journal",
            )
        self._apply_live_altitude(section, snapshot)
        metadata = self._merged_snapshot_metadata(section, snapshot)
        metadata["calculated_gate"] = {
            "center_latitude": gate.center_latitude,
            "center_longitude": gate.center_longitude,
            "heading": gate.heading,
            "width_m": gate.width_m,
            "forward_offset_m": gate.forward_offset_m,
            "body_radius_km": gate.body_radius_km,
            "latitude_a": gate.latitude_a,
            "longitude_a": gate.longitude_a,
            "latitude_b": gate.latitude_b,
            "longitude_b": gate.longitude_b,
        }
        self.project.capture_metadata[section.node_id] = metadata
        self.dirty = True
        self.refresh_navigation(section.node_id)
        self.refresh_validation_and_preview()
        self._update_title()
        mode = "centered on the live position" if gate.forward_offset_m == "0" else f"{gate.forward_offset_m} m ahead"
        self.statusBar().showMessage(f"Created a {gate.width_m} m gate {mode}.", 10000)

    def convert_gate_to_point(self) -> None:
        section = self._selected_stage_section()
        if section is None:
            return
        position = Position.parse(section.value("Position"))
        if not position.is_gate:
            QMessageBox.warning(
                self, "Cannot convert gate", "The selected stage does not contain a gate."
            )
            return
        try:
            latitude, longitude = spherical_midpoint(
                position.latitude_a,
                position.longitude_a,
                position.latitude_b,
                position.longitude_b,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot convert gate", str(exc))
            return

        answer = QMessageBox.question(
            self,
            "Convert gate to centered point",
            "Replace the selected gate with a point at its spherical midpoint?\n\n"
            f"Latitude: {latitude}\nLongitude: {longitude}\n\n"
            "The waypoint's proximity, altitude, instructions, conditions, name, and advanced entries "
            "will not be changed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            return

        original_position = position.format()
        section.set_value("Position", Position(latitude, longitude).format())
        metadata = dict(self.project.capture_metadata.get(section.node_id, {}))
        metadata["gate_midpoint_conversion"] = {
            "source_position": original_position,
            "latitude": latitude,
            "longitude": longitude,
        }
        self.project.capture_metadata[section.node_id] = metadata
        self.dirty = True
        self.refresh_navigation(section.node_id)
        self.refresh_validation_and_preview()
        self._update_title()
        self.statusBar().showMessage(
            f"Converted the gate to point {latitude}, {longitude}. Review Proximity before export.",
            10000,
        )

    def _capture_to(self, section: SectionNode, endpoint: str | None) -> None:
        snapshot = self.live_result.snapshot
        if snapshot is None or not snapshot.capture_ready:
            return
        stage = Stage(section)
        latitude = format(snapshot.latitude, "f")
        longitude = format(snapshot.longitude, "f")
        position = stage.position
        if endpoint == "b":
            position.gate = True
            position.latitude_b = latitude
            position.longitude_b = longitude
        elif endpoint == "a":
            if self.stage_form.position_mode.currentText() == "Gate":
                position.gate = True
            position.latitude_a = latitude
            position.longitude_a = longitude
        elif position.is_gate:
            if position.latitude_a and not position.latitude_b:
                position.latitude_b = latitude
                position.longitude_b = longitude
            else:
                position.latitude_a = latitude
                position.longitude_a = longitude
        else:
            position = Position(latitude, longitude)
        stage.position = position
        inherited_system, inherited_body, inherited_station = self._previous_context(section)
        stage.set("System", snapshot.system or stage.get("System") or inherited_system)
        stage.set("Body", snapshot.body or stage.get("Body") or inherited_body)
        stage.set("Station", snapshot.station or stage.get("Station") or inherited_station)
        if snapshot.body_radius_km is not None:
            self._apply_radius_to_section(
                section,
                format(snapshot.body_radius_km, "f"),
                snapshot.body_radius_source or "Journal",
            )
        self._apply_live_altitude(section, snapshot)
        self.project.capture_metadata[section.node_id] = self._merged_snapshot_metadata(
            section, snapshot
        )
        self.dirty = True
        self.refresh_navigation(section.node_id)
        self.refresh_validation_and_preview()
        self._update_title()

    def _merged_snapshot_metadata(
        self,
        section: SectionNode,
        snapshot: LocationSnapshot,
    ) -> dict:
        existing = dict(self.project.capture_metadata.get(section.node_id, {}))
        radius_metadata = {
            key: existing[key]
            for key in ("body_radius_km", "body_radius_source")
            if key in existing
        }
        existing.update(snapshot.to_metadata())
        if snapshot.body_radius_km is None:
            for key in ("body_radius_km", "body_radius_source"):
                existing.pop(key, None)
            existing.update(radius_metadata)
        return existing

    def _apply_live_altitude(self, section: SectionNode, snapshot: LocationSnapshot) -> None:
        if snapshot.altitude is None:
            return
        metadata = self.project.metadata_for(section.node_id)
        mode = inferred_altitude_mode(section, metadata)
        if metadata.altitude_locked or mode is AltitudeMode.MAXIMUM:
            return
        if mode is AltitudeMode.NONE:
            metadata.altitude_mode = AltitudeMode.TARGET
            metadata.maximum_altitude = ""
            section.set_value("AltitudeProximity", "")
        section.set_value("Altitude", format(snapshot.altitude, "f"))

    def choose_journal_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose Elite Dangerous journal folder", str(self.live_reader.journal_directory))
        if path:
            self.live_reader.set_journal_directory(path)
            self.settings.setValue("journal_directory", path)
            self._poll_live()

    def _recent_paths(self) -> list[str]:
        value = self.settings.value("recent_files", [], list)
        return [str(path) for path in value]

    def _add_recent(self, path: Path) -> None:
        resolved = str(path.resolve())
        paths = [item for item in self._recent_paths() if item != resolved]
        paths.insert(0, resolved)
        self.settings.setValue("recent_files", paths[:10])
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        if self.recent_menu is None:
            return
        self.recent_menu.clear()
        paths = self._recent_paths()
        if not paths:
            empty = self.recent_menu.addAction("No recent files")
            empty.setEnabled(False)
            return
        for path in paths:
            action = self.recent_menu.addAction(path)
            action.triggered.connect(lambda _checked=False, selected=path: self.open_file(selected))

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        self.settings.setValue("window_geometry", self.saveGeometry())
        self.settings.setValue("window_state", self.saveState())
        self._persist_radius_cache()
        event.accept()
