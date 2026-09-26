#!/usr/bin/env python3
"""KC Vendetta Bounce: cinematic cracked-gold lyric video, beat-synced, with an audio spectrum."""

from __future__ import annotations

import argparse
import math
import os
import subprocess  # nosec B404 - argv lists only, never shell=True
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from vb_art import build_title, ease_out_back, fit_text, oswald, paste_rgba, text_img  # noqa: E402
from vb_lyrics import energy_at, load_lyrics, song_title  # noqa: E402
from vb_scene import (LAYOUTS, Embers, background, draw_spectrum, flare,  # noqa: E402
                      smoke_at, smoke_field, spectrum)

FPS = 30


class CliError(Exception):
    pass


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def _capcut_beats():
    """The capcut-cli beat module, looked up next to this skill or in ~/.claude/skills."""
    for base in (os.path.join(HERE, "..", ".."), os.path.expanduser("~/.claude/skills")):
        path = os.path.join(base, "capcut-cli", "scripts")
        if os.path.isfile(os.path.join(path, "capcut_beats.py")):
            sys.path.insert(0, path)
            import capcut_beats  # noqa: PLC0415
            return capcut_beats
    return None


def find_beats(music: str, start: float, duration: float, bpm: Optional[float]) -> List[float]:
    cb = _capcut_beats()
    if cb:
        found = cb.detect_beats(music, start, duration, bpm)
    else:
        try:
            import librosa  # noqa: PLC0415
        except ImportError as exc:
            raise CliError("Install the capcut-cli skill or librosa for beat detection") from exc
        from vb_scene import decode_mono  # noqa: PLC0415
        _, times = librosa.beat.beat_track(y=decode_mono(music, start, duration), sr=22050,
                                           units="time", start_bpm=bpm or 120.0)
        found = {"beats": [start + float(t) for t in times], "engine": "librosa"}
    print(f"beats: {len(found['beats'])} ({found.get('engine')})", file=sys.stderr)
    return [b - start for b in found["beats"]]


def probe_duration(music: str) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", music]
    r = subprocess.run(cmd, capture_output=True, text=True)  # nosec B603
    try:
        return float(r.stdout.strip())
    except ValueError as exc:
        raise CliError(f"Not an audio file ffprobe can read: {music}") from exc


# --------------------------------------------------------------------------
# Per-frame state
# --------------------------------------------------------------------------

@dataclass
class Job:
    music: str
    title: str
    start: float
    duration: float
    fmt: str
    beats: List[float]
    lines: list
    sections: list
    brand: str
    fade: float = 1.0
    frames: range = field(default_factory=lambda: range(0))


def beat_state(beats: List[float], t: float):
    """(pulse 0..1 decaying after each beat, True on every 4th beat)."""
    i = int(np.searchsorted(beats, t, side="right")) - 1
    if i < 0:
        return 0.0, False
    return math.exp(-(t - beats[i]) / 0.16), i % 4 == 0


class Scene:
    def __init__(self, job: Job):
        """Precompute the title art, backdrop, flare and spectrum for one job."""
        self.job, self.lay = job, LAYOUTS[job.fmt]
        lay = self.lay
        self.title, self.glow = build_title(job.title, lay.title_w)
        self.tcx, self.tcy = lay.width // 2, int(lay.band_h * 0.40)
        self.base, self.vign = background(lay)
        self.smoke = smoke_field(lay)
        self.flare = flare(lay, self.tcx, self.tcy - self.title.height * 0.30)
        self.embers = Embers(lay)
        self.brand = text_img(" ".join(job.brand.upper()), oswald(lay.brand_size, "Medium"),
                              (214, 170, 90, 200), 2, False)
        self.spec = spectrum(job.music, job.start, job.duration, FPS)
        self.subs = {}

    def subtitle(self, text: str):
        if text not in self.subs:
            self.subs[text] = fit_text(text, self.lay.sub_size, self.lay.width * 0.9, (232, 190, 105, 255))
        return self.subs[text]

    def _atmosphere(self, t: float, hit: float, energy: float, intro: float) -> np.ndarray:
        lay = self.lay
        sm = smoke_at(self.smoke, lay, t)
        band = self.base * (1 + 0.5 * hit) + sm[..., None] * np.array([0.16, 0.05, 0.02], np.float32) * (0.8 + 0.4 * hit)
        g = self.glow
        gx, gy = self.tcx - g.shape[1] // 2, self.tcy - g.shape[0] // 2
        y0, x0 = max(0, gy), max(0, gx)
        y1, x1 = min(lay.band_h, gy + g.shape[0]), min(lay.width, gx + g.shape[1])
        band[y0:y1, x0:x1] += g[y0 - gy:y1 - gy, x0 - gx:x1 - gx] * (0.5 + 0.5 * hit) * energy * intro
        band += self.flare * (0.5 + 0.55 * hit) * energy * intro
        return band

    def _lyrics(self, band: np.ndarray, t: float, pulse: float) -> None:
        for s, e, text in self.job.lines:
            if not s - self.job.start - 0.15 <= t < e - self.job.start + 0.1:
                continue
            s, e = s - self.job.start, e - self.job.start
            img = self.subtitle(text)
            p = (t - (s - 0.15)) / 0.3
            fade = min(1.0, max(0.0, p)) * (1.0 if t < e - 0.05 else max(0.0, (e + 0.1 - t) / 0.15))
            rise = int((1 - ease_out_back(min(p, 1.0))) * 26 * self.lay.scale)
            y = int(self.tcy + self.title.height * 0.62) + rise - int(3 * pulse)
            paste_rgba(band, img, self.lay.width // 2 - img.width // 2, y, fade)

    def frame(self, fi: int) -> np.ndarray:
        job, lay = self.job, self.lay
        t = fi / FPS
        pulse, on_bar = beat_state(job.beats, t)
        energy = energy_at(job.sections, t + job.start)
        hit = pulse * (1.6 if on_bar else 1.0) * energy
        intro = min(1.0, t / 0.6)
        band = self._atmosphere(t, hit, energy, intro)
        band += self.embers.draw(t, hit, 0.45 + 0.45 * energy) * 0.9 * intro
        band *= self.vign
        sc = (0.86 + 0.14 * ease_out_back(t / 0.7)) * (1 + 0.028 * hit)
        timg = self.title if abs(sc - 1) < 0.004 else self.title.resize(
            (int(self.title.width * sc), int(self.title.height * sc)))
        paste_rgba(band, timg, self.tcx - timg.width // 2, self.tcy - timg.height // 2 - int(6 * hit), intro)
        paste_rgba(band, self.brand, lay.width // 2 - self.brand.width // 2, int(34 * lay.scale), 0.9 * intro)
        self._lyrics(band, t, pulse)
        draw_spectrum(band, self.spec[min(fi, len(self.spec) - 1)] * intro, int(lay.spec_h))
        out = np.zeros((lay.height, lay.width, 3), np.float32)
        out[lay.band_y:lay.band_y + lay.band_h] = np.clip(band, 0, 1)
        edge = min(1.0, t / job.fade, (job.duration - t) / job.fade) if job.fade > 0 else 1.0
        return out * max(0.0, edge)

    def advance(self, fi: int) -> None:
        """Move embers one frame without drawing (used to fast-forward chunks)."""
        pulse, on_bar = beat_state(self.job.beats, fi / FPS)
        self.embers.step(1 / FPS, pulse * 1.6 if on_bar else 0.0)


# --------------------------------------------------------------------------
# Rendering (parallel chunks, then concat + music)
# --------------------------------------------------------------------------

def render_chunk(job: Job, path: str) -> str:
    scene, lay = Scene(job), LAYOUTS[job.fmt]
    for fi in range(job.frames.start):
        scene.advance(fi)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{lay.width}x{lay.height}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", path]
    ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)  # nosec B603
    for fi in job.frames:
        scene.advance(fi)
        ff.stdin.write((scene.frame(fi) * 255).astype(np.uint8).tobytes())
    ff.stdin.close()
    if ff.wait():
        raise CliError("ffmpeg failed while encoding a chunk")
    return path


def _mux(job: Job, parts: List[str], output: str, tmp: str) -> None:
    listing = os.path.join(tmp, "parts.txt")
    with open(listing, "w") as fh:
        fh.writelines(f"file '{p}'\n" for p in parts)
    fade = f"afade=t=in:d=0.3,afade=t=out:st={max(0.0, job.duration - job.fade):.3f}:d={job.fade}"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", listing,
           "-ss", f"{job.start:.3f}", "-t", f"{job.duration:.3f}", "-i", job.music,
           "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-af", fade,
           "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", "-shortest", output]
    if subprocess.run(cmd).returncode:  # nosec B603
        raise CliError("ffmpeg failed while adding the music")


def render(job: Job, output: str, workers: int) -> None:
    total = int(round(job.duration * FPS))
    workers = max(1, min(workers, total // (FPS * 5) or 1))
    bounds = np.linspace(0, total, workers + 1).astype(int)
    with tempfile.TemporaryDirectory() as tmp:
        jobs = []
        for i in range(workers):
            part = Job(**{**job.__dict__, "frames": range(bounds[i], bounds[i + 1])})
            jobs.append((part, os.path.join(tmp, f"part{i:02d}.mp4")))
        with ProcessPoolExecutor(workers) as pool:
            parts = list(pool.map(render_chunk, *zip(*jobs)))
        _mux(job, parts, output, tmp)
    print(output)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_job(a) -> Job:
    if not os.path.isfile(a.music):
        raise CliError(f"Music not found: {a.music}")
    length = probe_duration(a.music)
    start = max(0.0, a.start)
    duration = min(a.duration or length - start, length - start)
    if duration < 2:
        raise CliError("Need at least 2 s of music after --start")
    lines, sections = ([], []) if a.no_lyrics else load_lyrics(a.music, a.srt)
    title = a.title or song_title(a.music)
    beats = find_beats(a.music, start, duration + 1, a.bpm)
    print(f"'{title}': {duration:.1f}s, {len(lines)} lyric lines, {len(sections)} sections",
          file=sys.stderr)
    return Job(a.music, title, start, duration, a.format, beats, lines, sections, a.brand,
               fade=a.fade)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="vendetta", description=__doc__)
    p.add_argument("music", help="song file (m4a/mp3/wav); Suno files carry their own lyrics")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--format", choices=sorted(LAYOUTS), default="vertical",
                   help="vertical 1080x1920 (Reels/TikTok/Shorts), landscape 1920x1080 (YouTube), square")
    p.add_argument("--title", help="title text (default: title tag or file name)")
    p.add_argument("--srt", help="lyrics .srt (default: the song's embedded lyrics)")
    p.add_argument("--no-lyrics", action="store_true")
    p.add_argument("--start", type=float, default=0.0, help="seconds into the song")
    p.add_argument("--duration", type=float, help="seconds (default: to the end)")
    p.add_argument("--bpm", type=float, help="override detected tempo")
    p.add_argument("--brand", default="Kash Crown")
    p.add_argument("--fade", type=float, default=1.0, help="fade in/out seconds")
    p.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    a = p.parse_args(argv)
    try:
        if os.path.exists(a.output) and os.path.samefile(a.output, a.music):
            raise CliError("Output must differ from the input")
        render(build_job(a), a.output, a.workers)
    except CliError as exc:
        print(f"vendetta: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
