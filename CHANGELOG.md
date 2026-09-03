# Changelog

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
