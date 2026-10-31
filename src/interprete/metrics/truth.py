"""How far `t_src_start` is from when a clause really started.

The ASR gives no word times in realtime, so `t_src_start` is an estimate (SPEC §7.4)
and it decides whether voice and lips meet. Gate S only compares the scheduler
against that estimate; this compares the estimate against the truth: the word times
of a fixture, or for a real voice the energy of the microphone where a turn opens.
"""

from __future__ import annotations

import difflib
import re

import numpy as np

FRAME_S = 0.02
PAUSE_S = 0.4                  # of quiet before speech counts as a new turn
SPEECH_S = 0.1                 # loud for this long: a click is not a turn


def turn_onsets(x: np.ndarray, rate: int, t0: float) -> list[float]:
    """Where speech starts after a pause, on the same clock as `t0`, the time of its first sample."""
    win = int(FRAME_S * rate)
    n = len(x) // win
    if n == 0:
        return []
    level = np.sqrt(np.mean(x[: n * win].reshape(n, win) ** 2, axis=1))
    room, voice = np.percentile(level, [20, 95])
    on = level > np.sqrt(room * voice)                # halfway between the two in dB
    keep = round(SPEECH_S / FRAME_S)
    onsets: list[float] = []
    quiet = 0
    for i in range(n - keep + 1):
        if not on[i: i + keep].all():
            quiet += 1
            continue
        if quiet >= round(PAUSE_S / FRAME_S):
            onsets.append(t0 + i * FRAME_S)
        quiet = 0
    return onsets


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word).lower()


def clause_truth(rows: list[dict], words: list[dict], t_fixture: float) -> dict[int, float]:
    """seq -> local time of the clause's first word. A clause whose first word the ASR changed is left out."""
    rows = sorted(rows, key=lambda r: r["seq"])
    said = [(_norm(w), r["seq"], i == 0) for r in rows for i, w in enumerate(r["text_src"].split())]
    real = [_norm(w["text"]) for w in words]
    sm = difflib.SequenceMatcher(None, [w for w, _, _ in said], real, autojunk=False)
    out: dict[int, float] = {}
    for m in sm.get_matching_blocks():
        for k in range(m.size):
            _, seq, first = said[m.a + k]
            if first:
                out[seq] = t_fixture + words[m.b + k]["t"]
    return out
