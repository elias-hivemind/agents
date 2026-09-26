#!/usr/bin/env python3
"""capcut-cli: CapCut draft management + CapCut-style quick edits.

CapCut has no official CLI, scripting API or headless export. This tool
covers what can be automated safely:

  Drafts (CapCut desktop project folders, CapCut must be CLOSED to edit):
    doctor, drafts, inspect, media, relink, backup
  Quick edits rendered with ffmpeg (no CapCut needed):
    probe, trim, concat, speed, reframe, captions, audio

Newer CapCut desktop builds encrypt draft_content.json. For those drafts
`drafts`, `backup` and `media --meta` still work (they read the plain
draft_meta_info.json); `inspect`/`relink` report the draft as encrypted
and leave it untouched.

Requires: Python 3.8+; ffmpeg + ffprobe on PATH for the edit commands.

Examples:
  capcut_cli.py drafts
  capcut_cli.py inspect "0925 Drop"
  capcut_cli.py media "0925 Drop" --missing
  capcut_cli.py relink "0925 Drop" --from D:/Old --to E:/Footage --dry-run
  capcut_cli.py concat a.mp4 b.mp4 c.mp4 -o reel.mp4 --size 1080x1920
  capcut_cli.py reframe wide.mp4 -o vertical.mp4 --aspect 9:16 --mode crop
  capcut_cli.py captions reel.mp4 --srt reel.srt -o reel_captioned.mp4
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

CONTENT_FILES = ("draft_content.json", "draft_info.json")
META_FILE = "draft_meta_info.json"


class CliError(Exception):
    pass


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
# ffmpeg edits
# --------------------------------------------------------------------------

def need_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise CliError(f"{tool} not found on PATH")


def probe(path: str) -> Dict[str, Any]:
    need_ffmpeg()
    if not os.path.isfile(path):
        raise CliError(f"Input not found: {path}")
    # argv list, no shell; fixed tool name, path passed as a single argument
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",  # nosec B603
                        "-show_format", "-show_streams", path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise CliError(f"ffprobe failed on {path}: {r.stderr.strip()}")
    data = json.loads(r.stdout)
    v = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
    return {
        "file": path,
        "duration": float(data["format"].get("duration") or 0),
        "width": v and v.get("width"), "height": v and v.get("height"),
        "fps": v and v.get("avg_frame_rate"),
        "vcodec": v and v.get("codec_name"),
        "has_audio": a is not None, "acodec": a and a.get("codec_name"),
    }


def parse_size(s: str) -> Tuple[int, int]:
    m = re.fullmatch(r"(\d+)x(\d+)", s)
    if not m or int(m.group(1)) % 2 or int(m.group(2)) % 2:
        raise CliError(f"--size must be WxH with even numbers, got {s}")
    return int(m.group(1)), int(m.group(2))


def parse_aspect(s: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)", s)
    if not m or float(m.group(2)) == 0:
        raise CliError(f"--aspect must look like 9:16, got {s}")
    return float(m.group(1)) / float(m.group(2))


def check_output(args) -> None:
    out = os.path.abspath(args.output)
    ins = getattr(args, "inputs", None) or [getattr(args, "input")]
    if any(os.path.abspath(i) == out for i in ins):
        raise CliError("Output must differ from the input")
    if os.path.exists(out) and not args.force:
        raise CliError(f"Output exists: {out} (pass --force)")
    Path(out).parent.mkdir(parents=True, exist_ok=True)


ENCODE = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]


def ffmpeg(args, argv: List[str]) -> int:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-stats",
           "-y" if args.force else "-n"] + argv
    if args.dry_run:
        print(" ".join(f'"{c}"' if " " in c else c for c in cmd))
        return 0
    # argv list, no shell; cmd[0] is always ffmpeg, filter args are escaped
    r = subprocess.run(cmd)  # nosec B603
    if r.returncode != 0:
        raise CliError(f"ffmpeg exited with {r.returncode}")
    print(args.output)
    return 0


def fit_filter(w: int, h: int, mode: str, fps: Optional[float] = None) -> str:
    if mode == "crop":
        f = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
             f"crop={w}:{h}")
    else:
        f = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
             f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    f += ",setsar=1"
    if fps:
        f += f",fps={fps}"
    return f


def cmd_probe(args) -> int:
    emit([probe(p) for p in args.inputs], args.json)
    return 0


def cmd_trim(args) -> int:
    check_output(args)
    info = probe(args.input)
    if args.end is None and args.duration is None:
        raise CliError("trim needs --end or --duration")
    end = args.end if args.end is not None else args.start + args.duration
    if not 0 <= args.start < end:
        raise CliError("need 0 <= start < end")
    if info["duration"] and args.start >= info["duration"]:
        raise CliError(f"--start {args.start}s is past the clip end ({info['duration']:.2f}s)")
    return ffmpeg(args, ["-ss", f"{args.start}", "-to", f"{end}", "-i", args.input,
                         *ENCODE, args.output])


def cmd_concat(args) -> int:
    if len(args.inputs) < 2:
        raise CliError("concat needs at least two inputs")
    check_output(args)
    w, h = parse_size(args.size)
    infos = [probe(p) for p in args.inputs]
    argv: List[str] = []
    for p in args.inputs:
        argv += ["-i", p]
    parts, labels = [], []
    for i, info in enumerate(infos):
        parts.append(f"[{i}:v]{fit_filter(w, h, args.mode, args.fps)},format=yuv420p[v{i}]")
        if info["has_audio"]:
            parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        else:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={info['duration']:.3f}[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    parts.append(f"{''.join(labels)}concat=n={len(infos)}:v=1:a=1[v][a]")
    argv += ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
             *ENCODE, args.output]
    return ffmpeg(args, argv)


def _atempo_chain(factor: float) -> str:
    steps = []
    while factor > 2.0:
        steps.append(2.0)
        factor /= 2.0
    while factor < 0.5:
        steps.append(0.5)
        factor /= 0.5
    steps.append(factor)
    return ",".join(f"atempo={s:.6f}" for s in steps)


def cmd_speed(args) -> int:
    if not 0.1 <= args.factor <= 10:
        raise CliError("--factor must be between 0.1 and 10")
    check_output(args)
    info = probe(args.input)
    argv = ["-i", args.input, "-filter:v", f"setpts=PTS/{args.factor}"]
    if info["has_audio"] and not args.mute:
        argv += ["-filter:a", _atempo_chain(args.factor)]
    else:
        argv += ["-an"]
    return ffmpeg(args, argv + ENCODE + [args.output])


def cmd_reframe(args) -> int:
    check_output(args)
    probe(args.input)
    if args.size:
        w, h = parse_size(args.size)
    else:
        ratio = parse_aspect(args.aspect)
        w, h = (1080, int(round(1080 / ratio / 2)) * 2) if ratio <= 1 else \
               (int(round(1080 * ratio / 2)) * 2, 1080)
    return ffmpeg(args, ["-i", args.input, "-vf", fit_filter(w, h, args.mode),
                         *ENCODE, args.output])


def _filter_path(p: str) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    p = os.path.abspath(p).replace("\\", "/")
    return p.replace(":", r"\:").replace("'", r"\'")


def cmd_captions(args) -> int:
    check_output(args)
    probe(args.input)
    if not os.path.isfile(args.srt):
        raise CliError(f"Subtitle file not found: {args.srt}")
    style = (f"FontName={args.font},FontSize={args.font_size},PrimaryColour={args.color},"
             f"OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
             f"Alignment=2,MarginV={args.margin}")
    vf = f"subtitles='{_filter_path(args.srt)}':force_style='{style}'"
    return ffmpeg(args, ["-i", args.input, "-vf", vf, *ENCODE, args.output])


def cmd_audio(args) -> int:
    modes = [bool(args.replace), args.mute, args.extract]
    if sum(modes) != 1:
        raise CliError("audio: pick exactly one of --replace FILE, --mute, --extract")
    check_output(args)
    info = probe(args.input)
    if args.extract:
        if not info["has_audio"]:
            raise CliError("Input has no audio track")
        return ffmpeg(args, ["-i", args.input, "-vn", "-c:a", "aac", "-b:a", "192k", args.output])
    if args.mute:
        return ffmpeg(args, ["-i", args.input, "-c:v", "copy", "-an", args.output])
    probe(args.replace)
    return ffmpeg(args, ["-i", args.input, "-stream_loop", "-1" if args.loop else "0",
                         "-i", args.replace, "-map", "0:v:0", "-map", "1:a:0",
                         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                         args.output])


# --------------------------------------------------------------------------
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
    p = argparse.ArgumentParser(prog="capcut-cli", description=__doc__,
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
