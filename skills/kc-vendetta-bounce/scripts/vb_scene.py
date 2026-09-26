"""Scene layers for KC Vendetta Bounce: layout, smoke, embers, flare, audio spectrum."""

from __future__ import annotations

import subprocess  # nosec B404 - argv lists only, never shell=True
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from vb_art import smooth_noise, to_np

SR = 22050


@dataclass
class Layout:
    width: int
    height: int
    band_y: int          # the scene is drawn in a band; black above/below (vertical format)
    band_h: int
    title_w: float       # max title width
    sub_size: int        # lyric font size
    brand_size: int
    spec_h: int          # max spectrum bar height
    embers: int

    @property
    def scale(self) -> float:
        return self.band_h / 640


LAYOUTS = {
    "vertical": Layout(1080, 1920, 640, 640, 1080 * 0.84, 40, 22, 112, 170),
    "landscape": Layout(1920, 1080, 0, 1080, 1920 * 0.74, 60, 30, 190, 320),
    "square": Layout(1080, 1080, 0, 1080, 1080 * 0.86, 44, 24, 150, 220),
}


def background(lay: Layout):
    """(warm radial base, vignette) for the band."""
    ys, xs = np.mgrid[0:lay.band_h, 0:lay.width].astype(np.float32)
    r = np.hypot((xs - lay.width / 2) / (lay.width * 0.55), (ys - lay.band_h * 0.45) / (lay.band_h * 0.7))
    warm = np.clip(1 - r, 0, 1) ** 1.8
    base = warm[..., None] * np.array([0.23, 0.07, 0.02], np.float32)
    vign = np.clip(1 - 0.85 * np.clip(r - 0.35, 0, 1), 0, 1)[..., None].astype(np.float32)
    return base.astype(np.float32), vign


def smoke_field(lay: Layout) -> np.ndarray:
    h, w = lay.band_h * 2, lay.width * 2
    big = smooth_noise(h, w, int(90 * lay.scale), 11) * 0.7 + smooth_noise(h, w, int(35 * lay.scale), 12) * 0.3
    return np.clip((big - 0.35) * 1.8, 0, 1).astype(np.float32)


def smoke_at(field: np.ndarray, lay: Layout, t: float) -> np.ndarray:
    ox = int(80 + 60 * np.sin(t * 0.13))
    oy = int(lay.band_h * 0.5 - t * 9 * lay.scale) % lay.band_h
    return field[oy:oy + lay.band_h, ox:ox + lay.width]


def flare(lay: Layout, cx: float, cy: float) -> np.ndarray:
    s = lay.scale
    ys, xs = np.mgrid[0:lay.band_h, 0:lay.width].astype(np.float32)
    body = np.exp(-(((xs - cx) / (260 * s)) ** 2 + ((ys - cy) / (38 * s)) ** 2))
    streak = np.exp(-((xs - cx) / (300 * s)) ** 2 - ((ys - cy) / (4 * s)) ** 2)
    core = np.exp(-(((xs - cx) / (40 * s)) ** 2 + ((ys - cy) / (14 * s)) ** 2))
    out = body[..., None] * np.array([1.0, 0.25, 0.05]) + streak[..., None] * np.array([0.6, 0.3, 0.12])
    return (out + core[..., None] * np.array([1.0, 0.85, 0.6])).astype(np.float32)


class Embers:
    """Deterministic ember particles; step() every frame, draw() only when rendering."""

    def __init__(self, lay: Layout, seed: int = 7):
        """Scatter lay.embers particles over the band with a fixed seed."""
        n, self.lay = lay.embers, lay
        self.rng = np.random.default_rng(seed)
        r = self.rng
        self.x, self.y = r.uniform(0, lay.width, n), r.uniform(0, lay.band_h, n)
        self.vx, self.vy = r.normal(0, 12, n) * lay.scale, -r.uniform(15, 70, n) * lay.scale
        self.size = r.choice([1, 1, 2, 2, 3], n) * max(1.0, lay.scale * 0.8)
        self.phase, self.hue = r.uniform(0, 6.283, n), r.uniform(0, 1, n)

    def step(self, dt: float, burst: float) -> None:
        self.x += (self.vx + 18 * np.sin(self.phase)) * dt
        self.y += self.vy * dt * (1 + 1.5 * burst)
        self.phase += dt * 2
        gone = (self.y < -10) | (self.x < -10) | (self.x > self.lay.width + 10)
        k = int(gone.sum())
        if k:
            self.x[gone] = self.rng.uniform(0, self.lay.width, k)
            self.y[gone] = self.lay.band_h + self.rng.uniform(0, 30, k)

    def draw(self, t: float, boost: float, density: float) -> np.ndarray:
        layer = Image.new("RGB", (self.lay.width, self.lay.band_h))
        d = ImageDraw.Draw(layer)
        flick = 0.55 + 0.45 * np.sin(self.phase * 3 + t * 9)
        count = int(len(self.x) * min(1.0, density))
        for i in range(count):
            b = float(np.clip(flick[i] * (0.7 + 0.6 * boost), 0, 1.4))
            c = (int(min(255, 255 * b)), int(min(255, (90 + 110 * self.hue[i]) * b)), int(25 * b))
            s = self.size[i]
            d.ellipse([self.x[i] - s, self.y[i] - s, self.x[i] + s, self.y[i] + s], fill=c)
        return to_np(layer) + 1.6 * to_np(layer.filter(ImageFilter.GaussianBlur(4 * self.lay.scale)))


def decode_mono(music: str, start: float, duration: float) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", music,
           "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout  # nosec B603
    return np.frombuffer(out, np.float32)


def spectrum(music: str, start: float, duration: float, fps: int, bars: int = 150) -> np.ndarray:
    """Per-frame bar levels 0..1 (log-spaced 35 Hz-11 kHz, fast attack, slow release)."""
    y = decode_mono(music, start, duration + 0.2)
    hop, nfft = SR // fps, 2048
    n = int(round(duration * fps))
    y = np.pad(y, (nfft // 2, nfft + n * hop))
    frames = np.lib.stride_tricks.sliding_window_view(y, nfft)[::hop][:n]
    mag = np.abs(np.fft.rfft(frames * np.hanning(nfft).astype(np.float32), axis=1))
    freqs = np.fft.rfftfreq(nfft, 1 / SR)
    edges = np.geomspace(35, 11000, bars + 1)
    idx = np.clip(np.searchsorted(freqs, edges), 1, len(freqs) - 1)
    levels = np.stack([mag[:, a:max(a + 1, b)].max(axis=1) for a, b in zip(idx, idx[1:])], axis=1)
    db = 20 * np.log10(levels + 1e-6)
    lo, hi = np.percentile(db, 30), np.percentile(db, 99.5)
    db = np.clip((db - lo) / max(1e-6, hi - lo), 0, 1) ** 1.4 * np.linspace(0.95, 0.7, bars)[None, :]
    held = np.empty_like(db)
    prev = np.zeros(bars, np.float32)
    for f, row in enumerate(db):
        prev = np.maximum(row, prev * 0.82)
        held[f] = prev
    return held.astype(np.float32)


def draw_spectrum(frame: np.ndarray, levels: np.ndarray, max_h: int) -> None:
    """Blocky amber bars along the bottom edge, hot orange tips on loud lows."""
    h_img, w_img = frame.shape[:2]
    bars = len(levels)
    bw = w_img / bars
    step = max(4, int(max_h / 28))
    for b, lv in enumerate(levels):
        h = int(round(lv * max_h / step)) * step + step * 2
        x0, x1 = int(b * bw), int((b + 1) * bw)
        frame[h_img - h:h_img, x0:x1] = (1.0, 0.74, 0.0)
        if b < bars * 0.08 and lv > 0.55:
            frame[h_img - h:h_img - h + step, x0:x1] = (1.0, 0.33, 0.02)
