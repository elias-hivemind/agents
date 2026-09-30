"""Lyrics, sections and title metadata for the KC Vendetta Bounce renderer."""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 - argv lists only, never shell=True
from typing import List, Optional, Tuple

Line = Tuple[float, float, str]

SECTION_RE = re.compile(r"^\[(?P<name>[^\]]+)\]$")
# visual energy per song section (multiplies glow, bounce and embers)
ENERGY = {"intro": 0.75, "verse": 1.0, "pre-chorus": 1.1, "hook": 1.3, "chorus": 1.3,
          "final hook": 1.4, "full energy": 1.45, "bridge": 0.7, "drop": 0.6, "outro": 0.7,
          "fade out": 0.5}


def _ts(stamp: str) -> float:
    h, m, rest = stamp.strip().split(":")
    sec, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000


def parse_srt(text: str) -> Tuple[List[Line], List[Line]]:
    """Split an SRT into (sung lines, section markers); drops zero-length ad-libs."""
    lines: List[Line] = []
    sections: List[Line] = []
    for block in text.replace("\r", "").strip().split("\n\n"):
        rows = block.strip().split("\n")
        if len(rows) < 3 or "-->" not in rows[1]:
            continue
        a, b = (_ts(x) for x in rows[1].split("-->"))
        body = " ".join(rows[2:]).strip()
        m = SECTION_RE.match(body)
        if m:
            sections.append((a, b, m.group("name").strip().lower()))
        elif b - a >= 0.3:
            lines.append((a, b, body))
    return lines, sections


def embedded_srt(music: str) -> Optional[str]:
    """Timed lyrics stored as a subtitle track (Suno exports do this), or None."""
    cmd = ["ffmpeg", "-v", "error", "-i", music, "-map", "0:s:0", "-f", "srt", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)  # nosec B603
    return r.stdout if r.returncode == 0 and "-->" in r.stdout else None


def load_lyrics(music: str, srt: Optional[str]) -> Tuple[List[Line], List[Line]]:
    if srt:
        with open(srt, encoding="utf-8") as fh:
            return parse_srt(fh.read())
    text = embedded_srt(music)
    return parse_srt(text) if text else ([], [])


def energy_at(sections: List[Line], t: float) -> float:
    """Energy of the latest section marker at or before t (1.0 if none)."""
    level = 1.0
    for start, _, name in sections:
        if start > t:
            break
        level = ENERGY.get(name, level)
    return level


def song_title(music: str) -> str:
    """Title tag if present, else a tidy version of the file name."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format_tags=title", "-of", "json", music]
    r = subprocess.run(cmd, capture_output=True, text=True)  # nosec B603
    try:
        tag = json.loads(r.stdout).get("format", {}).get("tags", {}).get("title")
    except ValueError:
        tag = None
    if tag:
        return tag
    stem = os.path.splitext(os.path.basename(music))[0]
    stem = re.sub(r"^[0-9a-f]{8}-", "", stem)           # upload prefixes like "552fda97-"
    return re.sub(r"[_\s]+", " ", stem).strip()
