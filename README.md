# EDTT Definer

**An unofficial time-trial editor for EDCoPilot.**

EDTT Definer is a native Windows editor for creating and maintaining EDCoPilot time-trial definition files. The current release is **v0.2.1**.

> **AI development disclosure:** This project was “vibe-coded” with OpenAI Codex. The project owner supplied the idea, requirements, EDCoPilot examples, testing feedback, and release decisions. Codex generated and revised most of the source code, automated tests, and documentation. The resulting application has been tested, but—as with any software, especially AI-generated software—it may still contain mistakes. See [AI_DEVELOPMENT.md](AI_DEVELOPMENT.md) for the full disclosure.

## Run the application

### Prebuilt Windows release

Open the [latest GitHub release](https://github.com/LouisH99/EDTT-Definer/releases/latest), download `EDTT-Definer-v0.2.1.exe` from its **Assets** section, and run it. No installation is required.

The executable is not code-signed, so Windows SmartScreen may show its normal warning on first launch.

### Run from source with a virtual environment

```powershell
git clone https://github.com/LouisH99/EDTT-Definer.git
cd EDTT-Definer
py -3.13 -m venv venv
.\venv\Scripts\python.exe -m pip install .
.\venv\Scripts\python.exe -m edtt_definer
```

### Run from source with your system Python

```powershell
git clone https://github.com/LouisH99/EDTT-Definer.git
cd EDTT-Definer
py -3.13 -m pip install .
py -3.13 -m edtt_definer
```

Python 3.13 is required. Using a virtual environment is recommended because it keeps EDTT Definer and its dependencies separate from other Python programs.

## Basic workflow

1. Choose **File → New** or open an existing `*.export.txt` definition.
2. Complete Event Info, Start, any intermediate Waypoints, and End.
3. Add, duplicate, delete, or reorder waypoints from the left pane.
4. When Elite Dangerous is reporting surface coordinates, use **Capture into selected** or **Capture + add waypoint**.
5. Optionally save incomplete work with **Save Editable Project (.json)**. This file is only for reopening in the editor.
6. Resolve all red validation errors and choose **Create EDCoPilot TXT**.
7. Import the resulting `*.export.txt` through EDCoPilot's Time Trials page.

For a manually captured gate, choose **Gate** in the position editor and capture endpoint A and endpoint B separately. Altitude is locked against live capture by default, so capturing point B does not replace the rule chosen at point A. Unlock it when live altitude should update a target; the app intentionally does not invent an altitude tolerance.

### Waypoint names and altitude rules

Waypoints can have optional editor-only names, displayed as labels such as `Waypoint 3 — Canyon Gate`. Names are stored in the editable `*.edttc.json` project and are not written to EDCoPilot TXT files.

Each stage offers three altitude modes: no restriction, a maximum height, or a target altitude with a tolerance. Maximum height is an authoring shortcut: a maximum of `500` metres is exported as `Altitude=250` and `AltitudeProximity=250`, representing the permitted range from zero through 500 metres. Maximum-height values are always manual and are never replaced by live capture.

New races start with `MaxShipSize=N/A`. Their initial stages and newly added waypoints start in Maximum height mode with altitude locking enabled; enter the intended maximum before exporting, or select a different altitude mode.

When a waypoint uses Maximum height, the next waypoint added after it inherits the same maximum. Changing that new waypoint's maximum changes what the following waypoint inherits. Target and No restriction modes are not converted into inherited maximums.

### Calculated gates from live heading

The editor can calculate a gate perpendicular to the ship or SRV's live heading:

1. Select Start, a Waypoint, or End and enter that body's `BodyRadius` in kilometres.
2. Wait for live latitude, longitude, and heading to appear.
3. Enter the total **Gate width** in metres. The default is `2000`.
4. Leave **Distance ahead** at `0` to center the gate on the current position, or enter a positive distance in metres to place its center ahead of you.
5. Choose **Create gate from live heading** and review endpoints A and B before exporting.

The calculation uses spherical destination geometry. A centered gate places half its width to the left of the heading and half to the right. Width, forward offset, body radius, center, heading, and calculated endpoints are retained in the editable project's capture metadata but only the EDCoPilot `Position` gate is exported.

Start, waypoint, and end forms also expose the instruction and timing fields found in EDCoPilot examples, including `EnterSystemInstruction`, `ApproachBodyInstruction`, `LastLegMessage`, `EndInstruction`, and the `MaxSpeed*` settings. The End section uses `EndInstruction`; `NextInstruction` is only used by Start and Waypoint sections.

## Game data

The normal journal location is detected automatically:

```text
%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous
```

Use **Journal folder…** in the live-location pane if the folder has been redirected. A stale status or “no planetary coordinates” message is normal when the game is closed, at the main menu, or away from a planetary surface.

The application reads `Status.json` for position, body, altitude, and heading. It reads the newest `Journal.*.log` for current system and station context. These files are read locally and never modified.

## Development

```powershell
py -3.13 -m venv venv
.\venv\Scripts\python.exe -m pip install -e ".[dev]"
.\venv\Scripts\python.exe -m edtt_definer
.\venv\Scripts\python.exe -m pytest
```

After installation, the application can also be started with `edtt-definer` from the activated virtual environment.

## Building the executable

```powershell
.\build.ps1
```

The unsigned v0.2.1 executable is written to `dist\EDTT-Definer-v0.2.1.exe`.

See [RELEASING.md](RELEASING.md) for the GitHub release checklist and [CHANGELOG.md](CHANGELOG.md) for release notes.

The project repository is [LouisH99/EDTT-Definer](https://github.com/LouisH99/EDTT-Definer). Bugs and feature requests can be submitted through [GitHub Issues](https://github.com/LouisH99/EDTT-Definer/issues).

## Files

- `*.edttc.json` is the versioned, editable project format. Do not import it into EDCoPilot.
- `*.export.txt` is the validated plain-text EDCoPilot import format.

The application reads Elite Dangerous telemetry locally. It does not send data over the network or automate EDCoPilot.

## License

EDTT Definer is released under the [MIT License](LICENSE).

Elite Dangerous, EDCoPilot, and their respective names and assets belong to their respective owners. This project is an independent community tool and is not affiliated with or endorsed by Frontier Developments or EDCoPilot.
