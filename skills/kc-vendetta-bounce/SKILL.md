---
name: kc-vendetta-bounce
description: KC VENDETTA BOUNCE — Kash Crown's cinematic lyric-video style. Cracked-gold condensed-serif title with orange backlight and lens flare, drifting embers and smoke, gold letter-spaced lyric line, and an amber audio-spectrum bar graph, all pulsing on the beat. Renders a full song or a section to 1080x1920 (Reels/TikTok/Shorts), 1920x1080 (YouTube) or 1080x1080. Use when the user says "KC Vendetta Bounce", "Vendetta style", "vendetta bounce", or wants a Kash Crown lyric / visualizer video from a song.
---

# KC Vendetta Bounce

A cinematic lyric visualizer in Kash Crown gold on black. `scripts/vendetta.py` renders it locally with numpy, Pillow and ffmpeg.

The look:

- **Title:** the song title in Cinzel Black, condensed, with an oversized first letter on each word. It has a metallic gold gradient, cracks, grunge, a bevel and a bronze rim, with an orange glow and lens flare behind it.
- **Atmosphere:** embers drift up through dark red smoke.
- **Lyric line:** the current lyric, from the song's own timed lyrics, in Oswald Bold, gold and letter-spaced. Each new line rises in.
- **Visualizer:** blocky amber spectrum bars across the bottom, driven by the actual audio.
- **Beat response:** the glow, flare, title bounce and embers pulse on every beat and hit harder on every 4th beat. Choruses run hotter and bridges or drops calmer, based on the song's section markers.

## Prerequisites

- Python 3.9+ with `numpy` and `pillow`, plus ffmpeg and ffprobe.
- Beat detection comes from the `capcut-cli` skill (install it alongside this one) or from `librosa` directly. librosa gives the best results on real mixes.
- The fonts (Cinzel and Oswald, SIL OFL) ship in `fonts/`.

```bash
pip install numpy pillow librosa
```

## Commands

```bash
V=scripts/vendetta.py

# YouTube: full song, 1920x1080
python3 $V song.m4a --format landscape -o song_youtube.mp4

# Reels / TikTok / Shorts: the hook only, 1080x1920 letterboxed band
python3 $V song.m4a --format vertical --start 80 --duration 21.6 -o hook_reel.mp4

# Custom title, external lyrics, square
python3 $V song.mp3 --title "Empire of Scars" --srt lyrics.srt --format square -o sq.mp4
```

| Flag                    | Default                    | Notes                                                           |
| ----------------------- | -------------------------- | --------------------------------------------------------------- |
| `--format`              | `vertical`                 | `vertical` 1080x1920, `landscape` 1920x1080, `square` 1080x1080 |
| `--title`               | title tag or file name     | Upload prefixes like `552fda97-` are stripped                   |
| `--srt`                 | the song's embedded lyrics | Suno `.m4a` exports carry timed lyrics as a subtitle track      |
| `--no-lyrics`           | off                        | Title and visualizer only                                       |
| `--start`, `--duration` | whole song                 | In seconds                                                      |
| `--bpm`                 | detected                   | Use if the pulses feel doubled or halved                        |
| `--brand`               | `Kash Crown`               | The small letter-spaced line above the title                    |
| `--fade`                | `1.0`                      | Fade in and out, in seconds                                     |
| `--workers`             | CPU count                  | Renders in parallel chunks, then joins them and adds the music  |

## Workflow

1. When the user sends a song and says "KC Vendetta Bounce", confirm the format: YouTube means `landscape`, Reels, TikTok and Shorts mean `vertical`. Also confirm whether it's the full song or a section.
2. For short clips, find the hook in the embedded lyrics. Section markers like `[Chorus]` or `[Hook]` are in the subtitle track (`ffmpeg -i song.m4a -map 0:s:0 -f srt -`). Start about half a beat before the first hook line.
3. Render. Speed is roughly 3 to 5 times real time on 4 cores at 1080p.
4. Before delivering, check the output with ffprobe (size and duration). Pull 2 or 3 frames with `ffmpeg -ss` and look at them: the title should fit, lyrics should be readable, and the bars should be moving.
5. Send the file. For posting, hand off to `stitch-reel` (Instagram and Pinterest through Zapier). For YouTube, the user uploads it.

## Notes

- Lines under 0.3 s are dropped. In Suno exports these are ad-libs and section tags stacked on one timestamp.
- Section energy: intro 0.75, verse 1.0, pre-chorus 1.1, chorus/hook 1.3, full energy 1.45, bridge 0.7, drop 0.6, outro 0.7.
- The music must be something the user has the rights to publish.
