"""Microphone capture: 20 ms float32 mono blocks stamped with `t_capture` (SPEC §7.2, §7.5).

The PortAudio callback never blocks. Consumers are plain callables run in the
audio thread, so they must be cheap: writing to a `Ring`, or handing the block
to asyncio with `loop.call_soon_threadsafe`.
"""

from __future__ import annotations

import time
from typing import Callable

import numpy as np
import sounddevice as sd
import soxr

from ..clock import block_start
from . import devices

Consumer = Callable[[float, np.ndarray], None]   # (t_capture, mono float32 block at capture rate)


class MicCapture:
    def __init__(self, name_sub: str = "Realtek", *, block_ms: int = 20,
                 exclude: tuple[str, ...] = ("CABLE",), emit: Callable[..., None] | None = None) -> None:
        self.name_sub = name_sub
        self.exclude = exclude
        self.block_ms = block_ms
        self._emit = emit or (lambda ev, **f: None)
        self._consumers: list[Consumer] = []
        self._stream: sd.InputStream | None = None
        self.device: devices.Device | None = None
        self.rate = 0
        self.overflows = 0
        self.blocks = 0

    def add_consumer(self, fn: Consumer) -> None:
        self._consumers.append(fn)

    def _callback(self, indata, frames, time_info, status) -> None:
        t_capture = block_start(frames, self.rate)
        if status.input_overflow:
            self.overflows += 1
        block = indata[:, 0].copy() if indata.shape[1] == 1 else indata.mean(axis=1, dtype=np.float32)
        self.blocks += 1
        for fn in self._consumers:
            fn(t_capture, block)

    def _open(self, dev: devices.Device) -> sd.InputStream:
        rate = dev.default_samplerate
        stream = sd.InputStream(
            device=dev.index, samplerate=rate, channels=1, dtype="float32",
            blocksize=int(rate * self.block_ms / 1000), latency="low",
            extra_settings=devices.stream_extra(dev), callback=self._callback,
        )
        stream.start()
        self.rate = rate
        return stream

    def start(self) -> None:
        """WASAPI twice, then the shared duplicates of the same device; never the jack mic."""
        tried = []
        for dev in devices.candidates(self.name_sub, "input", self.exclude):
            attempts = 2 if dev.is_wasapi else 1
            for attempt in range(attempts):
                try:
                    self._stream = self._open(dev)
                    self.device = dev
                    self._emit("state", what="mic_open", device=dev.name, hostapi=dev.hostapi,
                               rate=self.rate, attempt=attempt + 1, failed=tried)
                    return
                except sd.PortAudioError as exc:
                    tried.append(f"{dev.hostapi}: {exc}")
                    time.sleep(0.2)
        raise RuntimeError(f"cannot open microphone matching {self.name_sub!r}: {tried}")

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._consumers.clear()     # drops the soxr streams they hold before interpreter shutdown


class To16k:
    """Streaming resampler capture-rate float32 → 16 kHz PCM16 bytes for the ASR."""

    def __init__(self, in_rate: int, out_rate: int = 16000) -> None:
        self.in_rate = in_rate
        self.out_rate = out_rate
        self._rs = None if in_rate == out_rate else soxr.ResampleStream(in_rate, out_rate, 1, dtype="float32")

    def __call__(self, block: np.ndarray) -> bytes:
        y = block if self._rs is None else self._rs.resample_chunk(block)
        return float_to_pcm16(y)


class QueueFeeder:
    """Capture consumer that hands `(t_capture, pcm16)` to an asyncio queue, dropping the oldest when full."""

    def __init__(self, loop, q, convert: Callable[[np.ndarray], bytes] | None = None) -> None:
        self.loop = loop
        self.q = q
        self.convert = convert or (lambda pcm: pcm)     # None: the source already gives PCM16 bytes
        self.dropped = 0

    def _offer(self, item) -> None:
        if self.q.full():
            self.q.get_nowait()
            self.dropped += 1
        self.q.put_nowait(item)

    def __call__(self, t_capture: float, block) -> None:
        self.loop.call_soon_threadsafe(self._offer, (t_capture, self.convert(block)))


def float_to_pcm16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
