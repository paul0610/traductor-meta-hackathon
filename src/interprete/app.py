"""The interpreter, both directions, consecutive mode.

OUT: mic → Muse Voice Transcribe → Muse Spark → Edge TTS → scheduler → CABLE Input (the other side only hears English)
IN:  system loopback → Muse Voice Transcribe → Muse Spark → subtitle overlay

    uv run python -m interprete.app                  # until Ctrl-C
    uv run python -m interprete.app --record-cable   # also record CABLE Output (what the call app hears)
    uv run python -m interprete.app --wav-out es.wav --wav-in en.wav   # fixtures instead of mic and loopback
    uv run python -m interprete.app --simulate       # no API key: canned commits exercise TTS → cable

F7 initial notice · F8 mute · F9 live voice on/off · F10 physical mic back as Windows default.
Every mixer transition is a `state` event in the JSONL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import openai
import soxr

from . import config
from .asr.meta_realtime import MetaRealtimeAsr
from .audio import devices
from .audio.cable_mixer import CableMixer, State
from .audio.capture import MicCapture, QueueFeeder, To16k
from .audio.device_restore import endpoint_volume, restore_mic
from .audio.fixture import read_wav, replay
from .audio.loopback import LoopbackCapture
from .audio.recorder import Recorder
from .audio.ring import Ring
from .clock import now
from .contracts import AsrEvent, AsrKind, Channel, Commit, PcmChunk
from .metrics.jsonl import Metrics, read
from .metrics.summary import summarize
from .mt.spark import Spark
from .policy.consecutivo import Consecutive
from .policy.scheduler import MT_TIMEOUT_S, TTS_TIMEOUT_S, Scheduler
from .safety import ExitGuard, Watchdog
from .tts.edge import synthesize_hedged

MT_ERRORS = (TimeoutError, openai.APIError, json.JSONDecodeError, KeyError)
ASR_FALLBACK_S = 3.0            # ASR down this long: live voice, nothing can be translated
CABLE_MIN_VOLUME = 0.95
NOTICE_EN = ("Hi, a quick note before we start: I speak Spanish and you are hearing an AI interpreter. "
             "It runs a few seconds behind me, so please give me a moment after you finish speaking.")
SIM_LINES = [
    ("Buenos días, gracias por recibirme.", "Good morning, thanks for having me."),
    ("Tengo cinco años de experiencia en datos.", "I have five years of experience in data."),
    ("¿Podrías repetir la pregunta, por favor?", "Could you repeat the question, please?"),
]


class NoUi:
    """Stands in for the overlay in `--no-overlay` runs."""

    def __getattr__(self, name):
        return lambda *a: None


class Outgoing:
    def __init__(self, args, settings: config.Settings, metrics: Metrics, ui, stop: asyncio.Event) -> None:
        self.args = args
        self.settings = settings
        self.metrics = metrics
        self.emit = metrics.emit
        self.ui = ui
        self.stop = stop
        self.loop = asyncio.get_running_loop()
        self.mixer = CableMixer(settings.cable_name, emit=self.emit)
        self.sched = Scheduler(self.mixer, args.delay, emit=self._on_sched)
        self.watchdog = Watchdog(self.mixer, self.sched, emit=self.emit)
        self.mic = MicCapture(settings.mic_name, emit=self.emit)
        self.q_pcm: asyncio.Queue = asyncio.Queue(maxsize=250)
        self.q_commits: asyncio.Queue[Commit] = asyncio.Queue()
        self.q_translated: asyncio.Queue[Commit] = asyncio.Queue()
        self.history: list[tuple[str, str]] = []
        self.timings: dict[int, dict] = {}
        self.lags: list[float] = []
        self.cable_warning = ""
        self.t_start = now()
        self.asr: MetaRealtimeAsr | None = None
        self.spark: Spark | None = None
        self.policy: Consecutive | None = None

    # --- wiring --------------------------------------------------------------------

    def setup(self, key: str, spark: Spark) -> None:
        self.asr = MetaRealtimeAsr(Channel.OUT, key, mode="ENDPOINTING", emit=self.emit)
        self.spark = spark
        self.policy = Consecutive(Channel.OUT, self.asr.clock.to_local, emit=self.emit)

    def start_audio(self) -> None:
        if not self.args.wav_out:
            self.mic.start()
            ring = Ring(self.mixer.rate)
            if self.mic.rate == self.mixer.rate:
                self.mic.add_consumer(lambda t, b: ring.write(b))
            else:                                   # passthrough needs the cable's rate
                rs = soxr.ResampleStream(self.mic.rate, self.mixer.rate, 1, dtype="float32")
                self.mic.add_consumer(lambda t, b: ring.write(rs.resample_chunk(b)))
            self.mixer.mic = ring
            if not self.args.simulate:
                self.mic.add_consumer(QueueFeeder(self.loop, self.q_pcm, To16k(self.mic.rate)))
            print(f"mic   {self.mic.device.name} [{self.mic.device.hostapi}] {self.mic.rate} Hz", file=sys.stderr)
        self.mixer.start()
        print(f"cable {self.mixer.device.name} [{self.mixer.device.hostapi}] {self.mixer.rate} Hz "
              f"{self.mixer.channels} ch, state {self.mixer.state.value}", file=sys.stderr)
        self.check_cable_level()

    def check_cable_level(self) -> None:
        """A low Windows slider scales the whole voice before the call app sees it; nothing else would show it."""
        vol = endpoint_volume(self.settings.cable_name)
        if vol is None:
            return
        self.emit("state", what="cable_volume", volume=vol[0], mute=vol[1])
        if vol[0] < CABLE_MIN_VOLUME or vol[1]:
            self.cable_warning = f" │ CABLE {vol[0] * 100:.0f} %{' MUDO' if vol[1] else ''}"
            print(f"AVISO: {self.settings.cable_name} está al {vol[0] * 100:.0f} %{' y mudo' if vol[1] else ''} en Windows: "
                  "el otro lado te oirá muy bajo. Súbelo a 100 en Sonido > Salida > CABLE Input.", file=sys.stderr)

    def _on_sched(self, ev: str, **f) -> None:
        self.emit(ev, **f)
        row = self.timings.get(f.get("seq"))
        if row is not None and f.get("t_play_start") is not None:
            row["t_play_start"] = f["t_play_start"]
            self.lags.append(f["t_play_start"] - row["t_commit"])

    def idle(self) -> bool:
        return self.sched.in_flight() == 0 and not self.mixer.busy()

    # --- tasks ---------------------------------------------------------------------

    def on_asr_event(self, ev: AsrEvent) -> None:
        if ev.kind is AsrKind.SPEECH_COMPLETE:
            print(f"\r[es] {ev.text}".ljust(100))
            self.ui.mine(ev.text)
        elif ev.kind is AsrKind.PARTIAL:
            self.ui.mine(ev.text)
            if self.args.no_overlay:
                print(f"\r… {ev.text}"[:100].ljust(100), end="", flush=True)
        commit = self.policy.on_event(ev)
        if commit is not None:
            self.submit_commit(commit)

    def submit_commit(self, c: Commit) -> None:
        self.sched.expect(c.seq, c.utterance_id, c.t_commit, c.t_src_start)
        self.timings[c.seq] = dict(seq=c.seq, text_src=c.text_src, t_commit=c.t_commit, t_src_start=c.t_src_start,
                                   t_audio=c.t_src_start, t_src_end=c.t_src_end, t_src_source=c.t_src_source)
        if self.mixer.latched:                       # F9 is on: nothing takes the cable until it is released
            self.sched.tombstone(c.seq, "latched")
            return
        self.q_commits.put_nowait(c)

    async def mt_worker(self) -> None:
        while True:
            c = await self.q_commits.get()
            if self.sched.is_cancelled(c.utterance_id):
                self.sched.tombstone(c.seq, "cancelled")
                continue
            if c.text_tgt:                           # notice and simulate mode: already in English
                await self.q_translated.put(c)
                continue
            try:
                r = await asyncio.wait_for(self.spark.translate(c.text_src, history=self.history[-4:]), MT_TIMEOUT_S)
            except MT_ERRORS as exc:
                self.sched.tombstone(c.seq, f"mt:{type(exc).__name__}")
                self.emit("mt", channel="out", seq=c.seq, utterance_id=c.utterance_id, error=f"{type(exc).__name__}: {exc}"[:300])
                print(f"\r[mt] dropped seq {c.seq}: {type(exc).__name__}".ljust(100))
                continue
            c.text_tgt, c.t_mt, c.mt_calls = r.translation, r.t_end, 1
            self.history.append((c.text_src, c.text_tgt))
            self.timings[c.seq].update(t_mt=r.t_end, mt_s=r.t_end - r.t_start, text_tgt=c.text_tgt)
            self.emit("mt", channel="out", seq=c.seq, utterance_id=c.utterance_id, t_mt=r.t_end, mt_s=r.t_end - r.t_start,
                      text_tgt=c.text_tgt, mt_calls_per_commit=1, reasoning_tokens=r.reasoning_tokens,
                      effort=r.effort)
            print(f"\r[en] {c.text_tgt}".ljust(100))
            self.ui.mine(c.text_src, c.text_tgt)
            await self.q_translated.put(c)

    async def tts_worker(self) -> None:
        while True:
            c = await self.q_translated.get()
            if self.sched.is_cancelled(c.utterance_id):
                self.sched.tombstone(c.seq, "cancelled")
                continue
            def on_hedge() -> None:
                self.emit("tts", channel="out", seq=c.seq, utterance_id=c.utterance_id, hedged=True)

            try:
                r = await asyncio.wait_for(synthesize_hedged(c.text_tgt, self.settings.tts_voice, self.mixer.rate, on_hedge),
                                           TTS_TIMEOUT_S)
            except Exception as exc:                 # network, decode, timeout: never stall the queue
                self.sched.tombstone(c.seq, f"tts:{type(exc).__name__}")
                self.emit("tts", channel="out", seq=c.seq, utterance_id=c.utterance_id, error=f"{type(exc).__name__}: {exc}"[:300])
                continue
            if not r.pcm:
                self.sched.tombstone(c.seq, "tts:empty")
                continue
            chunk = PcmChunk(
                utterance_id=c.utterance_id, commit_id=c.commit_id, seq=c.seq, channel=c.channel,
                pcm=r.pcm, sample_rate=r.sample_rate, duration_s=r.duration_s,
                t_src_start=c.t_src_start, t_src_end=c.t_src_end,
                deadline=self.sched.deadline_for(c.t_src_start), t_tts_first=r.t_first, t_tts_end=r.t_ready,
            )
            self.timings[c.seq].update(t_tts_first=r.t_first, t_tts_end=r.t_ready, tts_ttfb_s=r.t_first - r.t_request,
                                       duration_s=r.duration_s)
            self.emit("tts", channel="out", seq=c.seq, utterance_id=c.utterance_id, t_tts_first=r.t_first, t_tts_end=r.t_ready,
                      ttfb_s=r.t_first - r.t_request, synth_s=r.t_end - r.t_request, duration_s=r.duration_s,
                      trimmed_lead_s=r.trimmed_lead_s, trimmed_tail_s=r.trimmed_tail_s)
            self.sched.submit(chunk)

    def fallback(self, t: float) -> None:
        """ASR down for a while: nothing can be translated, so the other side at least hears me."""
        down = self.asr is not None and self.asr.t_down is not None and t - self.asr.t_down > ASR_FALLBACK_S
        rest = State.PASSTHROUGH if down else State.SILENT
        if rest != self.mixer.rest:
            self.mixer.set_rest(rest)

    def bar_text(self, t: float) -> str:
        lag = f"{self.lags[-1]:.1f}s p95 {np.percentile(self.lags, 95):.1f}s" if self.lags else "--"
        link = " │ ASR CAÍDO" if self.asr is not None and self.asr.t_down is not None else ""
        return (f"LAG {lag} │ CORR 0 │ ES→EN {self.mixer.state.value}{link}{self.cable_warning}"
                f" │ META {(t - self.t_start) / 60:.0f} min")

    async def controller(self) -> None:
        """TTS → rest once idle, watchdog, overlay line: all need the scheduler, so none lives in the callback."""
        t_bar = 0.0
        while True:
            t = now()
            self.mixer.release_if_idle(self.sched.in_flight(), t)
            self.watchdog.check(t)
            self.fallback(t)
            if t >= t_bar:
                t_bar = t + 0.5
                self.ui.bar(self.bar_text(t))
            await asyncio.sleep(0.05)

    def send_notice(self) -> None:
        if self.policy is None or self.mixer.latched:
            return
        t = now()
        seq = self.policy.take_seq()
        self.submit_commit(Commit(utterance_id=f"out:notice:{seq}", commit_id=0, seq=seq, channel=Channel.OUT,
                                  text_src="(aviso inicial)", prefix_src="", boundary="notice", t_src_start=t,
                                  t_src_end=None, t_src_source="notice", t_commit=t, text_tgt=NOTICE_EN))
        self.emit("state", what="notice", seq=seq)

    async def simulate(self, on_f9) -> None:
        await asyncio.sleep(1.0)
        for i, (es, en) in enumerate(SIM_LINES):
            t = now()
            c = Commit(utterance_id=f"out:sim:{i}", commit_id=0, seq=i, channel=Channel.OUT, text_src=es,
                       prefix_src="", boundary="speechComplete", t_src_start=t - 2.0, t_src_end=t - 0.3,
                       t_src_source="simulated", t_commit=t, text_tgt=en)
            self.submit_commit(c)
            await asyncio.sleep(4.0)
        if self.args.sim_f9:
            # one more commit, then F9 while it plays: measures the kill switch end to end
            t = now()
            c = Commit(utterance_id="out:sim:f9", commit_id=0, seq=len(SIM_LINES), channel=Channel.OUT,
                       text_src="...", prefix_src="", boundary="speechComplete", t_src_start=t, t_src_end=t,
                       t_src_source="simulated", t_commit=t,
                       text_tgt="This sentence is long on purpose so the kill switch interrupts it halfway through.")
            self.submit_commit(c)
            while self.mixer.state.value != "TTS":
                await asyncio.sleep(0.01)
            await asyncio.sleep(1.0)
            on_f9()
            await asyncio.sleep(1.0)
        await asyncio.sleep(1.0)
        self.stop.set()

    def tasks(self) -> dict:
        jobs = {"out.sched": self.sched.run(), "out.mt": self.mt_worker(), "out.tts": self.tts_worker(),
                "out.controller": self.controller()}
        if self.asr is not None:
            jobs["out.asr"] = self.asr.run(self.q_pcm, self.on_asr_event, self.stop)
        return jobs

    def close(self) -> None:
        self.mixer.stop()
        self.mic.stop()

    def report(self) -> None:
        for r in self.timings.values():
            if r["t_src_source"] != "notice":        # no ASR or Spark behind it: it would flatter the latency
                self.emit("commit", phase="report", channel="out", **r)
        self.emit("run", seq_skipped=self.sched.seq_skipped, cable_gaps=self.mixer.cable_gaps,
                  watchdog_trips=self.watchdog.trips)


class Incoming:
    def __init__(self, args, metrics: Metrics, ui, stop: asyncio.Event) -> None:
        self.args = args
        self.emit = metrics.emit
        self.ui = ui
        self.stop = stop
        self.loop = asyncio.get_running_loop()
        self.q_pcm: asyncio.Queue = asyncio.Queue(maxsize=250)
        self.q_commits: asyncio.Queue[Commit] = asyncio.Queue()
        self.history: list[tuple[str, str]] = []
        self.rows: dict[int, dict] = {}
        self.pending = 0
        self.loopback: LoopbackCapture | None = None
        self.asr: MetaRealtimeAsr | None = None
        self.spark: Spark | None = None
        self.policy: Consecutive | None = None

    def setup(self, key: str, spark: Spark) -> None:
        self.asr = MetaRealtimeAsr(Channel.IN, key, mode="ENDPOINTING", emit=self.emit)
        self.spark = spark
        self.policy = Consecutive(Channel.IN, self.asr.clock.to_local, emit=self.emit)

    def start_audio(self) -> None:
        if self.args.wav_in:
            return
        self.loopback = LoopbackCapture(QueueFeeder(self.loop, self.q_pcm), emit=self.emit)
        self.loopback.start()

    def idle(self) -> bool:
        return self.pending == 0

    def on_asr_event(self, ev: AsrEvent) -> None:
        if ev.kind is AsrKind.SPEECH_COMPLETE:
            print(f"\r[them] {ev.text}".ljust(100))
            self.ui.their_final(ev.text)
        elif ev.kind is AsrKind.PARTIAL:
            self.ui.their_partial(ev.text)
        c = self.policy.on_event(ev)
        if c is not None:
            self.rows[c.seq] = dict(seq=c.seq, text_src=c.text_src, t_commit=c.t_commit, t_src_start=c.t_src_start,
                                    t_audio=c.t_src_start, t_src_end=c.t_src_end, t_src_source=c.t_src_source)
            self.pending += 1
            self.q_commits.put_nowait(c)

    async def mt_worker(self) -> None:
        while True:
            c = await self.q_commits.get()
            row = self.rows[c.seq]
            try:
                r = await asyncio.wait_for(self.spark.translate(c.text_src, src="en", tgt="es", history=self.history[-4:]),
                                           MT_TIMEOUT_S)
            except MT_ERRORS as exc:
                self.ui.their_failed(c.text_src)
                self.emit("mt", channel="in", seq=c.seq, utterance_id=c.utterance_id, error=f"{type(exc).__name__}: {exc}"[:300])
                self.pending -= 1
                continue
            self.history.append((c.text_src, r.translation))
            self.ui.their_translation(r.translation)
            print(f"\r[sub] {r.translation}".ljust(100))
            row.update(t_mt=r.t_end, t_shown=now(), mt_s=r.t_end - r.t_start, text_tgt=r.translation)
            self.emit("mt", channel="in", seq=c.seq, utterance_id=c.utterance_id, t_mt=r.t_end, mt_s=r.t_end - r.t_start,
                      text_tgt=r.translation, mt_calls_per_commit=1, reasoning_tokens=r.reasoning_tokens, effort=r.effort)
            self.pending -= 1

    def tasks(self) -> dict:
        return {"in.mt": self.mt_worker(), "in.asr": self.asr.run(self.q_pcm, self.on_asr_event, self.stop)}

    def close(self) -> None:
        if self.loopback is not None:
            self.loopback.stop()

    def report(self) -> None:
        for r in self.rows.values():
            self.emit("commit", phase="report", channel="in", **r)


class App:
    def __init__(self, args, settings: config.Settings, metrics: Metrics, ui) -> None:
        self.args = args
        self.metrics = metrics
        self.emit = metrics.emit
        self.ui = ui
        self.stop = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.out = None if args.in_only else Outgoing(args, settings, metrics, ui, self.stop)
        self.inc = None if args.out_only or args.simulate else Incoming(args, metrics, ui, self.stop)
        self.spark: Spark | None = None
        self.mic_name = devices.find(settings.mic_name, "input", ("CABLE",)).name     # the one F10 brings back
        self.tasks: list[asyncio.Task] = []
        self.guard = ExitGuard(self.cleanup, lambda: self.loop.call_soon_threadsafe(self.stop.set))

    @property
    def chains(self) -> list:
        return [c for c in (self.out, self.inc) if c is not None]

    # --- hotkeys (the `keyboard` thread) ---------------------------------------------

    def on_f9(self) -> None:
        if self.out.mixer.latched:
            self.out.mixer.unlatch()
            self.emit("state", what="f9_release")
            return
        t_key = now()
        self.out.mixer.kill(t_key)                   # audio thread sees it on its next block
        self.loop.call_soon_threadsafe(self.out.sched.flush)
        self.emit("state", what="f9", t_key=t_key)

    async def on_f10(self) -> None:
        """Panic: live voice on the cable and the physical mic as Windows default, without closing anything."""
        if self.out is not None and not self.out.mixer.latched:
            self.on_f9()
        r = await asyncio.to_thread(restore_mic, self.mic_name)
        self.emit("state", what="f10", ok=r.ok, changed=r.changed, f10_s=r.elapsed_s, detail=r.detail)
        print(f"\r[f10] {'ok' if r.ok else 'FALLÓ'} {r.elapsed_s * 1000:.0f} ms: {r.detail}".ljust(100))

    def hotkeys(self) -> None:
        import keyboard

        def on_loop(fn) -> None:
            self.loop.call_soon_threadsafe(fn)

        keyboard.add_hotkey("f10", on_loop, args=(lambda: self.loop.create_task(self.on_f10()),))
        if self.out is not None:
            keyboard.add_hotkey("f9", self.on_f9)
            keyboard.add_hotkey("f8", self.out.mixer.toggle_mute)
            keyboard.add_hotkey("f7", on_loop, args=(self.out.send_notice,))

    def cleanup(self) -> None:
        """Whatever way the process ends: hooks off, and the cable never stays as the default mic."""
        if "keyboard" in sys.modules:
            sys.modules["keyboard"].unhook_all()
        r = restore_mic(self.mic_name, only_from="CABLE")
        self.emit("state", what="exit_restore", ok=r.ok, changed=r.changed, f10_s=r.elapsed_s, detail=r.detail)

    # --- run ---------------------------------------------------------------------------

    def _task(self, name: str, coro) -> None:
        task = asyncio.create_task(coro, name=name)
        task.add_done_callback(self._on_task_done)
        self.tasks.append(task)

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled() or task.exception() is None:
            return
        exc = task.exception()
        self.emit("state", what="fatal", task=task.get_name(), error=repr(exc)[:300])
        print(f"\r[error] {task.get_name()}: {exc!r}"[:300], file=sys.stderr)
        self.ui.bar(f"ERROR {task.get_name()}: voz cruda en el cable")
        if self.out is not None and task.get_name().startswith("out."):
            self.out.mixer.kill()                    # a dead outgoing chain never leaves the cable mute

    async def warm(self) -> None:
        """First Spark call of a process is the slow one (measured: 2.4 s cold, 1.5 s warm)."""
        try:
            await asyncio.wait_for(self.spark.translate("Buenos días."), 5.0)
            self.emit("state", what="spark_warm")
        except MT_ERRORS as exc:
            self.emit("state", what="spark_warm", error=f"{type(exc).__name__}: {exc}"[:300])

    async def fixtures(self) -> None:
        jobs = []
        if self.out is not None and self.args.wav_out:
            jobs.append(replay(read_wav(self.args.wav_out), self.out.q_pcm, self.stop))
        if self.inc is not None and self.args.wav_in:
            jobs.append(replay(read_wav(self.args.wav_in), self.inc.q_pcm, self.stop))
        await asyncio.gather(*jobs)
        await asyncio.sleep(4.0)                     # the sender fills silence: the last turn closes
        t_end = now() + 15.0
        while now() < t_end and not all(c.idle() for c in self.chains):
            await asyncio.sleep(0.2)
        self.stop.set()

    async def run(self) -> None:
        if not self.args.simulate:
            key = config.require_api_key()
            self.spark = Spark(key, timeout_s=MT_TIMEOUT_S)
            for c in self.chains:
                c.setup(key, self.spark)
        for c in self.chains:
            c.start_audio()
        self.hotkeys()
        print("F7 aviso · F8 mudo · F9 voz cruda on/off · F10 mic físico · Ctrl-C salir", file=sys.stderr)
        self.ui.mine("Al otro lado solo llega inglés. F7 aviso · F8 mudo · F9 voz cruda · F10 mic físico")
        if self.args.seconds:
            self.loop.call_later(self.args.seconds, self.stop.set)
        for c in self.chains:
            for name, coro in c.tasks().items():
                self._task(name, coro)
        if self.spark is not None:
            self._task("app.warm", self.warm())
        if self.args.notice and self.out is not None:
            self.out.send_notice()
        if self.args.simulate:
            self._task("out.simulate", self.out.simulate(self.on_f9))
        if self.args.wav_out or self.args.wav_in:
            self._task("app.fixtures", self.fixtures())
        try:
            await self.stop.wait()
            await asyncio.sleep(0.5)
        finally:
            for t in self.tasks:
                t.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            for c in self.chains:
                c.close()
            self.guard.run()

    def report(self) -> None:
        for c in self.chains:
            c.report()


async def run_app(args, ui) -> None:
    settings = config.load()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    metrics = Metrics(Path("logs") / f"app_{stamp}.jsonl")
    app = App(args, settings, metrics, ui)
    rec = Recorder() if args.record_cable else None
    try:
        if rec:
            with rec:
                await app.run()
        else:
            await app.run()
    finally:
        app.report()
        if rec:
            path = rec.save(Path("logs") / f"cable_app_{stamp}.wav")
            print(f"cable recording: {path}")
        metrics.close()
    print(json.dumps(summarize(read(metrics.path)), indent=2, ensure_ascii=False))
    print(f"metrics: {metrics.path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulate", action="store_true", help="no API key: canned commits through TTS → cable")
    ap.add_argument("--sim-f9", action="store_true", help="with --simulate: press F9 programmatically mid-chunk")
    ap.add_argument("--record-cable", action="store_true", help="record CABLE Output to logs/")
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--delay", type=float, default=0.0, help="D in seconds (0 this week until piece 10)")
    ap.add_argument("--out-only", action="store_true", help="no incoming chain")
    ap.add_argument("--in-only", action="store_true", help="no outgoing chain")
    ap.add_argument("--no-overlay", action="store_true", help="console only")
    ap.add_argument("--wav-out", default=None, help="16 kHz WAV replayed instead of the mic")
    ap.add_argument("--wav-in", default=None, help="16 kHz WAV replayed instead of the loopback")
    ap.add_argument("--notice", action="store_true", help="play the initial notice at start, as F7 does")
    return ap.parse_args(argv)


def main() -> None:
    args = parse_args()
    if args.no_overlay:
        asyncio.run(run_app(args, NoUi()))
        return

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from qasync import QEventLoop

    from .ui.overlay import Overlay

    qt = QApplication([sys.argv[0]])
    overlay = Overlay(hide_from_capture=config.load().overlay_hide_from_capture)
    overlay.show()
    timer = QTimer()                                 # lets Python run signal handlers while Qt sleeps
    timer.timeout.connect(lambda: None)
    timer.start(200)
    loop = QEventLoop(qt)
    asyncio.set_event_loop(loop)
    with loop:
        loop.run_until_complete(run_app(args, overlay))


if __name__ == "__main__":
    main()
    # All is flushed and restored. Skip interpreter shutdown: the `keyboard` hook thread cannot be stopped, and
    # a native callback firing while Python finalizes ended a full run with 0xC000041D after its last log line.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
