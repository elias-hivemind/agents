"""ffmpeg-backed CapCut-style edits for capcut_cli.py."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 - argv lists only, never shell=True
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class CliError(Exception):
    pass


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
