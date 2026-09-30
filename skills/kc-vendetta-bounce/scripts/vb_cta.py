"""Follow call-to-action for the KC Vendetta Bounce renderer: an end card and a mid-song banner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np
from PIL import Image

from vb_art import cinzel, ease_out_back, oswald, paste_rgba, text_img

PLATFORMS = ("YouTube", "Instagram", "Facebook", "Threads", "Pinterest", "X")
GOLD = (232, 190, 105, 255)
BRIGHT = (255, 226, 160, 255)


@dataclass
class Cta:
    """Who to follow and for how long the end card holds."""

    brand: str = "Kash Crown"
    handle: str = "kashcrown"      # every platform in PLATFORMS
    tiktok: str = "kashcrown0"     # TikTok has its own handle
    end_len: float = 8.0           # seconds; capped at 20% of the video
    mid_len: float = 6.0           # mid-song banner seconds (only on videos of 60 s or more)


def at(handle: str) -> str:
    """Lowercase @handle, so a trailing 0 can't be misread as the letter O."""
    return "@" + handle.lstrip("@").lower()


def _line(text: str, font, fill, spacing: int) -> Image.Image:
    img = text_img(text, font, fill, spacing)
    return img.crop(img.getbbox() or (0, 0, 1, 1))


def _stack(lines: List[Image.Image], gap: int) -> Image.Image:
    w = max(i.width for i in lines)
    h = sum(i.height for i in lines) + gap * (len(lines) - 1)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    y = 0
    for img in lines:
        out.alpha_composite(img, ((w - img.width) // 2, y))
        y += img.height + gap
    return out


def _fit(img: Image.Image, max_w: float, max_h: float) -> Image.Image:
    k = min(1.0, max_w / img.width, max_h / img.height)
    if k >= 1.0:
        return img
    return img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)


def end_card(cta: Cta, size: int) -> Image.Image:
    """FOLLOW <BRAND> / platform list / @handle / TikTok @handle, centred."""
    sep = "  ·  "
    lines = [
        _line(f"FOLLOW {cta.brand.upper()}", cinzel(int(size * 1.1)), BRIGHT, 4),
        _line(sep.join(p.upper() for p in PLATFORMS), oswald(int(size * 0.6), "Medium"), GOLD, 3),
        _line(at(cta.handle), oswald(size), BRIGHT, 5),
        _line(f"TIKTOK  {at(cta.tiktok)}", oswald(int(size * 0.72)), GOLD, 4),
    ]
    return _stack(lines, max(8, size // 3))


def banner(cta: Cta, size: int) -> Image.Image:
    """One-line mid-song reminder: follow @handle, TikTok @handle."""
    text = f"FOLLOW {at(cta.handle)}   ·   TIKTOK {at(cta.tiktok)}"
    return _line(text, oswald(size, "Medium"), GOLD, 4)


def placement(lay, title_bottom: int):
    """Return (y0, y1, text size, shares the lyric area) for the CTA in full-frame pixels."""
    # Letterboxed vertical video puts the CTA in the empty bar under the scene; the other
    # formats use the space between the title and the spectrum, where the lyrics sit.
    if lay.band_y > 0:
        y0 = lay.band_y + lay.band_h + int(70 * lay.scale)
        return y0, lay.height - int(140 * lay.scale), int(lay.sub_size * 1.5), False
    return title_bottom, lay.band_h - lay.spec_h - int(16 * lay.scale), lay.sub_size, True


class CtaArt:
    """Pre-rendered CTA images for one layout, plus when and where to show them."""

    def __init__(self, cta: Cta, lay, title_bottom: int, duration: float):
        """Fit the end card and banner to the CTA area for this layout."""
        self.cta, self.lay, self.duration = cta, lay, duration
        self.y0, self.y1, size, self.shares_lyrics = placement(lay, title_bottom)
        room = self.y1 - self.y0
        self.card = _fit(end_card(cta, size), lay.width * 0.9, room)
        self.banner = _fit(banner(cta, int(size * 0.62)), lay.width * 0.9, size * 1.2)
        self.end_start = duration - min(cta.end_len, duration * 0.2)
        self.mid_start = duration * 0.45 if duration >= 60 and cta.mid_len > 0 else None

    def lyric_alpha(self, t: float) -> float:
        """Lyrics fade out as the end card comes in, when both share one area."""
        if not self.shares_lyrics:
            return 1.0
        return float(np.clip((self.end_start - t) / 0.4, 0.0, 1.0))

    def draw(self, frame: np.ndarray, t: float) -> None:
        """Paint whichever CTA is live at time t onto the full frame."""
        lay = self.lay
        if t >= self.end_start:
            p = (t - self.end_start) / 0.6
            rise = int((1 - ease_out_back(min(p, 1.0))) * 30 * lay.scale)
            y = self.y0 + (self.y1 - self.y0 - self.card.height) // 2 + rise
            paste_rgba(frame, self.card, lay.width // 2 - self.card.width // 2, y, min(1.0, p))
            return
        m = self.mid_start
        if m is not None and m <= t < m + self.cta.mid_len:
            a = min(1.0, (t - m) / 0.5, (m + self.cta.mid_len - t) / 0.5)
            y = self.y1 - self.banner.height if self.shares_lyrics else self.y0
            paste_rgba(frame, self.banner, lay.width // 2 - self.banner.width // 2, y, a)
