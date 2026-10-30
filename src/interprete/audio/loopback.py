"""System audio tap: WASAPI loopback of the default render endpoint, i.e. what the call app plays (SPEC §1).

Passive: the call app never knows, and no audio device is touched. It runs in
its own thread because pyaudiowpatch owns a PortAudio instance per open. The
stream is reopened when it dies or when Windows changes the default output
(polled every second), so plugging in headphones mid-call just follows them. The
rate can change on reopen, so resampling to 16 kHz lives here, not downstream.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np
import pyaudiowpatch as pyaudio

from ..clock import block_start
from .capture import To16k

Feed = Callable[[float, bytes], None]       # (t_capture, 16 kHz PCM16 bytes), called in the audio thread


class LoopbackCapture:
    def __init__(self, feed: Feed, *, block_ms: int = 20, emit: Callable[..., None] | None = None) -> None:
        self.feed = feed
        self.block_ms = block_ms
        self._emit = emit or (lambda ev, **f: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.device_name = ""
        self.rate = 0
        self.blocks = 0
        self.reopens = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="loopback", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            try:
                self._capture_once()
                backoff = 0.5
            except (OSError, LookupError, RuntimeError) as exc:
                self._emit("state", what="loopback_reopen", error=repr(exc)[:300], backoff_s=backoff)
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 2.0)
            self.reopens += 1

    def _capture_once(self) -> None:
        with pyaudio.PyAudio() as pa:
            dev = pa.get_default_wasapi_loopback()
            identity = (dev["index"], dev["name"], dev["defaultSampleRate"], dev["maxInputChannels"])
            rate, channels = int(dev["defaultSampleRate"]), int(dev["maxInputChannels"])
            to16 = To16k(rate)

            def callback(data, frames, time_info, status):
                t_capture = block_start(frames, rate)
                block = np.frombuffer(data, dtype="<f4").reshape(-1, channels).mean(axis=1, dtype=np.float32)
                self.blocks += 1
                self.feed(t_capture, to16(block))
                return (None, pyaudio.paContinue)

            stream = pa.open(format=pyaudio.paFloat32, channels=channels, rate=rate, input=True,
                             input_device_index=int(dev["index"]), frames_per_buffer=rate * self.block_ms // 1000,
                             stream_callback=callback)
            self.device_name, self.rate = dev["name"], rate
            self._emit("state", what="loopback_open", device=dev["name"], rate=rate, channels=channels)
            try:
                t_check = time.monotonic() + 1.0
                while not self._stop.wait(0.1):
                    if not stream.is_active():
                        raise RuntimeError("loopback stream stopped")
                    if time.monotonic() < t_check:
                        continue
                    t_check = time.monotonic() + 1.0
                    cur = pa.get_default_wasapi_loopback()
                    if (cur["index"], cur["name"], cur["defaultSampleRate"], cur["maxInputChannels"]) != identity:
                        self._emit("state", what="loopback_default_changed", device=cur["name"])
                        return
            finally:
                stream.stop_stream()
                stream.close()
