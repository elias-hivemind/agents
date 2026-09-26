---
name: davinci-resolve-cli
description: Control DaVinci Resolve from the command line through its official scripting API — list/open/create projects, set resolution and fps, import media into bins, build timelines from clips, add markers, export EDL/FCPXML/XML/AAF/OTIO, and queue and wait on renders with presets or format/codec. Use when the user wants Resolve automated, scripted, batch-rendered, or "Resolve as a CLI".
---

# davinci-resolve-cli

`scripts/resolve_cli.py` talks to a **running** DaVinci Resolve through `DaVinciResolveScript` (fusionscript). Python stdlib only.

## Prerequisites (check in this order)

1. DaVinci Resolve 18+ is installed and **open**.
2. In Resolve, set Preferences → System → General → **External scripting using: Local**.
3. External scripting from a separate process needs **DaVinci Resolve Studio**. On the free edition, `doctor` reports `connected: false`. There, paste the logic into Workspace → Console (Py3) instead.
4. Use 64-bit Python 3 that matches Resolve's supported version (3.6–3.12 on recent releases).
5. Non-default install paths: set `RESOLVE_SCRIPT_API` (the `.../Developer/Scripting` folder) and `RESOLVE_SCRIPT_LIB` (path to `fusionscript.dll` or `fusionscript.so`).

Run `python3 scripts/resolve_cli.py doctor` first. Stop and report the error if it is not connected.

## Commands

```bash
S=scripts/resolve_cli.py

python3 $S doctor --json
python3 $S project list
python3 $S project create "Drop 07" --fps 30 --resolution 1080x1920
python3 $S project open "Drop 07"
python3 $S project set --setting "timelineOutputResolutionWidth=1080"
python3 $S project save

python3 $S import "footage/*.mp4" --bin Raw
python3 $S media --json
python3 $S timeline create Reel --clips a.mp4 b.mp4 c.mp4 --bin Raw
python3 $S timeline append d.mp4 --timeline Reel
python3 $S timeline marker 86430 --timeline Reel --name "hook" --color Red
python3 $S timeline export Reel out/reel.fcpxml --format fcpxml   # edl|fcpxml|xml|aaf|otio|csv|drt
python3 $S page deliver

python3 $S render --list-presets
python3 $S render --list-formats
python3 $S render --timeline Reel --preset "YouTube - 1080p" --target-dir exports --name reel_v1 --wait
python3 $S render --timeline Reel --format mp4 --codec H264 --target-dir exports --wait
python3 $S render --status --json

```

## Rules

1. Set the frame rate (`--fps`) **before** creating the first timeline. Resolve locks it afterwards, and the CLI then errors instead of silently ignoring it.
2. Timeline frames are absolute and usually start at `86400` (01:00:00:00 at 24 fps). Get real values from `timeline list --json` before adding markers or `--in-frame`/`--out-frame`.
3. `render` without `--wait` only queues and starts the job. With `--wait` it polls until `Complete` and exits 2 on failure or cancel. The default timeout is 6 h.
4. Pass `--format` and `--codec` together. Take the values from `--list-formats`.
5. Run `project save` after edits. Resolve does not autosave scripted changes on its own schedule.
6. For API calls not wrapped here, use Resolve's own Workspace → Console. This CLI deliberately does not execute arbitrary Python.

## Exit codes

`0` ok · `1` doctor not connected · `2` Resolve rejected the operation / bad input.
