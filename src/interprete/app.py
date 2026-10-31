"""The interpreter, both directions.

OUT: mic → Muse Voice Transcribe → Muse Spark → Edge TTS → scheduler → CABLE Input (the other side only hears English)
IN:  system loopback → Muse Voice Transcribe → Muse Spark → subtitle overlay

    uv run python -m interprete.app                  # until Ctrl-C
    uv run python -m interprete.app --record-cable   # also record CABLE Output (what the call app hears)
    uv run python -m interprete.app --wav-out es.wav --wav-in en.wav   # fixtures instead of mic and loopback
    uv run python -m interprete.app --simulate       # no API key: canned commits exercise TTS → cable
    uv run python -m interprete.app --dub            # synchronized dub: voice and TTS D seconds behind me

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
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import openai
import soxr

from . import config
from .asr.meta_realtime import QUEUE_BLOCKS, MetaRealtimeAsr
from .audio import devices
from .audio.cable_mixer import CableMixer, State
from .audio.capture import MicCapture, QueueFeeder, To16k
from .audio.delay import DelayLine
from .audio.device_restore import app_volume, endpoint_volume, restore_mic
from .audio.fixture import RATE as FIXTURE_RATE
from .audio.fixture import read_wav, replay
from .audio.loopback import LoopbackCapture
from .audio.recorder import Recorder
from .audio.ring import Ring
from .clock import now
from .contracts import AsrEvent, AsrKind, Channel, Commit, PcmChunk
from .metrics.jsonl import Metrics, read
from .metrics.summary import summarize
from .mt.context import Conversation, load_glossary
from .mt.spark import EmptyTranslation, Spark
from .policy.consecutivo import Consecutive
from .policy.incremental import Incremental
from .policy.scheduler import MT_TIMEOUT_S, TTS_TIMEOUT_S, Scheduler
from .safety import ExitGuard, Watchdog
from .tts.backup import make_voice
from .tts.edge import FAST_RATE_PCT
from .turns.floor import Floor
from .ui.status import Phase, health
from .video.virtual_cam import VirtualCam

MT_ERRORS = (TimeoutError, openai.APIError, json.JSONDecodeError, KeyError)
LANGUAGE_BIAS = ["en", "es"]    # this order keeps the most Spanish inside English speech, and pure Spanish stays intact
ASR_FALLBACK_S = 3.0            # ASR down this long: live voice, nothing can be translated
CABLE_MIN_VOLUME = 0.95
CATCH_UP_BACKLOG_S = 3.0        # behind by more than this: the next clause is spoken faster
CATCH_UP_SYNC_S = 0.5           # with D > 0: the next clause would start this late
SYNC_LATE_S = 1.0               # three clauses in a row this late: D is too short, the bar says so
SYNC_LATE_STREAK = 3
WORKERS = 2                     # concurrent Spark calls and syntheses, outgoing
POLICIES = {"consecutive": Consecutive, "incremental": Incremental}
NOTICE_EN = ("Hi, a quick note before we start: I speak Spanish and you are hearing an AI interpreter. "
             "It runs a few seconds behind me, so please give me a moment after you finish speaking.")
NOTICE_DUB_EN = ("Hi, a quick note before we start: I speak Spanish and you are hearing an AI interpreter. My video "
                 "reaches you about {n} seconds late so it stays in sync with the voice, so please give me a moment "
                 "after you finish speaking.")
SIM_LINES = [
    ("Buenos días, gracias por recibirme.", "Good morning, thanks for having me."),
    ("Tengo cinco años de experiencia en datos.", "I have five years of experience in data."),
    ("¿Podrías repetir la pregunta, por favor?", "Could you repeat the question, please?"),
]


def dub_delay(args, settings: config.Settings) -> float:
    """D of this session, fixed until it ends; 0 is no dub."""
    if args.delay is not None:
        return args.delay
    return settings.delay_s if args.dub else 0.0


class NoUi:
    """Stands in for the screen in `--no-overlay` runs."""

    def __getattr__(self, name):
        return lambda *a: None

    async def go(self, stop: asyncio.Event) -> None:
        pass


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
        self.delay_s = dub_delay(args, settings)
        self.delay: DelayLine | None = None
        if self.delay_s > 0:
            self.delay = DelayLine(self.mixer.rate, self.delay_s)
            self.mixer.dub(self.delay, settings.voice_under_db)
        self.rest = self.mixer.rest
        self.sched = Scheduler(self.mixer, self.delay_s, emit=self._on_sched)
        self.voice = make_voice(settings, self.mixer.rate, size=WORKERS + 1, emit=self.emit)   # one spare socket
        self.watchdog = Watchdog(self.mixer, self.sched, emit=self.emit)
        self.mic = MicCapture(settings.mic_name, emit=self.emit)
        self.q_pcm: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_BLOCKS)
        self.q_commits: asyncio.Queue[Commit] = asyncio.Queue()
        self.q_translated: asyncio.Queue[Commit] = asyncio.Queue()
        self.translated: dict[int, tuple[str, str]] = {}
        self.timings: dict[int, dict] = {}
        self.lags: list[float] = []
        self.sync_err = 0.0
        self.sync_late = 0                           # clauses in a row later than SYNC_LATE_S
        self.fixture_start: float | None = None
        self.video: VirtualCam | None = None
        self.video_warning = ""
        self.cable_warning = ""
        self.conversation = Conversation()           # shared with the incoming chain
        self.floor: Floor | None = None              # the incoming chain's, when there is one
        self.held = False                           # gave way: my clauses wait until their turn ends
        self.yields = 0
        self.unsaid: list[str] = []                  # on screen in grey, to say again
        self.t_start = now()
        self.asr: MetaRealtimeAsr | None = None
        self.spark: Spark | None = None
        self.policy: Consecutive | None = None

    # --- wiring --------------------------------------------------------------------

    def setup(self, key: str, spark: Spark) -> None:
        self.asr = MetaRealtimeAsr(Channel.OUT, key, mode="ENDPOINTING", language_bias=LANGUAGE_BIAS,
                                   keywords=[*spark.glossary] or None, emit=self.emit)
        self.spark = spark
        self.policy = POLICIES[self.args.mode](Channel.OUT, self.asr.clock.to_local, emit=self.emit)

    def start_audio(self) -> None:
        if not self.args.wav_out:
            self.mic.start()
            ring = Ring(self.mixer.rate)
            rs = None if self.mic.rate == self.mixer.rate else soxr.ResampleStream(self.mic.rate, self.mixer.rate, 1,
                                                                                   dtype="float32")

            def to_cable(t: float, block: np.ndarray) -> None:      # live voice and the dub need the cable's rate
                y = block if rs is None else rs.resample_chunk(block)
                ring.write(y)
                if self.delay is not None:
                    self.delay.write(y)

            self.mic.add_consumer(to_cable)
            self.mixer.mic = ring
            if not self.args.simulate:
                self.mic.add_consumer(QueueFeeder(self.loop, self.q_pcm, To16k(self.mic.rate)))
            print(f"mic   {self.mic.device.name} [{self.mic.device.hostapi}] {self.mic.rate} Hz", file=sys.stderr)
        self.mixer.start()
        print(f"cable {self.mixer.device.name} [{self.mixer.device.hostapi}] {self.mixer.rate} Hz "
              f"{self.mixer.channels} ch, state {self.mixer.state.value}", file=sys.stderr)
        self.check_cable_level()

    async def start_video(self) -> None:
        """Off the loop: the webcam takes seconds, and the ASR connects meanwhile.

        Without the delayed camera the dub still runs, but the lips lead the voice by D, so the bar says so.
        """
        video = VirtualCam(self.delay_s + self.settings.av_offset_ms / 1000, lambda: self.mixer.live,
                           name_sub=self.settings.camera_name, emit=self.emit)
        try:
            await asyncio.to_thread(video.start)
        except (RuntimeError, OSError) as exc:
            self.video_warning = " │ SIN CÁMARA RETRASADA"
            self.emit("video", error=repr(exc)[:300])
            print(f"AVISO: sin cámara retrasada, el otro lado ve tus labios {self.delay_s:.1f} s antes que la voz. "
                  f"¿OBS Studio instalado? {exc}", file=sys.stderr)
            return
        self.video = video
        print(f"cámara {self.settings.camera_name} -> OBS Virtual Camera, {video.frames.delay_s:.2f} s tarde", file=sys.stderr)

    def check_cable_level(self) -> None:
        """A low Windows slider scales the whole voice before the call app sees it; nothing else would show it."""
        vol = endpoint_volume(self.settings.cable_name)
        if vol is None:
            return
        own = app_volume(self.settings.cable_name)
        self.emit("state", what="cable_volume", volume=vol[0], mute=vol[1], app_volume=own)
        if vol[0] < CABLE_MIN_VOLUME or vol[1]:
            self.cable_warning = f" │ CABLE {vol[0] * 100:.0f} %{' MUDO' if vol[1] else ''}"
            print(f"AVISO: {self.settings.cable_name} está al {vol[0] * 100:.0f} %{' y mudo' if vol[1] else ''} en Windows: "
                  "el otro lado te oirá muy bajo. Súbelo a 100 en Sonido > Salida > CABLE Input.", file=sys.stderr)
        if own is not None and own < CABLE_MIN_VOLUME:
            self.cable_warning += f" │ PYTHON {own * 100:.0f} %"
            print(f"AVISO: Python está al {own * 100:.0f} % en el mezclador de volumen de Windows: el otro lado te oirá "
                  "muy bajo. Súbelo a 100 en Sistema > Sonido > Mezclador de volumen, con la app en marcha.", file=sys.stderr)

    def fixture_tap(self):
        """The fixture also stands in for the mic on the delay line, so a dub can be judged with no one speaking."""
        rs = soxr.ResampleStream(FIXTURE_RATE, self.mixer.rate, 1, dtype="float32")

        def tap(t_capture: float, pcm: bytes) -> None:
            if self.fixture_start is None:
                self.fixture_start = t_capture
                self.emit("state", what="fixture_start", t_start=t_capture)
            if self.delay is not None:
                self.delay.write(rs.resample_chunk(np.frombuffer(pcm, "<i2").astype(np.float32) / 32768.0))

        return tap

    def _on_sched(self, ev: str, **f) -> None:
        self.emit(ev, **f)
        row = self.timings.get(f.get("seq"))
        if row is not None and f.get("t_play_start") is not None:
            row["t_play_start"] = f["t_play_start"]
            # with a dub every clause waits for its deadline on purpose: the lag worth showing is the pipeline's
            self.lags.append((f["t_tts_first"] if self.delay_s > 0 else f["t_play_start"]) - row["t_commit"])
            self.note_sync(f["sync_err_s"])

    def note_sync(self, err: float) -> None:
        self.sync_err = err
        self.sync_late = self.sync_late + 1 if err > SYNC_LATE_S else 0
        if self.delay_s > 0 and self.sync_late == SYNC_LATE_STREAK:
            self.emit("state", what="sync_late", sync_err_s=err, delay_s=self.delay_s)

    def idle(self) -> bool:
        return self.sched.in_flight() == 0 and not self.mixer.busy()

    def recent(self) -> list[tuple[str, str]]:
        """Last clauses in spoken order: with several workers they finish out of order."""
        return [self.translated[s] for s in sorted(self.translated)[-4:]]

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
        if self.held:
            self.sched.tombstone(c.seq, "yielded")
            self.note_unsaid([c.seq])
            return
        if self.unsaid:                              # back to speaking: the old grey line has served
            self.unsaid = []
            self.ui.unsaid("")
        self.q_commits.put_nowait(c)

    async def mt_worker(self) -> None:
        while True:
            c = await self.q_commits.get()
            if self.sched.is_cancelled(c.utterance_id) or self.sched.resolved(c.seq):
                self.sched.tombstone(c.seq, "cancelled")
                continue
            if c.text_tgt:                           # notice and simulate mode: already in English
                await self.q_translated.put(c)
                continue
            def on_mt_hedge() -> None:
                self.emit("mt", channel="out", seq=c.seq, utterance_id=c.utterance_id, hedged=True)

            try:
                r = await asyncio.wait_for(self.spark.translate(c.text_src, prefix=c.prefix_src, history=self.recent(),
                                                                fragment=self.args.mode == "incremental",
                                                                context=self.conversation.text(),
                                                                on_hedge=on_mt_hedge), MT_TIMEOUT_S)
            except EmptyTranslation:                 # only a filler: nothing was lost, there was nothing to say
                self.sched.tombstone(c.seq, "filler")
                self.emit("mt", channel="out", seq=c.seq, utterance_id=c.utterance_id, filler=True)
                continue
            except MT_ERRORS as exc:
                self.sched.tombstone(c.seq, f"mt:{type(exc).__name__}")
                self.emit("mt", channel="out", seq=c.seq, utterance_id=c.utterance_id, error=f"{type(exc).__name__}: {exc}"[:300])
                print(f"\r[mt] dropped seq {c.seq}: {type(exc).__name__}".ljust(100))
                continue
            c.text_tgt, c.t_mt, c.mt_calls = r.translation, r.t_end, 1
            self.translated[c.seq] = (c.text_src, c.text_tgt)
            self.conversation.add("Me", c.text_tgt)
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
            if self.sched.is_cancelled(c.utterance_id) or self.sched.resolved(c.seq):
                self.sched.tombstone(c.seq, "cancelled")
                continue
            def on_hedge() -> None:
                self.emit("tts", channel="out", seq=c.seq, utterance_id=c.utterance_id, hedged=True)

            catch_up = self.behind(c, now())
            src_s = None if c.t_src_end is None else c.t_src_end - c.t_src_start
            rate_pct = FAST_RATE_PCT if catch_up else self.voice.rate_for(c.text_tgt, src_s)
            try:
                r = await asyncio.wait_for(self.voice.say(c.text_tgt, rate_pct=rate_pct, on_hedge=on_hedge), TTS_TIMEOUT_S)
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
                stretch_ratio=100 / (100 + rate_pct),
            )
            self.timings[c.seq].update(t_tts_first=r.t_first, t_tts_end=r.t_ready, tts_ttfb_s=r.t_first - r.t_request,
                                       duration_s=r.duration_s)
            self.emit("tts", channel="out", seq=c.seq, utterance_id=c.utterance_id, t_tts_first=r.t_first, t_tts_end=r.t_ready,
                      ttfb_s=r.t_first - r.t_request, synth_s=r.t_end - r.t_request, duration_s=r.duration_s,
                      trimmed_lead_s=r.trimmed_lead_s, trimmed_tail_s=r.trimmed_tail_s, catch_up=catch_up,
                      rate_pct=rate_pct, stretch_ratio=chunk.stretch_ratio, backlog_s=self.sched.backlog_s(now()))
            self.sched.submit(chunk)

    def note_unsaid(self, seqs: list[int]) -> None:
        for s in seqs:
            self.emit("turn", what="unsaid", channel="out", seq=s)
        self.unsaid += [self.timings[s]["text_src"] for s in seqs if s in self.timings]
        self.ui.unsaid(" ".join(self.unsaid))

    def give_way(self, t: float) -> None:
        """Their turn: my voice ends with the clause it is on, and what it did not say stays on screen."""
        self.held = True
        self.yields += 1
        self.unsaid = []
        dropped = self.sched.give_way()
        self.emit("turn", what="yield", channel="out", t_yield=t, t_voice_ends=max(t, self.sched.voice_ends_at),
                  dropped=len(dropped))
        self.ui.collision(True)
        self.note_unsaid(dropped)

    def turns(self, t: float) -> None:
        theirs = self.floor is not None and self.floor.theirs(t)
        if theirs and not self.held and not self.idle():
            self.give_way(t)
        elif self.held and not theirs:
            self.held = False
            self.emit("turn", what="floor_back", channel="out", t_back=t)
            self.ui.collision(False)

    def behind(self, c: Commit, t: float) -> bool:
        if self.delay_s > 0:
            return self.sched.lateness(self.sched.deadline_for(c.t_src_start), t) > CATCH_UP_SYNC_S
        return self.sched.backlog_s(t) > CATCH_UP_BACKLOG_S

    def fallback(self, t: float) -> None:
        """ASR down for a while: nothing can be translated, so the other side at least hears me."""
        down = self.asr is not None and self.asr.t_down is not None and t - self.asr.t_down > ASR_FALLBACK_S
        rest = State.PASSTHROUGH if down else self.rest
        if rest != self.mixer.rest:
            self.mixer.set_rest(rest)

    def bar_text(self, t: float) -> str:
        lag = f"{self.lags[-1]:.1f}s p95 {np.percentile(self.lags, 95):.1f}s" if self.lags else "--"
        link = " │ ASR CAÍDO" if self.asr is not None and self.asr.t_down is not None else ""
        sync = ""
        if self.delay_s > 0:
            sync = f" │ SYNC {self.sync_err:+.1f}s (D {self.delay_s:.1f}s)"
            if self.sync_late >= SYNC_LATE_STREAK:
                sync += f" TARDE: reinicia con D {self.delay_s + 0.5:.1f}"
        turn = " │ TURNO: ellos" if self.floor is not None and self.floor.theirs(t) else " │ TURNO: yo"
        return (f"LAG {lag}{sync} │ CORR 0{turn} │ ES→EN {self.mixer.state.value}{link}{self.cable_warning}"
                f"{self.video_warning}{self.voice.warning} │ META {(t - self.t_start) / 60:.0f} min")

    async def controller(self) -> None:
        """TTS → rest once idle, watchdog, overlay line: all need the scheduler, so none lives in the callback."""
        t_bar = 0.0
        while True:
            t = now()
            self.mixer.release_if_idle(self.sched.in_flight(), t)
            self.watchdog.check(t)
            self.fallback(t)
            self.turns(t)
            if t >= t_bar:
                t_bar = t + 0.5
                self.ui.bar(self.bar_text(t))
                lag = self.lags[-1] if self.lags else None
                self.ui.vitals(lag, health(lag, self.sync_err, self.delay_s))
                self.ui.phase(Phase.RAW if self.mixer.latched else Phase.PAUSED if self.held else Phase.LIVE)
            await asyncio.sleep(0.05)

    def send_notice(self) -> None:
        if self.policy is None or self.mixer.latched:
            return
        t = now()
        seq = self.policy.take_seq()
        text = NOTICE_DUB_EN.format(n=round(self.delay_s)) if self.delay_s > 0 else NOTICE_EN
        self.submit_commit(Commit(utterance_id=f"out:notice:{seq}", commit_id=0, seq=seq, channel=Channel.OUT,
                                  text_src="(aviso inicial)", prefix_src="", boundary="notice",
                                  t_src_start=t - self.delay_s, t_src_end=None, t_src_source="notice", t_commit=t,
                                  text_tgt=text))
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
            while not self.mixer.busy():
                await asyncio.sleep(0.01)
            await asyncio.sleep(1.0)
            on_f9()
            await asyncio.sleep(1.0)
        await asyncio.sleep(1.0)
        self.stop.set()

    def tasks(self) -> dict:
        jobs = {"out.sched": self.sched.run(), "out.controller": self.controller()}
        for i in range(WORKERS):                     # a clause never queues behind the previous one; seq keeps the order
            jobs[f"out.mt{i}"] = self.mt_worker()
            jobs[f"out.tts{i}"] = self.tts_worker()
        if self.asr is not None:
            jobs["out.asr"] = self.asr.run(self.q_pcm, self.on_asr_event, self.stop)
        if self.delay_s > 0 and not self.args.wav_out:
            jobs["out.video"] = self.start_video()
        return jobs

    async def close(self) -> None:
        await self.voice.close()
        if self.video is not None:
            await asyncio.to_thread(self.video.stop)
        self.mixer.stop()
        self.mic.stop()

    def report(self) -> None:
        for r in self.timings.values():
            if r["t_src_source"] != "notice":        # no ASR or Spark behind it: it would flatter the latency
                self.emit("commit", phase="report", channel="out", **r)
        self.emit("run", seq_skipped=self.sched.seq_skipped, cable_gaps=self.mixer.cable_gaps,
                  watchdog_trips=self.watchdog.trips, mode=self.args.mode, tts_reconnects=self.voice.reconnects,
                  tts_voice=self.voice.name, yields=self.yields, delay_s=self.delay_s, voice_under_db=self.settings.voice_under_db if self.delay_s > 0 else None,
                  delay_jumps=self.delay.jumps if self.delay is not None else 0,
                  corrections_blocked=self.policy.corrections_blocked if self.policy is not None else 0)


class Incoming:
    def __init__(self, args, metrics: Metrics, ui, stop: asyncio.Event) -> None:
        self.args = args
        self.emit = metrics.emit
        self.ui = ui
        self.stop = stop
        self.loop = asyncio.get_running_loop()
        self.q_pcm: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_BLOCKS)
        self.q_commits: asyncio.Queue[Commit] = asyncio.Queue()
        self.history: list[tuple[str, str]] = []
        self.rows: dict[int, dict] = {}
        self.turn_text = ""                          # the subtitle grows clause by clause within a turn
        self.floor = Floor()
        self.conversation = Conversation()           # replaced by the outgoing chain's when both run
        self.pending = 0
        self.loopback: LoopbackCapture | None = None
        self.asr: MetaRealtimeAsr | None = None
        self.spark: Spark | None = None
        self.policy: Consecutive | None = None

    def setup(self, key: str, spark: Spark) -> None:
        self.asr = MetaRealtimeAsr(Channel.IN, key, mode="ENDPOINTING", keywords=[*spark.glossary.values()] or None,
                                   emit=self.emit)
        self.spark = spark
        self.policy = POLICIES[self.args.mode](Channel.IN, self.asr.clock.to_local, emit=self.emit)

    def start_audio(self) -> None:
        if self.args.wav_in:
            return
        self.loopback = LoopbackCapture(QueueFeeder(self.loop, self.q_pcm), emit=self.emit)
        self.loopback.start()

    def idle(self) -> bool:
        return self.pending == 0

    def on_asr_event(self, ev: AsrEvent) -> None:
        self.floor.on_event(ev)
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
            def on_mt_hedge() -> None:
                self.emit("mt", channel="in", seq=c.seq, utterance_id=c.utterance_id, hedged=True)

            try:
                r = await asyncio.wait_for(self.spark.translate(c.text_src, src="en", tgt="es", prefix=c.prefix_src,
                                                                history=self.history[-4:],
                                                                fragment=self.args.mode == "incremental",
                                                                context=self.conversation.text(),
                                                                on_hedge=on_mt_hedge), MT_TIMEOUT_S)
            except EmptyTranslation:
                self.emit("mt", channel="in", seq=c.seq, utterance_id=c.utterance_id, filler=True)
                self.pending -= 1
                continue
            except MT_ERRORS as exc:
                self.ui.their_failed(c.text_src)
                self.emit("mt", channel="in", seq=c.seq, utterance_id=c.utterance_id, error=f"{type(exc).__name__}: {exc}"[:300])
                self.pending -= 1
                continue
            self.history.append((c.text_src, r.translation))
            self.conversation.add("Them", c.text_src)
            self.turn_text = r.translation if c.commit_id == 0 else f"{self.turn_text} {r.translation}"
            self.ui.their_translation(self.turn_text)
            print(f"\r[sub] {r.translation}".ljust(100))
            row.update(t_mt=r.t_end, t_shown=now(), mt_s=r.t_end - r.t_start, text_tgt=r.translation)
            self.emit("mt", channel="in", seq=c.seq, utterance_id=c.utterance_id, t_mt=r.t_end, mt_s=r.t_end - r.t_start,
                      text_tgt=r.translation, mt_calls_per_commit=1, reasoning_tokens=r.reasoning_tokens, effort=r.effort)
            self.pending -= 1

    def tasks(self) -> dict:
        return {"in.mt": self.mt_worker(), "in.asr": self.asr.run(self.q_pcm, self.on_asr_event, self.stop)}

    async def close(self) -> None:
        if self.loopback is not None:
            self.loopback.stop()

    def report(self) -> None:
        for r in self.rows.values():
            self.emit("commit", phase="report", channel="in", **r)


class App:
    def __init__(self, args, settings: config.Settings, metrics: Metrics, ui) -> None:
        self.args = args
        self.settings = settings
        self.metrics = metrics
        self.emit = metrics.emit
        self.ui = ui
        self.stop = asyncio.Event()
        self.loop = asyncio.get_running_loop()
        self.out = None if args.in_only else Outgoing(args, settings, metrics, ui, self.stop)
        self.inc = None if args.out_only or args.simulate else Incoming(args, metrics, ui, self.stop)
        if self.out is not None and self.inc is not None:
            self.inc.conversation = self.out.conversation
            if not args.no_yield:
                self.out.floor = self.inc.floor
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
        except (*MT_ERRORS, EmptyTranslation) as exc:
            self.emit("state", what="spark_warm", error=f"{type(exc).__name__}: {exc}"[:300])

    async def fixtures(self) -> None:
        jobs = []
        if self.out is not None and self.args.wav_out:
            jobs.append(replay(read_wav(self.args.wav_out), self.out.q_pcm, self.stop, self.out.fixture_tap()))
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
            self.spark = Spark(key, timeout_s=MT_TIMEOUT_S, glossary=load_glossary(self.settings.glossary_path))
            for c in self.chains:
                c.setup(key, self.spark)
        if self.out is not None:                     # first: two voices in one call is worse than a later start
            self.ui.mine("Cargando la voz…")
            await self.out.voice.start()
        if self.spark is not None:
            await self.warm()
        self.ui.phase(Phase.READY)
        self.ui.mine("Listo. Nada sale de esta PC hasta que pulses el botón.")
        await self.ui.go(self.stop)
        if self.stop.is_set():
            if self.out is not None:
                await self.out.voice.close()
            self.guard.run()
            self.ui.phase(Phase.OFF)
            return
        self.ui.phase(Phase.LIVE)
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
                await c.close()
            self.guard.run()
            self.ui.phase(Phase.OFF)
            self.ui.mine("Apagado. Micrófono físico restaurado.")

    def report(self) -> None:
        for c in self.chains:
            c.report()


async def run_app(args, ui) -> None:
    settings = config.load()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    metrics = Metrics(Path("logs") / f"app_{stamp}.jsonl")
    app = App(args, settings, metrics, ui)
    recs = {"cable": Recorder() if args.record_cable else None,
            "mic": Recorder(settings.mic_name) if args.record_mic else None}
    try:
        with recs["cable"] or nullcontext(), recs["mic"] or nullcontext():
            await app.run()
    finally:
        app.report()
        for name, rec in recs.items():
            if rec:
                path = rec.save(Path("logs") / f"{name}_app_{stamp}.wav")
                metrics.emit("state", what=f"{name}_recording", path=str(path), t_start=rec.signal()[0])
                print(f"{name} recording: {path}")
        metrics.close()
    print(json.dumps(summarize(read(metrics.path)), indent=2, ensure_ascii=False))
    print(f"metrics: {metrics.path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulate", action="store_true", help="no API key: canned commits through TTS → cable")
    ap.add_argument("--sim-f9", action="store_true", help="with --simulate: press F9 programmatically mid-chunk")
    ap.add_argument("--record-cable", action="store_true", help="record CABLE Output to logs/")
    ap.add_argument("--record-mic", action="store_true", help="record the microphone to logs/, as truth for gate S")
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--dub", action="store_true", default=config.load().dub,
                    help="synchronized dub with D = RTT_DELAY_S; only makes sense with the delayed camera")
    ap.add_argument("--delay", type=float, default=None, help="D in seconds, fixed for the session; 0 turns the dub off")
    ap.add_argument("--mode", choices=sorted(POLICIES), default=config.load().mode,
                    help="incremental: clause by clause; consecutive: whole turns")
    ap.add_argument("--out-only", action="store_true", help="no incoming chain")
    ap.add_argument("--in-only", action="store_true", help="no outgoing chain")
    ap.add_argument("--no-overlay", action="store_true", help="console only")
    ap.add_argument("--no-yield", action="store_true", help="my voice never gives way: the 60 s gate talks over itself")
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

    from .ui.widget import Screen

    qt = QApplication([sys.argv[0]])
    screen = Screen(hide_from_capture=config.load().overlay_hide_from_capture)
    screen.show()
    timer = QTimer()                                # lets Python run signal handlers while Qt sleeps
    timer.timeout.connect(lambda: None)
    timer.start(200)
    loop = QEventLoop(qt)
    asyncio.set_event_loop(loop)
    with loop:
        loop.run_until_complete(run_app(args, screen))


if __name__ == "__main__":
    main()
    # All is flushed and restored. Skip interpreter shutdown: the `keyboard` hook thread cannot be stopped, and
    # a native callback firing while Python finalizes ended a full run with 0xC000041D after its last log line.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
