"""See a recorded session again with nothing but `uv sync`: no key, no VB-CABLE, no OBS, on any OS.

    uv run python -m interprete.replay                   # the interview in demo/interview
    uv run python -m interprete.replay demo/other        # another session, or RTT_REPLAY=demo/other
    uv run python -m interprete.replay --no-overlay      # subtitles in the console
    uv run python -m interprete.replay --wav out.wav     # into a file instead of the speakers

A session is what a real run logged (scripts/export_session.py): when each word was heard and each
clause committed, what Spark answered, on both sides, and the audio that went in. The replay hands
those clauses to the same scheduler at the moments they arrived, and the English voice is
synthesized again, here, by MMS-TTS. It plays on the default speakers instead of the cable, with the
subtitle band on screen. Nothing reaches Meta's API: listening and translating were done when it was
recorded, and the session says when and with which commit.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import wave
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import soxr

from . import config
from .clock import now
from .contracts import Channel, PcmChunk
from .policy.scheduler import Scheduler
from .tts.mms import MmsVoice
from .tts.shaping import FAST_RATE_PCT

DEMO = config.ROOT / "demo" / "interview"
TRACK_RATE = 16000
LEAD_S = 1.5                   # from the voice loaded to the first event
TAIL_S = 2.0                   # after the last sound, before closing
MY_GAIN_DB = -14.0             # my Spanish under the English: a reference for the listener, not what the other side hears
THEIR_GAIN_DB = -3.0
CATCH_UP_BACKLOG_S = 3.0       # as the app: behind by this much, the next clause is spoken faster
CATCH_UP_SYNC_S = 0.5
BLOCK_S = 0.01


@dataclass
class Session:
    folder: Path
    events: list[dict]
    about: dict
    tracks: dict[str, tuple[np.ndarray, float]] = field(default_factory=dict)   # channel -> (16 kHz audio, start)

    @property
    def delay_s(self) -> float:
        return float(self.about.get("delay_s") or 0.0)

    @property
    def length(self) -> float:
        times = [_when(e) for e in self.events]
        ends = [t + len(x) / TRACK_RATE for x, t in self.tracks.values()]
        return max(times + ends + [0.0])


def _when(e: dict) -> float:
    return e.get("t_recv", e.get("t_commit", e.get("t_mt", e.get("t", e.get("t_start", 0.0)))))


def _read_track(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, TRACK_RATE):
            raise SystemExit(f"{path}: a session track is mono 16-bit 16 kHz")
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768.0


def load(folder: Path) -> Session:
    if not (folder / "session.jsonl").exists():
        raise SystemExit(f"No session in {folder}: it needs session.jsonl, about.json and the WAVs (scripts/export_session.py).")
    with open(folder / "session.jsonl", encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]
    about_path = folder / "about.json"
    about = json.loads(about_path.read_text(encoding="utf-8")) if about_path.exists() else {}
    starts = {e["what"]: e["t_start"] for e in events if e.get("ev") == "state"}
    session = Session(folder, events, about)
    for channel, what, name in (("out", "fixture_start", "out.wav"), ("in", "fixture_start_in", "in.wav")):
        if what in starts and (folder / name).exists():
            session.tracks[channel] = (_read_track(folder / name), starts[what])
    return session


class Speakers:
    """The mixer for a replay: the default output instead of the cable, and the tracks that went in, under the voice.

    Chunks and tracks arrive through a deque, as in the cable mixer; once one starts it advances by samples, never by
    the clock again, so blocks join without a click. With `file` the same callback runs on a thread at the same pace
    and what it makes goes into a WAV.
    """

    def __init__(self, *, file: Path | None = None, rate: int | None = None, clock: Callable[[], float] = now) -> None:
        self.file = file
        self.clock = clock
        self._sd = None
        if file is None:
            import sounddevice as sd                 # here, not at import: a file needs no audio device

            self._sd = sd
            device = sd.query_devices(kind="output")
            self.rate = rate or int(device["default_samplerate"])
            self.channels = max(1, min(2, int(device["max_output_channels"])))
        else:
            self.rate = rate or 48000
            self.channels = 1
        self.block = int(self.rate * BLOCK_S)
        self._new: deque[list] = deque()             # [samples, start_at, gain, pos]
        self._active: list[list] = []
        self._clear = False
        self._stream = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._written: list[np.ndarray] = []
        self.underflows = 0

    # --- what the scheduler sees ---------------------------------------------------------

    def play(self, chunk: PcmChunk, start_at: float) -> None:
        self.add(np.frombuffer(chunk.pcm, "<i2").astype(np.float32) / 32768.0, start_at, 1.0)

    def clear(self) -> None:
        self._clear = True

    # --- the replay ------------------------------------------------------------------------

    def add(self, samples: np.ndarray, start_at: float, gain: float) -> None:
        self._new.append([samples, start_at, gain, None])

    def busy(self) -> bool:
        return bool(self._new or self._active)

    def start(self) -> None:
        self._running = True
        if self._sd is not None:
            self._stream = self._sd.OutputStream(samplerate=self.rate, channels=self.channels, dtype="float32",
                                                 blocksize=self.block, callback=self._callback)
            self._stream.start()
        else:
            self._thread = threading.Thread(target=self._pace, name="replay-file", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            with wave.open(str(self.file), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(self.rate)
                x = np.concatenate(self._written) if self._written else np.zeros(0, np.float32)
                w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())

    def _pace(self) -> None:
        out = np.zeros((self.block, 1), np.float32)
        t_next = self.clock()
        while self._running:
            self._callback(out, self.block, None, None)
            self._written.append(out[:, 0].copy())
            t_next += self.block / self.rate
            delay = t_next - self.clock()
            if delay > 0:
                threading.Event().wait(delay)

    def _callback(self, outdata, frames, time_info, status) -> None:
        t = self.clock()
        if status is not None and status.output_underflow:
            self.underflows += 1
        if self._clear:
            self._clear = False
            self._new.clear()
            self._active.clear()
        while self._new:
            self._active.append(self._new.popleft())
        mix = np.zeros(frames, np.float32)
        keep = []
        for item in self._active:
            samples, start_at, gain, pos = item
            if pos is None:
                off = int(round((start_at - t) * self.rate))
                if off >= frames:
                    keep.append(item)
                    continue
                j, pos = max(off, 0), max(-off, 0)   # a track that should have begun already joins where it is
            else:
                j = 0
            n = min(frames - j, len(samples) - pos)
            if n > 0:
                mix[j:j + n] += gain * samples[pos:pos + n]
                pos += n
            if pos < len(samples):
                item[3] = pos
                keep.append(item)
        self._active = keep
        outdata[:] = np.clip(mix, -1.0, 1.0)[:, None]


class ConsoleUi:
    """The band, as lines: only what is final, or the console would scroll every partial."""

    def mine(self, es: str, en: str = "") -> None:
        if en:
            print(f"  me    {es}\n        → {en}")

    def their_partial(self, text: str) -> None:
        pass

    def their_final(self, text: str) -> None:
        print(f"  them  {text}")

    def their_translation(self, tgt: str) -> None:
        print(f"        → {tgt}")

    def their_failed(self, src: str) -> None:
        print(f"        (sin traducción) {src}")


class Replay:
    def __init__(self, session: Session, voice, sink, ui, *, lead_s: float = LEAD_S, tail_s: float = TAIL_S) -> None:
        self.session = session
        self.voice = voice
        self.sink = sink
        self.ui = ui
        self.lead_s = lead_s
        self.tail_s = tail_s
        self.sched = Scheduler(sink, session.delay_s, emit=self._on_sched)
        self.commits = {(e["channel"], e["seq"]): e for e in session.events if e.get("ev") == "commit"}
        self.played: list[int] = []
        self.turn = ""
        self.t_base = 0.0

    def _on_sched(self, ev: str, **f) -> None:
        if ev == "play" and f.get("t_play_start") is not None:
            self.played.append(f["seq"])

    def local(self, t: float | None) -> float | None:
        return None if t is None else self.t_base + t

    async def run(self) -> None:
        """The voice is loaded first (`_run`): the first clause must not pay for it, nor the clock wait for it."""
        self.t_base = now() + self.lead_s
        for channel, (samples, t_start) in self.session.tracks.items():
            gain = 10 ** ((MY_GAIN_DB if channel == "out" else THEIR_GAIN_DB) / 20)
            self.sink.add(soxr.resample(samples, TRACK_RATE, self.sink.rate), self.local(t_start), gain)
        runner = asyncio.create_task(self.sched.run())
        speaking: list[asyncio.Task] = []
        try:
            for e in sorted(self.session.events, key=_when):
                delay = self.local(_when(e)) - now()
                if delay > 0:
                    await asyncio.sleep(delay)
                task = self.act(e)
                if task is not None:
                    speaking.append(asyncio.create_task(task))
            await asyncio.gather(*speaking)
            end = self.local(self.session.length)
            while now() < end or self.sched.in_flight() or self.sink.busy():
                await asyncio.sleep(0.1)
            await asyncio.sleep(self.tail_s)
        finally:
            runner.cancel()

    def act(self, e: dict):
        """Does what the run did at this moment; a clause to speak comes back as a coroutine, so others go on."""
        out = e.get("channel") == Channel.OUT.value
        if e["ev"] == "asr":
            if out:
                self.ui.mine(e["text"])
            elif e["kind"] == "speechComplete":
                self.ui.their_final(e["text"])
            else:
                self.ui.their_partial(e["text"])
        elif e["ev"] == "commit" and out:
            self.sched.expect(e["seq"], e["utterance_id"], self.local(e["t_commit"]), self.local(e["t_src_start"]))
        elif e["ev"] == "mt":
            c = self.commits.get((e["channel"], e["seq"]))
            if c is None:
                return None
            if out:
                if e.get("text_tgt"):
                    self.ui.mine(c["text_src"], e["text_tgt"])
                    return self.speak(c, e["text_tgt"])
                self.sched.tombstone(c["seq"], e.get("failed", "mt"))
            elif e.get("text_tgt"):
                self.turn = e["text_tgt"] if c["commit_id"] == 0 else f"{self.turn} {e['text_tgt']}"
                self.ui.their_translation(self.turn)
            else:
                self.ui.their_failed(c["text_src"])
        return None

    async def speak(self, c: dict, text: str) -> None:
        t_src_start = self.local(c["t_src_start"])
        deadline = self.sched.deadline_for(t_src_start)
        t = now()
        late = (self.sched.lateness(deadline, t) > CATCH_UP_SYNC_S if self.session.delay_s > 0
                else self.sched.backlog_s(t) > CATCH_UP_BACKLOG_S)
        src_s = None if c.get("t_src_end") is None else c["t_src_end"] - c["t_src_start"]
        rate_pct = FAST_RATE_PCT if late else self.voice.rate_for(text, src_s)
        r = await self.voice.say(text, rate_pct=rate_pct)
        if not r.pcm:
            self.sched.tombstone(c["seq"], "tts:empty")
            return
        self.sched.submit(PcmChunk(utterance_id=c["utterance_id"], commit_id=c["commit_id"], seq=c["seq"], channel=Channel.OUT,
                                   pcm=r.pcm, sample_rate=r.sample_rate, duration_s=r.duration_s, t_src_start=t_src_start,
                                   t_src_end=self.local(c.get("t_src_end")), deadline=deadline, stretch_ratio=100 / (100 + rate_pct),
                                   t_tts_first=r.t_first, t_tts_end=r.t_ready))


def describe(session: Session) -> str:
    a = session.about
    when = str(a.get("recorded", "?")).replace("T", " ")[:16]
    measured = a.get("measured", {}).get("out", {}).get("speech_end_to_first_pcm_s", {})
    lag = f", the English {measured['p50']:.1f} s after each phrase ended (p50)" if measured.get("p50") else ""
    return (f"Replaying {session.folder.name}, recorded {when} with commit {a.get('commit', '?')}: Muse Voice Transcribe "
            f"and Muse Spark did the listening and translating then{lag}. MMS-TTS makes the English voice again on this PC. "
            f"You hear my Spanish {abs(MY_GAIN_DB):.0f} dB under the English, and the other side in English. Ctrl-C stops.")


async def _run(session: Session, ui, args) -> int:
    settings = config.load()
    sink = Speakers(file=Path(args.wav) if args.wav else None)
    voice = MmsVoice(settings.mms_model, sink.rate, speaking_rate=settings.mms_speaking_rate)
    replay = Replay(session, voice, sink, ui)
    print("Loading the voice (the first run downloads about 150 MB)…", flush=True)
    await voice.start()
    sink.start()
    try:
        await replay.run()
    finally:
        sink.stop()
    spoken = sum(1 for e in session.events if e.get("ev") == "mt" and e.get("channel") == "out" and e.get("text_tgt"))
    print(f"Done: {len(replay.played)} of {spoken} English clauses spoken" + (f", written to {args.wav}" if args.wav else "") + ".",
          flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay a recorded session: no key, no VB-CABLE, no OBS.")
    ap.add_argument("session", nargs="?", default=os.environ.get("RTT_REPLAY") or str(DEMO), help="a folder made by export_session.py")
    ap.add_argument("--no-overlay", action="store_true", help="subtitles in the console instead of on screen")
    ap.add_argument("--wav", default=None, help="write what would play into this WAV instead of the speakers")
    args = ap.parse_args(argv)
    session = load(Path(args.session))
    print(describe(session), flush=True)
    if args.no_overlay:
        return asyncio.run(_run(session, ConsoleUi(), args))

    from PySide6.QtWidgets import QApplication
    from qasync import QEventLoop

    from .ui.overlay import Overlay

    qt = QApplication([sys.argv[0]])
    overlay = Overlay()
    overlay.show()
    loop = QEventLoop(qt)
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(_run(session, overlay, args))


if __name__ == "__main__":
    code = main()
    # Done and written. Skip interpreter shutdown, as the app does: native threads (Qt, PortAudio) torn down while
    # Python finalizes ended a full replay with 0xC0000409 after its last line.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code or 0)
