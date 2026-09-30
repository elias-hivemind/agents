"""Cracked-gold title art and text helpers for the KC Vendetta Bounce renderer."""

from __future__ import annotations

import math
import os
from typing import Tuple

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fonts")
GOLD_STOPS = [(0.00, (70, 38, 10)), (0.28, (190, 132, 48)), (0.42, (250, 214, 140)),
              (0.52, (226, 170, 82)), (0.70, (150, 92, 30)), (1.00, (60, 30, 8))]


def cinzel(size: int) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(os.path.join(FONTS, "Cinzel.ttf"), size)
    f.set_variation_by_name("Black")
    return f


def oswald(size: int, weight: str = "Bold") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(os.path.join(FONTS, "Oswald.ttf"), size)
    f.set_variation_by_name(weight)
    return f


def to_np(img: Image.Image) -> np.ndarray:
    return np.asarray(img, dtype=np.float32) / 255.0


def smooth_noise(h: int, w: int, scale: int, seed: int) -> np.ndarray:
    r = np.random.default_rng(seed)
    small = r.random((max(2, h // scale), max(2, w // scale))).astype(np.float32)
    img = Image.fromarray((small * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    return to_np(img.filter(ImageFilter.GaussianBlur(scale / 3)))


def _fit_title_fonts(words, max_w2: float, squash: float, top: int):
    for size in range(top, 40, -6):
        big, small = cinzel(int(size * 1.22)), cinzel(size)
        gap = small.getlength(" ") * 0.9
        width = sum(big.getlength(w[0]) + small.getlength(w[1:]) for w in words)
        width += gap * (len(words) - 1)
        if width * squash <= max_w2:
            return big, small, gap, width
    return big, small, gap, width


def title_mask(text: str, max_w: float, squash: float = 0.70) -> Image.Image:
    """Condensed Cinzel Black with oversized first letters, as a 2x-supersampled L mask."""
    words = text.upper().split() or ["KASH", "CROWN"]
    big, small, gap, width = _fit_title_fonts(words, max_w * 2, squash, 420)
    asc, base = big.getbbox("V")[1], big.getbbox("V")[3]
    mask = Image.new("L", (int(width) + 40, int(big.size * 1.25)), 0)
    d = ImageDraw.Draw(mask)
    x = 20.0
    for w in words:
        d.text((x, 0), w[0], font=big, fill=255)
        x += big.getlength(w[0])
        d.text((x, base - small.getbbox("E")[3]), w[1:], font=small, fill=255)
        x += small.getlength(w[1:]) + gap
    mask = mask.crop((0, max(0, asc - 6), mask.width, base + 10))
    mask = mask.filter(ImageFilter.MaxFilter(5))
    return mask.resize((int(mask.width * squash), mask.height), Image.LANCZOS)


def cracks(size: Tuple[int, int], seed: int, n: int = 34) -> Image.Image:
    """Jagged crack lines as an L image (255 = crack)."""
    w, h = size
    r = np.random.default_rng(seed)
    img = Image.new("L", size, 0)
    d = ImageDraw.Draw(img)
    for _ in range(n):
        x, y, ang = r.uniform(0, w), r.uniform(0, h), r.uniform(0, 2 * math.pi)
        pts = [(x, y)]
        for _ in range(int(r.integers(4, 14))):
            ang += r.normal(0, 0.7)
            step = r.uniform(6, 26)
            x, y = x + math.cos(ang) * step, y + math.sin(ang) * step
            pts.append((x, y))
        d.line(pts, fill=int(r.uniform(120, 230)), width=int(r.integers(1, 4)))
    return img


def _gold_gradient(height: int, width: int, top: float, bot: float) -> np.ndarray:
    ys = np.linspace(0, 1, height)[:, None]
    t = np.clip((ys - top) / max(1e-6, bot - top), 0, 1)
    grad = np.zeros((height, 1, 3), np.float32)
    for (p0, c0), (p1, c1) in zip(GOLD_STOPS, GOLD_STOPS[1:]):
        sel = (t >= p0) & (t <= p1)
        k = np.where(sel, (t - p0) / (p1 - p0), 0)
        for c in range(3):
            grad[..., c] = np.where(sel, (c0[c] + (c1[c] - c0[c]) * k) / 255, grad[..., c])
    return np.repeat(grad, width, axis=1)


def _texture(M: Image.Image, top: float, bot: float) -> np.ndarray:
    col = _gold_gradient(M.height, M.width, top, bot)
    blot = smooth_noise(M.height, M.width, 40, 1)
    grain = smooth_noise(M.height, M.width, 3, 2)
    col *= (0.72 + 0.5 * blot[..., None]) * (0.88 + 0.24 * grain[..., None])
    ck = cracks(M.size, 3)
    core = to_np(ck)
    lip = to_np(ImageChops.offset(ck, -2, -2).filter(ImageFilter.GaussianBlur(1)))
    col = col * (1 - 0.65 * core[..., None])
    col += 0.35 * np.clip(lip - core, 0, 1)[..., None] * np.array([1, .85, .55], np.float32)
    soft = to_np(M.filter(ImageFilter.GaussianBlur(5)))
    shifted = np.roll(np.roll(soft, 4, 0), 4, 1)
    col += np.clip(soft - shifted, 0, 1)[..., None] * np.array([1.0, .9, .6], np.float32) * 0.9
    col -= np.clip(shifted - soft, 0, 1)[..., None] * 0.6
    return np.clip(col, 0, 1)


def build_title(text: str, max_w: float) -> Tuple[Image.Image, np.ndarray]:
    """(RGBA cracked-gold title, orange glow array of the same size)."""
    m2 = title_mask(text, max_w)
    pad = 120
    M = Image.new("L", (m2.width + 2 * pad, m2.height + 2 * pad), 0)
    M.paste(m2, (pad, pad))
    mask = to_np(M)[..., None]
    col = _texture(M, pad / M.height, (pad + m2.height) / M.height)
    rim = to_np(M.filter(ImageFilter.MaxFilter(7)))
    rgba = np.zeros((M.height, M.width, 4), np.float32)
    rgba[..., :3] = col * mask + np.array([40, 18, 4], np.float32) / 255 * (1 - mask)
    rgba[..., 3] = np.maximum(mask[..., 0], rim * 0.85)
    art = Image.fromarray((rgba * 255).astype(np.uint8), "RGBA")
    art = art.resize((art.width // 2, art.height // 2), Image.LANCZOS)
    glow = M.resize(art.size, Image.LANCZOS).filter(ImageFilter.GaussianBlur(26))
    return art, to_np(glow)[..., None] * np.array([1.0, 0.36, 0.05], np.float32)


def text_img(text: str, font, fill, spacing: int = 3, shadow: bool = True) -> Image.Image:
    """Letter-spaced text on a transparent canvas, with an optional drop shadow."""
    w = int(sum(font.getlength(c) for c in text) + spacing * len(text)) + 20
    img = Image.new("RGBA", (max(1, w), font.size + 30), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 10.0
    for c in text:
        if shadow:
            d.text((x + 2, 12), c, font=font, fill=(0, 0, 0, 200))
        d.text((x, 10), c, font=font, fill=fill)
        x += font.getlength(c) + spacing
    return img


def fit_text(text: str, size: int, max_w: float, fill) -> Image.Image:
    font = oswald(size)
    while text_img(text, font, fill, 4, False).width > max_w and font.size > 18:
        font = oswald(font.size - 2)
    return text_img(text, font, fill, 4)


def paste_rgba(frame: np.ndarray, img: Image.Image, x: int, y: int, alpha: float = 1.0) -> None:
    a = to_np(img)
    h, w = a.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(frame.shape[1], x + w), min(frame.shape[0], y + h)
    if x1 <= x0 or y1 <= y0 or alpha <= 0:
        return
    src = a[y0 - y:y1 - y, x0 - x:x1 - x]
    al = src[..., 3:4] * alpha
    frame[y0:y1, x0:x1] = frame[y0:y1, x0:x1] * (1 - al) + src[..., :3] * al


def ease_out_back(x: float, s: float = 1.9) -> float:
    x = min(max(x, 0.0), 1.0) - 1
    return 1 + (s + 1) * x ** 3 + s * x ** 2
