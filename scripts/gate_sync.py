"""Gate S (SPEC §5): runs the dub on the Spanish fixture and judges the sync from its JSONL and cable recording.

    uv run python scripts/gate_sync.py                       # D = RTT_DELAY_S, no one speaking
    uv run python scripts/gate_sync.py --delay 6.0
    uv run python scripts/gate_sync.py --log logs/app_x.jsonl   # judge a run that already happened

Three questions, in order: did the scheduler meet its deadlines (the gate), how far
are those deadlines from when each clause was really said (`t_src_start` against the
fixture's word times), and is the voice under the dub really D seconds late (cross
correlation of the cable recording with the fixture). The 20 s clip of what the other
side sees needs the camera and is judged by eye.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import soxr

from interprete.audio.fixture import RATE, read_wav
from interprete.audio.recorder import find_offset
from interprete.metrics.jsonl import read
from interprete.metrics.summary import _stats, gate_s, summarize
from interprete.metrics.truth import clause_truth

FIXTURE = Path("logs/fixture_es.wav")


def run_app(delay: float | None) -> Path:
    before = set(Path("logs").glob("app_*.jsonl"))
    cmd = [sys.executable, "-m", "interprete.app", "--dub", "--out-only", "--record-cable", "--no-overlay",
           "--wav-out", str(FIXTURE)]
    if delay is not None:
        cmd += ["--delay", str(delay)]
    subprocess.run(cmd, check=True)
    new = sorted(set(Path("logs").glob("app_*.jsonl")) - before)
    if not new:
        raise SystemExit("the app wrote no log")
    return new[-1]


def state(events: list[dict], what: str) -> dict | None:
    return next((e for e in events if e.get("what") == what), None)


def source_errors(events: list[dict], delay_s: float) -> None:
    """`t_src_start` minus the truth, per source; and the sync the other side really gets."""
    words_path = FIXTURE.with_suffix(".words.json")
    start = state(events, "fixture_start")
    if start is None or not words_path.exists():
        print("sin verdad de terreno: corre scripts/make_fixtures.py")
        return
    rows = sorted((e for e in events if e["ev"] == "commit" and e.get("phase") == "report"
                   and e.get("channel") == "out" and e.get("text_src")), key=lambda e: e["seq"])
    truth = clause_truth(rows, json.loads(words_path.read_text(encoding="utf-8")), start["t_start"])
    print(f"cláusulas con verdad: {len(truth)} de {len(rows)}")
    for source in ("speech_start", "partial_arrival"):
        errs = [r["t_src_start"] - truth[r["seq"]] for r in rows if r["seq"] in truth and r["t_src_source"] == source]
        print(f"  t_src_start - verdad, {source}: {_stats(errs)}")
    real = [r["t_play_start"] - (truth[r["seq"]] + delay_s) for r in rows
            if r["seq"] in truth and r.get("t_play_start") is not None]
    print(f"  arranque de la voz inglesa - (verdad + D): {_stats(real)}")
    if real:
        print(f"  p95 de |error real|: {np.percentile(np.abs(real), 95):.3f} s")
    ends = [a["t_play_start"] + a["duration_s"] - (truth[b["seq"]] + delay_s) for a, b in zip(rows, rows[1:])
            if b["seq"] in truth and a.get("t_play_start") is not None and a.get("duration_s") is not None]
    print(f"  fin de la voz inglesa - (inicio real de la siguiente cláusula + D): {_stats(ends)}")


def voice_delay(events: list[dict]) -> None:
    rec = state(events, "cable_recording")
    start = state(events, "fixture_start")
    if rec is None or start is None:
        return
    with wave.open(rec["path"], "rb") as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768.0
    ref = soxr.resample(np.frombuffer(read_wav(FIXTURE), "<i2").astype(np.float32) / 32768.0, RATE, rate)
    lag, peak = find_offset(x, ref)
    print(f"voz bajo el doblaje en el cable: {rec['t_start'] + lag / rate - start['t_start']:.3f} s tarde "
          f"(pico {peak:.2f}; suma la latencia del propio cable)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=None)
    ap.add_argument("--delay", type=float, default=None)
    args = ap.parse_args()
    path = Path(args.log) if args.log else run_app(args.delay)
    events = read(path)
    s = summarize(events)
    print(json.dumps(s["out"]["dub"], indent=2, ensure_ascii=False))
    print(f"commits {s['out']['commits']}, sonaron {s['out']['played']}, bytes {s['out']['bytes_pct']} %, "
          f"huecos {s['out']['cable_gaps']}, D estimado {s['out']['d_estimate_s']}")
    print(f"log: {path}\n")
    fails = gate_s(s)
    print(f"Compuerta S (cifras): {'PASA' if not fails else 'NO PASA'}")
    for f in fails:
        print(f"  - {f}")
    if s["out"]["dub"] is not None:
        source_errors(events, s["out"]["dub"]["delay_s"])
    voice_delay(events)
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
