"""Edge TTS → PCM16 mono at the cable's rate, leading/trailing silence trimmed.

Not part of the hackathon build: `uv sync --extra pocket` brings it back. Edge pads
every request with silence, so only PCM silence is trimmed (conservative
threshold, 60 ms margin): speech is never cut.
"""

from __future__ import annotations

import asyncio
import io

import edge_tts
import numpy as np
import soundfile as sf
import soxr

from ..clock import now
from .shaping import TtsResult, isochrony_pct, trim_silence  # noqa: F401  (edge_session reads them here)


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
