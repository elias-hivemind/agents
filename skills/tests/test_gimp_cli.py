"""
Tests for gimp_cli.py.

Script generation always runs; the end-to-end test runs when a gimp-console
is found (set GIMP_CONSOLE to pick one).

Run: python3 -m unittest discover -s skills/tests
"""

import argparse
import contextlib
import io
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "gimp-cli" / "scripts"))

import gimp_cli  # noqa: E402


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = gimp_cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def ops(**kw):
    base = dict(crop=None, resize=None, rotate=None, flip=None, grayscale=False,
                brightness=0.0, contrast=0.0)
    base.update(kw)
    return argparse.Namespace(**base)


class ScriptGenTest(unittest.TestCase):
    def test_quoting(self):
        self.assertEqual(gimp_cli.sq('C:\\a "b"'), '"C:\\\\a \\"b\\""')

    def test_dialects(self):
        v2 = gimp_cli.build_edit_script(gimp_cli.Dialect(2), "/i.png", "/o.jpg", ops(resize="100x"))
        v3 = gimp_cli.build_edit_script(gimp_cli.Dialect(3), "/i.png", "/o.jpg", ops(resize="100x"))
        self.assertIn('(gimp-file-load RUN-NONINTERACTIVE "/i.png" "/i.png")', v2)
        self.assertIn("gimp-image-width", v2)
        self.assertIn("gimp-image-get-width", v3)
        self.assertIn('(gimp-file-save RUN-NONINTERACTIVE img "/o.jpg" -1)', v3)
        self.assertIn("gimp-image-flatten", v3)  # jpg has no alpha
        png = gimp_cli.build_edit_script(gimp_cli.Dialect(3), "/i.png", "/o.png", ops(flip="v"))
        self.assertNotIn("gimp-image-flatten", png)
        self.assertEqual(v2.count("("), v2.count(")"))
        self.assertEqual(v3.count("("), v3.count(")"))

    def test_parsers(self):
        self.assertEqual(gimp_cli.parse_resize("50%"), ("pct", 50.0, None))
        self.assertEqual(gimp_cli.parse_resize("x600"), ("px", None, 600))
        self.assertEqual(gimp_cli.parse_crop("10x20+3+4"), (10, 20, 3, 4))
        for bad in ("x", "0x10", "-5%", "abc"):
            with self.assertRaises(gimp_cli.CliError):
                gimp_cli.parse_resize(bad)
        with self.assertRaises(gimp_cli.CliError):
            gimp_cli.parse_crop("10x20")

    def test_rejects_non_gimp_executable(self):
        with self.assertRaises(gimp_cli.CliError):
            gimp_cli.find_gimp(sys.executable)

    def test_output_planning(self):
        with tempfile.TemporaryDirectory() as t:
            a = os.path.join(t, "a.png")
            Path(a).write_bytes(b"x")
            with self.assertRaises(gimp_cli.CliError):
                gimp_cli.plan_outputs([a], a, None, None, True)  # in-place
            with self.assertRaises(gimp_cli.CliError):
                gimp_cli.plan_outputs([a, a], os.path.join(t, "o.png"), None, None, False)
            outs = gimp_cli.plan_outputs([a], None, os.path.join(t, "o"), "webp", False)
            self.assertTrue(outs[0].endswith(os.path.join("o", "a.webp")))


def _gimp_available():
    try:
        return gimp_cli.find_gimp(None)
    except gimp_cli.CliError:
        return None


@unittest.skipUnless(_gimp_available() and shutil.which("ffmpeg"), "GIMP/ffmpeg not installed")
class GimpEndToEndTest(unittest.TestCase):
    def test_edit_and_info(self):
        with tempfile.TemporaryDirectory() as t:
            src = os.path.join(t, "in.jpg")
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",  # nosec B603
                            "testsrc=s=640x480", "-frames:v", "1", src], check=True)
            dst = os.path.join(t, "out.png")
            code, out, err = run("edit", src, "-o", dst, "--crop", "400x400+0+0",
                                 "--resize", "50%", "--rotate", "90", "--grayscale")
            self.assertEqual(code, 0, out + err)
            code, out, _ = run("info", dst)
            self.assertIn("200x200\tGRAY", out)
            code, out, _ = run("edit", src, "-o", os.path.join(t, "bad.png"),
                               "--crop", "9999x1+0+0")
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
