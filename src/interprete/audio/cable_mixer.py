"""Outgoing mixer: writes the virtual microphone (`CABLE Input`) from one output callback (SPEC §7.6).

States this week: OFF, SILENT (the rest state: the other side only ever hears
English, so raw Spanish stays off the cable), TTS (translated voice), PASSTHROUGH
(live voice, only by F9 or as the fallback while the ASR is down), MUTE. DUB
(piece 10) plugs in here later. The TTS takes the cable at the exact sample its
first chunk starts. Going back to rest is decided outside the callback
(`release_if_idle`), because it needs to know whether the scheduler still has
work in flight.

The endpoint is opened with its real channel count (VB-CABLE: 2) and the mono
mix goes to every channel, so nothing depends on how Windows would upmix mono.

The callback never blocks: chunks arrive through a deque (append/popleft are
atomic), control through plain attribute writes.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import numpy as np
import sounddevice as sd

from ..clock import now
from ..contracts import PcmChunk
from . import devices
from .ring import Ring

RELEASE_AFTER_S = 0.30          # TTS → rest once idle this long with nothing in flight
PASSTHROUGH_MAX_S = 0.06        # bound live-voice latency: drop older mic audio beyond this


class State(str, Enum):
    OFF = "OFF"
    SILENT = "SILENT"
    PASSTHROUGH = "PASSTHROUGH"
    TTS = "TTS"
    MUTE = "MUTE"


@dataclass(slots=True)
class _Item:
    chunk: PcmChunk
    start_at: float
    pcm: np.ndarray
    pos: int = 0
    actual_start: float | None = None


class CableMixer:
    def __init__(self, name_sub: str = "CABLE Input", *, mic: Ring | None = None, rest: State = State.SILENT,
                 block_ms: int = 10, emit: Callable[..., None] | None = None) -> None:
        self.device = devices.find(name_sub, "output")
        self.rate = self.device.default_samplerate       # the endpoint's real rate, never assumed
        self.channels = min(2, self.device.max_output_channels)
        self.block = int(self.rate * block_ms / 1000)
        self.mic = mic
        self.rest = rest
        self.latched = False                             # F9: live voice until the next F9
        self._emit = emit or (lambda ev, **f: None)
        self._items: deque[_Item] = deque()
        self._cur: _Item | None = None
        self._clear_req = False
        self._state = State.OFF
        self._before_mute = rest
        self._last_end = 0.0
        self._kill_t: float | None = None
        self._stream: sd.OutputStream | None = None
        self.tts_idle_since: float | None = None
        self.t_callback = 0.0                            # heartbeat for the watchdog
        self.cable_gaps = 0
        self.passthrough_underruns = 0
        self.frames_out = 0

    # --- lifecycle -------------------------------------------------------------------

    def _open(self) -> None:
        self._stream = sd.OutputStream(
            device=self.device.index, samplerate=self.rate, channels=self.channels, dtype="float32",
            blocksize=self.block, latency="low",
            extra_settings=devices.stream_extra(self.device), callback=self._callback,
        )
        self._stream.start()
        self.t_callback = now()

    def start(self) -> None:
        self._open()
        self._set_state(self.rest, "start")

    def reopen(self) -> None:
        """Watchdog: the callback stopped firing, replace the stream; the state is kept."""
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.abort()
            stream.close()
        self._open()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._set_state(State.OFF, "stop")

    @property
    def state(self) -> State:
        return self._state

    @property
    def output_latency_s(self) -> float:
        return float(self._stream.latency) if self._stream is not None else 0.0

    # --- scheduler-facing (Sink protocol) ----------------------------------------------

    def play(self, chunk: PcmChunk, start_at: float) -> None:
        if chunk.sample_rate != self.rate:
            raise ValueError(f"chunk at {chunk.sample_rate} Hz, cable at {self.rate} Hz")
        pcm = np.frombuffer(chunk.pcm, dtype="<i2").astype(np.float32) / 32768.0
        self._items.append(_Item(chunk, start_at, pcm))

    def clear(self) -> None:
        self._clear_req = True

    # --- control ---------------------------------------------------------------------

    def kill(self, t_key: float | None = None) -> None:
        """F9: drop all TTS and latch live voice; effective on the next callback (≤ one block)."""
        self._kill_t = now() if t_key is None else t_key
        self._clear_req = True
        self.latched = True
        if self._state != State.OFF:
            self._set_state(State.PASSTHROUGH, "kill")

    def unlatch(self) -> None:
        """F9 again: back to interpreting without restarting the call."""
        self.latched = False
        if self._state == State.PASSTHROUGH:
            self._set_state(self.rest, "unlatch")

    def set_rest(self, rest: State) -> None:
        """Change the rest state; takes effect now if the mixer is resting (ASR down → live voice)."""
        before, self.rest = self.rest, rest
        if self._state == before and not self.latched:
            self._set_state(rest, "rest")

    def release(self, why: str) -> None:
        """Back to rest right now, buffers emptied (watchdog); a later chunk may retake the cable."""
        self._clear_req = True
        if self._state == State.TTS:
            self._set_state(self.rest, why)

    def toggle_mute(self) -> None:
        if self._state == State.MUTE:
            self._set_state(self._before_mute, "unmute")
        elif self._state != State.OFF:
            self._before_mute = self._state
            self._set_state(State.MUTE, "mute")

    def busy(self) -> bool:
        return self._cur is not None or bool(self._items)

    def release_if_idle(self, in_flight: int, t: float | None = None) -> bool:
        """TTS → rest when nothing plays, nothing is queued or in flight, for RELEASE_AFTER_S."""
        t = now() if t is None else t
        if (self._state == State.TTS and in_flight == 0 and not self.busy()
                and self.tts_idle_since is not None and t - self.tts_idle_since >= RELEASE_AFTER_S):
            self._set_state(self.rest, "idle")
            return True
        return False

    def _set_state(self, to: State, why: str) -> None:
        frm, self._state = self._state, to
        if frm != to:
            self._emit("state", frm=frm.value, to=to.value, why=why)

    # --- audio thread ------------------------------------------------------------------

    def _read_mic(self, frames: int) -> np.ndarray | None:
        if self.mic is None:
            return None
        self.mic.drop_to(frames + int(self.rate * PASSTHROUGH_MAX_S))
        x = self.mic.read(frames)
        if len(x) < frames:
            self.passthrough_underruns += 1
            x = np.concatenate([np.zeros(frames - len(x), np.float32), x])
        return x

    def _callback(self, outdata, frames, time_info, status) -> None:
        t_cb = now()
        self.t_callback = t_cb
        if status.output_underflow:
            self.cable_gaps += 1
        # the latch also covers a chunk handed over between the F9 key and the scheduler's flush
        if self._clear_req or (self.latched and (self._items or self._cur)):
            self._clear_req = False
            self._items.clear()
            self._cur = None
            self.tts_idle_since = t_cb
        mic = self._read_mic(frames)          # always drained, so live voice stays current
        state0 = self._state
        outdata.fill(0.0)
        out = outdata[:, 0]
        if state0 in (State.OFF, State.MUTE):
            self.frames_out += frames
            return

        tts_from = frames                     # first sample of this block that belongs to the TTS
        pos = 0
        while pos < frames:
            if self._cur is None:
                if not self._items:
                    break
                head = self._items[0]
                off = int((head.start_at - t_cb) * self.rate)
                if off >= frames:
                    break
                off = max(off, pos)
                self._items.popleft()
                head.actual_start = t_cb + off / self.rate
                # a chunk scheduled back-to-back with the previous one that starts late = audible gap
                if head.start_at <= self._last_end + 0.002 and head.actual_start - head.start_at > 2 * self.block / self.rate:
                    self.cable_gaps += 1
                self._cur = head
                tts_from = min(tts_from, off)
                pos = off
                if self._state in (State.SILENT, State.PASSTHROUGH):
                    self._set_state(State.TTS, "first_pcm")
                self._emit("play", phase="start", seq=head.chunk.seq, start_at=head.start_at,
                           actual_start=head.actual_start)
            cur = self._cur
            n = min(frames - pos, len(cur.pcm) - cur.pos)
            out[pos:pos + n] = cur.pcm[cur.pos:cur.pos + n]
            tts_from = min(tts_from, pos)
            cur.pos += n
            pos += n
            if cur.pos >= len(cur.pcm):
                end = t_cb + pos / self.rate
                self._last_end = end
                self.tts_idle_since = end
                self._emit("play", phase="end", seq=cur.chunk.seq, t_end=end,
                           bytes_written=cur.pos * 2, bytes_expected=len(cur.pcm) * 2,
                           cable_gaps=self.cable_gaps)
                self._cur = None
        if self._cur is not None:
            self.tts_idle_since = None

        if state0 == State.PASSTHROUGH and mic is not None:
            out[:tts_from] = mic[:tts_from]
        if self._kill_t is not None and state0 == State.PASSTHROUGH:
            # F9 budget is < 200 ms from key to the first live-voice block written
            self._emit("state", what="f9_effective", f9_latency_s=t_cb - self._kill_t)
            self._kill_t = None
        if self.channels > 1:
            outdata[:, 1] = out
        self.frames_out += frames
