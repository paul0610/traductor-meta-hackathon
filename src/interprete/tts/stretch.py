"""Time stretch without changing pitch, for a voice with no speed control of its own.

WSOLA: overlapping 30 ms grains, each taken from the spot near its place in time that
best continues the grain before it, so the voice's periods line up instead of phasing.
"""

from __future__ import annotations

import numpy as np

GRAIN_S = 0.030
SEARCH_S = 0.012


def wsola(x: np.ndarray, speed: float, rate: int) -> np.ndarray:
    """`speed` times shorter. Too short to cut into grains comes back as it is."""
    grain = int(GRAIN_S * rate)
    hop_out = grain // 2
    hop_in = hop_out * speed
    search = int(SEARCH_S * rate)
    n = int((len(x) - grain - search) / hop_in)
    if n < 2:
        return x
    window = np.hanning(grain).astype(np.float32)
    y = np.zeros(n * hop_out + grain, np.float32)
    weight = np.zeros_like(y)
    last = 0
    for i in range(n):
        at = 0
        if i:
            follow = x[last + hop_out: last + hop_out + grain]
            lo = max(0, int(i * hop_in) - search)
            hi = min(len(x) - grain, int(i * hop_in) + search)
            at = lo + int(np.argmax(np.correlate(x[lo: hi + grain], follow, "valid")))
        y[i * hop_out: i * hop_out + grain] += x[at: at + grain] * window
        weight[i * hop_out: i * hop_out + grain] += window
        last = at
    return y / np.maximum(weight, 1e-3)
