"""Beat detection and beat-synced cutting for capcut_cli.py (ffmpeg; librosa if installed)."""

from __future__ import annotations

import json
import math
import os
import subprocess  # nosec B404 - argv lists only, never shell=True
from array import array
from typing import Dict, List, Optional, Sequence, Tuple

from capcut_ffmpeg import (  # noqa: E402 - sibling module in this scripts/ folder
    ENCODE,
    CliError,
    check_output,
    ffmpeg,
    fit_filter,
    need_ffmpeg,
    parse_size,
    probe,
)

SR = 11025                 # analysis sample rate
HOP = 128                  # samples per onset frame (~11.6 ms)
HOP_S = HOP / SR
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic"}


# --------------------------------------------------------------------------
# Onset envelope
# --------------------------------------------------------------------------

def decode_pcm(path: str, start: float, duration: float, af: Optional[str] = None) -> array:
    """Mono 16-bit PCM at SR for [start, start+duration) of the audio in path."""
    need_ffmpeg()
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
           "-i", path, "-vn", "-ac", "1", "-ar", str(SR)]
    if af:
        cmd += ["-af", af]
    cmd += ["-f", "s16le", "-"]
    # argv list, no shell; path is a single argument
    r = subprocess.run(cmd, capture_output=True)  # nosec B603
    if r.returncode != 0:
        raise CliError(f"Could not decode audio from {path}: {r.stderr.decode(errors='replace').strip()}")
    pcm = array("h")
    pcm.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
    if len(pcm) < SR:
        raise CliError(f"Less than 1 s of audio in {path} from {start}s")
    return pcm


def onset_envelope(pcm: Sequence[int]) -> List[float]:
    """Half-wave rectified log-energy flux, one value per HOP samples."""
    n = len(pcm) // HOP
    energy = []
    for k in range(n):
        seg = pcm[k * HOP:(k + 1) * HOP]
        energy.append(math.log(1e-3 + sum(x * x for x in seg) / HOP))
    flux = [0.0] + [max(0.0, energy[k] - energy[k - 1]) for k in range(1, n)]
    return _subtract_local_mean(flux, 16)


def _subtract_local_mean(x: List[float], radius: int) -> List[float]:
    prefix = [0.0]
    for v in x:
        prefix.append(prefix[-1] + v)
    out = []
    for i, v in enumerate(x):
        lo, hi = max(0, i - radius), min(len(x), i + radius + 1)
        out.append(max(0.0, v - (prefix[hi] - prefix[lo]) / (hi - lo)))
    return out


def _normalise(x: List[float]) -> List[float]:
    peak = max(x) if x else 0.0
    return [v / peak for v in x] if peak > 0 else x


# (ffmpeg filter, weight): full band plus the kick-drum band
BANDS = (
    (None, 1.0),
    ("lowpass=f=150", 1.0),
)


def _mean_normalise(x: List[float]) -> List[float]:
    mean = sum(x) / len(x) if x else 0.0
    return [v / mean for v in x] if mean > 0 else x


def combined_envelope(path: str, start: float, duration: float) -> List[float]:
    """Weighted sum of per-band log-energy flux (full band + kick band)."""
    envs = [(_mean_normalise(onset_envelope(decode_pcm(path, start, duration, af))), w)
            for af, w in BANDS]
    n = min(len(e) for e, _ in envs)
    return [sum(e[i] * w for e, w in envs) for i in range(n)]


# --------------------------------------------------------------------------
# Tempo + beat grid
# --------------------------------------------------------------------------

def _interp(env: List[float], pos: float) -> float:
    i = int(pos)
    if i < 0 or i + 1 >= len(env):
        return 0.0
    f = pos - i
    return env[i] * (1 - f) + env[i + 1] * f


def estimate_period(env: List[float], bpm_lo: float = 60, bpm_hi: float = 200) -> float:
    """Autocorrelation tempo with a log-normal prior centred on 110 BPM."""
    lag_lo = int(60 / bpm_hi / HOP_S)
    lag_hi = int(math.ceil(60 / bpm_lo / HOP_S))
    best_lag, best_score = lag_lo, -1.0
    for lag in range(lag_lo, min(lag_hi, len(env) // 2) + 1):
        ac = sum(env[i] * env[i + lag] for i in range(len(env) - lag)) / (len(env) - lag)
        bpm = 60 / (lag * HOP_S)
        weight = math.exp(-0.5 * (math.log2(bpm / 110) / 0.9) ** 2)
        if ac * weight > best_score:
            best_lag, best_score = lag, ac * weight
    return float(best_lag)


def _grid_score(env: List[float], period: float, phase: float) -> float:
    total, t = 0.0, phase
    while t < len(env) - 1:
        total += max(_interp(env, t - 1), _interp(env, t), _interp(env, t + 1))
        t += period
    return total


def fit_grid(env: List[float], period: float, span: float = 0.04) -> Tuple[float, float]:
    """Refine period (+/- span) and phase by maximising onset energy on the grid."""
    best = (period, 0.0, -1.0)
    steps = 81
    for s in range(steps):
        p = period * (1 - span + 2 * span * s / (steps - 1))
        phase = 0.0
        while phase < p:
            score = _grid_score(env, p, phase)
            if score > best[2]:
                best = (p, phase, score)
            phase += 0.5
    return best[0], best[1]


def _local_score(env: List[float], period: float) -> List[float]:
    """Onset envelope smoothed with a narrow Gaussian (sigma = period / 32)."""
    sigma = max(0.5, period / 32)
    radius = int(3 * sigma) + 1
    kernel = [math.exp(-0.5 * (k / sigma) ** 2) for k in range(-radius, radius + 1)]
    n = len(env)
    return [sum(env[i + k - radius] * w for k, w in enumerate(kernel) if 0 <= i + k - radius < n)
            for i in range(n)]


def dp_track(env: List[float], period: float, tightness: float = 100.0) -> List[int]:
    """Dynamic-programming beat tracker (Ellis 2007): follows gentle tempo drift."""
    score = _local_score(env, period)
    n = len(score)
    lo, hi = max(1, int(round(period / 2))), int(round(2 * period))
    penalty = {d: -tightness * math.log(d / period) ** 2 for d in range(lo, hi + 1)}
    cum, back = [0.0] * n, [-1] * n
    for i in range(n):
        best, arg = 0.0, -1
        for d in range(lo, min(hi, i) + 1):
            cand = cum[i - d] + penalty[d]
            if arg < 0 or cand > best:
                best, arg = cand, i - d
        cum[i] = score[i] + (best if arg >= 0 and best > 0 else 0.0)
        back[i] = arg if arg >= 0 and best > 0 else -1
    tail = range(max(0, n - int(period)), n)
    i = max(tail, key=lambda k: cum[k])
    beats = []
    while i >= 0:
        beats.append(i)
        i = back[i]
    return beats[::-1]


def _librosa_beats(path: str, start: float, duration: float, bpm: Optional[float]) -> Optional[Dict]:
    """Beat-track with librosa when it is installed (more accurate on real mixes)."""
    if os.environ.get("CAPCUT_BEATS_ENGINE", "auto") == "builtin":
        return None
    try:
        import librosa  # noqa: PLC0415 - optional dependency
        import numpy as np  # noqa: PLC0415
    except ImportError:
        return None
    sr = 22050
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", path,
           "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    # argv list, no shell; path is a single argument
    r = subprocess.run(cmd, capture_output=True)  # nosec B603
    if r.returncode != 0:
        raise CliError(f"Could not decode audio from {path}")
    y = np.frombuffer(r.stdout, dtype=np.float32)
    if len(y) < sr:
        raise CliError(f"Less than 1 s of audio in {path} from {start}s")
    _, times = librosa.beat.beat_track(y=y, sr=sr, units="time", start_bpm=bpm or 120.0,
                                       tightness=400 if bpm else 100)
    if len(times) < 2:
        return None
    beats = [round(start + float(t), 3) for t in times]
    span = beats[-1] - beats[0]
    return {"bpm": round(60 * (len(beats) - 1) / span, 2), "beats": beats, "engine": "librosa"}


def detect_beats(path: str, start: float, duration: float, bpm: Optional[float] = None) -> Dict:
    found = _librosa_beats(path, start, duration, bpm)
    if found:
        return found
    env = combined_envelope(path, start, duration)
    if not any(env):
        raise CliError("No rhythmic onsets found; pass --bpm to cut on a fixed grid.")
    period = 60 / bpm / HOP_S if bpm else estimate_period(env)
    # a user-given BPM is trusted: only fine-tune it by +/-0.5 %
    period, _ = fit_grid(env, period, 0.005 if bpm else 0.04)
    frames = dp_track(env, period, 400.0 if bpm else 100.0)
    beats = [round(start + f * HOP_S, 3) for f in frames]
    span = beats[-1] - beats[0] if len(beats) > 1 else 0
    tempo = 60 * (len(beats) - 1) / span if span > 0 else 60 / (period * HOP_S)
    return {"bpm": round(tempo, 2), "beats": beats, "engine": "builtin"}


# --------------------------------------------------------------------------
# Cut plan
# --------------------------------------------------------------------------

def plan_segments(beats: List[float], start: float, duration: float, every: int = 2,
                  shift: int = 0, min_len: float = 0.25) -> List[Tuple[float, float]]:
    """Cut points every `every` beats, as (t0, t1) pairs relative to the music start."""
    if every < 1:
        raise CliError("--every must be >= 1")
    # a beat sitting on the start counts as beat 0, so cuts land on beats every, 2*every...
    rel = [b - start for b in beats if -0.02 <= b - start < duration]
    cuts = [0.0] + [t for i, t in enumerate(rel) if (i - shift) % every == 0] + [duration]
    bounds = [cuts[0]]
    for c in cuts[1:]:
        if c - bounds[-1] >= min_len:
            bounds.append(c)
        elif c == duration:
            bounds[-1] = duration
    if len(bounds) < 2:
        bounds = [0.0, duration]
    return list(zip(bounds[:-1], bounds[1:]))


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------

def _is_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS


def _segment_inputs(clips: List[str], segs: List[Tuple[float, float]], fps: float) -> List[str]:
    """One ffmpeg input per segment; videos advance through their footage."""
    durations = {c: (0.0 if _is_image(c) else probe(c)["duration"]) for c in set(clips)}
    cursor = {c: 0.0 for c in clips}
    argv: List[str] = []
    for i, (t0, t1) in enumerate(segs):
        clip, length = clips[i % len(clips)], t1 - t0 + 0.5
        if _is_image(clip):
            argv += ["-loop", "1", "-framerate", str(fps), "-t", f"{length:.3f}", "-i", clip]
            continue
        start = cursor[clip]
        if start + length > durations[clip]:
            start = 0.0
        cursor[clip] = start + (t1 - t0)
        argv += ["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", clip]
    return argv


def _video_filters(segs: List[Tuple[float, float]], w: int, h: int, mode: str,
                   fps: float) -> str:
    frames = [round(t * fps) for t in [segs[0][0]] + [s[1] for s in segs]]
    parts, labels = [], []
    for i in range(len(segs)):
        nf = max(1, frames[i + 1] - frames[i])
        parts.append(f"[{i}:v]{fit_filter(w, h, mode, fps)},tpad=stop_mode=clone:stop_duration=2,"
                     f"trim=end_frame={nf},setpts=PTS-STARTPTS,format=yuv420p[v{i}]")
        labels.append(f"[v{i}]")
    parts.append(f"{''.join(labels)}concat=n={len(segs)}:v=1:a=0[v]")
    return ";".join(parts)


def cmd_beats(args) -> int:
    info = probe(args.music)
    duration = args.duration or max(0.0, info["duration"] - args.music_start)
    result = detect_beats(args.music, args.music_start, duration, args.bpm)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"bpm: {result['bpm']} ({result['engine']})")
        print(" ".join(f"{b:.3f}" for b in result["beats"]))
    return 0


def cmd_beatsync(args) -> int:
    check_output(args)
    for clip in args.inputs:
        if not os.path.isfile(clip):
            raise CliError(f"Input not found: {clip}")
    w, h = parse_size(args.size)
    music = probe(args.music)
    if not music["has_audio"]:
        raise CliError(f"No audio track in {args.music}")
    duration = min(args.duration, music["duration"] - args.music_start)
    if duration <= 1:
        raise CliError("Less than 1 s of music after --music-start")
    found = detect_beats(args.music, args.music_start, duration + 2, args.bpm)
    start = args.music_start
    if not args.no_align and found["beats"]:
        # open the reel on a beat instead of a sliver before the first one
        start = next((b for b in found["beats"] if b >= start), start)
        duration = min(duration, music["duration"] - start)
    segs = plan_segments(found["beats"], start, duration, args.every, args.shift)
    print(f"bpm {found['bpm']} ({found['engine']}): {len(segs)} shots over {duration:.2f}s, song from {start:.2f}s",
          flush=True)
    argv = _segment_inputs(args.inputs, segs, args.fps)
    fade = max(0.0, duration - args.fade_out)
    argv += ["-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", args.music,
             "-filter_complex",
             _video_filters(segs, w, h, args.mode, args.fps)
             + f";[{len(segs)}:a]afade=t=out:st={fade:.3f}:d={args.fade_out}[a]",
             "-map", "[v]", "-map", "[a]", *ENCODE, "-t", f"{duration:.3f}", args.output]
    return ffmpeg(args, argv)


def add_beat_parsers(sub, common, render) -> None:
    s = sub.add_parser("beats", parents=[common], help="detect BPM and beat times in a track")
    s.add_argument("music")
    s.add_argument("--music-start", type=float, default=0.0, help="seconds into the track")
    s.add_argument("--duration", type=float, help="seconds to analyse (default: rest of track)")
    s.add_argument("--bpm", type=float, help="skip tempo detection and use this BPM")
    s.set_defaults(func=cmd_beats)

    s = sub.add_parser("beatsync", parents=[render],
                       help="cut clips/photos on the beat of a music track")
    s.add_argument("inputs", nargs="+", help="videos and/or photos, used in order, looping")
    s.add_argument("--music", required=True)
    s.add_argument("--music-start", type=float, default=0.0, help="start the song here (s)")
    s.add_argument("--duration", type=float, default=15.0, help="reel length in seconds")
    s.add_argument("--every", type=int, default=2, help="cut every N beats (default 2)")
    s.add_argument("--shift", type=int, default=0, help="offset cuts by N beats")
    s.add_argument("--bpm", type=float, help="override detected tempo")
    s.add_argument("--size", default="1080x1920")
    s.add_argument("--fps", type=float, default=30)
    s.add_argument("--mode", choices=["crop", "pad"], default="crop")
    s.add_argument("--fade-out", type=float, default=0.5, help="audio fade at the end (s)")
    s.add_argument("--no-align", action="store_true",
                   help="start the song exactly at --music-start, not on the next beat")
    s.set_defaults(func=cmd_beatsync)
