# Changelog

## Unreleased

- Add one embedded overhead map image per System/Body beneath the Course view grid, checkpoints, and race lines.
- Add top-down four-corner perspective alignment, whole-image dragging, opacity, visibility, locking, replacement, and removal controls. Locked images remain attached to the ground during normal camera movement.
- Add map rotation and scale sliders with live angle and percentage readouts. Rotate the unlocked image around its center, or shrink/enlarge it from 10% to 400% without changing its proportions. Save the scale setting alongside the aligned corners, with existing maps defaulting to 100%.
- Preserve camera orientation when importing, showing, unlocking, or adjusting maps and during routine scene refreshes; Top remains an explicit camera action.
- Keep map alignment geographically anchored across waypoint edits, include visible maps in Fit, and save maps in editable-project schema version 5 while retaining compatibility with schemas 1-4. Map data is not exported to EDCoPilot TXT.

## v0.7.0 — 2026-10-02

- Add optional aliases to Start and End alongside waypoint names, and show them consistently in the race structure and Course view.
- Export standalone `EDTTD` comments directly after stage headers. Every waypoint includes its `WPxx` number, while available lap/section context and aliases are included and recovered when reopening the TXT.
- Add persistent time-trial versions and last-saved timestamps using editable-project schema version 4, with automatic migration from schemas 1–3.
- Require unsaved projects to be saved before export, include the saved version in the generated TXT, and stop successful exports from marking projects dirty.
- Show accurate single-body checkpoint-to-checkpoint surface distance in Course view, using spherical gate midpoints and the resolved BodyRadius, rounded to three decimal places in kilometres.
- Prevent closed selection lists from changing under the mouse wheel while preserving normal form scrolling, open-list navigation, and keyboard controls.

## v0.6.7 — 2026-09-18

- Replace the two-angle course camera with a world-up turntable orientation so normal orbiting keeps positive altitude above ground from either side and from below the surface, stopping at the vertical poles instead of flipping the scene.
- Separate intentional camera roll from normal orbiting: use **Shift + left-drag** for continuous roll, **R** to restore upright roll, or **Shift + R** to turn the view 180 degrees.
- Preserve the horizontal mouse-orbit direction used by EDTT Definer v0.6.6.
- Make **Lock center at 0 m** a world-space ground-plane constraint: pan moves the pivot across the surface while its altitude remains exactly zero, including after roll, zoom, non-renderable scenes, and all camera controls. Ground-pan sensitivity now decreases toward a horizontal side view instead of producing large distance jumps.
- Keep showing the session's last known valid live marker through stale, missing, or partially written `Status.json` states. Capture and race-line recording still stop after the normal 15-second freshness limit, and authoritative no-coordinate data clears the marker.
- Allow valid Start-to-End races with no intermediate waypoints. New races still begin with Waypoint 1, which may now be deleted.
- Keep **Follow ship** active while left-dragging to adjust its heading-relative yaw, tilt, or intentional roll; right- or middle-drag panning still releases follow mode.

## v0.6.6 — 2026-09-16

- Fix live coordinates and race-line recording being incorrectly marked stale when Elite's embedded `Status.json` timestamp lags behind the PC clock while the file is still updating.
- Replace the repeated-instruction list controls with one free-form multiline editor where each non-empty line becomes an EDCoPilot instruction and `Terse:` can be typed directly.
- Add an **Insert Terse:** helper beside every instruction editor for prefixing the current line or all selected lines without duplicating existing prefixes.
- Allow gate endpoint B to be captured before endpoint A while continuing to block export until the gate is complete.
- Start and reset the 3D course overview at a fitted 45-degree angle from above, with North toward the top and East to the right; **Top** remains the straight-down view.

## v0.6.5 — 2026-09-14

- Fix repeated EDSM permission dialogs caused by live updates re-entering the radius lookup while the first modal prompt was still open.
- Add collapsible lap/section dividers to the Race Structure list without changing project or EDCoPilot formats ([#2](https://github.com/LouisH99/EDTT-Definer/issues/2)).
- Let **Duplicate section** copy every waypoint in the selected section, including complete syntax blocks and editor metadata, with automatic next-unused numbering for lap labels.
- Collapse repeated identical validation results into count summaries that expand to show and navigate to each affected waypoint.

## v0.6.4 — 2026-09-11

- Add editor-only named lap/section dividers to make long and multi-lap routes easier to scan without changing EDCoPilot TXT.
- Add guarded reversal of a contiguous multi-waypoint selection, preserving complete waypoint blocks and keeping divider boundaries at their route positions.
- Upgrade editable projects to schema version 3 with automatic loading of schema versions 1 and 2.
- Make divider rows directly selectable: double-click or **Section…** renames them, while **Delete section** removes only the divider.
- Insert new waypoints immediately after the active waypoint and inherit that waypoint's maximum-height defaults; non-waypoint selections still append to the route.

## v0.6.3 — 2026-09-08

- Add Ctrl/Shift multi-selection in the Race structure list so ordered waypoint groups can be duplicated, deleted, or moved together for faster multi-lap course authoring ([#1](https://github.com/LouisH99/EDTT-Definer/issues/1)).
- Warn when exactly one waypoint uses a different vehicle type from every other waypoint, while treating Ship and SLF PlayerState variants as their respective vehicle groups.
- Add a default-enabled **Lock center at 0 m** course-camera option, keeping the orbit pivot on ground level while still allowing full 360-degree rotation.
- Double-click Start, a waypoint, or End in the 3D view to select it and center the camera on it; disabling the height lock uses the checkpoint's actual altitude as the orbit pivot.
- Fix GitHub rate-limit header handling for the PySide6 6.11 network API used by automatic update checks.
- Publish the accumulated v0.6 course-view and race-line work as application version `0.6.3`.

## v0.6.1 development

- Remove the previous vertical camera limit so manual orbit can rotate continuously through a full 360 degrees around both axes.
- Restyle the software 3D canvas with RaceViz-inspired dark presentation, green Start, yellow waypoints, blue End, translucent solid checkpoint volumes, and a cyan-to-red speed gradient, used with the RaceViz author's explicit permission.
- Add independent live race-line recording from filesystem-triggered `Status.json` updates, with pause/resume gaps, stop, clear, duplicate suppression, and derived 3D speed.
- Save exact decimal recordings as versioned `*.edtttrace.json` and import/export EDCoPilot/RaceViz `#Version2` and `#Version3` CSV traces.
- Filter traces by System and Body, project them with the selected course radius, keep them visible while editing, and include them in Fit without changing project or EDCoPilot formats.
- Warn before discarding an unsaved recording when replacing the project, loading another trace, clearing, starting over, or closing the application.
- Replace periodic game-file polling with a Windows file watcher for `Status.json`, the journal directory, and the newest journal; reattach after Elite replaces the status file and debounce write bursts safely.
- Identify the application as `0.6.1.dev0`; a future stable v0.6.1 release is considered newer, while stable v0.6.0 is not.

## v0.6 development

- Add a live software-rendered 3D course overview as a third tab beside Validation and File preview.
- Draw point proximity circles and altitude cylinders, real-width gate planes, ordered route lines, distinct Start and End markers, and matching live-player heading data.
- Group stages by System and Body, automatically follow editor selection, and use project, live, or cached body radii without inventing a fallback scale.
- Use antimeridian-safe spherical projection, accurate 1:1 altitude scale, a visible distance scale, and safe reporting for malformed or skipped stages.
- Start with a straight-down North-up view and support orbit, tilt, pan, zoom, Top, Fit, hover details, and click-to-select synchronization.
- Add an optional live follow camera that centers the player at a 45-degree tilt, rotates with the reported heading, retains user-controlled zoom, and cycles the viewpoint 90 degrees around the player on repeated clicks.
- Let the same live course panel pop out into a resizable non-modal window and redock it without changing project dirty state.
- Identify the application as `0.6.0.dev0` and recognize a future stable v0.6 GitHub release as newer than this development build.
- Keep editable-project schema version 2 and EDCoPilot TXT syntax unchanged.

## v0.5 — 2026-09-05

- Add a guarded whole-race conversion between Ship, SLF, SRV, and On Foot that previews every RaceType and PlayerState change.
- Warn when Event Info `RaceType` does not match the Start `PlayerState`, and require explicit confirmation before exporting that mismatch.
- Offer flying-start PlayerState choices only for Start and Waypoint 1 while preserving imported values losslessly.
- Preserve ship/SLF flying starts when converting to Ship and create the required Start/Waypoint 1 pairing when converting to SLF.
- Keep positions, instructions, conditions, restrictions, altitude rules, waypoint names, advanced entries, and Maximum ship size unchanged during conversion.
- Add asynchronous GitHub stable-release checks at startup, with a persistent disable option and a manual Help action.
- Let users open the release page, be reminded later, or skip one version; updates are never downloaded or installed automatically.
- Validate update version tags and restrict opened release links to this repository on github.com.
- Expand and centralize delayed hover explanations, including detailed descriptions for every PlayerState.
- Add three privacy-safe README screenshots generated from a synthetic race.
- Keep the editable project schema at version 2 and leave EDCoPilot TXT syntax unchanged.

## v0.4 — 2026-09-03

- Convert a complete gate into a point at the spherical midpoint of its shorter great-circle arc, including correct antimeridian and high-latitude handling.
- Show the midpoint coordinates for confirmation and preserve the waypoint's proximity, altitude, instructions, conditions, name, and advanced entries.
- Replace the inactive checkable Advanced control with genuinely collapsible sections whose contents are hidden when closed.
- Make Advanced and Maximum-speed sections collapsed by default, while Travel instructions and Conditions remain open, and remember each choice.
- Display populated-entry counts in collapsible headers and highlight a closed section when it contains a validation error.
- Add Follow Windows, Light, and Dark appearance modes with a persistent selection and theme-aware validation and live-status colors.
- Add delayed hover explanations and accessible descriptions for ambiguous editor controls.
- Replace the basic About message with a dedicated dialog linking to the repository, issue tracker, releases, and EDSM while retaining the MIT, unofficial-project, and vibe-coding disclosures.
- Keep the editable project schema at version 2 and leave EDCoPilot TXT syntax unchanged.

## v0.3 — 2026-09-02

- Display the great-circle distance between manually entered or captured gate endpoints using the stage's body radius.
- Read exact planetary radius values from Elite Dangerous journal `Scan` events and cache them locally by game identifiers and names.
- Fill only empty `BodyRadius` fields, preserving imported and manually entered values.
- Add a **Find radius** action and visible Manual, Journal, Local cache, EDSM, and EDSM cache source labels.
- Add an optional asynchronous EDSM fallback with allow-once, always-allow, and not-now permission choices.
- Store system address, body ID, body radius, and radius source in capture metadata without changing EDCoPilot TXT syntax.
- Add validation to the shared great-circle distance helper for coordinate ranges and positive body radius.

## v0.2.1 — 2026-09-01

- New waypoints inherit the previous waypoint's Maximum height value, including when using Capture + add. A changed maximum becomes the default for the following waypoint.

## v0.2 — 2026-09-01

- Add editor-only waypoint names that remain attached when waypoints are moved or duplicated.
- Add No restriction, Maximum height, and Target altitude ± tolerance authoring modes.
- Default new stages and waypoints to Maximum height mode with altitude locking enabled.
- Default new races to `MaxShipSize=N/A` while keeping the field editable.
- Convert maximum height into EDCoPilot's existing Altitude and AltitudeProximity pair without floating-point drift.
- Detect imported equal altitude/tolerance pairs as maximum-height rules without rewriting unchanged files.
- Lock altitude against live capture by default, including manual gate endpoints and calculated gates.
- Upgrade editable projects to schema version 2 while automatically migrating version-1 projects.

## v0.1 — 2026-08-31

Initial public release.

This release was “vibe-coded” with OpenAI Codex under the project owner's direction and testing. Most source code, automated tests, and documentation were generated and revised by Codex. See `AI_DEVELOPMENT.md` for the full disclosure.

- Create, open, edit, validate, and losslessly re-export EDCoPilot time trials.
- Preserve comments, unknown fields, repeated values, section ordering, coordinate precision, encoding, and newline style.
- Capture live latitude, longitude, altitude, heading, body, system, and station context from Elite Dangerous files.
- Create point checkpoints and manually captured gates.
- Calculate gates of a requested physical width from live position, heading, and planetary body radius, either centered on the current position or placed a requested distance ahead.
- Edit start, waypoint, end, travel, speed-warning, restriction, and condition fields used by supplied EDCoPilot examples.
- Save incomplete work in the native `*.edttc.json` project format and create validated `*.export.txt` files for EDCoPilot.
- Package as an unsigned, single-file Windows executable.
