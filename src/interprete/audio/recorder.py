"""Records an input endpoint (e.g. CABLE Output: what the call app hears) with t_capture per block."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

from ..clock import block_start
from . import devices


class Recorder:
    def __init__(self, name_sub: str = "CABLE Output") -> None:
        self.device = devices.find(name_sub, "input")
        self.rate = self.device.default_samplerate
        self.blocks: list[tuple[float, np.ndarray]] = []
        self._stream = sd.InputStream(
            device=self.device.index, samplerate=self.rate, channels=1, dtype="float32",
            blocksize=int(self.rate * 0.01), latency="low",
            extra_settings=devices.stream_extra(self.device), callback=self._cb,
        )

    def _cb(self, indata, frames, time_info, status) -> None:
        self.blocks.append((block_start(frames, self.rate), indata[:, 0].copy()))

    def __enter__(self):
        self._stream.start()
        return self

    def __exit__(self, *exc):
        self._stream.stop()
        self._stream.close()

    def signal(self) -> tuple[float, np.ndarray]:
        return self.blocks[0][0], np.concatenate([b for _, b in self.blocks])

    def save(self, path: Path | str) -> Path:
        _, x = self.signal()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
        return path


def find_offset(rec: np.ndarray, ref: np.ndarray) -> tuple[int, float]:
    """Lag of `ref` inside `rec` by FFT cross-correlation, and the normalized peak."""
    n = 1 << int(np.ceil(np.log2(len(rec) + len(ref))))
    corr = np.fft.irfft(np.fft.rfft(rec, n) * np.conj(np.fft.rfft(ref, n)), n)[: len(rec)]
    lag = int(np.argmax(corr))
    seg = rec[lag: lag + len(ref)]
    if len(seg) < len(ref):
        seg = np.pad(seg, (0, len(ref) - len(seg)))
    denom = np.linalg.norm(seg) * np.linalg.norm(ref) or 1.0
    return lag, float(np.dot(seg, ref) / denom)
