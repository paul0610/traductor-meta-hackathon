"""Mic capture → 16 kHz for the ASR, and mic → mixer (PASSTHROUGH) → CABLE Input, recorded on CABLE Output.

Reports the device actually opened (and fallbacks tried), block cadence, level,
the 16 kHz byte rate, and the passthrough latency mic → CABLE Output.

    uv run python scripts/test_mic.py --seconds 4      (talk or tap the desk while it runs)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from test_cable import Recorder, find_offset  # noqa: E402

from interprete import config  # noqa: E402
from interprete.audio.cable_mixer import CableMixer  # noqa: E402
from interprete.audio.capture import MicCapture, To16k  # noqa: E402
from interprete.audio.ring import Ring  # noqa: E402
from interprete.metrics.jsonl import Metrics  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=4.0)
    args = ap.parse_args()
    settings = config.load()
    metrics = Metrics(run_name="test_mic")

    mic = MicCapture(settings.mic_name, emit=metrics.emit)
    mixer = CableMixer(settings.cable_name, emit=metrics.emit)
    ring = Ring(mixer.rate)                      # 1 s capacity; the mixer keeps it ~60 ms deep
    to16 = None
    blocks: list[tuple[float, np.ndarray]] = []
    sent16 = [0]

    def consumer(t_capture: float, block: np.ndarray) -> None:
        blocks.append((t_capture, block))
        ring.write(block)
        sent16[0] += len(to16(block))

    mic.add_consumer(consumer)
    rec = Recorder()
    with rec:
        mic.start()
        to16 = To16k(mic.rate)
        assert mic.rate == mixer.rate, "passthrough needs a resampler when mic and cable rates differ"
        mixer.mic = ring
        mixer.start()
        print(f"mic: {mic.device.name} [{mic.device.hostapi}] {mic.rate} Hz; speak or tap for {args.seconds:.0f} s")
        time.sleep(args.seconds)
        mixer.stop()
        mic.stop()

    ts = np.array([t for t, _ in blocks])
    x = np.concatenate([b for _, b in blocks])
    intervals = np.diff(ts) * 1000
    t_rec0, sig = rec.signal()
    # passthrough latency: where the first 1.5 s of mic audio appears in the cable recording
    ref = x[int(0.5 * mic.rate): int(2.0 * mic.rate)]
    lag, peak = find_offset(sig, ref)
    t_mic = ts[0] + 0.5
    latency_ms = (t_rec0 + lag / rec.rate - t_mic) * 1000
    summary = dict(
        device=mic.device.name, hostapi=mic.device.hostapi, rate=mic.rate, blocks=len(blocks),
        block_interval_ms_p50=round(float(np.median(intervals)), 2),
        block_interval_ms_max=round(float(intervals.max()), 2),
        rms_dbfs=round(float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12)), 1),
        peak_dbfs=round(float(20 * np.log10(np.abs(x).max() + 1e-12)), 1),
        pcm16k_bytes_per_s=round(sent16[0] / (len(x) / mic.rate)),
        overflows=mic.overflows, passthrough_underruns=mixer.passthrough_underruns,
        passthrough_latency_ms=round(latency_ms, 1), passthrough_correlation=round(peak, 3),
    )
    metrics.emit("run", summary=summary)
    metrics.close()
    for k, v in summary.items():
        print(f"  {k:26s} {v}")


if __name__ == "__main__":
    main()
