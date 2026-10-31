"""Finds claps in the audio and in the video, and how far apart the two arrive.

The audio side is exact: a clap is the sharpest attack in the signal. The video side is
good to a frame: hands move fast, stop on contact, and the motion between frames falls
off a cliff, so the contact is the first frame after the peak below half of it.
"""

from __future__ import annotations

import numpy as np

ENVELOPE_S = 0.005
MIN_GAP_S = 0.7                # two claps closer than this are one, or an echo
AUDIO_FLOOR = 0.3              # of the loudest clap
MOTION_FLOOR = 0.4             # of the fastest movement
ABOVE_BACKGROUND = 8.0         # times the median: room noise and a still person are never a clap
MATCH_S = 1.0                  # a clap whose video is further than this was out of frame


def _floor(x: np.ndarray, share: float) -> float:
    return max(float(x.max(initial=0.0)) * share, float(np.median(x)) * ABOVE_BACKGROUND)


def audio_claps(x: np.ndarray, rate: int, t0: float) -> list[float]:
    """Attack time of each clap in `x`, on the same clock as `t0`, the time of its first sample."""
    win = max(1, int(ENVELOPE_S * rate))
    n = len(x) // win
    if n == 0:
        return []
    env = np.abs(x[: n * win]).reshape(n, win).max(axis=1)
    floor = _floor(env, AUDIO_FLOOR)
    if floor <= 0.0 or env.max() < floor:
        return []
    claps: list[float] = []
    for i in np.flatnonzero(env >= floor):
        t = t0 + i * win / rate
        if not claps or t - claps[-1] >= MIN_GAP_S:
            claps.append(t)
    return claps


def video_claps(t: np.ndarray, motion: np.ndarray) -> list[float]:
    """Contact time of each clap; `motion[i]` is the change between frames i - 1 and i, seen at `t[i]`."""
    if len(motion) == 0:
        return []
    floor = _floor(motion, MOTION_FLOOR)
    if floor <= 0.0:
        return []
    claps: list[float] = []
    i = 0
    while i < len(motion):
        if motion[i] < floor or (claps and t[i] - claps[-1] < MIN_GAP_S):
            i += 1
            continue
        end = min(np.searchsorted(t, t[i] + MIN_GAP_S), len(motion))
        peak = i + int(np.argmax(motion[i:end]))
        after = np.flatnonzero(motion[peak:end] < motion[peak] / 2)
        claps.append(float(t[peak + (int(after[0]) if len(after) else 0)]))
        i = end
    return claps


def offsets(audio: list[float], video: list[float]) -> list[float]:
    """Audio minus video per clap: positive means the picture arrives first."""
    out = []
    for a in audio:
        v = min(video, key=lambda t: abs(t - a), default=None)
        if v is not None and abs(v - a) <= MATCH_S:
            out.append(a - v)
    return out
