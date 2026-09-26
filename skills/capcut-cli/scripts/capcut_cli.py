#!/usr/bin/env python3
"""capcut-cli: CapCut draft management + CapCut-style quick edits."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

CONTENT_FILES = ("draft_content.json", "draft_info.json")
META_FILE = "draft_meta_info.json"


EPILOG = """
CapCut has no official CLI, scripting API or headless export. This tool
covers what can be automated safely:

  Drafts (CapCut desktop project folders, CapCut must be CLOSED to edit):
    doctor, drafts, inspect, media, relink, backup
  Quick edits rendered with ffmpeg (no CapCut needed):
    probe, trim, concat, speed, reframe, captions, audio, beats, beatsync

Newer CapCut desktop builds encrypt draft_content.json. For those drafts
`drafts`, `backup` and `media --meta` still work (they read the plain
draft_meta_info.json); `inspect`/`relink` report the draft as encrypted
and leave it untouched.

Requires: Python 3.8+; ffmpeg + ffprobe on PATH for the edit commands.

Usage examples --
  capcut_cli.py drafts
  capcut_cli.py inspect "0925 Drop"
  capcut_cli.py media "0925 Drop" --missing
  capcut_cli.py relink "0925 Drop" --from D:/Old --to E:/Footage --dry-run
  capcut_cli.py concat a.mp4 b.mp4 c.mp4 -o reel.mp4 --size 1080x1920
  capcut_cli.py reframe wide.mp4 -o vertical.mp4 --aspect 9:16 --mode crop
  capcut_cli.py captions reel.mp4 --srt reel.srt -o reel_captioned.mp4
  capcut_cli.py beatsync a.mp4 b.mp4 c.jpg --music beat.mp3 --duration 15 -o reel.mp4
"""


from capcut_ffmpeg import (  # noqa: E402 - sibling module in this scripts/ folder
    CliError,
    _atempo_chain,
    cmd_audio,
    cmd_captions,
    cmd_concat,
    cmd_reframe,
    cmd_speed,
    cmd_trim,
    probe,
)

from capcut_beats import add_beat_parsers  # noqa: E402

__all__ = ["CliError", "_atempo_chain", "main", "probe"]


# --------------------------------------------------------------------------
# Draft discovery
# --------------------------------------------------------------------------

def default_roots() -> List[Path]:
    home = Path.home()
    system = platform.system()
    roots: List[Path] = []
    if system == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        roots += [local / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft",
                  local / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"]
    elif system == "Darwin":
        roots += [home / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft",
                  home / "Movies" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft"]
    return roots


def draft_root(explicit: Optional[str]) -> Path:
    choice = explicit or os.environ.get("CAPCUT_DRAFTS")
    if choice:
        p = Path(choice).expanduser()
        if not p.is_dir():
            raise CliError(f"Draft folder not found: {p}")
        return p
    for r in default_roots():
        if r.is_dir():
            return r
    raise CliError(
        "CapCut draft folder not found. In CapCut: Settings > Projects > Draft "
        "location; pass it with --root or set CAPCUT_DRAFTS."
    )


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def list_drafts(root: Path) -> List[Dict[str, Any]]:
    rows = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        meta = read_json(d / META_FILE)
        if meta is None:
            continue
        rows.append({
            "name": meta.get("draft_name") or d.name,
            "folder": str(d),
            "duration_s": round((meta.get("tm_duration") or 0) / 1_000_000, 2),
            "modified": _ts(meta.get("tm_draft_modified")),
            "encrypted": content_file(d) is not None and load_content(d) is None,
        })
    return rows


def _ts(value: Any) -> Optional[str]:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    # CapCut stores microseconds in recent builds, seconds in older ones.
    seconds = value / 1_000_000 if value > 10 ** 12 else value
    return dt.datetime.fromtimestamp(seconds).isoformat(timespec="seconds")


def resolve_draft(root_arg: Optional[str], name: str) -> Path:
    p = Path(name).expanduser()
    if p.is_dir() and (p / META_FILE).exists():
        return p
    root = draft_root(root_arg)
    matches = [Path(r["folder"]) for r in list_drafts(root)
               if r["name"] == name or Path(r["folder"]).name == name]
    if not matches:
        raise CliError(f"No draft named '{name}' in {root} (see: capcut_cli.py drafts)")
    if len(matches) > 1:
        raise CliError(f"'{name}' is ambiguous; pass the folder path instead: "
                       + ", ".join(map(str, matches)))
    return matches[0]


def content_file(d: Path) -> Optional[Path]:
    for n in CONTENT_FILES:
        if (d / n).is_file():
            return d / n
    return None


def load_content(d: Path) -> Optional[Dict[str, Any]]:
    f = content_file(d)
    return read_json(f) if f else None


def require_content(d: Path) -> Tuple[Path, Dict[str, Any]]:
    f = content_file(d)
    if f is None:
        raise CliError(f"No draft_content.json in {d}")
    data = read_json(f)
    if data is None:
        raise CliError(
            f"{f.name} is encrypted or not JSON (newer CapCut builds encrypt drafts). "
            "Only drafts/backup/media --meta work on this draft."
        )
    return f, data


def iter_material_paths(content: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    materials = content.get("materials") or {}
    for kind in ("videos", "audios", "images", "stickers"):
        for m in materials.get(kind) or []:
            if isinstance(m, dict) and isinstance(m.get("path"), str) and m["path"]:
                yield kind, m


def iter_meta_paths(meta: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, Any]]]:
    for group in meta.get("draft_materials") or []:
        for m in (group or {}).get("value") or []:
            if isinstance(m, dict) and isinstance(m.get("file_Path"), str) and m["file_Path"]:
                yield "meta", m


# --------------------------------------------------------------------------
# Draft commands
# --------------------------------------------------------------------------

def emit(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif isinstance(data, list):
        for row in data:
            print("\t".join(f"{v}" for v in row.values()) if isinstance(row, dict) else row)
    else:
        for k, v in data.items():
            print(f"{k}: {v}")


def cmd_doctor(args) -> int:
    info: Dict[str, Any] = {"platform": platform.system(),
                            "ffmpeg": shutil.which("ffmpeg"),
                            "ffprobe": shutil.which("ffprobe")}
    try:
        root = draft_root(args.root)
        info["draft_root"] = str(root)
        info["drafts"] = len(list_drafts(root))
    except CliError as exc:
        info["draft_root"] = None
        info["note"] = str(exc)
    emit(info, args.json)
    return 0


def cmd_drafts(args) -> int:
    emit(list_drafts(draft_root(args.root)), args.json)
    return 0


def cmd_inspect(args) -> int:
    d = resolve_draft(args.root, args.draft)
    _, c = require_content(d)
    canvas = c.get("canvas_config") or {}
    tracks = c.get("tracks") or []
    texts = []
    for t in (c.get("materials") or {}).get("texts") or []:
        raw = t.get("content")
        try:
            raw = json.loads(raw).get("text", raw) if isinstance(raw, str) and raw.startswith("{") else raw
        except ValueError:
            pass
        texts.append(raw)
    summary = {
        "draft": str(d),
        "canvas": f"{canvas.get('width')}x{canvas.get('height')} ({canvas.get('ratio')})",
        "fps": c.get("fps"),
        "duration_s": round((c.get("duration") or 0) / 1_000_000, 2),
        "tracks": [{"type": t.get("type"), "segments": len(t.get("segments") or [])} for t in tracks],
        "media": sum(1 for _ in iter_material_paths(c)),
        "texts": texts,
    }
    emit(summary, args.json)
    return 0


def cmd_media(args) -> int:
    d = resolve_draft(args.root, args.draft)
    if args.meta:
        meta = read_json(d / META_FILE) or {}
        pairs = [(k, m["file_Path"]) for k, m in iter_meta_paths(meta)]
    else:
        _, c = require_content(d)
        pairs = [(k, m["path"]) for k, m in iter_material_paths(c)]
    rows = []
    for kind, path in pairs:
        exists = os.path.exists(path)
        if args.missing and exists:
            continue
        rows.append({"kind": kind, "exists": exists, "path": path})
    emit(rows, args.json)
    return 1 if args.missing and rows else 0


def _norm(p: str) -> str:
    return p.replace("\\", "/")


def _swap_prefix(path: str, old: str, new: str) -> Optional[str]:
    """Replace a path prefix, case-insensitively on Windows-style paths."""
    np_, no = _norm(path), _norm(old).rstrip("/")
    if np_.lower() == no.lower() or np_.lower().startswith(no.lower() + "/"):
        return _norm(new).rstrip("/") + np_[len(no):]
    return None


def _relink_paths(items, key: str, old: str, new: str) -> List[Tuple[str, str]]:
    """Rewrite m[key] in place for every item under the old prefix."""
    changes = []
    for _, m in items:
        swapped = _swap_prefix(m[key], old, new)
        if swapped and swapped != m[key]:
            changes.append((m[key], swapped))
            m[key] = swapped
    return changes


def _write_json_with_backup(path: Path, data: Dict[str, Any], stamp: str) -> None:
    shutil.copy2(path, path.with_name(f"{path.name}.{stamp}.bak"))
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def cmd_relink(args) -> int:
    d = resolve_draft(args.root, args.draft)
    f, content = require_content(d)
    meta_path = d / META_FILE
    meta = read_json(meta_path)
    changes = _relink_paths(iter_material_paths(content), "path",
                            args.from_prefix, args.to_prefix)
    meta_changes = _relink_paths(iter_meta_paths(meta), "file_Path",
                                 args.from_prefix, args.to_prefix) if meta else []

    for old, new in changes:
        flag = "" if os.path.exists(new) else "   [target missing]"
        print(f"{old} -> {new}{flag}")
    if not changes and not meta_changes:
        print("nothing matched --from; no changes")
        return 1
    if args.dry_run:
        print(f"dry run: {len(changes)} material path(s), {len(meta_changes)} meta path(s)")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    _write_json_with_backup(f, content, stamp)
    if meta_changes:
        _write_json_with_backup(meta_path, meta, stamp)
    print(f"relinked {len(changes)} material path(s), {len(meta_changes)} meta path(s); "
          f"backups *.{stamp}.bak")
    return 0


def cmd_backup(args) -> int:
    d = resolve_draft(args.root, args.draft)
    dest_dir = Path(args.dest or ".").expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^\w.-]+", "_", d.name)
    zpath = dest_dir / f"{safe}-{stamp}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(d.rglob("*")):
            if p.is_file():
                z.write(p, Path(d.name) / p.relative_to(d))
    print(zpath)
    return 0


# --------------------------------------------------------------------------
def cmd_probe(args) -> int:
    emit([probe(p) for p in args.inputs], args.json)
    return 0


# argparse
# --------------------------------------------------------------------------

def _add_draft_parsers(sub, common) -> None:
    s = sub.add_parser("doctor", parents=[common], help="find drafts folder and ffmpeg")
    s.set_defaults(func=cmd_doctor)
    s = sub.add_parser("drafts", parents=[common], help="list drafts")
    s.set_defaults(func=cmd_drafts)
    s = sub.add_parser("inspect", parents=[common], help="canvas, tracks, texts of a draft")
    s.add_argument("draft", help="draft name or folder")
    s.set_defaults(func=cmd_inspect)
    s = sub.add_parser("media", parents=[common], help="media referenced by a draft")
    s.add_argument("draft")
    s.add_argument("--missing", action="store_true", help="only offline files (exit 1 if any)")
    s.add_argument("--meta", action="store_true",
                   help="read draft_meta_info.json (works on encrypted drafts)")
    s.set_defaults(func=cmd_media)
    s = sub.add_parser("relink", parents=[common], help="rewrite media path prefixes (CapCut closed!)")
    s.add_argument("draft")
    s.add_argument("--from", dest="from_prefix", required=True)
    s.add_argument("--to", dest="to_prefix", required=True)
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_relink)
    s = sub.add_parser("backup", parents=[common], help="zip a draft folder")
    s.add_argument("draft")
    s.add_argument("--dest", help="directory for the zip (default: cwd)")
    s.set_defaults(func=cmd_backup)
    s = sub.add_parser("probe", parents=[common], help="duration/size/codecs via ffprobe")
    s.add_argument("inputs", nargs="+")
    s.set_defaults(func=cmd_probe)


def _add_clip_parsers(sub, render) -> None:
    s = sub.add_parser("trim", parents=[render], help="cut a clip")
    s.add_argument("input")
    s.add_argument("--start", type=float, default=0.0)
    s.add_argument("--end", type=float)
    s.add_argument("--duration", type=float)
    s.set_defaults(func=cmd_trim)
    s = sub.add_parser("concat", parents=[render], help="join clips on one canvas")
    s.add_argument("inputs", nargs="+")
    s.add_argument("--size", default="1080x1920")
    s.add_argument("--fps", type=float, default=30)
    s.add_argument("--mode", choices=["pad", "crop"], default="pad")
    s.set_defaults(func=cmd_concat)
    s = sub.add_parser("speed", parents=[render], help="speed up / slow down")
    s.add_argument("input")
    s.add_argument("--factor", type=float, required=True, help="2 = twice as fast")
    s.add_argument("--mute", action="store_true")
    s.set_defaults(func=cmd_speed)
    s = sub.add_parser("reframe", parents=[render], help="change aspect (crop or pad)")
    s.add_argument("input")
    s.add_argument("--aspect", default="9:16")
    s.add_argument("--size", help="exact WxH, overrides --aspect")
    s.add_argument("--mode", choices=["crop", "pad"], default="crop")
    s.set_defaults(func=cmd_reframe)


def _add_overlay_parsers(sub, render) -> None:
    s = sub.add_parser("captions", parents=[render], help="burn in an .srt")
    s.add_argument("input")
    s.add_argument("--srt", required=True)
    s.add_argument("--font", default="Arial")
    s.add_argument("--font-size", type=int, default=16)
    s.add_argument("--color", default="&H00FFFFFF", help="ASS &HAABBGGRR, default white")
    s.add_argument("--margin", type=int, default=60)
    s.set_defaults(func=cmd_captions)
    s = sub.add_parser("audio", parents=[render], help="replace / mute / extract audio")
    s.add_argument("input")
    s.add_argument("--replace", metavar="FILE")
    s.add_argument("--loop", action="store_true", help="loop --replace audio to video length")
    s.add_argument("--mute", action="store_true")
    s.add_argument("--extract", action="store_true", help="write audio only (use .m4a)")
    s.set_defaults(func=cmd_audio)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="capcut-cli", description=__doc__, epilog=EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    common.add_argument("--root", help="CapCut draft folder (default: auto / $CAPCUT_DRAFTS)")
    render = argparse.ArgumentParser(add_help=False)
    render.add_argument("-o", "--output", required=True)
    render.add_argument("--force", action="store_true", help="overwrite output")
    render.add_argument("--dry-run", action="store_true", help="print the ffmpeg command")
    sub = p.add_subparsers(dest="cmd", required=True)
    _add_draft_parsers(sub, common)
    _add_clip_parsers(sub, render)
    _add_overlay_parsers(sub, render)
    add_beat_parsers(sub, common, render)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"capcut-cli: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
