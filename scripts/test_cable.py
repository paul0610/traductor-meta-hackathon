"""The voice → scheduler (D = 0) → mixer → CABLE Input, verified by recording CABLE Output.

Measures: synthesis, silence trimmed, cable latency (mixer start → audio on CABLE Output),
integrity (correlation of what was written vs what came out) and gaps.

    uv run python scripts/test_cable.py
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
import wave
from pathlib import Path

import numpy as np

from interprete import config
from interprete.audio.cable_mixer import CableMixer
from interprete.audio.recorder import Recorder, find_offset
from interprete.clock import now
from interprete.contracts import Channel, PcmChunk
from interprete.metrics.jsonl import Metrics
from interprete.policy.scheduler import Scheduler
from interprete.tts.mms import MmsVoice

PHRASES = [
    "Good morning, thanks for having me today.",
    "I have five years of experience building data pipelines.",
    "Could you repeat the last part of the question, please?",
    "We reduced the latency of the system by forty percent.",
    "I think the main challenge was coordinating with three teams.",
]


def save_wav(path: Path, x: np.ndarray, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def pctl(xs: list[float], q: float) -> float:
    return float(np.percentile(xs, q)) if xs else float("nan")


async def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    settings = config.load()
    metrics = Metrics(run_name="test_cable")

    mixer = CableMixer(settings.cable_name, emit=metrics.emit)
    print(f"cable: {mixer.device.name} [{mixer.device.hostapi}] {mixer.rate} Hz")

    # 1) voice timings
    voice = MmsVoice(settings.mms_model, mixer.rate, speaking_rate=settings.mms_speaking_rate)
    await voice.start()
    results = []
    for text in PHRASES:
        r = await voice.say(text)
        results.append(r)
        metrics.emit("tts", text=text, ttfb_s=r.t_first - r.t_request, synth_s=r.t_end - r.t_request,
                     post_s=r.t_ready - r.t_end, trimmed_lead_s=r.trimmed_lead_s,
                     trimmed_tail_s=r.trimmed_tail_s, duration_s=r.duration_s)
        print(f"  ttfb {r.t_first - r.t_request:.3f}s  synth {r.t_end - r.t_request:.3f}s  "
              f"trim lead {r.trimmed_lead_s * 1000:.0f}ms "
              f"tail {r.trimmed_tail_s * 1000:.0f}ms  dur {r.duration_s:.2f}s  {text}")

    # 2) two chunks back-to-back through the scheduler into the cable, recorded on CABLE Output
    sched = Scheduler(mixer, 0.0, emit=metrics.emit)
    runner = asyncio.create_task(sched.run())
    rec = Recorder()
    print(f"recording: {rec.device.name} [{rec.device.hostapi}] {rec.rate} Hz")
    plays = results[:2]
    with rec:
        mixer.start()
        await asyncio.sleep(0.5)
        t = now()
        chunks = []
        for i, r in enumerate(plays):
            c = PcmChunk(utterance_id="out:test:0", commit_id=i, seq=i, channel=Channel.OUT, pcm=r.pcm,
                         sample_rate=r.sample_rate, duration_s=r.duration_s, t_src_start=t, t_src_end=None,
                         deadline=sched.deadline_for(t), t_tts_first=r.t_first)
            chunks.append(c)
        sched.submit(chunks[1])           # out of order on purpose: seq 1 first
        await asyncio.sleep(0.05)
        sched.submit(chunks[0])
        await asyncio.sleep(sum(r.duration_s for r in plays) + 1.0)
        out_latency_s = mixer.output_latency_s
        mixer.stop()
    runner.cancel()

    t_rec0, sig = rec.signal()
    out_path = Path("logs") / f"cable_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    save_wav(out_path, sig, rec.rate)

    # 3) analysis
    lat, corrs = [], []
    for c in chunks:
        ref = np.frombuffer(c.pcm, "<i2").astype(np.float32) / 32768
        lag, peak = find_offset(sig, ref)
        t_heard = t_rec0 + lag / rec.rate
        lat.append(t_heard - c.t_play_start)
        corrs.append(peak)
    order_ok = lat and (chunks[0].t_play_start < chunks[1].t_play_start)
    gap_ms = (chunks[1].t_play_start - (chunks[0].t_play_start + chunks[0].duration_s)) * 1000

    ttfb = [r.t_first - r.t_request for r in results]
    synth = [r.t_end - r.t_request for r in results]
    summary = dict(
        tts_ttfb_p50=statistics.median(ttfb), tts_ttfb_p95=pctl(ttfb, 95),
        tts_synth_p50=statistics.median(synth), tts_synth_p95=pctl(synth, 95),
        trim_lead_ms=statistics.median(r.trimmed_lead_s * 1000 for r in results),
        trim_tail_ms=statistics.median(r.trimmed_tail_s * 1000 for r in results),
        cable_latency_ms=[round(x * 1000, 1) for x in lat], correlation=[round(x, 4) for x in corrs],
        seq_order_ok=bool(order_ok), scheduled_gap_ms=round(gap_ms, 2), cable_gaps=mixer.cable_gaps,
        output_latency_ms=round(out_latency_s * 1000, 1), recording=str(out_path),
    )
    metrics.emit("run", summary=summary)
    metrics.close()
    print()
    for k, v in summary.items():
        print(f"  {k:22s} {v}")


if __name__ == "__main__":
    asyncio.run(main())
