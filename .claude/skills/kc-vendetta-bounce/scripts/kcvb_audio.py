"""KC VENDETTA BOUNCE: audio decoding, 48-band analysis and loudest-section search."""
from __future__ import annotations

# subprocess only runs the resolved ffmpeg binary with a fixed argv list (no shell).
import subprocess  # nosec B404
import sys
from pathlib import Path

import numpy as np

from kcvb_style import ANALYSIS_SR, ATTACK, FPS, N_BARS, RELEASE

def decode_mono(ffmpeg: str, path: Path, sr: int = ANALYSIS_SR) -> np.ndarray:
    """Decode any audio file to mono float32 at `sr` Hz."""
    argv = [ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    # Fixed argv list, no shell; `ffmpeg` is a resolved absolute path from find_ffmpeg().
    proc = subprocess.run(argv, capture_output=True, check=False)  # nosec B603 # nosemgrep
    if proc.returncode != 0:
        sys.exit(f"ffmpeg could not decode {path}:\n{proc.stderr.decode(errors='replace')}")
    y = np.frombuffer(proc.stdout, np.float32).copy()
    if y.size < sr:
        sys.exit(f"Audio too short or empty: {path}")
    return y


def _band_weights(freqs: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """(bins x 48) averaging matrix over log-spaced bands, and band centre freqs."""
    edges = np.geomspace(40.0, min(12000.0, sr / 2 * 0.95), N_BARS + 1)
    fc = np.sqrt(edges[:-1] * edges[1:])
    band_of_bin = np.digitize(freqs, edges) - 1
    weights = np.zeros((freqs.size, N_BARS), np.float32)
    for band in range(N_BARS):
        idx = np.nonzero(band_of_bin == band)[0]
        if idx.size == 0:  # low bands narrower than an FFT bin
            idx = np.array([np.argmin(np.abs(freqs - fc[band]))])
        weights[idx, band] = 1.0 / idx.size
    return weights, fc


def _smooth(raw: np.ndarray) -> np.ndarray:
    """Fast-attack / slow-release envelope per bar."""
    levels = np.empty_like(raw)
    prev = np.zeros(N_BARS, np.float32)
    for i in range(raw.shape[0]):
        x = raw[i]
        prev = np.where(x > prev, prev + ATTACK * (x - prev), prev * RELEASE + x * (1 - RELEASE))
        levels[i] = prev
    return levels


def analyze(y: np.ndarray, sr: int, n_frames: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (levels[n_frames, 48] 0..1 smoothed, bass[n_frames] 0..1, rms[n_frames])."""
    n_fft = 2048
    win = np.hanning(n_fft).astype(np.float32)
    padded = np.pad(y, (n_fft // 2, n_fft * 2))
    centers = np.round(np.arange(n_frames) * sr / FPS).astype(np.int64)
    weights, fc = _band_weights(np.fft.rfftfreq(n_fft, 1 / sr), sr)

    power = np.empty((n_frames, N_BARS), np.float32)
    offs = np.arange(n_fft)
    for s in range(0, n_frames, 256):
        c = centers[s:s + 256]
        seg = padded[c[:, None] + offs[None, :]] * win
        power[s:s + c.size] = (np.abs(np.fft.rfft(seg, axis=1)) ** 2) @ weights

    # +3 dB/oct tilt keeps highs alive
    db = 10 * np.log10(power + 1e-10) + 3.0 * np.log2(fc / 100.0)
    ceil = np.percentile(db, 99.5)
    levels = _smooth(np.clip((db - (ceil - 45.0)) / 45.0, 0, 1) ** 1.6)
    bass = levels[:, :6].mean(axis=1)
    bass = np.clip(bass / max(np.percentile(bass, 98), 1e-6), 0, 1)

    energy = np.concatenate([[0.0], np.cumsum(y.astype(np.float64) ** 2)])
    starts = np.clip(centers, 0, y.size)
    ends = np.clip(np.round((np.arange(n_frames) + 1) * sr / FPS).astype(np.int64), 0, y.size)
    rms = np.sqrt((energy[ends] - energy[starts]) / np.maximum(ends - starts, 1))
    return levels, bass.astype(np.float32), rms.astype(np.float32)


def loudest_window(rms: np.ndarray, bass: np.ndarray, win_frames: int) -> int:
    """Start frame of the loudest window, nudged onto the nearest bass hit (±1s)."""
    n = rms.size
    if win_frames >= n:
        return 0
    energy = np.concatenate([[0.0], np.cumsum(rms.astype(np.float64) ** 2)])
    sums = energy[win_frames:] - energy[:-win_frames]
    start = int(np.argmax(sums))
    onset = np.maximum(np.diff(bass, prepend=bass[0]), 0)
    lo, hi = max(0, start - FPS), min(n - win_frames, start + FPS)
    if hi > lo:
        start = lo + int(np.argmax(onset[lo:hi + 1]))
    return min(start, n - win_frames)
