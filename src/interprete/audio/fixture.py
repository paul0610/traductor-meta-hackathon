"""A 16 kHz mono WAV replayed as if it were a live capture, for runs that need no one speaking."""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path
from typing import Callable

from ..clock import now

RATE = 16000
BLOCK = 320                     # 20 ms


def save_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def read_wav(path: str | Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, RATE):
            raise SystemExit(f"{path}: need mono 16-bit 16 kHz (ffmpeg -i in -ac 1 -ar 16000 -sample_fmt s16 out.wav)")
        return w.readframes(w.getnframes())


async def replay(pcm: bytes, q: asyncio.Queue, stop: asyncio.Event,
                 tap: Callable[[float, bytes], None] | None = None) -> None:
    """Real time against an absolute schedule, so `t_capture` is exact by construction."""
    t_start = now() + 0.05
    step = BLOCK * 2
    for k, i in enumerate(range(0, len(pcm), step)):
        t_cap = t_start + k * BLOCK / RATE
        delay = t_cap + BLOCK / RATE - now()      # a block "exists" once its last sample is captured
        if delay > 0:
            await asyncio.sleep(delay)
        chunk = pcm[i:i + step]
        if tap is not None:
            tap(t_cap, chunk)
        await q.put((t_cap, chunk))
        if stop.is_set():
            break
