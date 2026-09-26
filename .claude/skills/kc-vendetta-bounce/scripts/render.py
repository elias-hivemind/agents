#!/usr/bin/env python3
"""KC VENDETTA BOUNCE — Kash Crown signature music visualizer.

Renders one song into:
  1. {TITLE}_Youtube_Bars_HD.mp4  16:9 1280x720 @24fps, full song length
  2. {TITLE}_TikTok_63s.mp4       9:16 720x1280 @24fps, loudest 63s section
  3. {TITLE}_Thumbnail.png        16:9 still from the loudest moment

Look: black + slow red/gold smoke + embers, distressed cracked gold title,
gold tagline, 48 beat-reactive bars (orange -> yellow -> cyan) on a 70% black strip.

Requires: Python 3.9+, numpy, Pillow, and ffmpeg (on PATH, or via `pip install imageio-ffmpeg`).
"""
from __future__ import annotations

import argparse
import math
import re
import shutil
import subprocess
import sys
import time
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
JUNK_WORDS = r"(final|master(ed)?|mix(down)?|v\d+|wav|mp3|export|bounce|prod|clean|dirty|explicit|\d{2,3}\s?bpm)"


# ── Utilities ───────────────────────────────────────────────────────────────
def hex_rgb(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)], dtype=np.float32)


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        sys.exit("ffmpeg not found. Install ffmpeg or run: pip install imageio-ffmpeg")


def parse_time(v: str | None) -> float | None:
    if v is None:
        return None
    if ":" in v:
        m, s = v.split(":", 1)
        return int(m) * 60 + float(s)
    return float(v)


def title_from_filename(path: Path) -> str:
    name = path.stem
    name = re.sub(r"[\[(].*?[\])]", " ", name)
    name = re.sub(r"[_\-.]+", " ", name)
    name = re.sub(rf"\b{JUNK_WORDS}\b", " ", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip()
    return (name or path.stem).upper()


def safe_filename(title: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
    return s or "SONG"


def fbm(h: int, w: int, rng: np.random.Generator, base: int = 3, octaves: int = 5,
        persistence: float = 0.55) -> np.ndarray:
    """Fractal value noise in [0,1], float32 (h, w)."""
    acc = np.zeros((h, w), np.float32)
    amp, total = 1.0, 0.0
    aspect = w / h
    for o in range(octaves):
        gh = base * 2 ** o + 1
        gw = max(2, int(round(gh * aspect)))
        grid = rng.random((gh, gw)).astype(np.float32)
        layer = np.asarray(Image.fromarray(grid).resize((w, h), Image.BICUBIC), np.float32)
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
    """Gaussian-like blur (3 box passes) for float 2-D arrays."""
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
    proc = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr),
                           "-f", "f32le", "-"], capture_output=True)
    if proc.returncode != 0:
        sys.exit(f"ffmpeg could not decode {path}:\n{proc.stderr.decode(errors='replace')}")
    y = np.frombuffer(proc.stdout, np.float32).copy()
    if y.size < sr:
        sys.exit(f"Audio too short or empty: {path}")
    return y


def analyze(y: np.ndarray, sr: int, n_frames: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (levels[n_frames, 48] in 0..1 smoothed, bass[n_frames] 0..1, rms[n_frames])."""
    n_fft = 2048
    win = np.hanning(n_fft).astype(np.float32)
    padded = np.pad(y, (n_fft // 2, n_fft * 2))
    centers = np.round(np.arange(n_frames) * sr / FPS).astype(np.int64)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    edges = np.geomspace(40.0, min(12000.0, sr / 2 * 0.95), N_BARS + 1)
    fc = np.sqrt(edges[:-1] * edges[1:])
    band_of_bin = np.digitize(freqs, edges) - 1
    weights = np.zeros((freqs.size, N_BARS), np.float32)
    for b in range(N_BARS):
        idx = np.nonzero(band_of_bin == b)[0]
        if idx.size == 0:  # low bands narrower than an FFT bin
            idx = np.array([np.argmin(np.abs(freqs - fc[b]))])
        weights[idx, b] = 1.0 / idx.size

    power = np.empty((n_frames, N_BARS), np.float32)
    offs = np.arange(n_fft)
    for s in range(0, n_frames, 256):
        c = centers[s:s + 256]
        seg = padded[c[:, None] + offs[None, :]] * win
        power[s:s + c.size] = (np.abs(np.fft.rfft(seg, axis=1)) ** 2) @ weights

    db = 10 * np.log10(power + 1e-10) + 3.0 * np.log2(fc / 100.0)  # +3 dB/oct tilt keeps highs alive
    ceil = np.percentile(db, 99.5)
    raw = np.clip((db - (ceil - 45.0)) / 45.0, 0, 1) ** 1.6

    levels = np.empty_like(raw)
    prev = np.zeros(N_BARS, np.float32)
    for i in range(n_frames):
        x = raw[i]
        prev = np.where(x > prev, prev + ATTACK * (x - prev), prev * RELEASE + x * (1 - RELEASE))
        levels[i] = prev
    bass = levels[:, :6].mean(axis=1)
    bass = np.clip(bass / max(np.percentile(bass, 98), 1e-6), 0, 1)

    e = np.concatenate([[0.0], np.cumsum(y.astype(np.float64) ** 2)])
    starts = np.clip(centers, 0, y.size)
    ends = np.clip(np.round((np.arange(n_frames) + 1) * sr / FPS).astype(np.int64), 0, y.size)
    rms = np.sqrt((e[ends] - e[starts]) / np.maximum(ends - starts, 1)).astype(np.float32)
    return levels, bass.astype(np.float32), rms


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
    return sum(font.getlength(ch) for ch in text) + tracking * max(len(text) - 1, 0)


def draw_tracked(draw: ImageDraw.ImageDraw, cx: float, cy: float, text: str,
                 font: ImageFont.FreeTypeFont, tracking: float, fill=255) -> None:
    x = cx - tracked_width(font, text, tracking) / 2
    for ch in text:
        draw.text((x, cy), ch, font=font, fill=fill, anchor="lm")
        x += font.getlength(ch) + tracking


def fit_lines(title: str, font_path: Path, max_w: float, max_size: int, min_one_line: int):
    """Pick one or two lines and the largest font size that fits."""
    def best(lines):
        size = max_size
        while size > 8:
            f = ImageFont.truetype(str(font_path), size)
            if all(tracked_width(f, ln, size * 0.02) <= max_w for ln in lines):
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


class TitlePlate:
    """Precomputed title layers cropped to a bbox: fill rgb, alpha, glow rgb, shadow."""

    def __init__(self, W: int, H: int, title: str, tagline: str, font_path: Path,
                 center_y: float, portrait: bool, S: float, rng: np.random.Generator):
        max_w = W * (0.86 if portrait else 0.62)
        max_size = int(H * (0.11 if portrait else 0.17))
        lines, size = fit_lines(title, font_path, max_w, max_size, int(max_size * 0.6))
        font = ImageFont.truetype(str(font_path), size)
        track = size * 0.02
        line_h = size * 1.02
        sub_size = max(int(size * 0.22), int(18 * S))
        sub_font = ImageFont.truetype(str(font_path), sub_size)
        sub_gap = size * 0.22 if tagline else 0
        block_h = line_h * len(lines) + (sub_gap + sub_size if tagline else 0)
        top = center_y - block_h / 2

        mask_img = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mask_img)
        vmap = np.zeros((H, W), np.float32)
        for k, ln in enumerate(lines):
            cy = top + line_h * (k + 0.5)
            draw_tracked(md, W / 2, cy, ln, font, track)
            y0, y1 = int(cy - size * 0.55), int(cy + size * 0.55)
            ramp = np.linspace(0, 1, max(y1 - y0, 1), dtype=np.float32)
            y0c, y1c = max(y0, 0), min(y1, H)
            vmap[y0c:y1c] = ramp[y0c - y0:y1c - y0, None]
        m = np.asarray(mask_img, np.float32) / 255.0

        sub = np.zeros((H, W), np.float32)
        if tagline:
            sub_img = Image.new("L", (W, H), 0)
            sub_cy = top + line_h * len(lines) + sub_gap + sub_size / 2
            draw_tracked(ImageDraw.Draw(sub_img), W / 2, sub_cy, tagline, sub_font, sub_size * 0.08)
            sub = np.asarray(sub_img, np.float32) / 255.0

        # Metallic gold gradient + grain
        stops_t = np.array([t for t, _ in GOLD_STOPS], np.float32)
        stops_c = np.stack([hex_rgb(c) for _, c in GOLD_STOPS])
        grad = np.stack([np.interp(vmap, stops_t, stops_c[:, ch]) for ch in range(3)], -1)
        grain = fbm(H, W, rng, base=24, octaves=3)
        grad *= (0.78 + 0.38 * grain)[..., None]

        # Cracks: jagged random-walk polylines, darkened into the gold
        crack_img = Image.new("L", (W, H), 0)
        cd = ImageDraw.Draw(crack_img)
        ys, xs = np.nonzero(m > 0.5)
        n_cracks = int(10 + 4 * len(title))
        if xs.size:
            for _ in range(n_cracks):
                j = rng.integers(xs.size)
                x, y = float(xs[j]), float(ys[j])
                ang = rng.uniform(0, 2 * np.pi)
                pts = [(x, y)]
                for _ in range(int(rng.integers(4, 12))):
                    ang += rng.normal(0, 0.55)
                    step = rng.uniform(3, 11) * S
                    x += math.cos(ang) * step
                    y += math.sin(ang) * step
                    pts.append((x, y))
                cd.line(pts, fill=int(rng.integers(110, 220)), width=max(1, int(round(rng.uniform(0.7, 1.4) * S))))
        crack = np.asarray(crack_img, np.float32) / 255.0
        grad *= (1 - 0.6 * crack)[..., None]

        # Bevel: light top edges, dark bottom edges
        d = max(1, int(round(2 * S)))
        up = np.roll(m, d, axis=0)
        down = np.roll(m, -d, axis=0)
        hl = blur(np.clip(m - up, 0, 1), 0.8 * S)
        sh = blur(np.clip(m - down, 0, 1), 0.8 * S)
        grad = grad + 0.45 * hl[..., None] * (1 - grad)
        grad *= (1 - 0.55 * sh)[..., None]

        # Distressed wear: specks knocked out of the letters
        speck = fbm(H, W, rng, base=40, octaves=3)
        speck = blur((speck > 0.86).astype(np.float32), 0.7 * S)
        grad *= (1 - 0.55 * speck)[..., None]          # worn spots go dark bronze
        alpha_t = m * (1 - 0.2 * speck)

        sub_rgb = np.broadcast_to(hex_rgb(TAGLINE_GOLD), (H, W, 3)) * (0.85 + 0.25 * grain)[..., None]
        alpha = np.clip(alpha_t + sub, 0, 1)
        fill = np.where(sub[..., None] > alpha_t[..., None], sub_rgb, grad).astype(np.float32)

        # Glow (pulses with bass) + flare above the title + drop shadow
        wide = blur(m, max(size * 0.18, 4))
        tight = blur(m, max(size * 0.05, 2))
        glow = wide[..., None] * np.array(GLOW_RED, np.float32) * 0.75 \
            + tight[..., None] * np.array([1.0, 0.5, 0.1], np.float32) * 0.28 \
            + blur(sub, max(sub_size * 0.3, 2))[..., None] * np.array(GLOW_RED, np.float32) * 0.6
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        fy = top + size * 0.05
        flare = np.exp(-(((xx - W * 0.47) / (W * 0.13)) ** 2 + ((yy - fy) / (size * 0.35)) ** 2))
        glow += flare[..., None] * np.array([1.0, 0.35, 0.08], np.float32) * 0.55
        shadow = blur(np.roll(alpha, int(4 * S), axis=0), 6 * S)

        # Crop everything to the glow bbox for cheap per-frame compositing
        live = (glow.max(-1) > 0.004) | (alpha > 0) | (shadow > 0.004)
        rows, cols = np.nonzero(live.any(1))[0], np.nonzero(live.any(0))[0]
        self.y0, self.y1 = int(rows[0]), int(rows[-1]) + 1
        self.x0, self.x1 = int(cols[0]), int(cols[-1]) + 1
        sl = (slice(self.y0, self.y1), slice(self.x0, self.x1))
        self.fill, self.alpha = fill[sl], alpha[sl][..., None]
        self.glow, self.shadow = glow[sl], shadow[sl][..., None]
        self.lines, self.size = lines, size

    def composite(self, rgb: np.ndarray, pulse: float) -> None:
        r = rgb[self.y0:self.y1, self.x0:self.x1]
        r *= 1 - 0.65 * self.shadow
        r += self.glow * pulse
        r *= 1 - self.alpha
        r += self.fill * self.alpha


# ── Scene ───────────────────────────────────────────────────────────────────
class Embers:
    def __init__(self, W: int, H: int, S: float, rng: np.random.Generator):
        self.W, self.H, self.S, self.rng = W, H, S, rng
        n = int(110 * (W * H) / (1280 * 720))
        self.x = rng.uniform(0, W, n)
        self.y = rng.uniform(0, H, n)
        self.vx, self.vy, self.bright, self.phase = (np.zeros(n) for _ in range(4))
        self.kind = np.zeros(n, int)
        self.col = np.zeros((n, 3), np.float32)
        self._reset_motion(np.arange(n))
        self.sprites = []
        for rad in (1.1, 1.7, 2.5, 3.4):
            r = rad * S
            k = int(math.ceil(r * 3)) | 1
            g = np.arange(k) - k // 2
            s = np.exp(-(g[:, None] ** 2 + g[None, :] ** 2) / (2 * (r / 1.6) ** 2)).astype(np.float32)
            self.sprites.append(s)

    def _reset_motion(self, idx):
        n = idx.size
        r = self.rng
        self.vy[idx] = -r.uniform(12, 60, n) * self.S
        self.vx[idx] = r.uniform(-12, 18, n) * self.S
        self.kind[idx] = r.choice(4, n, p=[0.45, 0.3, 0.17, 0.08])
        self.bright[idx] = r.uniform(0.35, 1.0, n)
        g = r.uniform(0.25, 0.6, n)
        self.col[idx] = np.stack([np.ones(n), g, g * 0.25], -1)
        self.phase[idx] = r.uniform(0, 2 * np.pi, n)

    def draw(self, rgb: np.ndarray, t: float, dt: float, bass: float) -> None:
        self.x += (self.vx + np.sin(t * 1.3 + self.phase) * 10 * self.S) * dt
        self.y += self.vy * dt * (1 + 0.6 * bass)
        dead = np.nonzero((self.y < -10) | (self.x < -10) | (self.x > self.W + 10))[0]
        if dead.size:
            self.x[dead] = self.rng.uniform(0, self.W, dead.size)
            self.y[dead] = self.rng.uniform(self.H * 0.55, self.H + 8, dead.size)
            self._reset_motion(dead)
        flick = 0.65 + 0.35 * np.sin(t * 7 + self.phase * 3)
        amp = self.bright * flick * (0.85 + 0.5 * bass)
        H, W = rgb.shape[:2]
        for i in range(self.x.size):
            sp = self.sprites[self.kind[i]]
            k = sp.shape[0]
            x0, y0 = int(self.x[i]) - k // 2, int(self.y[i]) - k // 2
            xa, ya, xb, yb = max(x0, 0), max(y0, 0), min(x0 + k, W), min(y0 + k, H)
            if xa >= xb or ya >= yb:
                continue
            rgb[ya:yb, xa:xb] += sp[ya - y0:yb - y0, xa - x0:xb - x0, None] * (self.col[i] * amp[i])


class Scene:
    def __init__(self, W: int, H: int, title: str, tagline: str, font: Path,
                 song_seconds: float, seed: int):
        self.W, self.H = W, H
        self.portrait = H > W
        self.S = S = min(W, H) / 720.0
        self.song_seconds = max(song_seconds, 1.0)
        rng = np.random.default_rng(seed)
        self.bar_h = int(round(BAR_H_720 * S))
        center_y = H * 0.42 if self.portrait else (H - self.bar_h) * 0.5 + 18 * S
        self.title_cy = center_y

        # Smoke: two drifting fBm layers, larger than the frame
        self.amp = (0.10 * W, 0.08 * H)
        tw, th = int(W * 1.35), int(H * 1.35)
        self.smoke = []
        for base, color, gain, period in ((3, (0.62, 0.12, 0.03), 0.55, 47.0),
                                          (4, (0.55, 0.34, 0.07), 0.32, 61.0)):
            n = fbm(th, tw, rng, base=base, octaves=6)
            n = np.clip((n - 0.38) * 1.9, 0, 1) ** 1.6
            self.smoke.append((Image.fromarray(n.astype(np.float32)),
                               np.array(color, np.float32) * gain, period, rng.uniform(0, 6.28)))

        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        ex = (xx - W / 2) / (W * (0.55 if not self.portrait else 0.75))
        ey = (yy - center_y) / (H * (0.35 if not self.portrait else 0.28))
        self.smoke_mask = (0.18 + 0.82 * np.exp(-(ex ** 2 + ey ** 2) * 1.4))[..., None]
        r2 = ((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2
        self.vignette = np.clip(1 - 0.55 * r2 ** 1.3, 0.12, 1)[..., None].astype(np.float32)

        self.embers = Embers(W, H, S, rng)
        self.title = TitlePlate(W, H, title, tagline, font, center_y, self.portrait, S, rng)

        # Bars
        stops = np.stack([hex_rgb(c) for c in BAR_PALETTE])
        pos = np.linspace(0, 1, len(BAR_PALETTE))
        at = np.linspace(0, 1, N_BARS)
        self.bar_cols = np.stack([np.interp(at, pos, stops[:, c]) for c in range(3)], -1).astype(np.float32)
        slot = W / N_BARS
        self.bar_x = [(int(round(i * slot + slot * 0.11)), int(round((i + 1) * slot - slot * 0.11)))
                      for i in range(N_BARS)]
        self.hl = max(2, int(round(3 * S)))

    def _smoke_layer(self, img: Image.Image, t: float, period: float, phase: float, zoom: float) -> np.ndarray:
        tw, th = img.size
        cw, ch = self.W / zoom, self.H / zoom
        cx = tw / 2 + self.amp[0] * math.sin(2 * math.pi * t / period + phase)
        cy = th / 2 + self.amp[1] * math.sin(2 * math.pi * t / (period * 1.37) + phase * 0.7)
        box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
        return np.asarray(img.resize((self.W, self.H), Image.BILINEAR, box=box), np.float32)

    def frame(self, t: float, levels: np.ndarray, bass: float, fade: float) -> np.ndarray:
        zoom = 1.0 + 0.08 * min(t / self.song_seconds, 1.0)  # slow push-in over the song
        rgb = np.zeros((self.H, self.W, 3), np.float32)
        for img, color, period, phase in self.smoke:
            rgb += self._smoke_layer(img, t, period, phase, zoom)[..., None] * color
        rgb *= self.smoke_mask * (0.9 + 0.25 * bass)
        self.embers.draw(rgb, t, 1.0 / FPS, bass)
        rgb *= self.vignette
        self.title.composite(rgb, 0.75 + 0.55 * bass)

        y0 = self.H - self.bar_h
        rgb[y0:] *= 1 - BAR_STRIP_OPACITY
        max_h = self.bar_h - 6 * self.S
        for i, (xa, xb) in enumerate(self.bar_x):
            h = int(levels[i] * max_h)
            if h < 1:
                continue
            top = self.H - h
            rgb[top:, xa:xb] = self.bar_cols[i]
            hh = min(self.hl, h)
            rgb[top:top + hh, xa:xb] = self.bar_cols[i] * 0.15 + 0.85
        if fade < 1.0:
            rgb *= fade
        return (np.clip(rgb, 0, 1) * 255).astype(np.uint8)


# ── Encode ──────────────────────────────────────────────────────────────────
def render(ffmpeg: str, audio: Path, out: Path, W: int, H: int, title: str, tagline: str,
           font: Path, levels: np.ndarray, bass: np.ndarray, start_f: int, n_frames: int,
           song_seconds: float, seed: int, fade_out: float, thumb_at: int | None = None,
           thumb_path: Path | None = None) -> None:
    scene = Scene(W, H, title, tagline, font, song_seconds, seed)
    start_s, dur = start_f / FPS, n_frames / FPS
    af = [f"afade=t=in:d={0.25 if start_f else 0.02}"]
    if fade_out > 0:
        af.append(f"afade=t=out:st={max(dur - fade_out, 0):.3f}:d={fade_out}")
    cmd = [ffmpeg, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-ss", f"{start_s:.3f}", "-t", f"{dur:.3f}", "-i", str(audio),
           "-map", "0:v", "-map", "1:a", "-af", ",".join(af),
           "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-movflags", "+faststart",
           "-shortest", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0, last = time.time(), -1
    try:
        for k in range(n_frames):
            f = start_f + k
            t = f / FPS
            fade = min(1.0, (k + 1) / (0.5 * FPS))
            if fade_out > 0:
                fade = min(fade, (n_frames - k) / (fade_out * FPS))
            img = scene.frame(t, levels[min(f, len(levels) - 1)], float(bass[min(f, len(bass) - 1)]), fade)
            proc.stdin.write(img.tobytes())
            if thumb_path is not None and k == thumb_at:
                Image.fromarray(img).save(thumb_path)
            pct = int(100 * (k + 1) / n_frames)
            if pct // 5 != last:
                last = pct // 5
                el = time.time() - t0
                eta = el / (k + 1) * (n_frames - k - 1)
                print(f"  {out.name}: {pct:3d}%  ({el:5.0f}s elapsed, ~{eta:4.0f}s left)", file=sys.stderr)
    finally:
        proc.stdin.close()
        rc = proc.wait()
    if rc != 0:
        sys.exit(f"ffmpeg failed while writing {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description="KC VENDETTA BOUNCE — Kash Crown music visualizer")
    ap.add_argument("audio", type=Path, help="WAV/MP3/M4A/FLAC song file")
    ap.add_argument("--title", help="Song title (default: cleaned file name, ALL CAPS)")
    ap.add_argument("--tagline", default="KASH CROWN", help='Subtitle line (use "" for none)')
    ap.add_argument("--out", type=Path, default=None, help="Output folder (default: next to the audio)")
    ap.add_argument("--only", choices=["both", "youtube", "tiktok"], default="both")
    ap.add_argument("--tiktok-start", help="Force TikTok start (seconds or m:ss); default = loudest 63s")
    ap.add_argument("--tiktok-length", type=float, default=TIKTOK_SECONDS)
    ap.add_argument("--preview", type=float, default=None, help="Render only N seconds of each (test run)")
    ap.add_argument("--scale", type=float, default=1.0, help="1.0 = 720p spec; 1.5 = 1080p")
    ap.add_argument("--font", type=Path, default=DEFAULT_FONT)
    ap.add_argument("--seed", type=int, default=7, help="Smoke/ember/crack randomness (fixed = same look)")
    a = ap.parse_args()

    if not a.audio.is_file():
        sys.exit(f"Audio file not found: {a.audio}")
    if not a.font.is_file():
        sys.exit(f"Font not found: {a.font}")
    title = (a.title or title_from_filename(a.audio)).strip().upper()
    tagline = a.tagline.strip()
    out_dir = a.out or a.audio.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(title)
    ffmpeg = find_ffmpeg()

    print(f"KC VENDETTA BOUNCE → {title!r} / {tagline!r}", file=sys.stderr)
    y = decode_mono(ffmpeg, a.audio)
    song_s = y.size / ANALYSIS_SR
    total_f = int(math.floor(song_s * FPS))
    levels, bass, rms = analyze(y, ANALYSIS_SR, total_f)

    tt_len = min(int(round(a.tiktok_length * FPS)), total_f)
    forced = parse_time(a.tiktok_start)
    tt_start = (min(int(forced * FPS), total_f - tt_len) if forced is not None
                else loudest_window(rms, bass, tt_len))
    print(f"  song {song_s:.1f}s · TikTok window {tt_start / FPS:.1f}s → {(tt_start + tt_len) / FPS:.1f}s",
          file=sys.stderr)

    s = a.scale
    even = lambda v: int(round(v * s / 2)) * 2
    outputs = []
    if a.only in ("both", "youtube"):
        n = total_f if a.preview is None else min(total_f, int(a.preview * FPS))
        yt = out_dir / f"{stem}_Youtube_Bars_HD.mp4"
        thumb = out_dir / f"{stem}_Thumbnail.png"
        peak = tt_start + min(tt_len // 3, 4 * FPS)
        render(ffmpeg, a.audio, yt, even(1280), even(720), title, tagline, a.font, levels, bass,
               0, n, song_s, a.seed, fade_out=1.5 if a.preview is None else 0.0,
               thumb_at=peak if peak < n else n // 2, thumb_path=thumb)
        outputs += [yt, thumb]
    if a.only in ("both", "tiktok"):
        n = tt_len if a.preview is None else min(tt_len, int(a.preview * FPS))
        tt = out_dir / f"{stem}_TikTok_{int(round(a.tiktok_length))}s.mp4"
        render(ffmpeg, a.audio, tt, even(720), even(1280), title, tagline, a.font, levels, bass,
               tt_start, n, song_s, a.seed, fade_out=1.2)
        outputs.append(tt)

    print("\nDONE", file=sys.stderr)
    for p in outputs:
        print(p)


if __name__ == "__main__":
    main()
