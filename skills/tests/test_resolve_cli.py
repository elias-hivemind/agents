"""Tests for resolve_cli.py against the in-memory fake scripting API."""

# Run: python3 -m unittest discover -s skills/tests

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "davinci-resolve-cli" / "scripts"))
os.environ["RESOLVE_SCRIPT_API"] = str(HERE / "fake_resolve")
os.environ["RESOLVE_SCRIPT_LIB"] = str(HERE / "fake_resolve" / "missing.so")

import resolve_cli  # noqa: E402


def run(*argv):
    buf_out, buf_err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        code = resolve_cli.main(list(argv))
    return code, buf_out.getvalue(), buf_err.getvalue()


class ResolveCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.clips = []
        for name in ("a.mp4", "b.mp4"):
            p = Path(cls.tmp.name) / name
            p.write_bytes(b"\0")
            cls.clips.append(str(p))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_01_doctor(self):
        code, out, _ = run("doctor", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["connected"])

    def test_02_offline(self):
        with mock.patch.dict(os.environ, {"FAKE_RESOLVE_OFFLINE": "1"}):
            code, _, err = run("project", "list")
        self.assertEqual(code, 2)
        self.assertIn("External scripting", err)

    def test_03_project_create_and_settings(self):
        code, out, _ = run("project", "create", "Drop07", "--fps", "30",
                           "--resolution", "1080x1920")
        self.assertEqual(code, 0, out)
        code, out, _ = run("project", "current", "--json")
        info = json.loads(out)
        self.assertEqual(info["name"], "Drop07")
        self.assertEqual(info["resolution"], "1080x1920")
        self.assertEqual(run("project", "create", "Drop07")[0], 2)

    def test_04_timeline_flow(self):
        code, _, err = run("timeline", "create", "Reel", "--clips", *self.clips, "--bin", "Raw")
        self.assertEqual(code, 0, err)
        code, out, _ = run("timeline", "list", "--json")
        self.assertEqual(json.loads(out)[0]["name"], "Reel")
        code, out, _ = run("timeline", "append", self.clips[0], "--timeline", "Reel")
        self.assertIn("appended 1", out)
        code, out, _ = run("media", "--json")
        self.assertTrue(any(r["bin"] == "Master/Raw" for r in json.loads(out)))
        self.assertEqual(run("timeline", "marker", "86400", "--timeline", "Reel")[0], 0)
        self.assertEqual(run("timeline", "marker", "86400", "--timeline", "Reel")[0], 2)
        # fps is locked once a timeline exists
        self.assertEqual(run("project", "set", "--fps", "60")[0], 2)

    def test_05_export(self):
        target = os.path.join(self.tmp.name, "reel.fcpxml")
        code, _, err = run("timeline", "export", "Reel", target, "--format", "fcpxml")
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(target))
        edl = os.path.join(self.tmp.name, "reel.edl")
        self.assertEqual(run("timeline", "export", "Reel", edl, "--format", "edl")[0], 0)
        with open(edl) as fh:
            self.assertEqual(fh.read().strip(), "1:0:Reel")  # EXPORT_EDL with EXPORT_NONE
        self.assertEqual(run("timeline", "export", "Nope", target, "--format", "edl")[0], 2)

    def test_06_render(self):
        self.assertIn("YouTube", run("render", "--list-presets")[1])
        self.assertEqual(run("render", "--timeline", "Reel", "--preset", "Bogus")[0], 2)
        self.assertEqual(run("render", "--timeline", "Reel", "--format", "mp4")[0], 2)
        outdir = os.path.join(self.tmp.name, "exports")
        with mock.patch.object(resolve_cli.time, "sleep"):
            code, out, err = run("render", "--timeline", "Reel", "--preset", "YouTube - 1080p",
                                 "--format", "mp4", "--codec", "H264",
                                 "--target-dir", outdir, "--name", "reel_v1", "--wait")
        self.assertEqual(code, 0, err)
        self.assertIn("status: Complete", out)
        jobs = json.loads(run("render", "--status", "--json")[1])
        self.assertEqual(jobs[-1]["CustomName"], "reel_v1")
        self.assertEqual(jobs[-1]["TargetDir"], os.path.abspath(outdir))

    def test_07_missing_media(self):
        self.assertEqual(run("import", "/definitely/not/here.mp4")[0], 2)


if __name__ == "__main__":
    unittest.main()
