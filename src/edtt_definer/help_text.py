from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QWidget


HELP_TEXT = {
    "event_ref": (
        "Purpose: stable identifier used by EDCoPilot to distinguish this event. "
        "Use letters and numbers only for best compatibility. Example: CanyonSprint01."
    ),
    "event_state": (
        "Purpose: controls the publication state. Values: AUTHOR while editing; ALPHA for testing; "
        "PROD after publication (normally immutable); DELETE requests removal. Example: AUTHOR."
    ),
    "event_name": "Purpose: event name shown to players in EDCoPilot. Example: Pomeche Canyon Sprint.",
    "short_desc": (
        "Purpose: short event description shown with the time trial. Keep it concise and on one line. "
        "Example: Race through five canyon gates below 500 m."
    ),
    "max_ship_size": (
        "Purpose: largest permitted ship or landing-pad class. Values: N/A, S, M, or L. "
        "Use N/A for SRV, SLF, and On Foot races."
    ),
    "game_type": (
        "Purpose: game version required by the course. Values: BOTH, ODYSSEY, or HORIZONS. "
        "Use ODYSSEY when the race depends on an Odyssey settlement or terrain."
    ),
    "race_type": (
        "Purpose: vehicle category and icon used for the event. Values: SHIP, FIGHTER (SLF), SRV, or ONFOOT. "
        "Changing this field alone does not change stage PlayerState values; use Convert whole race for that."
    ),
    "convert_race": (
        "Purpose: safely change RaceType and every existing stage PlayerState together. "
        "A confirmation previews all changes. Conditions and Maximum ship size are kept for manual review."
    ),
    "time_penalty": "Purpose: optional seconds added for each penalty. Enter a non-negative number, for example 5.",
    "max_penalties": "Purpose: optional number of penalties allowed before disqualification. Example: 3.",
    "pause_resume": "Purpose: adds Condition=PauseResume so this event can be paused and resumed.",
    "restrictions": (
        "Purpose: repeated EDCoPilot event restrictions. Add one value per row. "
        "Example: NoHullRepair. Unknown values are preserved for compatibility."
    ),
    "waypoint_name": (
        "Purpose: optional name used only in EDTT Definer's race list. It is saved in the editable project "
        "and never exported to EDCoPilot. Example: Canyon Gate."
    ),
    "player_state": (
        "Purpose: in-game state EDCoPilot requires when this stage is completed. Values: Ship means the ship must "
        "be landed; ShipPass means a ship flies through the checkpoint; ShipFlyingStart requires Start and "
        "Waypoint 1 to match and starts timing when that waypoint is crossed; FighterFlyingStart does the same "
        "for an SLF; Fighter means an SLF passes the checkpoint; SRV requires the rover; OnFoot requires the "
        "commander on foot. Flying starts require matching Start and Waypoint 1 states. "
        "Flying-start choices are only offered for Start and Waypoint 1. "
        "Example: use ShipPass for an airborne ship gate."
    ),
    "system": "Purpose: exact, case-sensitive star-system name for this stage. Example: Sol.",
    "body": "Purpose: exact body name containing the surface coordinates. Example: Pomeche 2 C.",
    "body_radius": (
        "Purpose: planetary radius in kilometres, used for gate width, distance, and midpoint geometry. "
        "It can come from the journal, local cache, EDSM, or manual input. Example: 5411.771."
    ),
    "find_radius": (
        "Purpose: look for Body radius in local journal data and cache, then optionally query EDSM. "
        "The existing value is never replaced automatically."
    ),
    "station": "Purpose: optional exact station or settlement name required at this stage.",
    "position_mode": (
        "Purpose: choose the checkpoint shape. Point uses coordinate A and a positive Proximity radius; "
        "Gate uses the line between endpoints A and B."
    ),
    "latitude_a": "Purpose: latitude of a point or gate endpoint A, from -90 through 90 degrees.",
    "longitude_a": "Purpose: longitude of a point or gate endpoint A, from -180 through 180 degrees.",
    "latitude_b": "Purpose: latitude of gate endpoint B, from -90 through 90 degrees.",
    "longitude_b": "Purpose: longitude of gate endpoint B, from -180 through 180 degrees.",
    "capture_a": "Purpose: copy the current live latitude and longitude into point/gate endpoint A.",
    "capture_b": "Purpose: copy the current live latitude and longitude into gate endpoint B without changing A.",
    "gate_width": (
        "Purpose: total width in metres for a calculated gate. Half is placed on each side of the heading. "
        "Example: 2000 creates a 2 km gate."
    ),
    "gate_offset": (
        "Purpose: distance in metres from the live position to the calculated gate centre. "
        "Use 0 for a gate centred on you, or a positive value to place it ahead."
    ),
    "calculate_gate": (
        "Purpose: calculate both gate endpoints from live coordinates, heading, width, offset, and Body radius. "
        "The result is shown for review and is not exported automatically."
    ),
    "convert_gate": (
        "Purpose: replace a complete gate with a point at the spherical midpoint of A and B. "
        "All other stage settings are preserved and confirmation is required."
    ),
    "gate_distance": "Purpose: read-only great-circle distance between gate endpoints A and B using Body radius.",
    "proximity": (
        "Purpose: point-checkpoint tolerance in metres. It must be positive for a point. "
        "EDTT Definer never invents this value. Example: 50."
    ),
    "altitude_mode": (
        "Purpose: select no restriction, a maximum height, or a target altitude with tolerance. "
        "Maximum height is converted into equal Altitude and AltitudeProximity values for EDCoPilot."
    ),
    "maximum_altitude": (
        "Purpose: highest permitted height above the surface in metres. Example: 500 exports as "
        "Altitude=250 and AltitudeProximity=250. Live capture never replaces it."
    ),
    "target_altitude": "Purpose: centre altitude in metres for a target band. Example: 250 with tolerance 50 allows 200–300 m.",
    "altitude_tolerance": "Purpose: permitted distance above or below Target altitude in metres. It must be provided with a target.",
    "altitude_lock": (
        "Purpose: prevent all live capture paths, including gate A/B and calculated gates, from replacing altitude. "
        "It is enabled by default."
    ),
    "enter_system": "Purpose: optional instruction spoken when the commander enters this stage's system.",
    "approach_body": "Purpose: optional instruction spoken while approaching this stage's body.",
    "last_leg": "Purpose: optional message for the final leg before the End checkpoint.",
    "instructions": "Purpose: repeated spoken EDCoPilot instructions. Select a row to edit it; use Terse for the short-speech variant.",
    "terse": "Purpose: prefixes the instruction with Terse: for EDCoPilot's shorter speech mode.",
    "max_speed": "Purpose: optional maximum permitted speed for this stage. Enter a positive numeric value.",
    "max_speed_distance": "Purpose: warning distance before the stage; one number or comma-separated values such as 1000,500.",
    "max_speed_trigger": "Purpose: optional warning speed thresholds paired with warning distances, for example 300,250.",
    "max_speed_warning": "Purpose: instruction spoken when the speed warning is triggered.",
    "max_speed_exceeded": "Purpose: instruction spoken after the maximum speed has been exceeded.",
    "conditions": (
        "Purpose: additional requirements for completing a stage. Choose a guided condition or Custom for "
        "future/undocumented syntax. Example: HeadingLimit=(90,180)."
    ),
    "condition_value": "Purpose: parameter for the selected condition. The placeholder shows the required format.",
    "advanced": (
        "Purpose: preserve or edit unrecognized and future EDCoPilot lines. These entries generate warnings but "
        "are never silently deleted. Example: FutureField=value."
    ),
    "list_add": "Add a new entry, or replace the currently selected entry with the edited value.",
    "list_remove": "Remove the selected repeated entry from this section.",
    "navigation": "Select Event Info, Start, a numbered Waypoint, or End to edit it in the centre pane.",
    "add_waypoint": "Add a new waypoint after the existing waypoints, inheriting the previous maximum height when available.",
    "capture_add": "Create a waypoint and fill its coordinates and available location context from live game data.",
    "duplicate_waypoint": "Duplicate the selected waypoint, including its editor-only name, altitude mode, lock, and capture metadata.",
    "delete_waypoint": "Delete the selected waypoint and its editor-only metadata. At least one waypoint must remain.",
    "move_up": "Move the selected waypoint one place earlier while keeping its comments, unknown entries, and metadata together.",
    "move_down": "Move the selected waypoint one place later while keeping its comments, unknown entries, and metadata together.",
    "capture_selected": "Fill the selected Start, Waypoint, or End from valid live surface coordinates without exporting.",
    "journal_folder": (
        "Override the automatically detected Elite Dangerous Saved Games journal folder. EDTT Definer "
        "watches this directory, Status.json, and the newest journal and reads them only after changes."
    ),
    "validation": "Double-click an issue to select its section and reveal a collapsed area containing the problem.",
    "preview": "Read-only preview of the exact EDCoPilot TXT that Create EDCoPilot TXT will write.",
    "course_body": (
        "Choose which System and Body to display. Stages on different bodies cannot share one accurate "
        "coordinate space, so the course view shows one body group at a time."
    ),
    "course_follow": (
        "Keep the matching live ship or SRV position centered with a 45-degree camera tilt. The camera "
        "rotates with live heading so the vehicle initially points toward the top. Click Follow ship again "
        "to rotate the camera another 90 degrees around the vehicle while continuing to follow. Zoom remains "
        "available; orbiting, panning, Top, or Fit turns follow mode off. If live data is unavailable, follow "
        "waits for it."
    ),
    "course_center_height_lock": (
        "Keep the camera's orbit center on ground level at 0 metres. This is enabled by default. When "
        "disabled, double-clicking a checkpoint centers the orbit at that checkpoint's actual altitude, "
        "and Fit uses the course's vertical midpoint."
    ),
    "course_top": (
        "Turn off ship following, return to the straight-down course overview with North up, and fit the "
        "route to the window."
    ),
    "course_fit": (
        "Turn off ship following and fit all visible checkpoints and the live marker into the current camera angle."
    ),
    "course_popout": "Move the course view from the editor tab into a separate resizable window.",
    "course_dock": "Return the separate course view to its tab in the main editor.",
    "course_status": (
        "Shows the selected body, radius source, true 1:1 altitude scale, and any stages skipped because "
        "their coordinates are incomplete or invalid."
    ),
    "course_canvas": (
        "Interactive true-scale 3D course overview. Left-drag rotates freely through 360 degrees around "
        "both axes, right- or middle-drag pans, and the "
        "mouse wheel zooms. Manual orbit or pan stops Follow ship. Click a checkpoint to select it in the "
        "editor, or double-click it to select it and make it the camera's orbit center. Keyboard: arrows "
        "pan, plus/minus zoom, T returns to Top, and F fits the view."
    ),
    "trace_record": (
        "Start a new live race-line recording for the body currently shown. This replaces any loaded "
        "trace after confirmation, samples valid Status.json positions, and derives speed from distance "
        "and elapsed time. A positive BodyRadius is required."
    ),
    "trace_pause": (
        "Pause or resume live race-line recording. Resuming begins a new line segment so unavailable or "
        "paused movement is never joined by a misleading straight line."
    ),
    "trace_stop": "Stop sampling live positions while keeping the recorded line visible for review or saving.",
    "trace_clear": "Remove the displayed race line after confirmation. This does not change the course project.",
    "trace_open": (
        "Open an EDTT *.edtttrace.json recording or an EDCoPilot/RaceViz #Version2/#Version3 CSV trace. "
        "Trace files are independent from editable course projects."
    ),
    "trace_save": (
        "Save the displayed race line as exact, editable *.edtttrace.json or export a RaceViz-compatible "
        "#Version3 CSV. CSV stores less context than the EDTT trace format."
    ),
    "trace_status": "Shows trace recording state, point/segment count, live-data waits, and unsaved status.",
    "action_new": "Create a new blank time-trial project after confirming any unsaved changes.",
    "action_open": "Open an editable EDTT project or an existing EDCoPilot TXT definition.",
    "action_save": "Save the editable project, including editor-only names and capture metadata.",
    "action_save_as": "Save a separate editable *.edttc.json project file.",
    "action_export": (
        "Validate and create the plain-text *.export.txt file imported by EDCoPilot. A RaceType mismatch "
        "with the Start PlayerState requires confirmation before the file is written."
    ),
    "action_exit": "Close EDTT Definer after confirming any unsaved changes.",
    "action_update": "Contact GitHub now and report whether a newer stable EDTT Definer release exists.",
    "action_auto_update": "Contact GitHub shortly after startup to check for a newer stable release.",
    "action_reset_edsm": "Forget the saved EDSM permission so the next radius lookup asks again.",
    "action_about": "Show version, project links, network disclosure, license, and AI-development disclosure.",
}


PLAYER_STATE_ITEM_HELP = {
    "Ship": "Ship must be landed when the checkpoint completes.",
    "ShipFlyingStart": "Ship flying start; Start and Waypoint 1 must both use this value.",
    "ShipPass": "Ship passes through the point or gate without landing.",
    "FighterFlyingStart": "SLF flying start; Start and Waypoint 1 must both use this value.",
    "Fighter": "A ship-launched fighter passes through or completes the checkpoint.",
    "SRV": "Commander must be driving an SRV.",
    "OnFoot": "Commander must be on foot.",
}


def set_help_text(target: QWidget | QAction, text: str) -> None:
    target.setToolTip(text)
    if isinstance(target, QAction):
        target.setStatusTip(text)
        target.setWhatsThis(text)
    else:
        if target.property("edttHelpText") is not None:
            target.setProperty("edttHelpText", text)
            target.setToolTip("")
        target.setAccessibleDescription(text)


def set_help(target: QWidget | QAction, key: str) -> None:
    set_help_text(target, HELP_TEXT[key])
