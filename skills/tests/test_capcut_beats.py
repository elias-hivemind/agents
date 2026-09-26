"""Tests for capcut-cli beat detection and beat-synced cutting."""

# Synthetic tracks have known tempo and beat positions, so detection is checked
# against ground truth; rendered reels are checked frame by frame for cut timing.
#
# Run: python3 -m unittest discover -s skills/tests

import contextlib
import io
import json
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "capcut-cli" / "scripts"))

import capcut_beats  # noqa: E402
import capcut_cli  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = capcut_cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def ff(*argv):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *argv], check=True)  # nosec B603


class PlanTest(unittest.TestCase):
    beats = [0.5 * k for k in range(40)]

    def test_every_two_beats_from_a_beat(self):
        self.assertEqual(capcut_beats.plan_segments(self.beats, 0.0, 4, 2),
                         [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4)])

    def test_shift_moves_cuts_by_one_beat(self):
        segs = capcut_beats.plan_segments(self.beats, 0.0, 4, 2, shift=1)
        self.assertEqual([round(a, 3) for a, _ in segs], [0.0, 0.5, 1.5, 2.5, 3.5])

    def test_short_tail_is_merged(self):
        self.assertEqual(capcut_beats.plan_segments(self.beats, 0.0, 3.1, 2)[-1], (2.0, 3.1))

    def test_segments_cover_duration_without_gaps(self):
        segs = capcut_beats.plan_segments(self.beats, 0.13, 7.3, 1)
        self.assertEqual(segs[0][0], 0.0)
        self.assertEqual(segs[-1][1], 7.3)
        for (_, a), (b, _) in zip(segs, segs[1:]):
            self.assertEqual(a, b)

    def test_rejects_bad_every(self):
        with self.assertRaises(capcut_beats.CliError):
            capcut_beats.plan_segments(self.beats, 0, 4, 0)


def frame_colours(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", "scale=1:1",  # nosec B603
                          "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    names = []
    for i in range(0, len(raw), 3):
        r, g = raw[i], raw[i + 1]
        names.append("Y" if r > 150 and g > 150 else "R" if r > 150 else "G" if g > 100 else "B")
    return names


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not installed")
class BeatTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = cls.tmp.name
        cls.k95 = os.path.join(t, "k95.wav")   # kick every 60/95 s from 0.13 s, 8th-note hats
        ff("-f", "lavfi", "-i",
           "aevalsrc='0.9*sin(2*PI*55*t)*exp(-25*mod(t-0.13+10*60/95,60/95))"
           "+0.15*sin(2*PI*880*t)*exp(-60*mod(t-0.13+10*60/190,60/190))':s=44100:d=20",
           cls.k95)
        cls.clips = []
        for colour in ("red", "green", "blue"):
            p = os.path.join(t, f"{colour}.mp4")
            ff("-f", "lavfi", "-i", f"color=c={colour}:s=1280x720:r=25:d=6",
               "-pix_fmt", "yuv420p", p)
            cls.clips.append(p)
        cls.photo = os.path.join(t, "photo.jpg")
        ff("-f", "lavfi", "-i", "color=c=yellow:s=800x800", "-frames:v", "1", cls.photo)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_detects_tempo_and_beats(self):
        code, out, err = run("beats", self.k95, "--duration", "16", "--json")
        self.assertEqual(code, 0, err)
        found = json.loads(out)
        self.assertAlmostEqual(found["bpm"], 95, delta=1)
        truth = [0.13 + k * 60 / 95 for k in range(len(found["beats"]))]
        for got, want in zip(found["beats"], truth):
            self.assertAlmostEqual(got, want, delta=0.025)

    def test_beatsync_cuts_on_every_second_beat(self):
        out = os.path.join(self.tmp.name, "reel.mp4")
        code, _, err = run("beatsync", *self.clips[:2], self.photo, self.clips[2],
                           "--music", self.k95, "--music-start", "0.5",
                           "--duration", "8", "-o", out)
        self.assertEqual(code, 0, err)
        info = capcut_cli.probe(out)
        self.assertEqual((info["width"], info["height"]), (1080, 1920))
        self.assertAlmostEqual(info["duration"], 8.0, delta=0.1)
        self.assertTrue(info["has_audio"])

        colours = frame_colours(out)
        cuts = [i / 30 for i in range(1, len(colours)) if colours[i] != colours[i - 1]]
        # song is aligned to the beat at 0.762 s; cuts every 2 beats of 60/95 s
        expected = [k * 2 * 60 / 95 for k in range(1, len(cuts) + 1)]
        self.assertEqual(len(cuts), 6)
        for got, want in zip(cuts, expected):
            self.assertAlmostEqual(got, want, delta=0.05)
        order = [colours[0]] + [colours[round(c * 30)] for c in cuts]
        self.assertEqual(order[:5], ["R", "G", "Y", "B", "R"])

    def test_beatsync_guards(self):
        out = os.path.join(self.tmp.name, "x.mp4")
        self.assertEqual(run("beatsync", self.clips[0], "--music", self.clips[0], "-o", out)[0], 2)
        self.assertEqual(run("beatsync", "missing.mp4", "--music", self.k95, "-o", out)[0], 2)


if __name__ == "__main__":
    unittest.main()
