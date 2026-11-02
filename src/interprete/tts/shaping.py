"""What becomes of a clause once the voice has it: the result it hands over, the silence it trims, the speed it aims for."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

TRIM_THRESHOLD_DB = -50.0      # peak level per 5 ms window below which audio counts as silence
TRIM_MARGIN_S = 0.060
ISOCHRONY_EXCESS = 0.10        # this much longer than its slot: an overflow delays every clause behind it
MIN_SRC_S = 0.6
FAST_RATE_PCT = 12             # on top of MMS at 1.45, +25 % could not be understood, by ear; few clauses need it (SPEC §10)


@dataclass(slots=True)
class TtsResult:
    pcm: bytes                 # int16 LE mono at `sample_rate`
    sample_rate: int
    duration_s: float
    t_request: float
    t_first: float | None      # first audio out of the voice
    t_end: float               # last audio out of the voice
    t_ready: float             # resampled, trimmed
    trimmed_lead_s: float
    trimmed_tail_s: float


def trim_silence(x: np.ndarray, rate: int, threshold_db: float = TRIM_THRESHOLD_DB,
                 margin_s: float = TRIM_MARGIN_S) -> tuple[np.ndarray, float, float]:
    win = max(1, int(rate * 0.005))
    n = len(x) // win
    if n == 0:
        return x, 0.0, 0.0
    peaks = np.abs(x[: n * win]).reshape(n, win).max(axis=1)
    loud = np.flatnonzero(peaks > 10 ** (threshold_db / 20))
    if len(loud) == 0:
        return x[:0], len(x) / rate, 0.0
    margin = int(rate * margin_s)
    start = max(0, loud[0] * win - margin)
    end = min(len(x), (loud[-1] + 1) * win + margin)
    return x[start:end], start / rate, (len(x) - end) / rate


def isochrony_pct(text: str, src_s: float | None, s_per_char: float, s_per_clause: float) -> int:
    """`rate` that brings the voice close to the clause it translates (SPEC §4.1, ±15 % goal).

    Only ever faster: slower would add lag at D = 0, and the capped case still
    leaves the voice intelligible. A clause cut short by the ASR can look tiny,
    so `src_s` is floored at the length of one short word in the voice.
    """
    if src_s is None:
        return 0
    expected_s = s_per_char * len(text) + s_per_clause
    return FAST_RATE_PCT if expected_s / max(src_s, MIN_SRC_S) - 1.0 >= ISOCHRONY_EXCESS else 0
