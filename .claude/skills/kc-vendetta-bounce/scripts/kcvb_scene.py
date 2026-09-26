"""KC VENDETTA BOUNCE: title plate, embers, bounce bars and the per-frame scene."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from kcvb_style import (BAR_H_720, BAR_PALETTE, BAR_STRIP_OPACITY, BILINEAR, FPS, GLOW_RED,
                        GOLD_STOPS, N_BARS, TAGLINE_GOLD, Canvas, Text, blur, fbm, hex_rgb)

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
