"""Finds claps in the audio, then each one in the video, and how far apart the two arrive.

The audio side is exact: a clap is the sharpest attack in the signal. It also says where
to look, and that matters: the title card giving way to the picture changes every pixel,
and getting into position moves more than two hands do, so no level over the whole run
can tell a clap. Near a clap the hands move fast and stop on contact, so the motion
between frames falls off a cliff, and the video is good to a frame.
"""

from __future__ import annotations

import numpy as np

ENVELOPE_S = 0.005
MIN_GAP_S = 0.7                # two claps closer than this are one, or an echo
AUDIO_FLOOR = 0.3              # of the loudest clap
ABOVE_BACKGROUND = 8.0         # times the median: room noise is never a clap
MATCH_S = 0.4                  # both lines share D and the clock: further than this, it is not the same clap
MOTION_MIN = 0.005             # share of the pixels: less than this is a person sitting still


def audio_claps(x: np.ndarray, rate: int, t0: float) -> list[float]:
    """Attack time of each clap in `x`, on the same clock as `t0`, the time of its first sample."""
    win = max(1, int(ENVELOPE_S * rate))
    n = len(x) // win
    if n == 0:
        return []
    env = np.abs(x[: n * win]).reshape(n, win).max(axis=1)
    floor = max(float(env.max()) * AUDIO_FLOOR, float(np.median(env)) * ABOVE_BACKGROUND)
    if floor <= 0.0 or env.max() < floor:
        return []
    claps: list[float] = []
    for i in np.flatnonzero(env >= floor):
        t = t0 + i * win / rate
        if not claps or t - claps[-1] >= MIN_GAP_S:
            claps.append(t)
    return claps


def contact(t: np.ndarray, motion: np.ndarray, near: float) -> float | None:
    """When the hands met around `near`, None if no clap shows; `motion[i]` is the change between frames i - 1 and i, seen at `t[i]`."""
    i0, i1 = np.searchsorted(t, [near - MATCH_S, near + MATCH_S])
    i0 = max(int(i0), 1)
    if i1 <= i0:
        return None
    peak = i0 + int(np.argmax(motion[i0:i1]))
    stopped = np.flatnonzero(motion[peak:] < motion[peak] / 2)
    if motion[peak] < MOTION_MIN or len(stopped) == 0:
        return None
    last = peak + int(stopped[0]) - 1                 # the last frame still shows them travelling
    return float(t[last - 1] + t[last]) / 2


def offsets(audio: list[float], t: np.ndarray, motion: np.ndarray) -> list[float]:
    """Audio minus video per clap that shows in the video: positive means the picture arrives first."""
    seen = [(a, contact(t, motion, a)) for a in audio]
    return [a - v for a, v in seen if v is not None]
