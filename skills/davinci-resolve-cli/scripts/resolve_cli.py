#!/usr/bin/env python3
r"""
resolve-cli: drive DaVinci Resolve from the command line.

Uses Blackmagic's official scripting API (DaVinciResolveScript /
fusionscript). Resolve must be RUNNING, with
  Preferences > System > General > External scripting using = Local
External scripting from a separate process is a DaVinci Resolve Studio
feature in current releases; on the free edition `doctor` will report
that it cannot connect.

Requires: Python 3.6+ (64-bit, matching Resolve), DaVinci Resolve 18+.

Usage examples --
  resolve_cli.py doctor
  resolve_cli.py project list
  resolve_cli.py project create "Drop 07" --fps 30 --resolution 1080x1920
  resolve_cli.py import clips/*.mp4 --bin Raw
  resolve_cli.py timeline create Reel --clips clips/a.mp4 clips/b.mp4
  resolve_cli.py render --timeline Reel --preset "YouTube - 1080p" \
      --target-dir exports --name reel_v1 --wait
  resolve_cli.py timeline export Reel reel.fcpxml --format fcpxml
"""

from __future__ import annotations

import argparse
import glob
import importlib
import json
import os
import platform
import sys
import time
from typing import Any, Dict, List, Optional


class CliError(Exception):
    pass


# --------------------------------------------------------------------------
# Connecting to Resolve
# --------------------------------------------------------------------------

def default_paths() -> Dict[str, str]:
    system = platform.system()
    if system == "Windows":
        pd = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        return {
            "api": os.path.join(pd, "Blackmagic Design", "DaVinci Resolve",
                                "Support", "Developer", "Scripting"),
            "lib": os.path.join(pf, "Blackmagic Design", "DaVinci Resolve",
                                "fusionscript.dll"),
        }
    if system == "Darwin":
        return {
            "api": "/Library/Application Support/Blackmagic Design/"
                   "DaVinci Resolve/Developer/Scripting",
            "lib": "/Applications/DaVinci Resolve/DaVinci Resolve.app/"
                   "Contents/Libraries/Fusion/fusionscript.so",
        }
    return {
        "api": "/opt/resolve/Developer/Scripting",
        "lib": "/opt/resolve/libs/Fusion/fusionscript.so",
    }


def load_module():
    """Import DaVinciResolveScript, filling in the env vars it reads."""
    defaults = default_paths()
    api = os.environ.setdefault("RESOLVE_SCRIPT_API", defaults["api"])
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", defaults["lib"])
    modules = os.path.join(api, "Modules")
    if modules not in sys.path:
        sys.path.append(modules)
    try:
        return importlib.import_module("DaVinciResolveScript")
    except ImportError as exc:
        raise CliError(
            f"Cannot import DaVinciResolveScript ({exc}). Looked in {modules}. "
            "Install DaVinci Resolve or set RESOLVE_SCRIPT_API / RESOLVE_SCRIPT_LIB."
        ) from exc


def connect():
    bmd = load_module()
    resolve = bmd.scriptapp("Resolve")
    if resolve is None:
        raise CliError(
            "Connected to the scripting library but not to Resolve. Check: "
            "(1) Resolve is running, (2) Preferences > System > General > "
            "External scripting using = Local, (3) you are on DaVinci Resolve "
            "Studio (the free edition only allows scripts from Workspace > Console), "
            "(4) Python bitness matches Resolve (64-bit)."
        )
    return resolve


class Ctx:
    def __init__(self, resolve):
        """Wrap a connected Resolve scripting object."""
        self.resolve = resolve
        self.pm = resolve.GetProjectManager()

    @property
    def project(self):
        proj = self.pm.GetCurrentProject()
        if not proj:
            raise CliError("No project is open. Use: project open NAME")
        return proj

    def timeline(self, name: Optional[str]):
        proj = self.project
        if not name:
            tl = proj.GetCurrentTimeline()
            if not tl:
                raise CliError("No current timeline; pass --timeline NAME.")
            return tl
        for i in range(1, (proj.GetTimelineCount() or 0) + 1):
            tl = proj.GetTimelineByIndex(i)
            if tl and tl.GetName() == name:
                return tl
        raise CliError(f"Timeline not found: {name}")


def out(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, default=str))
    elif isinstance(data, list):
        for row in data:
            print(row if not isinstance(row, dict) else "\t".join(f"{k}={v}" for k, v in row.items()))
    elif isinstance(data, dict):
        for k, v in data.items():
            print(f"{k}: {v}")
    else:
        print(data)


def expand_media(patterns: List[str]) -> List[str]:
    paths: List[str] = []
    for p in patterns:
        matches = sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p]
        if not matches:
            raise CliError(f"No files match: {p}")
        paths.extend(matches)
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        raise CliError("Not found: " + ", ".join(missing))
    return [os.path.abspath(p) for p in paths]


def find_or_create_bin(ctx: Ctx, name: Optional[str]):
    pool = ctx.project.GetMediaPool()
    root = pool.GetRootFolder()
    if not name:
        return pool, pool.GetCurrentFolder() or root
    for folder in root.GetSubFolderList() or []:
        if folder.GetName() == name:
            return pool, folder
    folder = pool.AddSubFolder(root, name)
    if not folder:
        raise CliError(f"Could not create bin: {name}")
    return pool, folder


def import_media(ctx: Ctx, files: List[str], bin_name: Optional[str]):
    pool, folder = find_or_create_bin(ctx, bin_name)
    pool.SetCurrentFolder(folder)
    items = pool.ImportMedia(files) or []
    if not items:
        raise CliError("Resolve imported nothing (unsupported format or offline path?)")
    return items


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def cmd_doctor(args) -> int:
    report: Dict[str, Any] = {"platform": platform.system(),
                              "python": sys.version.split()[0],
                              "bits": 64 if sys.maxsize > 2 ** 32 else 32}
    paths = default_paths()
    report["RESOLVE_SCRIPT_API"] = os.environ.get("RESOLVE_SCRIPT_API", paths["api"])
    report["RESOLVE_SCRIPT_LIB"] = os.environ.get("RESOLVE_SCRIPT_LIB", paths["lib"])
    report["lib_exists"] = os.path.isfile(report["RESOLVE_SCRIPT_LIB"])
    try:
        resolve = connect()
        report["connected"] = True
        report["product"] = resolve.GetProductName()
        report["version"] = resolve.GetVersionString()
        proj = resolve.GetProjectManager().GetCurrentProject()
        report["project"] = proj.GetName() if proj else None
        report["page"] = resolve.GetCurrentPage()
        code = 0
    except CliError as exc:
        report["connected"] = False
        report["error"] = str(exc)
        code = 1
    out(report, args.json)
    return code


def cmd_page(args, ctx: Ctx) -> int:
    if not ctx.resolve.OpenPage(args.page):
        raise CliError(f"Could not open page {args.page}")
    print(f"page: {args.page}")
    return 0


def cmd_project(args, ctx: Ctx) -> int:
    pm = ctx.pm
    if args.action == "list":
        out(list(pm.GetProjectListInCurrentFolder() or []), args.json)
    elif args.action == "current":
        proj = ctx.project
        out({"name": proj.GetName(),
             "timelines": proj.GetTimelineCount(),
             "fps": proj.GetSetting("timelineFrameRate"),
             "resolution": f"{proj.GetSetting('timelineResolutionWidth')}x"
                           f"{proj.GetSetting('timelineResolutionHeight')}"}, args.json)
    elif args.action == "open":
        if not pm.LoadProject(args.name):
            raise CliError(f"Could not open project: {args.name}")
        print(f"opened: {args.name}")
    elif args.action == "create":
        proj = pm.CreateProject(args.name)
        if not proj:
            raise CliError(f"Could not create project (name taken?): {args.name}")
        apply_settings(proj, args)
        print(f"created: {args.name}")
    elif args.action == "save":
        if not pm.SaveProject():
            raise CliError("Save failed")
        print("saved")
    elif args.action == "set":
        apply_settings(ctx.project, args)
        print("settings applied")
    return 0


def apply_settings(proj, args) -> None:
    settings: Dict[str, str] = {}
    if getattr(args, "fps", None):
        settings["timelineFrameRate"] = str(args.fps)
    if getattr(args, "resolution", None):
        try:
            w, h = (int(x) for x in args.resolution.lower().split("x"))
        except ValueError as exc:
            raise CliError("--resolution must be WxH, e.g. 1080x1920") from exc
        settings["timelineResolutionWidth"] = str(w)
        settings["timelineResolutionHeight"] = str(h)
    for kv in getattr(args, "setting", None) or []:
        k, sep, v = kv.partition("=")
        if not sep:
            raise CliError(f"--setting needs KEY=VALUE, got {kv}")
        settings[k] = v
    for k, v in settings.items():
        if not proj.SetSetting(k, v):
            # Frame rate is locked once a timeline exists; say so.
            raise CliError(f"Resolve rejected setting {k}={v}")


def cmd_import(args, ctx: Ctx) -> int:
    files = expand_media(args.files)
    items = import_media(ctx, files, args.bin)
    out([i.GetName() for i in items], args.json)
    return 0


def cmd_media(args, ctx: Ctx) -> int:
    pool = ctx.project.GetMediaPool()
    rows = []

    def walk(folder, path):
        for clip in folder.GetClipList() or []:
            rows.append({"bin": path, "name": clip.GetName(),
                         "file": clip.GetClipProperty("File Path"),
                         "duration": clip.GetClipProperty("Duration")})
        for sub in folder.GetSubFolderList() or []:
            walk(sub, f"{path}/{sub.GetName()}")

    walk(pool.GetRootFolder(), "Master")
    out(rows, args.json)
    return 0


def _tl_list(args, ctx: Ctx) -> None:
    proj = ctx.project
    rows = []
    for i in range(1, (proj.GetTimelineCount() or 0) + 1):
        tl = proj.GetTimelineByIndex(i)
        rows.append({"index": i, "name": tl.GetName(),
                     "start": tl.GetStartFrame(), "end": tl.GetEndFrame()})
    out(rows, args.json)


def _tl_create(args, ctx: Ctx) -> None:
    proj = ctx.project
    pool = proj.GetMediaPool()
    if args.clips:
        items = import_media(ctx, expand_media(args.clips), args.bin)
        tl = pool.CreateTimelineFromClips(args.name, items)
    else:
        tl = pool.CreateEmptyTimeline(args.name)
    if not tl:
        raise CliError(f"Could not create timeline (name taken?): {args.name}")
    proj.SetCurrentTimeline(tl)
    print(f"created timeline: {args.name}")


def _tl_use(args, ctx: Ctx) -> None:
    if not ctx.project.SetCurrentTimeline(ctx.timeline(args.name)):
        raise CliError(f"Could not switch to {args.name}")
    print(f"current timeline: {args.name}")


def _tl_append(args, ctx: Ctx) -> None:
    proj = ctx.project
    tl = ctx.timeline(args.timeline)
    proj.SetCurrentTimeline(tl)
    items = import_media(ctx, expand_media(args.clips), args.bin)
    appended = proj.GetMediaPool().AppendToTimeline(items) or []
    if not appended:
        raise CliError("AppendToTimeline returned nothing")
    print(f"appended {len(appended)} clip(s) to {tl.GetName()}")


# format -> (export type constants to try, in order; export subtype constant)
EXPORT_TYPES = {
    "edl": (("EXPORT_EDL",), "EXPORT_NONE"),
    "fcpxml": (("EXPORT_FCPXML_1_10", "EXPORT_FCPXML_1_9"), None),
    "xml": (("EXPORT_FCP_7_XML",), None),
    "aaf": (("EXPORT_AAF",), "EXPORT_AAF_NEW"),
    "otio": (("EXPORT_OTIO",), None),
    "csv": (("EXPORT_TEXT_CSV",), None),
    "drt": (("EXPORT_DRT",), None),
}


def _tl_export(args, ctx: Ctx) -> None:
    tl = ctx.timeline(args.name)
    names, subtype_name = EXPORT_TYPES[args.format]
    kind = next((getattr(ctx.resolve, n) for n in names if hasattr(ctx.resolve, n)), None)
    if kind is None:
        raise CliError(f"This Resolve version does not support {args.format} export")
    target = os.path.abspath(args.path)
    extra = (getattr(ctx.resolve, subtype_name),) if subtype_name and hasattr(ctx.resolve, subtype_name) else ()
    if not tl.Export(target, kind, *extra):
        raise CliError(f"Export failed: {target}")
    print(f"exported {tl.GetName()} -> {target}")


def _tl_marker(args, ctx: Ctx) -> None:
    tl = ctx.timeline(args.timeline)
    if not tl.AddMarker(args.frame, args.color, args.name, args.note or "", args.duration):
        raise CliError("AddMarker failed (frame outside timeline or already marked?)")
    print(f"marker @{args.frame} on {tl.GetName()}")


TIMELINE_ACTIONS = {"list": _tl_list, "create": _tl_create, "use": _tl_use,
                    "append": _tl_append, "export": _tl_export, "marker": _tl_marker}


def cmd_timeline(args, ctx: Ctx) -> int:
    TIMELINE_ACTIONS[args.action](args, ctx)
    return 0


def _render_listing(args, proj) -> bool:
    """Handle --list-presets / --list-formats / --status. True if handled."""
    if args.list_presets:
        out(list(proj.GetRenderPresetList() or []), args.json)
    elif args.list_formats:
        formats = proj.GetRenderFormats() or {}
        out({name: list((proj.GetRenderCodecs(ext) or {}).keys())
             for name, ext in formats.items()}, args.json)
    elif args.status:
        out(list(proj.GetRenderJobList() or []), args.json)
    else:
        return False
    return True


def _render_settings(args, tl) -> Dict[str, Any]:
    settings: Dict[str, Any] = {"SelectAllFrames": True}
    if args.target_dir:
        os.makedirs(args.target_dir, exist_ok=True)
        settings["TargetDir"] = os.path.abspath(args.target_dir)
    if args.name:
        settings["CustomName"] = args.name
    if args.in_frame is not None or args.out_frame is not None:
        settings["SelectAllFrames"] = False
        settings["MarkIn"] = tl.GetStartFrame() if args.in_frame is None else args.in_frame
        settings["MarkOut"] = tl.GetEndFrame() if args.out_frame is None else args.out_frame
    return settings


def _configure_render(args, proj, tl) -> None:
    proj.SetCurrentTimeline(tl)
    if args.preset and not proj.LoadRenderPreset(args.preset):
        raise CliError(f"Unknown render preset: {args.preset} (see render --list-presets)")
    if bool(args.format) != bool(args.codec):
        raise CliError("--format and --codec go together (see render --list-formats)")
    if args.format and not proj.SetCurrentRenderFormatAndCodec(args.format, args.codec):
        raise CliError(f"Resolve rejected format/codec {args.format}/{args.codec}")
    settings = _render_settings(args, tl)
    if not proj.SetRenderSettings(settings):
        raise CliError(f"Resolve rejected render settings: {settings}")


def _wait_for_render(proj, job: str, timeout: int) -> None:
    deadline = time.time() + timeout
    last = None
    while proj.IsRenderingInProgress():
        if time.time() > deadline:
            proj.StopRendering()
            raise CliError(f"Render exceeded --timeout {timeout}s; stopped.")
        pct = (proj.GetRenderJobStatus(job) or {}).get("CompletionPercentage")
        if pct != last:
            print(f"  {pct}%", flush=True)
            last = pct
        time.sleep(2)
    st = proj.GetRenderJobStatus(job) or {}
    status = st.get("JobStatus", "Unknown")
    print(f"status: {status}")
    if status != "Complete":
        raise CliError(f"Render did not complete: {st}")


def cmd_render(args, ctx: Ctx) -> int:
    proj = ctx.project
    if _render_listing(args, proj):
        return 0
    tl = ctx.timeline(args.timeline)
    _configure_render(args, proj, tl)
    job = proj.AddRenderJob()
    if not job:
        raise CliError("AddRenderJob failed (no target dir set in preset?)")
    if not proj.StartRendering([job], False):
        raise CliError("StartRendering failed")
    print(f"render job {job} started for {tl.GetName()}")
    if args.wait:
        _wait_for_render(proj, job, args.timeout)
    return 0


# --------------------------------------------------------------------------
# argparse
# --------------------------------------------------------------------------

MARKER_COLORS = ["Blue", "Cyan", "Green", "Yellow", "Red", "Pink", "Purple", "Fuchsia",
                 "Rose", "Lavender", "Sky", "Mint", "Lemon", "Sand", "Cocoa", "Cream"]


def _add_project_parser(sub, common) -> None:
    s = sub.add_parser("project", help="list/open/create/save/set")
    ps = s.add_subparsers(dest="action", required=True)
    for name in ("list", "current", "save"):
        ps.add_parser(name, parents=[common])
    ps.add_parser("open", parents=[common]).add_argument("name")
    for name in ("create", "set"):
        x = ps.add_parser(name, parents=[common])
        if name == "create":
            x.add_argument("name")
        x.add_argument("--fps", help="e.g. 24, 25, 29.97, 30, 60")
        x.add_argument("--resolution", help="WxH, e.g. 1080x1920")
        x.add_argument("--setting", action="append", metavar="KEY=VALUE",
                       help="any SetSetting key; repeatable")
    s.set_defaults(func=cmd_project)


def _add_timeline_parser(sub, common) -> None:
    s = sub.add_parser("timeline", help="list/create/use/append/export/marker")
    ts = s.add_subparsers(dest="action", required=True)
    ts.add_parser("list", parents=[common])
    x = ts.add_parser("create", parents=[common])
    x.add_argument("name")
    x.add_argument("--clips", nargs="+", help="media to import and lay down in order")
    x.add_argument("--bin")
    ts.add_parser("use", parents=[common]).add_argument("name")
    x = ts.add_parser("append", parents=[common])
    x.add_argument("clips", nargs="+")
    x.add_argument("--timeline")
    x.add_argument("--bin")
    x = ts.add_parser("export", parents=[common])
    x.add_argument("name")
    x.add_argument("path")
    x.add_argument("--format", required=True, choices=list(EXPORT_TYPES))
    x = ts.add_parser("marker", parents=[common])
    x.add_argument("frame", type=int)
    x.add_argument("--timeline")
    x.add_argument("--name", default="marker")
    x.add_argument("--note")
    x.add_argument("--duration", type=int, default=1)
    x.add_argument("--color", default="Blue", choices=MARKER_COLORS)
    s.set_defaults(func=cmd_timeline)


def _add_render_parser(sub, common) -> None:
    s = sub.add_parser("render", parents=[common], help="queue and run a render job")
    s.add_argument("--timeline", help="default: current timeline")
    s.add_argument("--preset", help="render preset name (see --list-presets)")
    s.add_argument("--format", help="e.g. mp4, mov (see --list-formats)")
    s.add_argument("--codec", help="e.g. H264, H265, ProRes422HQ")
    s.add_argument("--target-dir")
    s.add_argument("--name", help="output file name, no extension")
    s.add_argument("--in-frame", type=int)
    s.add_argument("--out-frame", type=int)
    s.add_argument("--wait", action="store_true", help="block until finished")
    s.add_argument("--timeout", type=int, default=6 * 3600)
    s.add_argument("--list-presets", action="store_true")
    s.add_argument("--list-formats", action="store_true")
    s.add_argument("--status", action="store_true", help="show the render queue")
    s.set_defaults(func=cmd_render)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="resolve-cli", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="JSON output")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("doctor", parents=[common], help="check the scripting connection")
    s.set_defaults(func=cmd_doctor, needs_ctx=False)
    s = sub.add_parser("page", parents=[common], help="switch Resolve page")
    s.add_argument("page", choices=["media", "cut", "edit", "fusion", "color", "fairlight", "deliver"])
    s.set_defaults(func=cmd_page)
    _add_project_parser(sub, common)
    s = sub.add_parser("import", parents=[common], help="import media into the pool")
    s.add_argument("files", nargs="+")
    s.add_argument("--bin", help="bin under Master (created if missing)")
    s.set_defaults(func=cmd_import)
    s = sub.add_parser("media", parents=[common], help="list media pool clips")
    s.set_defaults(func=cmd_media)
    _add_timeline_parser(sub, common)
    _add_render_parser(sub, common)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "needs_ctx", True):
            return args.func(args, Ctx(connect()))
        return args.func(args)
    except CliError as exc:
        print(f"resolve-cli: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
