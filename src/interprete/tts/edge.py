"""Edge TTS → PCM16 mono at the cable's rate, leading/trailing silence trimmed.

The Meta Model API has no TTS; the outgoing voice is Microsoft Edge TTS. Edge pads
every request with silence, so only PCM silence is trimmed (conservative
threshold, 60 ms margin): speech is never cut.
"""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass

import edge_tts
import numpy as np
import soundfile as sf
import soxr

from ..clock import now

TRIM_THRESHOLD_DB = -50.0      # peak level per 5 ms window below which audio counts as silence
TRIM_MARGIN_S = 0.060
S_PER_CHAR = 0.047             # en-US-GuyNeural at rate 0, 242 clauses: length in characters predicts the duration
S_PER_CLAUSE = 0.40            # twice as well as words (±0.13 s against ±0.29 s): "for mid-sized companies" is 3 words
ISOCHRONY_EXCESS = 0.10        # this much longer than its slot: an overflow delays every clause behind it
MIN_SRC_S = 0.6
FAST_RATE_PCT = 25             # Edge answers +0 % and +25 % at once; values between stall it (SPEC §9), so only these two


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


async def finish(mp3: bytes, out_rate: int, t_request: float, t_first: float | None, t_end: float) -> TtsResult:
    """Decode, resample and trim a finished MP3, off the event loop."""

    def _post() -> tuple[np.ndarray, float, float, float]:
        t0 = now()
        x, rate = decode_mp3(mp3)
        decode_s = now() - t0
        if rate != out_rate:
            x = soxr.resample(x, rate, out_rate)
        y, lead, tail = trim_silence(x, out_rate)
        return y, decode_s, lead, tail

    y, decode_s, lead, tail = await asyncio.to_thread(_post)
    pcm = (np.clip(y, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    return TtsResult(pcm, out_rate, len(y) / out_rate, t_request, t_first, t_end, now(),
                     decode_s, lead, tail, len(mp3))


async def synthesize(text: str, voice: str, out_rate: int, *, rate_pct: int = 0) -> TtsResult:
    """One clause over its own connection; `EdgeVoice` is the fast path, this one has nothing to keep alive."""
    t_request = now()
    t_first = None
    buf = bytearray()
    comm = edge_tts.Communicate(text, voice, rate=f"{rate_pct:+d}%")
    async for part in comm.stream():
        if part["type"] == "audio":
            if t_first is None:
                t_first = now()
            buf += part["data"]
    return await finish(bytes(buf), out_rate, t_request, t_first, now())


def isochrony_pct(text: str, src_s: float | None, s_per_char: float = S_PER_CHAR, s_per_clause: float = S_PER_CLAUSE) -> int:
    """`rate` that brings the voice close to the clause it translates (SPEC §4.1, ±15 % goal).

    Only ever faster: slower would add lag at D = 0, and the capped case still
    leaves the voice intelligible. A clause cut short by the ASR can look tiny,
    so `src_s` is floored at the length of one short word in the voice. The
    two seconds-per constants are Edge's; another voice passes its own.
    """
    if src_s is None:
        return 0
    expected_s = s_per_char * len(text) + s_per_clause
    return FAST_RATE_PCT if expected_s / max(src_s, MIN_SRC_S) - 1.0 >= ISOCHRONY_EXCESS else 0
