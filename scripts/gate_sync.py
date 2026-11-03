"""Gate S (SPEC §5): runs the dub on the Spanish fixture and judges the sync from its JSONL and cable recording.

    uv run python scripts/gate_sync.py                       # D = RTT_DELAY_S, no one speaking
    uv run python scripts/gate_sync.py --delay 6.0
    uv run python scripts/gate_sync.py --log logs/app_x.jsonl   # judge a run that already happened
    uv run python scripts/gate_sync.py --voice               # my own voice and camera, as the gate asks

Three questions, in order: did the scheduler meet its deadlines (the gate), how far
are those deadlines from when each clause was really said (`t_src_start` against the
fixture's word times, or the microphone for a real voice), and is the voice under the
dub really D seconds late (cross correlation of the cable recording with the fixture).
`--voice` also leaves in logs/ a 20 s clip of what the other side gets, judged by eye.
OBS stays closed. It holds a person's face and voice, and logs/ never reaches the repo.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import wave
import winsound
from pathlib import Path

import numpy as np
import soxr

from interprete import config
from interprete.audio.fixture import RATE, read_wav
from interprete.audio.recorder import find_offset
from interprete.metrics.jsonl import read
from interprete.metrics.summary import _stats, gate_s, summarize
from interprete.metrics.truth import clause_truth, turn_onsets

FIXTURE = Path("logs/fixture_es.wav")
VOICE_S = 60.0
AFTER_S = 15.0
BEEP = (880, 400)              # the speaker is looking at the camera, not at the terminal
CLIP_S = 20.0
CLIP_SOURCE = "video=OBS Virtual Camera:audio=CABLE Output (VB-Audio Virtual Cable)"
MATCH_S = 1.6                  # a phrase further than this from a clause's start is not that clause's


def new_log(before: set[Path]) -> Path:
    new = sorted(set(Path("logs").glob("app_*.jsonl")) - before)
    if not new:
        raise SystemExit("the app wrote no log")
    return new[-1]


def run_app(delay: float | None) -> Path:
    before = set(Path("logs").glob("app_*.jsonl"))
    cmd = [sys.executable, "-m", "interprete.app", "--dub", "--out-only", "--record-cable", "--no-overlay",
           "--wav-out", str(FIXTURE)]
    if delay is not None:
        cmd += ["--delay", str(delay)]
    subprocess.run(cmd, check=True)
    return new_log(before)


def record_clip(path: Path, seconds: float = CLIP_S) -> subprocess.Popen:
    """Stamped on arrival: the OBS driver and the cable keep clocks hours apart, and left alone no player sounds the file."""
    return subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-use_wallclock_as_timestamps", "1",
                             "-f", "dshow", "-rtbufsize", "256M", "-audio_buffer_size", "20", "-i", CLIP_SOURCE,
                             "-t", str(seconds), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                             "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)])


def run_voice(delay: float | None) -> Path:
    """The gate as defined: 60 s of a real voice with the camera, and the clip. D passes before anything real comes out."""
    d = config.load().delay_s if delay is None else delay
    before = set(Path("logs").glob("app_*.jsonl"))
    cmd = [sys.executable, "-m", "interprete.app", "--dub", "--out-only", "--record-cable", "--record-mic", "--no-overlay",
           "--seconds", str(VOICE_S + d + AFTER_S)]
    if delay is not None:
        cmd += ["--delay", str(delay)]
    app = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    camera = threading.Event()

    def relay() -> None:
        for line in app.stderr:
            sys.stderr.write(line)
            if line.startswith(("camera", "cámara")):      # RTT_UI_LANG en or es
                camera.set()

    threading.Thread(target=relay, daemon=True).start()
    print("Loading, about 20 s. Do not speak yet.", flush=True)
    while not camera.wait(0.5):
        if app.poll() is not None:
            raise SystemExit("The app ended without opening the camera: see the warning above.")
    t_go = time.monotonic()
    print("\nSPEAK NOW! Spanish, facing the camera, with a one-second pause between phrases, until the second beep.\n",
          flush=True)
    winsound.Beep(*BEEP)
    time.sleep(d + 1.0)
    clip_path = Path("logs") / f"clip_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
    clip = record_clip(clip_path)
    time.sleep(max(0.0, t_go + VOICE_S - time.monotonic()))
    print("\nYou can stop. A few more seconds for the rest to come out.\n", flush=True)
    winsound.Beep(*BEEP)
    app.wait()
    clip.wait()
    print(f"clip of what the other side sees: {clip_path}")
    return new_log(before)


def state(events: list[dict], what: str) -> dict | None:
    return next((e for e in events if e.get("what") == what), None)


def read_recording(rec: dict) -> tuple[np.ndarray, int]:
    with wave.open(rec["path"], "rb") as w:
        rate = w.getframerate()
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768.0, rate


def source_errors(events: list[dict], delay_s: float) -> None:
    """`t_src_start` minus the truth, per source; and the sync the other side really gets."""
    words_path = FIXTURE.with_suffix(".words.json")
    start = state(events, "fixture_start")
    if start is None:
        return
    if not words_path.exists():
        print("no ground truth: run scripts/make_fixtures.py")
        return
    rows = sorted((e for e in events if e["ev"] == "commit" and e.get("phase") == "report"
                   and e.get("channel") == "out" and e.get("text_src")), key=lambda e: e["seq"])
    truth = clause_truth(rows, json.loads(words_path.read_text(encoding="utf-8")), start["t_start"])
    print(f"clauses with ground truth: {len(truth)} of {len(rows)}")
    for source in ("mic_onset", "speech_start", "partial_arrival"):
        errs = [r["t_src_start"] - truth[r["seq"]] for r in rows if r["seq"] in truth and r["t_src_source"] == source]
        print(f"  t_src_start - truth, {source}: {_stats(errs)}")
    real = [r["t_play_start"] - (truth[r["seq"]] + delay_s) for r in rows
            if r["seq"] in truth and r.get("t_play_start") is not None]
    print(f"  English voice start - (truth + D): {_stats(real)}")
    if real:
        print(f"  p95 of |real error|: {np.percentile(np.abs(real), 95):.3f} s")
    ends = [a["t_play_start"] + a["duration_s"] - (truth[b["seq"]] + delay_s) for a, b in zip(rows, rows[1:])
            if b["seq"] in truth and a.get("t_play_start") is not None and a.get("duration_s") is not None]
    print(f"  English voice end - (real start of the next clause + D): {_stats(ends)}")


def real_voice_errors(events: list[dict], delay_s: float, offset_s: float) -> None:
    """What the other side sees, which `sync_err` cannot show: a phrase that opens after a pause is in the microphone, and the lips open there."""
    rec = state(events, "mic_recording")
    if rec is None:
        return
    x, rate = read_recording(rec)
    onsets = turn_onsets(x, rate, rec["t_start"])
    rows = [e for e in events if e["ev"] == "commit" and e.get("phase") == "report" and e.get("channel") == "out"
            and e.get("t_play_start") is not None]
    nearest: dict[int, tuple[dict, float]] = {}
    for t in onsets:
        r = min(rows, key=lambda r: abs(r["t_src_start"] - t), default=None)
        held = nearest.get(r["seq"]) if r is not None else None
        if r is not None and abs(r["t_src_start"] - t) <= MATCH_S and (held is None or abs(r["t_src_start"] - t) < abs(r["t_src_start"] - held[1])):
            nearest[r["seq"]] = (r, t)
    pairs = list(nearest.values())
    print(f"phrases in the microphone: {len(onsets)}; with their clause: {len(pairs)}; anchored to the microphone: "
          f"{sum(r['t_src_source'] == 'mic_onset' for r in rows)} of {len(rows)}")
    print(f"  t_src_start - real start: {_stats([r['t_src_start'] - t for r, t in pairs])}")
    lips = [r["t_play_start"] - (t + delay_s + offset_s) for r, t in pairs]
    print(f"  English voice - lips opening a phrase: {_stats(lips)}")
    if lips:
        off = np.abs(lips)
        print(f"  p95 of |error| {np.percentile(off, 95):.3f} s; over 0.5 s: {int((off > 0.5).sum())}; over 1 s: {int((off > 1.0).sum())}")


def voice_delay(events: list[dict]) -> None:
    rec = state(events, "cable_recording")
    start = state(events, "fixture_start")
    if rec is None or start is None:
        return
    x, rate = read_recording(rec)
    ref = soxr.resample(np.frombuffer(read_wav(FIXTURE), "<i2").astype(np.float32) / 32768.0, RATE, rate)
    lag, peak = find_offset(x, ref)
    print(f"voice under the dub on the cable: {rec['t_start'] + lag / rate - start['t_start']:.3f} s late "
          f"(peak {peak:.2f}; includes the cable's own latency)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=None)
    ap.add_argument("--delay", type=float, default=None)
    ap.add_argument("--voice", action="store_true", help="my own voice and camera, and a clip of what the other side gets")
    args = ap.parse_args()
    path = Path(args.log) if args.log else run_voice(args.delay) if args.voice else run_app(args.delay)
    events = read(path)
    s = summarize(events)
    print(json.dumps(s["out"]["dub"], indent=2, ensure_ascii=False))
    print(f"commits {s['out']['commits']}, played {s['out']['played']}, bytes {s['out']['bytes_pct']} %, "
          f"gaps {s['out']['cable_gaps']}, estimated D {s['out']['d_estimate_s']}, ASR lag p50 {s['out']['asr']['lag_s']['p50']}")
    print(f"log: {path}\n")
    fails = gate_s(s)
    print(f"Gate S (numbers): {'PASS' if not fails else 'FAIL'}")
    for f in fails:
        print(f"  - {f}")
    if s["out"]["dub"] is not None:
        source_errors(events, s["out"]["dub"]["delay_s"])
        real_voice_errors(events, s["out"]["dub"]["delay_s"], config.load().av_offset_ms / 1000)
    voice_delay(events)
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
