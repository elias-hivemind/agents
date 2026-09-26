"""
Tests for capcut_cli.py.

Draft handling runs on synthetic drafts; ffmpeg edits run when ffmpeg is installed.

Run: python3 -m unittest discover -s skills/tests
"""

import contextlib
import io
import json
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "capcut-cli" / "scripts"))

import capcut_cli  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = capcut_cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class DraftTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "drafts"
        self.media = Path(self.tmp.name) / "media"
        self.media.mkdir()
        (self.media / "a.mp4").write_bytes(b"\0")
        plain = self.root / "0925 Drop"
        plain.mkdir(parents=True)
        (plain / "draft_meta_info.json").write_text(json.dumps({
            "draft_name": "0925 Drop", "tm_duration": 12_500_000,
            "tm_draft_modified": 1_758_800_000_000_000,
            "draft_materials": [{"type": 0, "value": [
                {"file_Path": "D:/Old/a.mp4"}, {"file_Path": "D:/Old/b.mp3"}]}],
        }))
        (plain / "draft_content.json").write_text(json.dumps({
            "canvas_config": {"width": 1080, "height": 1920, "ratio": "9:16"},
            "fps": 30.0, "duration": 12_500_000,
            "materials": {
                "videos": [{"path": "D:\\Old\\a.mp4"}, {"path": "C:/Other/c.mp4"}],
                "audios": [{"path": "D:/Old/b.mp3"}],
                "texts": [{"content": json.dumps({"text": "KASH CROWN"})}],
            },
            "tracks": [{"type": "video", "segments": [{}, {}]},
                       {"type": "text", "segments": [{}]}],
        }))
        enc = self.root / "Encrypted"
        enc.mkdir()
        (enc / "draft_meta_info.json").write_text(json.dumps({"draft_name": "Encrypted"}))
        (enc / "draft_content.json").write_bytes(b"\x8f\x02binaryciphertext")
        (self.root / "not-a-draft").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_drafts(self):
        code, out, _ = run("drafts", "--root", str(self.root), "--json")
        rows = {r["name"]: r for r in json.loads(out)}
        self.assertEqual(code, 0)
        self.assertEqual(set(rows), {"0925 Drop", "Encrypted"})
        self.assertEqual(rows["0925 Drop"]["duration_s"], 12.5)
        self.assertFalse(rows["0925 Drop"]["encrypted"])
        self.assertTrue(rows["Encrypted"]["encrypted"])

    def test_inspect(self):
        code, out, _ = run("inspect", "0925 Drop", "--root", str(self.root), "--json")
        info = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(info["canvas"], "1080x1920 (9:16)")
        self.assertEqual(info["texts"], ["KASH CROWN"])
        self.assertEqual(info["tracks"][0], {"type": "video", "segments": 2})
        code, _, err = run("inspect", "Encrypted", "--root", str(self.root))
        self.assertEqual(code, 2)
        self.assertIn("encrypted", err)
        self.assertEqual(run("inspect", "nope", "--root", str(self.root))[0], 2)

    def test_media_missing_and_meta(self):
        code, out, _ = run("media", "0925 Drop", "--root", str(self.root), "--missing", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(len(json.loads(out)), 3)
        code, out, _ = run("media", "Encrypted", "--root", str(self.root), "--meta", "--json")
        self.assertEqual((code, json.loads(out)), (0, []))

    def test_relink(self):
        d = self.root / "0925 Drop"
        before = (d / "draft_content.json").read_text()
        code, out, _ = run("relink", "0925 Drop", "--root", str(self.root),
                           "--from", "d:/old", "--to", str(self.media), "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual((d / "draft_content.json").read_text(), before)
        self.assertIn("[target missing]", out)  # b.mp3 does not exist in media/

        code, out, _ = run("relink", str(d), "--from", "D:/Old", "--to", str(self.media))
        self.assertEqual(code, 0, out)
        content = json.loads((d / "draft_content.json").read_text())
        paths = [m["path"] for m in content["materials"]["videos"]]
        self.assertEqual(paths[0], str(self.media).replace("\\", "/") + "/a.mp4")
        self.assertEqual(paths[1], "C:/Other/c.mp4")  # untouched
        meta = json.loads((d / "draft_meta_info.json").read_text())
        self.assertTrue(meta["draft_materials"][0]["value"][0]["file_Path"].endswith("/a.mp4"))
        self.assertEqual(len(list(d.glob("*.bak"))), 2)
        # prefix must match on a path boundary
        self.assertEqual(run("relink", str(d), "--from", "C:/Oth", "--to", "X:/")[0], 1)

    def test_backup(self):
        dest = Path(self.tmp.name) / "bk"
        code, out, _ = run("backup", "0925 Drop", "--root", str(self.root), "--dest", str(dest))
        self.assertEqual(code, 0)
        with zipfile.ZipFile(out.strip()) as z:
            self.assertIn("0925 Drop/draft_content.json", z.namelist())

    def test_atempo_chain(self):
        self.assertEqual(capcut_cli._atempo_chain(4.0), "atempo=2.000000,atempo=2.000000")
        self.assertEqual(capcut_cli._atempo_chain(0.25), "atempo=0.500000,atempo=0.500000")


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not installed")
class FfmpegTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)
        cls.wide = str(t / "wide.mp4")
        cls.silent = str(t / "silent.mp4")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",  # nosec B603
                        "testsrc=s=1280x720:r=25:d=3", "-f", "lavfi", "-i",
                        "sine=f=440:d=3", "-shortest", "-pix_fmt", "yuv420p", cls.wide], check=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",  # nosec B603
                        "testsrc=s=720x1280:r=30:d=2", "-pix_fmt", "yuv420p", cls.silent], check=True)
        cls.srt = str(t / "c.srt")
        Path(cls.srt).write_text("1\n00:00:00,000 --> 00:00:02,000\nHello: it's live\n")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def out(self, name):
        return os.path.join(self.tmp.name, name)

    def test_pipeline(self):
        self.assertEqual(run("reframe", self.wide, "-o", self.out("v.mp4"))[0], 0)
        v = capcut_cli.probe(self.out("v.mp4"))
        self.assertEqual((v["width"], v["height"]), (1080, 1920))

        self.assertEqual(run("trim", self.wide, "--start", "1", "--duration", "1",
                             "-o", self.out("t.mp4"))[0], 0)
        self.assertAlmostEqual(capcut_cli.probe(self.out("t.mp4"))["duration"], 1.0, delta=0.15)

        code, _, err = run("concat", self.wide, self.silent, "-o", self.out("c.mp4"))
        self.assertEqual(code, 0, err)
        c = capcut_cli.probe(self.out("c.mp4"))
        self.assertTrue(c["has_audio"])
        self.assertAlmostEqual(c["duration"], 5.0, delta=0.3)

        self.assertEqual(run("speed", self.wide, "--factor", "2", "-o", self.out("s.mp4"))[0], 0)
        self.assertAlmostEqual(capcut_cli.probe(self.out("s.mp4"))["duration"], 1.5, delta=0.2)

        code, _, err = run("captions", self.silent, "--srt", self.srt, "-o", self.out("cap.mp4"))
        self.assertEqual(code, 0, err)

        self.assertEqual(run("audio", self.silent, "--replace", self.wide, "--loop",
                             "-o", self.out("r.mp4"))[0], 0)
        self.assertTrue(capcut_cli.probe(self.out("r.mp4"))["has_audio"])
        self.assertEqual(run("audio", self.wide, "--extract", "-o", self.out("a.m4a"))[0], 0)

    def test_guards(self):
        self.assertEqual(run("trim", self.wide, "-o", self.wide, "--end", "1")[0], 2)
        self.assertEqual(run("trim", self.wide, "--start", "9", "--end", "10",
                             "-o", self.out("x.mp4"))[0], 2)
        self.assertEqual(run("audio", self.silent, "--extract", "-o", self.out("y.m4a"))[0], 2)
        self.assertEqual(run("concat", self.wide, "-o", self.out("z.mp4"))[0], 2)


if __name__ == "__main__":
    unittest.main()
