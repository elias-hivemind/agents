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
    from PIL import Image, ImageDraw, ImageFont
except ImportError as exc:  # pragma: no cover
    sys.exit(f"Missing dependency: {exc.name}. Run: pip install numpy pillow imageio-ffmpeg")

# ── Locked style contract (do not change per song) ──────────────────────────
FPS = 24
N_BARS = 48
BAR_H_720 = 200                     # bar strip height at 720p short side
BAR_STRIP_OPACITY = 0.70            # black backdrop behind bars
ATTACK = 0.70                       # rise coefficient (fast)
RELEASE = 0.75                      # fall retention (slow)
TIKTOK_SECONDS = 63.0
BAR_PALETTE = ["#FF3300", "#FF6600", "#FFAA00", "#FFDD00",
               "#FFFF00", "#AAFF00", "#00FFCC", "#00FFFF"]
GOLD_STOPS = [(0.00, "#FFF3C4"), (0.30, "#F4C659"), (0.55, "#B98320"),
              (0.72, "#E3AE45"), (1.00, "#6E420B")]
TAGLINE_GOLD = "#E9B45A"
GLOW_RED = (1.0, 0.22, 0.02)
SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_FONT = SKILL_DIR / "assets" / "fonts" / "Anton-Regular.ttf"
ANALYSIS_SR = 22050
JUNK_WORDS = (r"(final|master(ed)?|mix(down)?|v\d+|wav|mp3|export|bounce|prod|clean|dirty"
              r"|explicit|\d{2,3}\s?bpm)")
BICUBIC = Image.Resampling.BICUBIC
BILINEAR = Image.Resampling.BILINEAR


@dataclass(frozen=True)
class Canvas:
    """Output frame size; `scale` is the short side relative to 720px."""

    width: int
    height: int

    @property
    def scale(self) -> float:
        """Pixel multiplier relative to the 720p spec."""
        return min(self.width, self.height) / 720.0

    @property
    def portrait(self) -> bool:
        """True for 9:16 output."""
        return self.height > self.width

    def grid(self) -> tuple[np.ndarray, np.ndarray]:
        """Per-pixel (y, x) float32 coordinates."""
        return np.meshgrid(np.arange(self.height, dtype=np.float32),
                           np.arange(self.width, dtype=np.float32), indexing="ij")


@dataclass(frozen=True)
class Text:
    """The per-song words and the font they are set in."""

    title: str
    tagline: str
    font: Path


# ── Utilities ───────────────────────────────────────────────────────────────
def hex_rgb(code: str) -> np.ndarray:
    """'#RRGGBB' -> float32 RGB in 0..1."""
    code = code.lstrip("#")
    return np.array([int(code[i:i + 2], 16) / 255.0 for i in (0, 2, 4)], dtype=np.float32)


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


def fbm(h: int, w: int, rng: np.random.Generator, base: int = 3, octaves: int = 5,
        persistence: float = 0.55) -> np.ndarray:
    """Fractal value noise in [0,1], float32 (h, w)."""
    acc = np.zeros((h, w), np.float32)
    amp, total = 1.0, 0.0
    aspect = w / h
    for octave in range(octaves):
        gh = base * 2 ** octave + 1
        gw = max(2, int(round(gh * aspect)))
        grid = rng.random((gh, gw)).astype(np.float32)
        layer = np.asarray(Image.fromarray(grid).resize((w, h), BICUBIC), np.float32)
        acc += amp * layer
        total += amp
        amp *= persistence
    acc /= total
    lo, hi = np.percentile(acc, 1), np.percentile(acc, 99)
    return np.clip((acc - lo) / max(hi - lo, 1e-6), 0, 1)


def _box(a: np.ndarray, k: int, axis: int) -> np.ndarray:
    pad = [(0, 0)] * a.ndim
    pad[axis] = (k // 2 + 1, k // 2)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float64)
    hi = np.take(c, np.arange(k, c.shape[axis]), axis=axis)
    lo = np.take(c, np.arange(0, c.shape[axis] - k), axis=axis)
    return ((hi - lo) / k).astype(np.float32)


def blur(a: np.ndarray, r: float) -> np.ndarray:
    """Blur a float 2-D array with three box passes (≈ Gaussian, sigma r)."""
    k = int(round(math.sqrt(4 * r * r + 1)))  # box width giving sigma≈r over 3 passes
    if k < 2:
        return a.astype(np.float32)
    k |= 1
    out = a.astype(np.float32)
    for _ in range(3):
        out = _box(_box(out, k, 0), k, 1)
    return out


# ── Audio analysis ──────────────────────────────────────────────────────────
def decode_mono(ffmpeg: str, path: Path, sr: int = ANALYSIS_SR) -> np.ndarray:
    """Decode any audio file to mono float32 at `sr` Hz."""
    argv = [ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    # Fixed argv list, no shell; `ffmpeg` is a resolved absolute path from find_ffmpeg().
    proc = subprocess.run(argv, capture_output=True, check=False)  # nosec B603 # nosemgrep
    if proc.returncode != 0:
        sys.exit(f"ffmpeg could not decode {path}:\n{proc.stderr.decode(errors='replace')}")
    y = np.frombuffer(proc.stdout, np.float32).copy()
    if y.size < sr:
        sys.exit(f"Audio too short or empty: {path}")
    return y


def _band_weights(freqs: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """(bins x 48) averaging matrix over log-spaced bands, and band centre freqs."""
    edges = np.geomspace(40.0, min(12000.0, sr / 2 * 0.95), N_BARS + 1)
    fc = np.sqrt(edges[:-1] * edges[1:])
    band_of_bin = np.digitize(freqs, edges) - 1
    weights = np.zeros((freqs.size, N_BARS), np.float32)
    for band in range(N_BARS):
        idx = np.nonzero(band_of_bin == band)[0]
        if idx.size == 0:  # low bands narrower than an FFT bin
            idx = np.array([np.argmin(np.abs(freqs - fc[band]))])
        weights[idx, band] = 1.0 / idx.size
    return weights, fc


def _smooth(raw: np.ndarray) -> np.ndarray:
    """Fast-attack / slow-release envelope per bar."""
    levels = np.empty_like(raw)
    prev = np.zeros(N_BARS, np.float32)
    for i in range(raw.shape[0]):
        x = raw[i]
        prev = np.where(x > prev, prev + ATTACK * (x - prev), prev * RELEASE + x * (1 - RELEASE))
        levels[i] = prev
    return levels


def analyze(y: np.ndarray, sr: int, n_frames: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (levels[n_frames, 48] 0..1 smoothed, bass[n_frames] 0..1, rms[n_frames])."""
    n_fft = 2048
    win = np.hanning(n_fft).astype(np.float32)
    padded = np.pad(y, (n_fft // 2, n_fft * 2))
    centers = np.round(np.arange(n_frames) * sr / FPS).astype(np.int64)
    weights, fc = _band_weights(np.fft.rfftfreq(n_fft, 1 / sr), sr)

    power = np.empty((n_frames, N_BARS), np.float32)
    offs = np.arange(n_fft)
    for s in range(0, n_frames, 256):
        c = centers[s:s + 256]
        seg = padded[c[:, None] + offs[None, :]] * win
        power[s:s + c.size] = (np.abs(np.fft.rfft(seg, axis=1)) ** 2) @ weights

    # +3 dB/oct tilt keeps highs alive
    db = 10 * np.log10(power + 1e-10) + 3.0 * np.log2(fc / 100.0)
    ceil = np.percentile(db, 99.5)
    levels = _smooth(np.clip((db - (ceil - 45.0)) / 45.0, 0, 1) ** 1.6)
    bass = levels[:, :6].mean(axis=1)
    bass = np.clip(bass / max(np.percentile(bass, 98), 1e-6), 0, 1)

    energy = np.concatenate([[0.0], np.cumsum(y.astype(np.float64) ** 2)])
    starts = np.clip(centers, 0, y.size)
    ends = np.clip(np.round((np.arange(n_frames) + 1) * sr / FPS).astype(np.int64), 0, y.size)
    rms = np.sqrt((energy[ends] - energy[starts]) / np.maximum(ends - starts, 1))
    return levels, bass.astype(np.float32), rms.astype(np.float32)


def loudest_window(rms: np.ndarray, bass: np.ndarray, win_frames: int) -> int:
    """Start frame of the loudest window, nudged onto the nearest bass hit (±1s)."""
    n = rms.size
    if win_frames >= n:
        return 0
    energy = np.concatenate([[0.0], np.cumsum(rms.astype(np.float64) ** 2)])
    sums = energy[win_frames:] - energy[:-win_frames]
    start = int(np.argmax(sums))
    onset = np.maximum(np.diff(bass, prepend=bass[0]), 0)
    lo, hi = max(0, start - FPS), min(n - win_frames, start + FPS)
    if hi > lo:
        start = lo + int(np.argmax(onset[lo:hi + 1]))
    return min(start, n - win_frames)


# ── Title plate ─────────────────────────────────────────────────────────────
def tracked_width(font: ImageFont.FreeTypeFont, text: str, tracking: float) -> float:
    """Width of `text` with extra `tracking` px between letters."""
    return sum(font.getlength(ch) for ch in text) + tracking * max(len(text) - 1, 0)


def draw_tracked(draw: ImageDraw.ImageDraw, center: tuple[float, float], text: str,
                 font: ImageFont.FreeTypeFont, tracking: float) -> None:
    """Draw letter-spaced `text` centred on `center` (x, y) in white."""
    x = center[0] - tracked_width(font, text, tracking) / 2
    for ch in text:
        draw.text((x, center[1]), ch, font=font, fill=255, anchor="lm")
        x += font.getlength(ch) + tracking


def fit_lines(title: str, font_path: Path, max_w: float, max_size: int, min_one_line: int):
    """Pick one or two lines and the largest font size that fits."""
    def best(lines):
        size = max_size
        while size > 8:
            font = ImageFont.truetype(str(font_path), size)
            if all(tracked_width(font, ln, size * 0.02) <= max_w for ln in lines):
                return size
            size -= 2
        return size

    one = [title]
    s1 = best(one)
    words = title.split()
    if s1 >= min_one_line or len(words) < 2:
        return one, s1
    splits = [(" ".join(words[:k]), " ".join(words[k:])) for k in range(1, len(words))]
    two = list(min(splits, key=lambda p: abs(len(p[0]) - len(p[1]))))
    s2 = best(two)
    return (two, s2) if s2 > s1 else (one, s1)


@dataclass
class Layout:
    """Where the title lines and tagline sit, and how big they are."""

    lines: list
    size: int
    top: float
    line_h: float
    sub_size: int
    sub_gap: float


def _layout(cv: Canvas, text: Text, center_y: float) -> Layout:
    max_w = cv.width * (0.86 if cv.portrait else 0.62)
    max_size = int(cv.height * (0.11 if cv.portrait else 0.17))
    lines, size = fit_lines(text.title, text.font, max_w, max_size, int(max_size * 0.6))
    line_h = size * 1.02
    sub_size = max(int(size * 0.22), int(18 * cv.scale))
    sub_gap = size * 0.22 if text.tagline else 0
    block_h = line_h * len(lines) + (sub_gap + sub_size if text.tagline else 0)
    return Layout(lines, size, center_y - block_h / 2, line_h, sub_size, sub_gap)


def _masks(cv: Canvas, text: Text, lay: Layout) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Title mask, per-line vertical ramp (for the gradient), tagline mask."""
    font = ImageFont.truetype(str(text.font), lay.size)
    mask_img = Image.new("L", (cv.width, cv.height), 0)
    draw = ImageDraw.Draw(mask_img)
    vmap = np.zeros((cv.height, cv.width), np.float32)
    for k, line in enumerate(lay.lines):
        cy = lay.top + lay.line_h * (k + 0.5)
        draw_tracked(draw, (cv.width / 2, cy), line, font, lay.size * 0.02)
        y0, y1 = int(cy - lay.size * 0.55), int(cy + lay.size * 0.55)
        ramp = np.linspace(0, 1, max(y1 - y0, 1), dtype=np.float32)
        y0c, y1c = max(y0, 0), min(y1, cv.height)
        vmap[y0c:y1c] = ramp[y0c - y0:y1c - y0, None]
    mask = np.asarray(mask_img, np.float32) / 255.0

    sub = np.zeros((cv.height, cv.width), np.float32)
    if text.tagline:
        sub_img = Image.new("L", (cv.width, cv.height), 0)
        sub_cy = lay.top + lay.line_h * len(lay.lines) + lay.sub_gap + lay.sub_size / 2
        sub_font = ImageFont.truetype(str(text.font), lay.sub_size)
        draw_tracked(ImageDraw.Draw(sub_img), (cv.width / 2, sub_cy), text.tagline, sub_font,
                     lay.sub_size * 0.08)
        sub = np.asarray(sub_img, np.float32) / 255.0
    return mask, vmap, sub


def _cracks(cv: Canvas, mask: np.ndarray, n_cracks: int, rng: np.random.Generator) -> np.ndarray:
    """Jagged random-walk polylines starting inside the letters."""
    crack_img = Image.new("L", (cv.width, cv.height), 0)
    draw = ImageDraw.Draw(crack_img)
    ys, xs = np.nonzero(mask > 0.5)
    if xs.size:
        for _ in range(n_cracks):
            j = rng.integers(xs.size)
            x, y = float(xs[j]), float(ys[j])
            ang = rng.uniform(0, 2 * np.pi)
            pts = [(x, y)]
            for _ in range(int(rng.integers(4, 12))):
                ang += rng.normal(0, 0.55)
                step = rng.uniform(3, 11) * cv.scale
                x += math.cos(ang) * step
                y += math.sin(ang) * step
                pts.append((x, y))
            shade = int(rng.integers(110, 220))
            draw.line(pts, fill=shade, width=max(1, int(round(rng.uniform(0.7, 1.4) * cv.scale))))
    return np.asarray(crack_img, np.float32) / 255.0


def _gold(cv: Canvas, mask: np.ndarray, vmap: np.ndarray, n_cracks: int,
          rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Distressed metallic gold: (fill rgb, title alpha, grain)."""
    stops_t = np.array([t for t, _ in GOLD_STOPS], np.float32)
    stops_c = np.stack([hex_rgb(c) for _, c in GOLD_STOPS])
    grad = np.stack([np.interp(vmap, stops_t, stops_c[:, ch]) for ch in range(3)], -1)
    grain = fbm(cv.height, cv.width, rng, base=24, octaves=3)
    grad *= (0.78 + 0.38 * grain)[..., None]
    grad *= (1 - 0.6 * _cracks(cv, mask, n_cracks, rng))[..., None]

    # Bevel: light top edges, dark bottom edges
    d = max(1, int(round(2 * cv.scale)))
    hl = blur(np.clip(mask - np.roll(mask, d, axis=0), 0, 1), 0.8 * cv.scale)
    sh = blur(np.clip(mask - np.roll(mask, -d, axis=0), 0, 1), 0.8 * cv.scale)
    grad = grad + 0.45 * hl[..., None] * (1 - grad)
    grad *= (1 - 0.55 * sh)[..., None]

    # Distressed wear: worn spots go dark bronze
    speck = fbm(cv.height, cv.width, rng, base=40, octaves=3)
    speck = blur((speck > 0.86).astype(np.float32), 0.7 * cv.scale)
    grad *= (1 - 0.55 * speck)[..., None]
    return grad, mask * (1 - 0.2 * speck), grain


def _glow(cv: Canvas, lay: Layout, mask: np.ndarray, sub: np.ndarray) -> np.ndarray:
    """Red-orange glow around the words plus the flare above the title."""
    red = np.array(GLOW_RED, np.float32)
    wide = blur(mask, max(lay.size * 0.18, 4))
    tight = blur(mask, max(lay.size * 0.05, 2))
    glow = wide[..., None] * red * 0.75 \
        + tight[..., None] * np.array([1.0, 0.5, 0.1], np.float32) * 0.28 \
        + blur(sub, max(lay.sub_size * 0.3, 2))[..., None] * red * 0.6
    yy, xx = cv.grid()
    fy = lay.top + lay.size * 0.05
    flare = np.exp(-(((xx - cv.width * 0.47) / (cv.width * 0.13)) ** 2
                     + ((yy - fy) / (lay.size * 0.35)) ** 2))
    glow += flare[..., None] * np.array([1.0, 0.35, 0.08], np.float32) * 0.55
    return glow


class TitlePlate:
    """Precomputed title layers cropped to a bbox: fill rgb, alpha, glow rgb, shadow."""

    def __init__(self, cv: Canvas, text: Text, center_y: float, rng: np.random.Generator):
        """Build every title layer once; only the glow strength changes per frame."""
        lay = _layout(cv, text, center_y)
        mask, vmap, sub = _masks(cv, text, lay)
        grad, alpha_t, grain = _gold(cv, mask, vmap, int(10 + 4 * len(text.title)), rng)

        sub_rgb = np.broadcast_to(hex_rgb(TAGLINE_GOLD), (cv.height, cv.width, 3)) \
            * (0.85 + 0.25 * grain)[..., None]
        alpha = np.clip(alpha_t + sub, 0, 1)
        fill = np.where(sub[..., None] > alpha_t[..., None], sub_rgb, grad).astype(np.float32)
        glow = _glow(cv, lay, mask, sub)
        shadow = blur(np.roll(alpha, int(4 * cv.scale), axis=0), 6 * cv.scale)

        # Crop everything to the glow bbox for cheap per-frame compositing
        live = (glow.max(-1) > 0.004) | (alpha > 0) | (shadow > 0.004)
        rows, cols = np.nonzero(live.any(1))[0], np.nonzero(live.any(0))[0]
        self.box = (slice(int(rows[0]), int(rows[-1]) + 1), slice(int(cols[0]), int(cols[-1]) + 1))
        self.fill, self.alpha = fill[self.box], alpha[self.box][..., None]
        self.glow, self.shadow = glow[self.box], shadow[self.box][..., None]
        self.lines = lay.lines

    def composite(self, rgb: np.ndarray, pulse: float) -> None:
        """Shadow, bass-pulsed glow, then the gold over `rgb` in place."""
        r = rgb[self.box]
        r *= 1 - 0.65 * self.shadow
        r += self.glow * pulse
        r *= 1 - self.alpha
        r += self.fill * self.alpha


# ── Scene ───────────────────────────────────────────────────────────────────
class Embers:
    """Floating ember particles drawn additively."""

    def __init__(self, cv: Canvas, rng: np.random.Generator):
        """Scatter embers over the frame and prebuild the four sprite sizes."""
        self.cv, self.rng = cv, rng
        n = int(110 * (cv.width * cv.height) / (1280 * 720))
        self.x = rng.uniform(0, cv.width, n)
        self.y = rng.uniform(0, cv.height, n)
        self.vx, self.vy, self.bright, self.phase = (np.zeros(n) for _ in range(4))
        self.kind = np.zeros(n, int)
        self.col = np.zeros((n, 3), np.float32)
        self._reset_motion(np.arange(n))
        self.sprites = []
        for rad in (1.1, 1.7, 2.5, 3.4):
            r = rad * cv.scale
            k = int(math.ceil(r * 3)) | 1
            g = np.arange(k) - k // 2
            s = np.exp(-(g[:, None] ** 2 + g[None, :] ** 2) / (2 * (r / 1.6) ** 2))
            self.sprites.append(s.astype(np.float32))

    def _reset_motion(self, idx):
        n = idx.size
        r = self.rng
        self.vy[idx] = -r.uniform(12, 60, n) * self.cv.scale
        self.vx[idx] = r.uniform(-12, 18, n) * self.cv.scale
        self.kind[idx] = r.choice(4, n, p=[0.45, 0.3, 0.17, 0.08])
        self.bright[idx] = r.uniform(0.35, 1.0, n)
        g = r.uniform(0.25, 0.6, n)
        self.col[idx] = np.stack([np.ones(n), g, g * 0.25], -1)
        self.phase[idx] = r.uniform(0, 2 * np.pi, n)

    def draw(self, rgb: np.ndarray, t: float, dt: float, bass: float) -> None:
        """Advance one frame and add the embers into `rgb`."""
        width, height = self.cv.width, self.cv.height
        self.x += (self.vx + np.sin(t * 1.3 + self.phase) * 10 * self.cv.scale) * dt
        self.y += self.vy * dt * (1 + 0.6 * bass)
        dead = np.nonzero((self.y < -10) | (self.x < -10) | (self.x > width + 10))[0]
        if dead.size:
            self.x[dead] = self.rng.uniform(0, width, dead.size)
            self.y[dead] = self.rng.uniform(height * 0.55, height + 8, dead.size)
            self._reset_motion(dead)
        amp = self.bright * (0.65 + 0.35 * np.sin(t * 7 + self.phase * 3)) * (0.85 + 0.5 * bass)
        for i in range(self.x.size):
            sp = self.sprites[self.kind[i]]
            k = sp.shape[0]
            x0, y0 = int(self.x[i]) - k // 2, int(self.y[i]) - k // 2
            xa, ya = max(x0, 0), max(y0, 0)
            xb, yb = min(x0 + k, width), min(y0 + k, height)
            if xa >= xb or ya >= yb:
                continue
            rgb[ya:yb, xa:xb] += sp[ya - y0:yb - y0, xa - x0:xb - x0, None] * (self.col[i] * amp[i])


class Bars:
    """The 48 bounce bars on their 70% black strip."""

    def __init__(self, cv: Canvas):
        """Precompute bar colours and x positions for this canvas."""
        self.cv = cv
        self.height = int(round(BAR_H_720 * cv.scale))
        stops = np.stack([hex_rgb(c) for c in BAR_PALETTE])
        pos = np.linspace(0, 1, len(BAR_PALETTE))
        at = np.linspace(0, 1, N_BARS)
        cols = np.stack([np.interp(at, pos, stops[:, c]) for c in range(3)], -1)
        self.cols = cols.astype(np.float32)
        slot = cv.width / N_BARS
        self.xs = [(int(round(i * slot + slot * 0.11)), int(round((i + 1) * slot - slot * 0.11)))
                   for i in range(N_BARS)]
        self.hl = max(2, int(round(3 * cv.scale)))

    def draw(self, rgb: np.ndarray, levels: np.ndarray) -> None:
        """Darken the strip and draw each bar with a white top edge."""
        bottom = self.cv.height
        rgb[bottom - self.height:] *= 1 - BAR_STRIP_OPACITY
        max_h = self.height - 6 * self.cv.scale
        for i, (xa, xb) in enumerate(self.xs):
            h = int(levels[i] * max_h)
            if h < 1:
                continue
            top = bottom - h
            rgb[top:, xa:xb] = self.cols[i]
            rgb[top:top + min(self.hl, h), xa:xb] = self.cols[i] * 0.15 + 0.85


class Scene:
    """One output format of the look; call frame() once per video frame, in order."""

    def __init__(self, cv: Canvas, text: Text, song_seconds: float, seed: int):
        """Precompute smoke, masks, embers, bars and title for this canvas."""
        self.cv = cv
        self.song_seconds = max(song_seconds, 1.0)
        rng = np.random.default_rng(seed)
        self.bars = Bars(cv)
        center_y = (cv.height * 0.42 if cv.portrait
                    else (cv.height - self.bars.height) * 0.5 + 18 * cv.scale)

        # Smoke: two drifting fBm layers, larger than the frame
        self.amp = (0.10 * cv.width, 0.08 * cv.height)
        tw, th = int(cv.width * 1.35), int(cv.height * 1.35)
        self.smoke = []
        for base, color, gain, period in ((3, (0.62, 0.12, 0.03), 0.55, 47.0),
                                          (4, (0.55, 0.34, 0.07), 0.32, 61.0)):
            noise = np.clip((fbm(th, tw, rng, base=base, octaves=6) - 0.38) * 1.9, 0, 1) ** 1.6
            self.smoke.append((Image.fromarray(noise.astype(np.float32)),
                               np.array(color, np.float32) * gain, period, rng.uniform(0, 6.28)))

        yy, xx = cv.grid()
        ex = (xx - cv.width / 2) / (cv.width * (0.55 if not cv.portrait else 0.75))
        ey = (yy - center_y) / (cv.height * (0.35 if not cv.portrait else 0.28))
        self.smoke_mask = (0.18 + 0.82 * np.exp(-(ex ** 2 + ey ** 2) * 1.4))[..., None]
        r2 = ((xx - cv.width / 2) / (cv.width / 2)) ** 2 \
            + ((yy - cv.height / 2) / (cv.height / 2)) ** 2
        self.vignette = np.clip(1 - 0.55 * r2 ** 1.3, 0.12, 1)[..., None].astype(np.float32)

        self.embers = Embers(cv, rng)
        self.title = TitlePlate(cv, text, center_y, rng)

    def _smoke_layer(self, layer: tuple, t: float, zoom: float) -> np.ndarray:
        img, _, period, phase = layer
        tw, th = img.size
        cw, ch = self.cv.width / zoom, self.cv.height / zoom
        cx = tw / 2 + self.amp[0] * math.sin(2 * math.pi * t / period + phase)
        cy = th / 2 + self.amp[1] * math.sin(2 * math.pi * t / (period * 1.37) + phase * 0.7)
        box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
        out = img.resize((self.cv.width, self.cv.height), BILINEAR, box=box)
        return np.asarray(out, np.float32)

    def frame(self, t: float, levels: np.ndarray, bass: float, fade: float) -> np.ndarray:
        """Render the frame at song time `t` as uint8 RGB."""
        zoom = 1.0 + 0.08 * min(t / self.song_seconds, 1.0)  # slow push-in over the song
        rgb = np.zeros((self.cv.height, self.cv.width, 3), np.float32)
        for layer in self.smoke:
            rgb += self._smoke_layer(layer, t, zoom)[..., None] * layer[1]
        rgb *= self.smoke_mask * (0.9 + 0.25 * bass)
        self.embers.draw(rgb, t, 1.0 / FPS, bass)
        rgb *= self.vignette
        self.title.composite(rgb, 0.75 + 0.55 * bass)
        self.bars.draw(rgb, levels)
        if fade < 1.0:
            rgb *= fade
        return (np.clip(rgb, 0, 1) * 255).astype(np.uint8)


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
