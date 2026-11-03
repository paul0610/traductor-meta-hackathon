"""Runs the interpreter and judges the run against the 60 s gate and gate A (SPEC §5), from its JSONL.

    uv run python scripts/gate_60s.py                      # synthetic voices from scripts/make_fixtures.py, no one speaking
    uv run python scripts/gate_60s.py --live               # 60 s of the real mic and system audio, sent to Meta (play the BBC video as the other side)
    uv run python scripts/gate_60s.py --log logs/app_x.jsonl   # judge a run that already happened

Exit code 0 only if both gates pass. No numbers, no gate.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from interprete.metrics.jsonl import read
from interprete.metrics.summary import gate_60s, gate_a, summarize


def run_app(live: bool, seconds: float) -> Path:
    before = set(Path("logs").glob("app_*.jsonl"))
    cmd = [sys.executable, "-m", "interprete.app", "--record-cable", "--no-overlay", "--no-yield", "--delay", "0"]
    if live:
        cmd += ["--seconds", str(seconds)]
    else:
        cmd += ["--wav-out", "logs/fixture_es.wav", "--wav-in", "logs/fixture_en.wav"]
    subprocess.run(cmd, check=True)
    new = sorted(set(Path("logs").glob("app_*.jsonl")) - before)
    if not new:
        raise SystemExit("the app wrote no log")
    return new[-1]


def verdict(name: str, fails: list[str]) -> bool:
    print(f"{name}: {'PASS' if not fails else 'FAIL'}")
    for f in fails:
        print(f"  - {f}")
    return not fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=None)
    ap.add_argument("--live", action="store_true", help="the real mic and system audio, sent to Meta; only on request")
    ap.add_argument("--seconds", type=float, default=60.0)
    args = ap.parse_args()
    path = Path(args.log) if args.log else run_app(args.live, args.seconds)
    s = summarize(read(path))
    print(json.dumps(s, indent=2, ensure_ascii=False))
    print(f"\nlog: {path}")
    ok_a = verdict("Gate A (both directions)", gate_a(s))
    ok_60 = verdict("60 s gate (commit -> first PCM, gaps, bytes)", gate_60s(s))
    return 0 if ok_a and ok_60 else 1


if __name__ == "__main__":
    raise SystemExit(main())
