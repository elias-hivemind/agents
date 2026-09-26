#!/usr/bin/env python3
"""KC VENDETTA BOUNCE — Kash Crown signature music visualizer.

Renders one song into:
  1. {TITLE}_Youtube_Bars_HD.mp4  16:9 1280x720 @24fps, full song length
  2. {TITLE}_TikTok_63s.mp4       9:16 720x1280 @24fps, loudest 63s section
  3. {TITLE}_Thumbnail.png        16:9 still from the loudest moment

Look: black + slow red/gold smoke + embers, distressed cracked gold title,
gold tagline, 48 beat-reactive bars (orange -> yellow -> cyan) on a 70% black strip.

Requires: Python 3.9+, numpy, Pillow 9.1+, and ffmpeg (on PATH, or `pip install imageio-ffmpeg`).
"""
from __future__ import annotations

import argparse
import math
import re
import shutil
# subprocess only runs the resolved ffmpeg binary with a fixed argv list (no shell).
import subprocess  # nosec B404
import sys
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    sys.exit(f"Missing dependency: {exc.name}. Run: pip install numpy pillow imageio-ffmpeg")

from kcvb_audio import analyze, decode_mono, loudest_window
from kcvb_scene import Scene
from kcvb_style import ANALYSIS_SR, DEFAULT_FONT, FPS, TIKTOK_SECONDS, Canvas, Text

JUNK_WORDS = (r"(final|master(ed)?|mix(down)?|v\d+|wav|mp3|export|bounce|prod|clean|dirty"
              r"|explicit|\d{2,3}\s?bpm)")


def find_ffmpeg() -> str:
    """Absolute path of a real ffmpeg binary (system first, then imageio-ffmpeg)."""
    exe = shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg  # pylint: disable=import-outside-toplevel
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError):
            exe = None
    if not exe or not Path(exe).is_file():
        sys.exit("ffmpeg not found. Install ffmpeg or run: pip install imageio-ffmpeg")
    return str(Path(exe).resolve())


def parse_time(value: str | None) -> float | None:
    """'75', '75.5' or '1:15' -> seconds."""
    if value is None:
        return None
    if ":" in value:
        minutes, seconds = value.split(":", 1)
        return int(minutes) * 60 + float(seconds)
    return float(value)


def title_from_filename(path: Path) -> str:
    """'Vendetta_Soul_(FINAL MASTER).wav' -> 'VENDETTA SOUL'."""
    name = path.stem
    name = re.sub(r"[\[(].*?[\])]", " ", name)
    name = re.sub(r"[_\-.]+", " ", name)
    name = re.sub(rf"\b{JUNK_WORDS}\b", " ", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip()
    return (name or path.stem).upper()


def safe_filename(title: str) -> str:
    """Title -> file-name stem using only [A-Za-z0-9_]."""
    stem = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
    return stem or "SONG"

# ── Encode ──────────────────────────────────────────────────────────────────
@dataclass
class Job:  # pylint: disable=too-many-instance-attributes
    """One output video: what to render and where."""

    ffmpeg: str
    audio: Path
    out: Path
    canvas: Canvas
    text: Text
    start_f: int
    n_frames: int
    fade_out: float
    thumb_at: int | None = None
    thumb_path: Path | None = None


@dataclass
class Analysis:
    """Per-frame audio features for the whole song."""

    levels: np.ndarray
    bass: np.ndarray
    song_seconds: float


def _ffmpeg_argv(job: Job) -> list[str]:
    start_s, dur = job.start_f / FPS, job.n_frames / FPS
    afilters = [f"afade=t=in:d={0.25 if job.start_f else 0.02}"]
    if job.fade_out > 0:
        afilters.append(f"afade=t=out:st={max(dur - job.fade_out, 0):.3f}:d={job.fade_out}")
    return [job.ffmpeg, "-y", "-v", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{job.canvas.width}x{job.canvas.height}",
            "-r", str(FPS), "-i", "-",
            "-ss", f"{start_s:.3f}", "-t", f"{dur:.3f}", "-i", str(job.audio),
            "-map", "0:v", "-map", "1:a", "-af", ",".join(afilters),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-movflags", "+faststart",
            "-shortest", str(job.out)]


def _progress(name: str, k: int, n: int, t0: float) -> None:
    pct = int(100 * (k + 1) / n)
    if k == 0 or pct // 5 != int(100 * k / n) // 5:
        elapsed = time.time() - t0
        eta = elapsed / (k + 1) * (n - k - 1)
        print(f"  {name}: {pct:3d}%  ({elapsed:5.0f}s elapsed, ~{eta:4.0f}s left)", file=sys.stderr)


def render(job: Job, feats: Analysis, seed: int) -> None:
    """Stream frames into ffmpeg and mux the song audio."""
    scene = Scene(job.canvas, job.text, feats.song_seconds, seed)
    last = len(feats.levels) - 1
    t0 = time.time()
    # Fixed argv list, no shell; job.ffmpeg is a resolved absolute path from find_ffmpeg().
    argv = _ffmpeg_argv(job)
    with subprocess.Popen(argv, stdin=subprocess.PIPE) as proc:  # nosec B603 # nosemgrep
        try:
            for k in range(job.n_frames):
                f = min(job.start_f + k, last)
                fade = min(1.0, (k + 1) / (0.5 * FPS))
                if job.fade_out > 0:
                    fade = min(fade, (job.n_frames - k) / (job.fade_out * FPS))
                img = scene.frame((job.start_f + k) / FPS, feats.levels[f], float(feats.bass[f]),
                                  fade)
                proc.stdin.write(img.tobytes())
                if job.thumb_path is not None and k == job.thumb_at:
                    Image.fromarray(img).save(job.thumb_path)
                _progress(job.out.name, k, job.n_frames, t0)
        finally:
            proc.stdin.close()
    if proc.returncode != 0:
        sys.exit(f"ffmpeg failed while writing {job.out}")


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="KC VENDETTA BOUNCE — Kash Crown music visualizer")
    ap.add_argument("audio", type=Path, help="WAV/MP3/M4A/FLAC song file")
    ap.add_argument("--title", help="Song title (default: cleaned file name, ALL CAPS)")
    ap.add_argument("--tagline", default="KASH CROWN", help='Subtitle line (use "" for none)')
    ap.add_argument("--out", type=Path, default=None,
                    help="Output folder (default: next to the audio)")
    ap.add_argument("--only", choices=["both", "youtube", "tiktok"], default="both")
    ap.add_argument("--tiktok-start",
                    help="Force TikTok start (seconds or m:ss); default = loudest 63s")
    ap.add_argument("--tiktok-length", type=float, default=TIKTOK_SECONDS)
    ap.add_argument("--preview", type=float, default=None,
                    help="Render only N seconds of each (test run)")
    ap.add_argument("--scale", type=float, default=1.0, help="1.0 = 720p spec; 1.5 = 1080p")
    ap.add_argument("--font", type=Path, default=DEFAULT_FONT)
    ap.add_argument("--seed", type=int, default=7,
                    help="Smoke/ember/crack randomness (fixed = same look)")
    args = ap.parse_args()
    if not args.audio.is_file():
        sys.exit(f"Audio file not found: {args.audio}")
    if not args.font.is_file():
        sys.exit(f"Font not found: {args.font}")
    return args


def _tiktok_window(args: argparse.Namespace, rms: np.ndarray, bass: np.ndarray,
                   total_f: int) -> tuple[int, int]:
    """(start frame, length in frames) of the TikTok cut."""
    tt_len = min(int(round(args.tiktok_length * FPS)), total_f)
    forced = parse_time(args.tiktok_start)
    if forced is not None:
        return min(int(forced * FPS), total_f - tt_len), tt_len
    return loudest_window(rms, bass, tt_len), tt_len


def _jobs(args: argparse.Namespace, ffmpeg: str, text: Text, total_f: int,
          window: tuple[int, int]) -> list[Job]:
    """Build the YouTube and/or TikTok jobs the CLI flags ask for."""
    out_dir = args.out or args.audio.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(text.title)
    tt_start, tt_len = window
    cap = total_f if args.preview is None else int(args.preview * FPS)

    def canvas(w: int, h: int) -> Canvas:
        return Canvas(int(round(w * args.scale / 2)) * 2, int(round(h * args.scale / 2)) * 2)

    jobs = []
    if args.only in ("both", "youtube"):
        n = min(total_f, cap)
        peak = tt_start + min(tt_len // 3, 4 * FPS)
        jobs.append(Job(ffmpeg, args.audio, out_dir / f"{stem}_Youtube_Bars_HD.mp4",
                        canvas(1280, 720), text, 0, n,
                        fade_out=1.5 if args.preview is None else 0.0,
                        thumb_at=peak if peak < n else n // 2,
                        thumb_path=out_dir / f"{stem}_Thumbnail.png"))
    if args.only in ("both", "tiktok"):
        jobs.append(Job(ffmpeg, args.audio,
                        out_dir / f"{stem}_TikTok_{int(round(args.tiktok_length))}s.mp4",
                        canvas(720, 1280), text, tt_start, min(tt_len, cap), fade_out=1.2))
    return jobs


def main() -> None:
    """CLI entry point: analyse the song, then render the requested outputs."""
    args = _parse_args()
    text = Text((args.title or title_from_filename(args.audio)).strip().upper(),
                args.tagline.strip(), args.font)
    ffmpeg = find_ffmpeg()

    print(f"KC VENDETTA BOUNCE → {text.title!r} / {text.tagline!r}", file=sys.stderr)
    y = decode_mono(ffmpeg, args.audio)
    song_s = y.size / ANALYSIS_SR
    total_f = int(math.floor(song_s * FPS))
    levels, bass, rms = analyze(y, ANALYSIS_SR, total_f)
    feats = Analysis(levels, bass, song_s)

    window = _tiktok_window(args, rms, bass, total_f)
    print(f"  song {song_s:.1f}s · TikTok window {window[0] / FPS:.1f}s → "
          f"{sum(window) / FPS:.1f}s", file=sys.stderr)

    outputs = []
    for job in _jobs(args, ffmpeg, text, total_f, window):
        render(job, feats, args.seed)
        outputs += [p for p in (job.out, job.thumb_path) if p is not None]

    print("\nDONE", file=sys.stderr)
    for path in outputs:
        print(path)

if __name__ == "__main__":
    main()
