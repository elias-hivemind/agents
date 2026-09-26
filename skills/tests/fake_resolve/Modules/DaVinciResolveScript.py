"""In-memory stand-in for Blackmagic's DaVinciResolveScript, for tests only.

Mirrors the subset of the Resolve scripting API that resolve_cli.py calls.
Set FAKE_RESOLVE_OFFLINE=1 to simulate scriptapp() returning None.
"""

import os


class _Clip:
    def __init__(self, path):
        self.path = path

    def GetName(self):
        return os.path.basename(self.path)

    def GetClipProperty(self, key):
        return {"File Path": self.path, "Duration": "00:00:05:00"}.get(key)


class _Folder:
    def __init__(self, name):
        self.name, self.clips, self.subs = name, [], []

    def GetName(self):
        return self.name

    def GetClipList(self):
        return self.clips

    def GetSubFolderList(self):
        return self.subs


class _Timeline:
    def __init__(self, name, clips):
        self.name, self.clips, self.markers = name, list(clips), {}

    def GetName(self):
        return self.name

    def GetStartFrame(self):
        return 86400

    def GetEndFrame(self):
        return 86400 + 150 * max(1, len(self.clips))

    def AddMarker(self, frame, color, name, note, duration, *extra):
        if frame in self.markers:
            return False
        self.markers[frame] = (color, name, note, duration)
        return True

    def Export(self, path, kind, subtype=None):
        with open(path, "w") as fh:
            fh.write(f"{kind}:{subtype}:{self.name}\n")
        return True


class _MediaPool:
    def __init__(self):
        self.root = _Folder("Master")
        self.current = self.root
        self.project = None

    def GetRootFolder(self):
        return self.root

    def GetCurrentFolder(self):
        return self.current

    def SetCurrentFolder(self, folder):
        self.current = folder
        return True

    def AddSubFolder(self, parent, name):
        folder = _Folder(name)
        parent.subs.append(folder)
        return folder

    def ImportMedia(self, paths):
        clips = [_Clip(p) for p in paths if os.path.exists(p)]
        self.current.clips.extend(clips)
        return clips

    def CreateEmptyTimeline(self, name):
        return self.project._add_timeline(name, [])

    def CreateTimelineFromClips(self, name, clips):
        return self.project._add_timeline(name, clips)

    def AppendToTimeline(self, clips):
        self.project.current_tl.clips.extend(clips)
        return clips


class _Project:
    def __init__(self, name):
        self.name = name
        self.settings = {"timelineFrameRate": "24", "timelineResolutionWidth": "1920",
                         "timelineResolutionHeight": "1080"}
        self.pool = _MediaPool()
        self.pool.project = self
        self.timelines, self.current_tl = [], None
        self.jobs, self.render_settings, self.preset = [], {}, None
        self._polls = 0

    def _add_timeline(self, name, clips):
        if any(t.name == name for t in self.timelines):
            return None
        tl = _Timeline(name, clips)
        self.timelines.append(tl)
        return tl

    def GetName(self):
        return self.name

    def GetSetting(self, key):
        return self.settings.get(key)

    def SetSetting(self, key, value):
        if key == "timelineFrameRate" and self.timelines:
            return False
        self.settings[key] = value
        return True

    def GetMediaPool(self):
        return self.pool

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, i):
        return self.timelines[i - 1]

    def GetCurrentTimeline(self):
        return self.current_tl

    def SetCurrentTimeline(self, tl):
        self.current_tl = tl
        return True

    def GetRenderPresetList(self):
        return ["YouTube - 1080p", "H.264 Master"]

    def LoadRenderPreset(self, name):
        self.preset = name
        return name in self.GetRenderPresetList()

    def GetRenderFormats(self):
        return {"MP4": "mp4", "QuickTime": "mov"}

    def GetRenderCodecs(self, ext):
        return {"H.264": "H264", "H.265": "H265"}

    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        return fmt in ("mp4", "mov")

    def SetRenderSettings(self, settings):
        self.render_settings.update(settings)
        return True

    def AddRenderJob(self):
        job = f"job-{len(self.jobs) + 1}"
        self.jobs.append({"JobId": job, "TimelineName": self.current_tl.name,
                          **self.render_settings})
        return job

    def GetRenderJobList(self):
        return self.jobs

    def StartRendering(self, jobs, interactive=False):
        self._polls = 2
        return True

    def IsRenderingInProgress(self):
        self._polls -= 1
        return self._polls > 0

    def GetRenderJobStatus(self, job):
        if self._polls > 0:
            return {"JobStatus": "Rendering", "CompletionPercentage": 50}
        return {"JobStatus": "Complete", "CompletionPercentage": 100}

    def StopRendering(self):
        self._polls = 0


class _ProjectManager:
    def __init__(self):
        self.projects = {"Untitled Project": _Project("Untitled Project")}
        self.current = self.projects["Untitled Project"]

    def GetProjectListInCurrentFolder(self):
        return list(self.projects)

    def GetCurrentProject(self):
        return self.current

    def CreateProject(self, name):
        if name in self.projects:
            return None
        self.current = self.projects[name] = _Project(name)
        return self.current

    def LoadProject(self, name):
        self.current = self.projects.get(name)
        return self.current

    def SaveProject(self):
        return True


class _Resolve:
    EXPORT_EDL, EXPORT_NONE, EXPORT_FCPXML_1_10, EXPORT_FCP_7_XML = 1, 0, 2, 3
    EXPORT_AAF, EXPORT_AAF_NEW, EXPORT_OTIO, EXPORT_TEXT_CSV, EXPORT_DRT = 4, 5, 6, 7, 8

    def __init__(self):
        self.pm, self.page = _ProjectManager(), "edit"

    def GetProjectManager(self):
        return self.pm

    def GetProductName(self):
        return "DaVinci Resolve Studio"

    def GetVersionString(self):
        return "19.1.0.12"

    def GetCurrentPage(self):
        return self.page

    def OpenPage(self, page):
        self.page = page
        return True


_INSTANCE = _Resolve()


def scriptapp(name):
    if os.environ.get("FAKE_RESOLVE_OFFLINE") == "1":
        return None
    return _INSTANCE if name == "Resolve" else None
