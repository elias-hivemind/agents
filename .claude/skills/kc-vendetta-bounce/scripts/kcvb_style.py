"""KC VENDETTA BOUNCE: the locked style contract plus shared canvas and noise helpers."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

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

def hex_rgb(code: str) -> np.ndarray:
    """'#RRGGBB' -> float32 RGB in 0..1."""
    code = code.lstrip("#")
    return np.array([int(code[i:i + 2], 16) / 255.0 for i in (0, 2, 4)], dtype=np.float32)

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
