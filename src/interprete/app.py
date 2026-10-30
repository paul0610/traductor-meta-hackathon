"""Outgoing consecutive chain: mic → Muse Voice Transcribe → Muse Spark → Edge TTS → scheduler → CABLE Input.

    uv run python -m interprete.app                  # until Ctrl-C; F9 kill switch, F8 mute
    uv run python -m interprete.app --record-cable   # also record CABLE Output (what the call app hears)
    uv run python -m interprete.app --simulate       # no API key: canned translated commits exercise TTS→cable

States PASSTHROUGH/TTS/MUTE live in the mixer; every transition is a `state` event in the JSONL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
import openai
import soxr

from . import config
from .asr.meta_realtime import MetaRealtimeAsr
from .audio.cable_mixer import CableMixer
from .audio.capture import MicCapture, QueueFeeder, To16k
from .audio.recorder import Recorder
from .audio.ring import Ring
from .clock import now
from .contracts import AsrEvent, AsrKind, Channel, Commit, PcmChunk
from .metrics.jsonl import Metrics
from .mt.spark import Spark
from .policy.consecutivo import Consecutive
from .policy.scheduler import MT_TIMEOUT_S, TTS_TIMEOUT_S, Scheduler
from .tts.edge import synthesize

SIM_LINES = [
    ("Buenos días, gracias por recibirme.", "Good morning, thanks for having me."),
    ("Tengo cinco años de experiencia en datos.", "I have five years of experience in data."),
    ("¿Podrías repetir la pregunta, por favor?", "Could you repeat the question, please?"),
]


class Outgoing:
    def __init__(self, args, settings: config.Settings, metrics: Metrics) -> None:
        self.args = args
        self.settings = settings
        self.metrics = metrics
        self.emit = metrics.emit
        self.loop = asyncio.get_running_loop()
        self.mixer = CableMixer(settings.cable_name, emit=self.emit)
        self.sched = Scheduler(self.mixer, args.delay, emit=self.emit)
        self.mic = MicCapture(settings.mic_name, emit=self.emit)
        self.q_pcm: asyncio.Queue = asyncio.Queue(maxsize=250)
        self.q_commits: asyncio.Queue[Commit] = asyncio.Queue()
        self.q_translated: asyncio.Queue[Commit] = asyncio.Queue()
        self.history: list[tuple[str, str]] = []
        self.timings: dict[int, dict] = {}
        self.stop = asyncio.Event()
        self.asr: MetaRealtimeAsr | None = None
        self.spark: Spark | None = None

    # --- wiring --------------------------------------------------------------------

    def start_audio(self) -> None:
        self.mic.start()
        ring = Ring(self.mixer.rate)
        if self.mic.rate == self.mixer.rate:
            self.mic.add_consumer(lambda t, b: ring.write(b))
        else:                                       # passthrough needs the cable's rate
            rs = soxr.ResampleStream(self.mic.rate, self.mixer.rate, 1, dtype="float32")
            self.mic.add_consumer(lambda t, b: ring.write(rs.resample_chunk(b)))
        self.mixer.mic = ring
        if not self.args.simulate:
            self.mic.add_consumer(QueueFeeder(self.loop, self.q_pcm, To16k(self.mic.rate)))
        self.mixer.start()
        print(f"mic  {self.mic.device.name} [{self.mic.device.hostapi}] {self.mic.rate} Hz\n"
              f"cable {self.mixer.device.name} [{self.mixer.device.hostapi}] {self.mixer.rate} Hz\n"
              f"state {self.mixer.state.value}   F9 kill · F8 mute · Ctrl-C quit", file=sys.stderr)

    def hotkeys(self) -> None:
        import keyboard

        def on_f9() -> None:
            t_key = now()
            self.mixer.kill(t_key)                   # audio thread sees it on its next block
            self.loop.call_soon_threadsafe(self.sched.flush)
            self.emit("state", what="f9", t_key=t_key)

        keyboard.add_hotkey("f9", on_f9)
        keyboard.add_hotkey("f8", self.mixer.toggle_mute)
        self._on_f9 = on_f9

    # --- tasks ---------------------------------------------------------------------

    def on_asr_event(self, ev: AsrEvent) -> None:
        if ev.kind is AsrKind.SPEECH_COMPLETE:
            print(f"\r[es] {ev.text}".ljust(100))
        elif ev.kind is AsrKind.PARTIAL:
            print(f"\r… {ev.text}"[:100].ljust(100), end="", flush=True)
        commit = self.policy.on_event(ev)
        if commit is not None:
            self.submit_commit(commit)

    def submit_commit(self, c: Commit) -> None:
        self.sched.expect(c.seq, c.utterance_id, c.t_commit, c.t_src_start)
        self.timings[c.seq] = dict(seq=c.seq, text_src=c.text_src, t_commit=c.t_commit,
                                   t_src_start=c.t_src_start, t_src_end=c.t_src_end, t_src_source=c.t_src_source)
        self.q_commits.put_nowait(c)

    async def mt_worker(self) -> None:
        while True:
            c = await self.q_commits.get()
            if self.sched.is_cancelled(c.utterance_id):
                self.sched.tombstone(c.seq, "cancelled")
                continue
            if c.text_tgt:                           # simulate mode: already translated
                await self.q_translated.put(c)
                continue
            try:
                r = await asyncio.wait_for(self.spark.translate(c.text_src, history=self.history[-4:]), MT_TIMEOUT_S)
            except (TimeoutError, openai.APIError, json.JSONDecodeError, KeyError) as exc:
                self.sched.tombstone(c.seq, f"mt:{type(exc).__name__}")
                self.emit("mt", seq=c.seq, utterance_id=c.utterance_id, error=f"{type(exc).__name__}: {exc}"[:300])
                print(f"\r[mt] dropped seq {c.seq}: {type(exc).__name__}".ljust(100))
                continue
            c.text_tgt, c.t_mt, c.mt_calls = r.translation, r.t_end, 1
            self.history.append((c.text_src, c.text_tgt))
            self.timings[c.seq].update(t_mt=r.t_end, mt_s=r.t_end - r.t_start, text_tgt=c.text_tgt)
            self.emit("mt", seq=c.seq, utterance_id=c.utterance_id, t_mt=r.t_end, mt_s=r.t_end - r.t_start,
                      text_tgt=c.text_tgt, mt_calls_per_commit=1, reasoning_tokens=r.reasoning_tokens,
                      effort=r.effort)
            print(f"\r[en] {c.text_tgt}".ljust(100))
            await self.q_translated.put(c)

    async def tts_worker(self) -> None:
        while True:
            c = await self.q_translated.get()
            if self.sched.is_cancelled(c.utterance_id):
                self.sched.tombstone(c.seq, "cancelled")
                continue
            try:
                r = await asyncio.wait_for(synthesize(c.text_tgt, self.settings.tts_voice, self.mixer.rate), TTS_TIMEOUT_S)
            except Exception as exc:                 # network, decode, timeout: never stall the queue
                self.sched.tombstone(c.seq, f"tts:{type(exc).__name__}")
                self.emit("tts", seq=c.seq, utterance_id=c.utterance_id, error=f"{type(exc).__name__}: {exc}"[:300])
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
            self.timings[c.seq].update(t_tts_first=r.t_first, t_tts_ready=r.t_ready, tts_ttfb_s=r.t_first - r.t_request,
                                       duration_s=r.duration_s, chunk=chunk)
            self.emit("tts", seq=c.seq, utterance_id=c.utterance_id, t_tts_first=r.t_first, t_tts_end=r.t_ready,
                      ttfb_s=r.t_first - r.t_request, synth_s=r.t_end - r.t_request, duration_s=r.duration_s,
                      trimmed_lead_s=r.trimmed_lead_s, trimmed_tail_s=r.trimmed_tail_s)
            self.sched.submit(chunk)

    async def controller(self) -> None:
        """TTS → PASSTHROUGH once idle; the decision needs the scheduler, so it lives here, not in the callback."""
        while True:
            self.mixer.release_if_idle(self.sched.in_flight())
            await asyncio.sleep(0.05)

    async def simulate(self) -> None:
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
            self._on_f9()
            await asyncio.sleep(1.0)
        await asyncio.sleep(1.0)
        self.stop.set()

    async def run(self) -> None:
        if not self.args.simulate:
            key = config.require_api_key()
            self.asr = MetaRealtimeAsr(Channel.OUT, key, mode="ENDPOINTING", emit=self.emit)
            self.spark = Spark(key, timeout_s=MT_TIMEOUT_S)
            self.policy = Consecutive(Channel.OUT, self.asr.clock.to_local, emit=self.emit)
        self.start_audio()
        self.hotkeys()
        if self.args.seconds:
            self.loop.call_later(self.args.seconds, self.stop.set)
        tasks = [asyncio.create_task(t) for t in (self.sched.run(), self.mt_worker(), self.tts_worker(), self.controller())]
        if self.args.simulate:
            tasks.append(asyncio.create_task(self.simulate()))
        else:
            tasks.append(asyncio.create_task(self.asr.run(self.q_pcm, self.on_asr_event, self.stop)))
        try:
            await self.stop.wait()
            await asyncio.sleep(0.5)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.mixer.stop()
            self.mic.stop()

    def report(self) -> dict:
        rows = []
        for seq, t in sorted(self.timings.items()):
            chunk = t.pop("chunk", None)
            if chunk is not None and chunk.t_play_start is not None:
                t["t_play_start"] = chunk.t_play_start
                t["commit_to_first_pcm_s"] = chunk.t_play_start - t["t_commit"]
                if t.get("t_src_end") is not None:
                    t["speech_end_to_first_pcm_s"] = chunk.t_play_start - t["t_src_end"]
                t["src_start_to_tts_first_s"] = t["t_tts_first"] - t["t_src_start"]
            rows.append(t)
        c2p = [r["commit_to_first_pcm_s"] for r in rows if "commit_to_first_pcm_s" in r]
        e2p = [r["speech_end_to_first_pcm_s"] for r in rows if "speech_end_to_first_pcm_s" in r]
        s2t = [r["src_start_to_tts_first_s"] for r in rows if "src_start_to_tts_first_s" in r]
        pc = lambda xs, q: round(float(np.percentile(xs, q)), 3) if xs else None  # noqa: E731
        summary = dict(commits=len(rows), played=len(c2p), seq_skipped=self.sched.seq_skipped,
                       cable_gaps=self.mixer.cable_gaps, commit_to_first_pcm_p50=pc(c2p, 50),
                       commit_to_first_pcm_p95=pc(c2p, 95), speech_end_to_first_pcm_p50=pc(e2p, 50),
                       speech_end_to_first_pcm_p95=pc(e2p, 95), src_start_to_tts_first_p95=pc(s2t, 95),
                       d_estimate_s=round(pc(s2t, 95) + 0.3, 2) if s2t else None)
        for r in rows:
            self.emit("commit", phase="report", **{k: v for k, v in r.items() if k != "chunk"})
        self.emit("run", summary=summary)
        return summary


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulate", action="store_true", help="no API key: canned commits through TTS → cable")
    ap.add_argument("--sim-f9", action="store_true", help="with --simulate: press F9 programmatically mid-chunk")
    ap.add_argument("--record-cable", action="store_true", help="record CABLE Output to logs/")
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--delay", type=float, default=0.0, help="D in seconds (0 this week until piece 10)")
    args = ap.parse_args()
    settings = config.load()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    metrics = Metrics(Path("logs") / f"app_{stamp}.jsonl")
    out = Outgoing(args, settings, metrics)
    rec = Recorder() if args.record_cable else None
    try:
        if rec:
            with rec:
                await out.run()
        else:
            await out.run()
    finally:
        summary = out.report()
        if rec:
            path = rec.save(Path("logs") / f"cable_app_{stamp}.wav")
            print(f"cable recording: {path}")
        metrics.close()
        print(json.dumps(summary, indent=2))
        print(f"metrics: {metrics.path}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
