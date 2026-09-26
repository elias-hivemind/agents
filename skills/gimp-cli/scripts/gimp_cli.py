#!/usr/bin/env python3
"""gimp-cli: headless GIMP image editing from the command line.

Drives gimp-console in batch mode with generated Script-Fu. Works with
GIMP 2.10 and GIMP 3.x (the Script-Fu PDB differs between them; this
script emits the right dialect for the detected version).

Requires: Python 3.8+, GIMP 2.10 or 3.x installed. Nothing else.

Examples:
  gimp_cli.py doctor
  gimp_cli.py info photo.jpg --json
  gimp_cli.py convert logo.xcf -o logo.png
  gimp_cli.py edit in.jpg -o out.jpg --crop 1000x1000+200+0 --resize 1080x
  gimp_cli.py edit *.png --out-dir web/ --format jpg --resize 50% --grayscale
  gimp_cli.py script '(gimp-message "hi")'
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

MARKER = "GIMPCLI:"
FLATTEN_EXTS = {".jpg", ".jpeg", ".bmp", ".ppm", ".pnm"}
ERROR_RE = re.compile(r"batch command experienced an execution error:?\s*(.*)", re.I)


class CliError(Exception):
    pass


# --------------------------------------------------------------------------
# Locating GIMP
# --------------------------------------------------------------------------

def _candidates() -> List[str]:
    names = [
        "gimp-console-3.0",
        "gimp-console-3",
        "gimp-console-2.10",
        "gimp-console",
    ]
    found = [p for p in (shutil.which(n) for n in names) if p]
    system = platform.system()
    if system == "Windows":
        roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")]
        for root in roots:
            for sub in ("GIMP 3", "GIMP 2"):
                for exe in ("gimp-console-3.0.exe", "gimp-console-3.exe",
                            "gimp-console-2.10.exe", "gimp-console.exe"):
                    found.append(os.path.join(root, sub, "bin", exe))
    elif system == "Darwin":
        for app in ("/Applications/GIMP.app", "/Applications/GIMP-2.10.app",
                    os.path.expanduser("~/Applications/GIMP.app")):
            for exe in ("gimp-console-3.0", "gimp-console-2.10",
                        "gimp-console", "gimp"):
                found.append(os.path.join(app, "Contents", "MacOS", exe))
    return found


def find_gimp(explicit: Optional[str]) -> str:
    choice = explicit or os.environ.get("GIMP_CONSOLE")
    if choice:
        path = shutil.which(choice) or choice
        if not os.path.isfile(path):
            raise CliError(f"GIMP executable not found: {choice}")
        return path
    for cand in _candidates():
        if cand and os.path.isfile(cand):
            return cand
    raise CliError(
        "Could not find gimp-console. Install GIMP 2.10/3.x or set "
        "GIMP_CONSOLE=/path/to/gimp-console-3.0 (or pass --gimp)."
    )


def gimp_version(exe: str) -> tuple:
    try:
        out = subprocess.run([exe, "--version"], capture_output=True,
                             text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CliError(f"Failed to run {exe} --version: {exc}") from exc
    m = re.search(r"version\s+(\d+)\.(\d+)(?:\.(\d+))?", out)
    if not m:
        raise CliError(f"Could not parse GIMP version from: {out.strip()!r}")
    return tuple(int(x or 0) for x in m.groups())


# --------------------------------------------------------------------------
# Script-Fu generation
# --------------------------------------------------------------------------

def sq(s: str) -> str:
    """Quote a Python string as a Scheme string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


@dataclass
class Dialect:
    major: int

    @property
    def v3(self) -> bool:
        return self.major >= 3

    def load(self, path: str) -> str:
        if self.v3:
            return f"(car (gimp-file-load RUN-NONINTERACTIVE {sq(path)}))"
        return f"(car (gimp-file-load RUN-NONINTERACTIVE {sq(path)} {sq(path)}))"

    def save(self, img: str, drw: str, path: str) -> str:
        if self.v3:
            return f"(gimp-file-save RUN-NONINTERACTIVE {img} {sq(path)} -1)"
        return f"(gimp-file-save RUN-NONINTERACTIVE {img} {drw} {sq(path)} {sq(path)})"

    def width(self, img: str) -> str:
        return f"(car (gimp-image-{'get-' if self.v3 else ''}width {img}))"

    def height(self, img: str) -> str:
        return f"(car (gimp-image-{'get-' if self.v3 else ''}height {img}))"

    def base_type(self, img: str) -> str:
        return f"(car (gimp-image-{'get-' if self.v3 else ''}base-type {img}))"

    def layer_count(self, img: str) -> str:
        if self.v3:
            return f"(vector-length (car (gimp-image-get-layers {img})))"
        return f"(car (gimp-image-get-layers {img}))"


def parse_resize(spec: str):
    """'800x600' | '800x' | 'x600' | '50%' -> (kind, a, b)."""
    spec = spec.strip().lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)%", spec)
    if m:
        pct = float(m.group(1))
        if pct <= 0:
            raise CliError("--resize percentage must be > 0")
        return ("pct", pct, None)
    m = re.fullmatch(r"(\d*)x(\d*)", spec)
    if not m or not (m.group(1) or m.group(2)):
        raise CliError(f"Bad --resize '{spec}'. Use WxH, Wx, xH or N%.")
    w = int(m.group(1)) if m.group(1) else None
    h = int(m.group(2)) if m.group(2) else None
    if (w is not None and w <= 0) or (h is not None and h <= 0):
        raise CliError("--resize dimensions must be > 0")
    return ("px", w, h)


def parse_crop(spec: str):
    m = re.fullmatch(r"(\d+)x(\d+)\+(\d+)\+(\d+)", spec.strip())
    if not m:
        raise CliError(f"Bad --crop '{spec}'. Use WxH+X+Y, e.g. 1080x1080+0+120.")
    w, h, x, y = map(int, m.groups())
    if w <= 0 or h <= 0:
        raise CliError("--crop width/height must be > 0")
    return w, h, x, y


def build_edit_script(d: Dialect, src: str, dst: str, ops: argparse.Namespace) -> str:
    """One self-contained Script-Fu expression that loads, edits, saves."""
    body: List[str] = []
    if ops.crop:
        w, h, x, y = parse_crop(ops.crop)
        body.append(
            f"(if (or (> (+ {x} {w}) {d.width('img')}) (> (+ {y} {h}) {d.height('img')}))"
            f" (error \"crop rectangle exceeds image bounds\"))"
        )
        body.append(f"(gimp-image-crop img {w} {h} {x} {y})")
    if ops.resize:
        kind, a, b = parse_resize(ops.resize)
        if kind == "pct":
            nw = f"(max 1 (round (* {d.width('img')} {a / 100.0})))"
            nh = f"(max 1 (round (* {d.height('img')} {a / 100.0})))"
        elif a is not None and b is not None:
            nw, nh = str(a), str(b)
        elif a is not None:
            nw = str(a)
            nh = f"(max 1 (round (/ (* {d.height('img')} {a}) {d.width('img')})))"
        else:
            nh = str(b)
            nw = f"(max 1 (round (/ (* {d.width('img')} {b}) {d.height('img')})))"
        # inexact->exact: gimp-image-scale wants integers
        body.append(f"(gimp-image-scale img (inexact->exact {nw}) (inexact->exact {nh}))")
    if ops.rotate:
        body.append(f"(gimp-image-rotate img {({90: 0, 180: 1, 270: 2})[ops.rotate]})")
    if ops.flip:
        body.append(f"(gimp-image-flip img {0 if ops.flip == 'h' else 1})")
    if ops.grayscale:
        body.append(f"(if (not (= {d.base_type('img')} 1)) (gimp-image-convert-grayscale img))")
    if ops.brightness or ops.contrast:
        body.append(f"(gimp-drawable-brightness-contrast drw {ops.brightness} {ops.contrast})")

    ext = Path(dst).suffix.lower()
    finish = "(set! drw (car (gimp-image-flatten img)))" if ext in FLATTEN_EXTS else ""
    return (
        f"(let* ((img {d.load(src)})"
        f" (drw (car (gimp-image-merge-visible-layers img 1))))"  # 1 = CLIP-TO-IMAGE
        f" {' '.join(body)} {finish}"
        f" {d.save('img', 'drw', dst)}"
        f" (gimp-message (string-append {sq(MARKER + 'OK ')} {sq(dst)}))"
        f" (gimp-image-delete img))"
    )


def build_info_script(d: Dialect, src: str) -> str:
    fields = [
        sq(MARKER + "INFO "), sq(src), '"\t"',
        f"(number->string {d.width('img')})", '"\t"',
        f"(number->string {d.height('img')})", '"\t"',
        f"(number->string {d.base_type('img')})", '"\t"',
        f"(number->string {d.layer_count('img')})",
    ]
    return (
        f"(let* ((img {d.load(src)}))"
        f" (gimp-message (string-append {' '.join(fields)}))"
        f" (gimp-image-delete img))"
    )


# --------------------------------------------------------------------------
# Running GIMP
# --------------------------------------------------------------------------

def run_batch(exe: str, major: int, commands: List[str], timeout: int,
              verbose: bool) -> subprocess.CompletedProcess:
    argv = [exe, "-i", "-d", "-f", "--batch-interpreter", "plug-in-script-fu-eval"]
    for c in commands:
        argv += ["-b", c]
    argv += ["--quit"] if major >= 3 else ["-b", "(gimp-quit 0)"]
    if verbose:
        print("+ " + " ".join(argv), file=sys.stderr)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise CliError(f"GIMP timed out after {timeout}s") from exc
    if verbose:
        sys.stderr.write(proc.stdout + proc.stderr)
    return proc


def batch_errors(proc: subprocess.CompletedProcess) -> List[str]:
    text = proc.stdout + "\n" + proc.stderr
    errs = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = ERROR_RE.search(line)
        if m:
            msg = m.group(1).strip() or (lines[i + 1].strip() if i + 1 < len(lines) else "")
            errs.append(msg)
    return errs


def expand_inputs(patterns: List[str]) -> List[str]:
    out: List[str] = []
    for p in patterns:
        matches = sorted(glob.glob(p)) if any(ch in p for ch in "*?[") else [p]
        if not matches:
            raise CliError(f"No files match: {p}")
        out.extend(matches)
    missing = [p for p in out if not os.path.isfile(p)]
    if missing:
        raise CliError("Input not found: " + ", ".join(missing))
    return [os.path.abspath(p) for p in out]


def plan_outputs(inputs: List[str], output: Optional[str], out_dir: Optional[str],
                 fmt: Optional[str], force: bool) -> List[str]:
    if output and out_dir:
        raise CliError("Use either -o/--output or --out-dir, not both.")
    if output:
        if len(inputs) != 1:
            raise CliError("-o/--output takes one input; use --out-dir for several.")
        outs = [os.path.abspath(output)]
    elif out_dir:
        os.makedirs(out_dir, exist_ok=True)
        outs = []
        for src in inputs:
            stem, ext = os.path.splitext(os.path.basename(src))
            new_ext = "." + fmt.lstrip(".") if fmt else ext
            outs.append(os.path.abspath(os.path.join(out_dir, stem + new_ext)))
    else:
        raise CliError("Specify -o/--output (single file) or --out-dir.")
    if len(set(outs)) != len(outs):
        raise CliError("Two inputs map to the same output name; rename or split the batch.")
    for src, dst in zip(inputs, outs):
        if os.path.normcase(src) == os.path.normcase(dst):
            raise CliError(f"Refusing to overwrite the input in place: {src}")
        if os.path.exists(dst) and not force:
            raise CliError(f"Output exists: {dst} (pass --force to overwrite)")
        if os.path.splitext(dst)[1] == "":
            raise CliError(f"Output needs a file extension to pick a format: {dst}")
    return outs


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_doctor(args) -> int:
    exe = find_gimp(args.gimp)
    ver = gimp_version(exe)
    print(json.dumps({"gimp": exe, "version": ".".join(map(str, ver)),
                      "dialect": "v3" if ver[0] >= 3 else "v2"}, indent=2))
    return 0


def _setup(args):
    exe = find_gimp(args.gimp)
    ver = gimp_version(exe)
    if ver[0] < 2 or (ver[0] == 2 and ver[1] < 10):
        raise CliError(f"GIMP {'.'.join(map(str, ver))} is too old; need 2.10+.")
    return exe, Dialect(ver[0])


def _run_edits(args, ops) -> int:
    inputs = expand_inputs(args.inputs)
    outputs = plan_outputs(inputs, args.output, args.out_dir, args.format, args.force)
    exe, dialect = _setup(args)
    scripts = [build_edit_script(dialect, s, o, ops) for s, o in zip(inputs, outputs)]
    if args.dry_run:
        for s in scripts:
            print(s)
        return 0
    started = time.time() - 1
    proc = run_batch(exe, dialect.major, scripts, args.timeout, args.verbose)
    errors = batch_errors(proc)
    failed = 0
    for src, dst in zip(inputs, outputs):
        ok = os.path.isfile(dst) and os.path.getmtime(dst) >= started
        print(f"{'ok  ' if ok else 'FAIL'} {src} -> {dst}")
        failed += not ok
    for e in errors:
        print(f"gimp error: {e}", file=sys.stderr)
    if failed and dialect.major >= 3 and len(scripts) > 1:
        print("note: GIMP 3 stops the batch at the first failing file.", file=sys.stderr)
    return 1 if failed else 0


def cmd_convert(args) -> int:
    ops = argparse.Namespace(crop=None, resize=None, rotate=None, flip=None,
                             grayscale=False, brightness=0.0, contrast=0.0)
    return _run_edits(args, ops)


def cmd_edit(args) -> int:
    for name in ("brightness", "contrast"):
        v = getattr(args, name)
        if not -1.0 <= v <= 1.0:
            raise CliError(f"--{name} must be between -1.0 and 1.0")
    if not any([args.crop, args.resize, args.rotate, args.flip, args.grayscale,
                args.brightness, args.contrast]):
        raise CliError("edit: no operation given (see --help); use 'convert' to just re-encode.")
    if args.crop:
        parse_crop(args.crop)
    if args.resize:
        parse_resize(args.resize)
    return _run_edits(args, args)


def cmd_info(args) -> int:
    inputs = expand_inputs(args.inputs)
    exe, dialect = _setup(args)
    scripts = [build_info_script(dialect, s) for s in inputs]
    if args.dry_run:
        print("\n".join(scripts))
        return 0
    proc = run_batch(exe, dialect.major, scripts, args.timeout, args.verbose)
    types = {0: "RGB", 1: "GRAY", 2: "INDEXED"}
    rows = []
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        idx = line.find(MARKER + "INFO ")
        if idx < 0:
            continue
        parts = line[idx + len(MARKER) + 5:].split("\t")
        if len(parts) != 5:
            continue
        path, w, h, t, n = parts
        rows.append({"file": path, "width": int(w), "height": int(h),
                     "mode": types.get(int(t), t), "layers": int(n)})
    for e in batch_errors(proc):
        print(f"gimp error: {e}", file=sys.stderr)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"{r['file']}\t{r['width']}x{r['height']}\t{r['mode']}\tlayers={r['layers']}")
    return 0 if len(rows) == len(inputs) else 1


def cmd_script(args) -> int:
    exe, dialect = _setup(args)
    code = args.code
    if code == "-":
        code = sys.stdin.read()
    elif args.file:
        code = Path(code).read_text(encoding="utf-8")
    if args.dry_run:
        print(code)
        return 0
    proc = run_batch(exe, dialect.major, [code], args.timeout, args.verbose)
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        if "script-fu-Warning" in line or MARKER in line:
            print(line.split(":", 1)[-1].strip())
    errors = batch_errors(proc)
    for e in errors:
        print(f"gimp error: {e}", file=sys.stderr)
    return 1 if errors else 0


# --------------------------------------------------------------------------
# argparse
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gimp-cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--gimp", help="path to gimp-console (default: auto-detect / $GIMP_CONSOLE)")
    common.add_argument("--timeout", type=int, default=600, help="seconds (default 600)")
    common.add_argument("--dry-run", action="store_true", help="print the Script-Fu, don't run GIMP")
    common.add_argument("-v", "--verbose", action="store_true", help="show GIMP's own output")

    io = argparse.ArgumentParser(add_help=False)
    io.add_argument("inputs", nargs="+", help="input files or globs")
    io.add_argument("-o", "--output", help="output file (single input)")
    io.add_argument("--out-dir", help="output directory (batch)")
    io.add_argument("--format", help="output extension for --out-dir, e.g. png, jpg, webp")
    io.add_argument("--force", action="store_true", help="overwrite existing outputs")

    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("doctor", parents=[common], help="locate GIMP and report its version")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("info", parents=[common], help="dimensions, mode, layer count")
    s.add_argument("inputs", nargs="+")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser("convert", parents=[common, io],
                       help="re-encode (xcf/psd/png/jpg/webp/tiff...), format from extension")
    s.set_defaults(func=cmd_convert)

    s = sub.add_parser("edit", parents=[common, io],
                       help="crop -> resize -> rotate -> flip -> grayscale -> brightness/contrast")
    s.add_argument("--crop", metavar="WxH+X+Y")
    s.add_argument("--resize", metavar="WxH|Wx|xH|N%")
    s.add_argument("--rotate", type=int, choices=[90, 180, 270])
    s.add_argument("--flip", choices=["h", "v"])
    s.add_argument("--grayscale", action="store_true")
    s.add_argument("--brightness", type=float, default=0.0, help="-1.0..1.0")
    s.add_argument("--contrast", type=float, default=0.0, help="-1.0..1.0")
    s.set_defaults(func=cmd_edit)

    s = sub.add_parser("script", parents=[common], help="run raw Script-Fu ('-' = stdin)")
    s.add_argument("code", help="Script-Fu expression, '-' for stdin, or a path with --file")
    s.add_argument("--file", action="store_true", help="treat CODE as a .scm file path")
    s.set_defaults(func=cmd_script)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"gimp-cli: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
