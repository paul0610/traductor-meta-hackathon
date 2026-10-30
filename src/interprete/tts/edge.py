"""Edge TTS → PCM16 mono at the cable's rate, leading/trailing silence trimmed.

There is no Meta TTS model; the outgoing voice is Microsoft Edge TTS. Edge pads
every request with silence, so only PCM silence is trimmed (conservative
threshold, 60 ms margin): speech is never cut.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from typing import Callable

import edge_tts
import numpy as np
import soundfile as sf
import soxr

from ..clock import now
from ..hedge import hedged

TRIM_THRESHOLD_DB = -50.0      # peak level per 5 ms window below which audio counts as silence
TRIM_MARGIN_S = 0.060
HEDGE_AFTER_S = 2.0


@dataclass(slots=True)
class TtsResult:
    pcm: bytes                 # int16 LE mono at `sample_rate`
    sample_rate: int
    duration_s: float
    t_request: float
    t_first: float | None      # first audio byte from Edge
    t_end: float               # last byte received
    t_ready: float             # decoded, resampled, trimmed
    decode_s: float
    trimmed_lead_s: float
    trimmed_tail_s: float
    mp3_bytes: int


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


def decode_mp3(mp3: bytes) -> tuple[np.ndarray, int]:
    x, rate = sf.read(io.BytesIO(mp3), dtype="float32")
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, rate


async def synthesize(text: str, voice: str, out_rate: int, *, rate_pct: int = 0) -> TtsResult:
    t_request = now()
    t_first = None
    buf = bytearray()
    comm = edge_tts.Communicate(text, voice, rate=f"{rate_pct:+d}%")
    async for part in comm.stream():
        if part["type"] == "audio":
            if t_first is None:
                t_first = now()
            buf += part["data"]
    t_end = now()

    def _post() -> tuple[np.ndarray, float, float, float]:
        t0 = now()
        x, rate = decode_mp3(bytes(buf))
        decode_s = now() - t0
        if rate != out_rate:
            x = soxr.resample(x, rate, out_rate)
        y, lead, tail = trim_silence(x, out_rate)
        return y, decode_s, lead, tail

    y, decode_s, lead, tail = await asyncio.to_thread(_post)
    pcm = (np.clip(y, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    return TtsResult(pcm, out_rate, len(y) / out_rate, t_request, t_first, t_end, now(),
                     decode_s, lead, tail, len(buf))


async def synthesize_hedged(text: str, voice: str, out_rate: int, on_hedge: Callable[[], None] | None = None) -> TtsResult:
    """Edge sometimes stalls 3.4 s and then answers NoAudioReceived, whatever the text (measured 2 clauses in 35).

    A retry after that no longer fits the 4 s budget, so a second request starts
    at HEDGE_AFTER_S (above the 1.9 s worst healthy synthesis seen) and races the first.
    """
    return await hedged(lambda: synthesize(text, voice, out_rate), HEDGE_AFTER_S, on_hedge)
