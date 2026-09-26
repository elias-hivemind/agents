---
name: capcut-cli
description: Command-line tooling around CapCut — list and inspect CapCut desktop drafts, find offline media, relink moved footage, zip-backup drafts — plus CapCut-style quick edits rendered with ffmpeg (trim, concat to a 9:16 canvas, speed, reframe/crop to vertical, burn SRT captions, replace/mute/extract audio). Use when the user mentions CapCut drafts or projects, wants CapCut "as a CLI", or needs fast reel edits without opening an editor.
---

# capcut-cli

CapCut has **no official CLI, scripting API or headless export**. `scripts/capcut_cli.py` (with its helper `scripts/capcut_ffmpeg.py`, keep them together) automates only what can be done safely:

- **Draft tools** read and patch CapCut desktop draft folders on disk.
- **Edit tools** do the everyday CapCut operations directly with ffmpeg. The result is a finished MP4, not a CapCut draft.

Tell the user this up front if they expect the CLI to drive the CapCut app or trigger its Export button. It cannot.

## Prerequisites

- Python 3.8+.
- `ffmpeg` and `ffprobe` on PATH, for the edit commands.
- Draft folder, auto-detected at:
  - Windows: `%LOCALAPPDATA%\CapCut\User Data\Projects\com.lveditor.draft`
  - macOS: `~/Movies/CapCut/User Data/Projects/com.lveditor.draft`
  - Custom location (CapCut → Settings → Projects): pass `--root` or set `CAPCUT_DRAFTS`.
- **CapCut must be closed** before `relink`, or CapCut overwrites the change.

## Draft commands

```bash
S=scripts/capcut_cli.py

python3 $S doctor
python3 $S drafts --json                         # name, folder, duration, modified, encrypted
python3 $S inspect "0925 Drop"                   # canvas, fps, tracks, text layers
python3 $S media "0925 Drop" --missing           # offline media; exit 1 if any
python3 $S media "0925 Drop" --meta              # works even on encrypted drafts
python3 $S relink "0925 Drop" --from "D:/Old Footage" --to "E:/Footage" --dry-run
python3 $S relink "0925 Drop" --from "D:/Old Footage" --to "E:/Footage"
python3 $S backup "0925 Drop" --dest backups/
```

Newer CapCut builds encrypt `draft_content.json`. For those drafts, `inspect` and `relink` exit 2 with "encrypted" and change nothing. `drafts`, `media --meta` and `backup` still work.

`relink` matches prefixes on path boundaries and case-insensitively, and accepts both `\` and `/`. It writes timestamped `*.bak` copies and replaces files atomically. Always run `--dry-run` first and show the user the mapping.

## Edit commands (ffmpeg, H.264 + AAC, faststart)

```bash
python3 $S probe clip.mp4 --json
python3 $S trim clip.mp4 --start 2.5 --duration 6 -o hook.mp4
python3 $S concat a.mp4 b.mov c.mp4 -o reel.mp4 --size 1080x1920 --mode pad   # or --mode crop
python3 $S speed clip.mp4 --factor 1.5 -o fast.mp4                            # 0.1–10, pitch kept
python3 $S reframe wide.mp4 --aspect 9:16 --mode crop -o vertical.mp4        # or --size 1080x1350
python3 $S captions reel.mp4 --srt reel.srt --font-size 18 -o reel_cc.mp4
python3 $S audio reel.mp4 --replace track.mp3 --loop -o reel_music.mp4
python3 $S audio reel.mp4 --mute -o silent.mp4
python3 $S audio reel.mp4 --extract -o voice.m4a
# add --dry-run to any edit to print the ffmpeg command, --force to overwrite
```

`concat` normalises every clip to one canvas and fps, and adds silent audio to clips that have none, so mixed sources join cleanly.

## Kash Crown hand-off

For branded gold-on-black reels built from photos, use the `stitch-reel` skill. Use this skill to prep the source clips first (trim, reframe, captions).

## Exit codes

`0` ok · `1` nothing matched / missing media found · `2` bad input, encrypted draft, or ffmpeg failure.
