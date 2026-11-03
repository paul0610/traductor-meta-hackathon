"""The other side's words in Spanish on my headphones, and the gate that keeps the app from hearing itself.

The subtitles come from the loopback of the default output, and this voice plays there too. So while it
speaks, and for a short tail after, `Gate` hands the ASR silence in place of what the loopback hears:
the timing of the stream goes on, the voice is not translated back, and what the other side says in that
window is not heard either. That is the price, and why F6 turns the voice off. It never reaches the
cable: the other side only ever hears English.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Callable

import numpy as np
import soxr

from ..clock import now
from . import devices

GATE_TAIL_S = 0.4              # the loopback hands a block over a little after it played
MAX_BEHIND_S = 6.0             # a clause that would start later than this is only read: the voice must not drift


class SpeakerVoice:
    """Clauses one after another on the default output; `deaf(t)` says whether the loopback could be hearing them."""

    def __init__(self, *, device: devices.Device | None = None, open_stream: Callable | None = None) -> None:
        self.device = device or devices.default_output()
        self.rate = self.device.default_samplerate
        self._open_stream = open_stream or self._sounddevice
        self._q: deque[np.ndarray] = deque()
        self._lock = threading.Lock()
        self._busy_until = 0.0
        self._stream = None

    def _sounddevice(self, callback):
        import sounddevice as sd

        return sd.OutputStream(device=self.device.index, samplerate=self.rate, channels=1, dtype="float32",
                               callback=callback, extra_settings=devices.stream_extra(self.device))

    def start(self) -> None:
        self._stream = self._open_stream(self._fill)
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.close()

    def behind_s(self, t: float | None = None) -> float:
        t = now() if t is None else t
        return max(0.0, self._busy_until - t)

    def play(self, pcm: bytes, rate: int) -> float:
        """Queues one clause and returns when it will have played, on the app's clock."""
        x = np.frombuffer(pcm, "<i2").astype(np.float32) / 32768.0
        if rate != self.rate:
            x = soxr.resample(x, rate, self.rate)
        with self._lock:
            self._q.append(x)
            self._busy_until = max(now(), self._busy_until) + len(x) / self.rate
            return self._busy_until

    def hush(self) -> None:
        """Drops what has not played yet; the tail still covers what already left."""
        with self._lock:
            self._q.clear()
            self._busy_until = min(self._busy_until, now())

    def deaf(self, t: float) -> bool:
        return t < self._busy_until + GATE_TAIL_S

    def _fill(self, outdata, frames, time_info, status) -> None:
        out = outdata[:, 0]
        filled = 0
        with self._lock:
            while filled < frames and self._q:
                head = self._q[0]
                n = min(frames - filled, len(head))
                out[filled:filled + n] = head[:n]
                filled += n
                if n == len(head):
                    self._q.popleft()
                else:
                    self._q[0] = head[n:]
        out[filled:] = 0.0


class Gate:
    """Between the loopback and the ASR: silence of the same length while `deaf(t_capture)`, so timing goes on."""

    def __init__(self, feed: Callable[[float, bytes], None], deaf: Callable[[float], bool]) -> None:
        self.feed = feed
        self.deaf = deaf
        self.blocked = 0

    def __call__(self, t_capture: float, block: bytes) -> None:
        if self.deaf(t_capture):
            self.blocked += 1
            block = bytes(len(block))
        self.feed(t_capture, block)
