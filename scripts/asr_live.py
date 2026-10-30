"""Live mic (or a WAV replayed at real time) → Muse Voice Transcribe; every event printed and logged.

    uv run python scripts/asr_live.py                         # mic, until Ctrl-C
    uv run python scripts/asr_live.py --seconds 60 --analyze  # 60 s monologue + ASR stats table
    uv run python scripts/asr_live.py --wav logs/monologo.wav --analyze   # same audio, re-measured
    uv run python scripts/asr_live.py --record-only --seconds 60          # just record the mic (no key needed)

The 16 kHz audio actually sent is saved next to the JSONL, so a run can be re-analyzed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from interprete import config
from interprete.asr.meta_realtime import MetaRealtimeAsr
from interprete.audio.capture import MicCapture, QueueFeeder, To16k
from interprete.audio.fixture import RATE, read_wav, replay, save_wav
from interprete.clock import now
from interprete.contracts import AsrEvent, AsrKind, Channel
from interprete.metrics import asr_stats
from interprete.metrics.jsonl import Metrics

class Tap:
    """Keeps the PCM sent to the ASR and the t_capture of its first sample."""

    def __init__(self) -> None:
        self.t0: float | None = None
        self.buf = bytearray()

    def add(self, t_capture: float, pcm: bytes) -> None:
        if self.t0 is None:
            self.t0 = t_capture
        self.buf += pcm


async def wav_source(pcm: bytes, q: asyncio.Queue, tap: Tap, stop: asyncio.Event) -> None:
    await replay(pcm, q, stop, tap.add)
    await asyncio.sleep(3.0)                      # let the last turn complete
    stop.set()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--wav", default=None)
    ap.add_argument("--analyze", action="store_true")
    ap.add_argument("--record-only", action="store_true")
    ap.add_argument("--mode", default="ENDPOINTING", choices=["PUSH_TO_TALK", "ENDPOINTING", "DIARIZATION"])
    ap.add_argument("--language-bias", action="append", default=None)
    args = ap.parse_args()
    settings = config.load()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    metrics = Metrics(Path("logs") / f"asr_{stamp}.jsonl")
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=250)          # ≤ 5 s of 20 ms blocks
    stop = asyncio.Event()
    tap = Tap()
    events: list[dict] = []
    mic = None

    if args.wav:
        source = asyncio.create_task(wav_source(read_wav(args.wav), q, tap, stop))
    else:
        mic = MicCapture(settings.mic_name, emit=metrics.emit)
        mic.start()
        to16 = To16k(mic.rate)
        feeder = QueueFeeder(loop, q, to16)
        if not args.record_only:
            mic.add_consumer(feeder)
        # the tap keeps exactly what was sent, re-converted on its own resampler instance
        to16_tap = To16k(mic.rate)
        mic.add_consumer(lambda t, b: loop.call_soon_threadsafe(tap.add, t, to16_tap(b)))
        print(f"mic: {mic.device.name} [{mic.device.hostapi}] {mic.rate} Hz -> 16 kHz", file=sys.stderr)
        source = None
        if args.seconds:
            loop.call_later(args.seconds, stop.set)

    if args.record_only:
        print(f"recording {args.seconds or 60:.0f} s ... speak now", file=sys.stderr)
        if not args.seconds:
            loop.call_later(60, stop.set)
        await stop.wait()
        mic.stop()
        path = Path("logs") / f"mic_{stamp}.wav"
        save_wav(path, bytes(tap.buf))
        print(f"saved {path} ({len(tap.buf) / 2 / RATE:.1f} s)")
        metrics.close()
        return 0

    asr = MetaRealtimeAsr(Channel.OUT, config.require_api_key(), mode=args.mode,
                          language_bias=args.language_bias, emit=metrics.emit)
    width = max(40, shutil.get_terminal_size((110, 24)).columns - 1)
    t_ref = now()

    def on_event(ev: AsrEvent) -> None:
        lag = asr.clock.lag(ev.t_recv, ev.t_asr_s)
        if ev.kind is not AsrKind.PROGRESS:
            events.append(dict(kind=ev.kind.value, turn_id=ev.turn_id, t_recv=ev.t_recv, t_asr_s=ev.t_asr_s,
                               lag_s=lag, text=ev.text))
        head = f"{ev.t_recv - t_ref:7.2f}s"
        ms = f"{int(ev.t_asr_s * 1000):>6}ms" if ev.t_asr_s is not None else "      --"
        lg = f"lag {lag * 1000:5.0f}ms" if lag is not None else "         "
        if ev.kind is AsrKind.PARTIAL:
            print(f"\r{head} {ms} {lg} … {ev.text}"[:width].ljust(width), end="", flush=True)
        elif ev.kind is AsrKind.PROGRESS:
            if ev.raw.get("type") == "session":
                print(f"{head} session {ev.session_id} (token form: {asr.token_form})")
        else:
            print(f"\r{head} {ms} {lg} {ev.kind.value:<14} turn={ev.turn_id} {ev.text}".ljust(width))

    asr_task = asyncio.create_task(asr.run(q, on_event, stop))
    try:
        await stop.wait()
        await asyncio.wait_for(asr_task, 8.0)
    except (KeyboardInterrupt, asyncio.CancelledError):
        stop.set()
    except TimeoutError:
        asr_task.cancel()
    finally:
        if mic:
            mic.stop()
    wav_path = Path("logs") / f"asr_{stamp}.wav"
    save_wav(wav_path, bytes(tap.buf))
    print(f"\naudio sent: {wav_path} ({len(tap.buf) / 2 / RATE:.1f} s); events: {metrics.path}")

    if args.analyze and tap.t0 is not None:
        x = np.frombuffer(bytes(tap.buf), "<i2").astype(np.float32) / 32768
        segs = asr_stats.vad_segments(x, RATE, tap.t0)
        summary = asr_stats.summarize(events, segs)
        summary["source"] = args.wav or "mic"
        summary["token_form"] = asr.token_form
        metrics.emit("run", summary=summary)
        print(json.dumps(summary, indent=2))
    metrics.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        pass
