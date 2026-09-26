"""Tests for the kc-vendetta-bounce renderer."""

# Lyric parsing always runs; rendering runs when numpy, Pillow and ffmpeg are available.
#
# Run: python3 -m unittest discover -s skills/tests

import importlib.util
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "kc-vendetta-bounce" / "scripts"))

import vb_lyrics  # noqa: E402

HAS_RENDER = all([importlib.util.find_spec("numpy"), importlib.util.find_spec("PIL"),
                  shutil.which("ffmpeg")])

SRT = """1
00:00:03,191 --> 00:00:03,191
[Intro]

2
00:00:03,191 --> 00:00:03,191
(Kash)

3
00:00:03,191 --> 00:00:10,851
Scars don't fade when di lights come on

4
00:00:17,713 --> 00:00:17,713
[Chorus]

5
00:00:17,713 --> 00:00:22,340
Built an empire out of every scar I hold
"""


class LyricsTest(unittest.TestCase):
    def test_parse_splits_sections_and_drops_adlibs(self):
        lines, sections = vb_lyrics.parse_srt(SRT)
        self.assertEqual([t for _, _, t in lines],
                         ["Scars don't fade when di lights come on",
                          "Built an empire out of every scar I hold"])
        self.assertEqual([n for _, _, n in sections], ["intro", "chorus"])

    def test_energy_follows_sections(self):
        _, sections = vb_lyrics.parse_srt(SRT)
        self.assertEqual(vb_lyrics.energy_at(sections, 1.0), 1.0)
        self.assertEqual(vb_lyrics.energy_at(sections, 5.0), 0.75)
        self.assertEqual(vb_lyrics.energy_at(sections, 20.0), 1.3)

    def test_title_from_upload_name(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "552fda97-Concrete_Gold.txt")
            Path(p).write_text("not audio")
            self.assertEqual(vb_lyrics.song_title(p), "Concrete Gold")


@unittest.skipUnless(HAS_RENDER, "numpy/Pillow/ffmpeg not installed")
class RenderTest(unittest.TestCase):
    def test_short_square_render(self):
        import vendetta  # noqa: PLC0415
        with tempfile.TemporaryDirectory() as t:
            song = os.path.join(t, "Test_Song.wav")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",  # nosec B603 B607
                            "aevalsrc='0.8*sin(2*PI*60*t)*exp(-20*mod(t,0.5))':s=44100:d=4",
                            song], check=True)
            srt = os.path.join(t, "l.srt")
            Path(srt).write_text("1\n00:00:00,500 --> 00:00:03,000\nHello crown\n")
            out = os.path.join(t, "out.mp4")
            code = vendetta.main([song, "--format", "square", "--srt", srt, "--workers", "1",
                                  "--bpm", "120", "-o", out])
            self.assertEqual(code, 0)
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",  # nosec B603 B607
                                    "stream=width,height", "-of", "csv=p=0", out],
                                   capture_output=True, text=True).stdout
            self.assertIn("1080,1080", probe)

    def test_rejects_missing_music(self):
        import vendetta  # noqa: PLC0415
        with tempfile.TemporaryDirectory() as t:
            missing = os.path.join(t, "nope.m4a")
            self.assertEqual(vendetta.main([missing, "-o", os.path.join(t, "x.mp4")]), 2)


if __name__ == "__main__":
    unittest.main()
