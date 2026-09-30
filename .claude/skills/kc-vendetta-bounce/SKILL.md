---
name: kc-vendetta-bounce
description: "KC VENDETTA BOUNCE — Kash Crown's signature music-video visualizer. Turns any song (WAV/MP3/M4A/FLAC) into a full-length 16:9 YouTube video plus a 63-second 9:16 TikTok cut of the loudest section, with black smoke/ember background, distressed cracked gold title, gold tagline, and 48 orange→yellow→cyan beat-reactive bounce bars. Use whenever the user uploads a song and says \"KC VENDETTA BOUNCE\", \"Vendetta Bounce\", \"use my bounce look\", \"make it Vendetta style\", \"visualizer for this song\", or asks for a YouTube + TikTok version of a track."
---

# KC VENDETTA BOUNCE

Kash Crown's locked signature visual. **Every song gets the exact same look; only the title and
tagline change.** Rendering is local and deterministic (numpy + Pillow + ffmpeg): no external
video services, no AI image generation, no randomness between runs.

## The locked look (do not change per song)

| Element | Spec |
|---|---|
| Background | Pure black, two slow-drifting red/gold smoke layers, floating ember particles, cinematic vignette, slow push-in zoom (1.00→1.08 across the song) |
| Title | `{SONG_TITLE}` ALL CAPS, centered, heavy condensed display font (Anton), metallic gold gradient, cracks, worn specks, bevel, red-orange outer glow that pulses with the bass, red flare above |
| Tagline | `{TAGLINE}` in gold `#E9B45A`, ~22% of title size, letter-spaced, under the title |
| Bars | 48 bars across the full bottom, 200px strip at 720p, gradient `#FF3300 #FF6600 #FFAA00 #FFDD00 #FFFF00 #AAFF00 #00FFCC #00FFFF`, white top-edge highlight, 70% black backdrop, attack 0.70 / release 0.75 |
| Extras | No watermark, no ID text |

Every value above is a constant in `scripts/kcvb_style.py`. Change the look **only** when
the user explicitly asks to change the signature style itself, and then edit the constants, not
per-song flags.

## Outputs (always both, unless the user asks for one)

| File | Format | Length |
|---|---|---|
| `{TITLE}_Youtube_Bars_HD.mp4` | 16:9, 1280×720, 24fps, H.264 CRF 18, AAC 320k | Full song |
| `{TITLE}_TikTok_63s.mp4` | 9:16, 720×1280, 24fps | 63s, auto-picked loudest section snapped to a bass hit, with a 0.25s fade-in and 1.2s fade-out |
| `{TITLE}_Thumbnail.png` | 1280×720 still from the loudest moment | — |

`{TITLE}` in file names is the title with spaces turned into underscores (`VENDETTA_SOUL_...`).

## Workflow

### 1. Get the inputs

- **Audio:** the uploaded WAV/MP3/M4A/FLAC. If several songs were uploaded, render each one in turn.
- **Title:** use the title the user gave. Otherwise the script cleans the file name
  (`Vendetta_Soul_(FINAL MASTER).wav` → `VENDETTA SOUL`). Confirm with the user only when the file
  name is gibberish (e.g. `track07.wav`, `Recording 12.m4a`).
- **Tagline:** use the one the user gave (e.g. "Me hard call Vendetta"). If none was given, use the
  song's hook line if the user shared the lyrics, otherwise the default `KASH CROWN`. Never invent lyrics.

Do not ask anything else. The user's standing instruction is "no need to explain again".

### 2. Make sure the dependencies are there (first run on a machine only)

```bash
python3 -c "import numpy, PIL" 2>/dev/null || pip install numpy pillow
command -v ffmpeg >/dev/null || python3 -c "import imageio_ffmpeg" 2>/dev/null || pip install imageio-ffmpeg
```

### 3. Render

```bash
python3 "<skill-dir>/scripts/render.py" "<song file>" \
  --title "VENDETTA SOUL" --tagline "Me hard call Vendetta" --out "<output dir>"
```

`<skill-dir>` is the folder holding this SKILL.md. Write outputs to the user's working or outputs
folder, never inside the skill folder.

Useful flags:

| Flag | Use |
|---|---|
| `--only youtube` / `--only tiktok` | Render just one version |
| `--tiktok-start 1:12` | User wants a specific part for TikTok instead of the auto-pick |
| `--tiktok-length 60` | A different clip length (default 63s) |
| `--preview 8` | Quick 8-second test of each output before a long render |
| `--scale 1.5` | 1080p versions (1920×1080 / 1080×1920) when the user asks for full HD |

Both files together take roughly 2× the song length on a typical CPU (measured: an 80s song
took 3.4 minutes), so a 3:26 song takes about 7 minutes. Run it in the background and tell the user
it's rendering. For a quick look first, use `--preview 8`.

### 4. Verify before delivering

- Confirm both MP4s exist and their durations are right: YouTube = song length, TikTok = 63s
  (or the whole song if it is shorter than 63s).
- Pull one frame from each (`ffmpeg -ss 5 -i file.mp4 -frames:v 1 check.png`) and look at it:
  the title must be fully on screen and not clipped, the bars must be moving (not flat), and the
  tagline must be readable.
- If the title is clipped or wraps badly, re-render with a shorter `--title` and tell the user why.

### 5. Deliver

Hand over the three files and report:

- the TikTok window used (the script prints `TikTok window 71.3s → 134.3s`), so the user knows which part was picked
- the durations of both videos

Offer exactly one follow-up: a different TikTok section, or a 1080p render.

## Guardrails

- Never claim a render finished without the files existing on disk.
- Don't post anywhere. Uploading to YouTube or TikTok is the user's call. If they ask, use the
  `stitch-reel` skill's publishing rules: explicit go-ahead first, and a public URL is required.
- Never change the colors, bar count, font, or layout for a single song. The consistency is the brand.
- The song audio is untouched apart from the TikTok fades. No re-mastering, no loudness changes.

## Files

- `scripts/render.py` is the CLI entry point (run this one)
- `scripts/kcvb_style.py` holds the locked look: every color, size and timing constant
- `scripts/kcvb_audio.py` decodes the song, measures the 48 bands and finds the loudest section
- `scripts/kcvb_scene.py` draws the smoke, embers, gold title and bounce bars
- `assets/fonts/Anton-Regular.ttf` is the title font (SIL OFL 1.1, license in `OFL.txt`)
- `assets/reference/` holds the original spec and the Vendetta Soul reference frame
