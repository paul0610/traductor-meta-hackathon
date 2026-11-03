"""Speakers, offline: does my mic send the other side their own words? Gate R.

    uv run python scripts/gate_speakers.py      # two runs, without and with the speakers' gate: about 4 min, uses the API

The two fixtures from scripts/make_fixtures.py take turns as in an interview: four questions and four answers, one
answered at once and one cut into by the next question. My mic track carries the room's echo of their voice and, as
it plays, of the Spanish voice, 9 dB down and 40 ms late, as speakers next to a laptop put them there. Nothing reaches
a real device: the cable and the Spanish voice are pulled at real-time pace into memory. The verdict reads each run's
JSONL for whose words each clause my mic sent was: it passes if with the gate none were theirs, and without it some
were, so the echo is real.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import soxr

RATE = 16000
ECHO = 0.35                     # 9 dB down
ECHO_DELAY_S = 0.04
NOISE_DBFS = -62.0              # a real mic is never digital silence
OUT = Path("logs/gate_speakers")


def read(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768


def write(path: Path, x: np.ndarray, rate: int = RATE) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


def tracks() -> tuple[np.ndarray, np.ndarray, list]:
    """Their track for the loopback, mine with their echo for the mic, and who speaks when."""
    en, es = read(Path("logs/fixture_en.wav")), read(Path("logs/fixture_es.wav"))
    cut = lambda x, a, b: x[int(a * RATE):int(b * RATE)]
    turns = [("them", 1.0, cut(en, 2.4, 5.4)), ("me", 11.0, cut(es, 0.6, 7.1)),       # I wait for the Spanish
             ("them", 19.0, cut(en, 8.1, 13.1)), ("me", 25.0, cut(es, 7.6, 15.2)),    # I answer at once
             ("them", 34.1, cut(en, 15.6, 19.6)), ("me", 45.1, cut(es, 15.3, 19.3)),
             ("me", 51.0, cut(es, 32.2, 37.9)), ("them", 53.0, cut(en, 22.4, 26.9))]  # they cut into my answer
    them = np.zeros(62 * RATE, np.float32)
    me = np.zeros(int(62.5 * RATE), np.float32)          # another length: the replay tells the tracks apart by it
    for who, at, seg in turns:
        track = them if who == "them" else me
        track[int(at * RATE):int(at * RATE) + len(seg)] += seg
    d_echo = int(ECHO_DELAY_S * RATE)
    me[d_echo:d_echo + len(them)] += ECHO * them[:len(me) - d_echo]
    me += np.random.default_rng(1).normal(0, 10 ** (NOISE_DBFS / 20), len(me)).astype(np.float32)
    return them, me, [(who, at, len(seg) / RATE) for who, at, seg in turns]


class Paced:
    """Pulls an output callback at real-time pace, as a sound card would, and hands each block to `keep`."""

    def __init__(self, callback, rate: int, frames: int, channels: int, keep) -> None:
        self.callback, self.rate, self.frames, self.channels, self.keep = callback, rate, frames, channels, keep
        self.done, self.latency = threading.Event(), 0.0

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        from interprete.clock import now

        k, t0, status = 0, time.perf_counter(), SimpleNamespace(output_underflow=False)
        while not self.done.is_set():
            buf = np.zeros((self.frames, self.channels), np.float32)
            t = now()
            self.callback(buf, self.frames, None, status)
            self.keep(t, buf[:, 0])
            k += 1
            time.sleep(max(0.0, t0 + k * self.frames / self.rate - time.perf_counter()))

    def stop(self) -> None:
        self.done.set()

    close = abort = stop


def run_one(d: Path, gated: bool) -> None:
    """In this process: the app on the tracks, its devices replaced, the Spanish voice echoed into my mic."""
    from interprete import app
    from interprete.audio import cable_mixer, fixture, speaker
    from interprete.clock import now

    d.mkdir(parents=True, exist_ok=True)
    them, me, turns = tracks()
    write(d / "them.wav", them)
    write(d / "me.wav", me)
    json.dump(turns, open(d / "turns.json", "w"))
    mine_len = len(me) * 2
    t0 = now()
    played = np.zeros(200 * RATE, np.float32)            # the Spanish voice on the app's clock, at 16 kHz

    def keep_voice(rate):
        def keep(t, x):
            y = soxr.resample(x, rate, RATE) if rate != RATE else x
            i = int((t - t0) * RATE)
            if 0 <= i < len(played) - len(y):
                played[i:i + len(y)] += y
        return keep

    speaker.SpeakerVoice._sounddevice = lambda self, cb: Paced(cb, self.rate, self.rate // 50, 1, keep_voice(self.rate))

    def cable(self):
        self._stream = Paced(self._callback, self.rate, self.block, self.channels, lambda t, x: None)
        self._stream.start()
        self.t_callback = now()

    cable_mixer.CableMixer._open = cable
    replay = fixture.replay

    async def echoed(pcm, q, stop, tap=None, gate=None):
        if len(pcm) != mine_len:
            return await replay(pcm, q, stop, tap, gate)
        t_start = now() + 0.05
        step = fixture.BLOCK * 2
        for k, i in enumerate(range(0, len(pcm), step)):
            t_cap = t_start + k * fixture.BLOCK / RATE
            if (wait := t_cap + fixture.BLOCK / RATE - now()) > 0:
                await asyncio.sleep(wait)
            x = np.frombuffer(pcm[i:i + step], "<i2").astype(np.float32) / 32768
            j = int((t_cap - ECHO_DELAY_S - t0) * RATE)
            if j >= 0:
                x = x + ECHO * played[j:j + len(x)]
            chunk = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
            if gate is not None and gate(t_cap):
                chunk = bytes(len(chunk))
            if tap is not None:
                tap(t_cap, chunk)
            await q.put((t_cap, chunk))
            if stop.is_set():
                break

    app.replay = echoed
    sys.argv = ["interprete.app", "--no-overlay", "--delay", "0", "--wav-out", str(d / "me.wav"),
                "--wav-in", str(d / "them.wav"), "--voice-in", "--seconds", "90"] + (["--speakers"] if gated else [])
    before = set(Path("logs").glob("app_*.jsonl"))
    app.main()
    (d / "log.txt").write_text(str(sorted(set(Path("logs").glob("app_*.jsonl")) - before)[-1]))


def report(d: Path) -> dict:
    turns = json.load(open(d / "turns.json"))
    events = [json.loads(line) for line in open((d / "log.txt").read_text(), encoding="utf-8")]
    t0 = next(e["t_start"] for e in events if e.get("what") == "fixture_start")
    voice = [(e["t_play_end"] - t0 - e["duration_s"], e["t_play_end"] - t0) for e in events
             if e["ev"] == "tts" and e.get("channel") == "in" and e.get("t_play_end")]
    skipped = [e["skipped"] for e in events if e["ev"] == "tts" and e.get("channel") == "in" and e.get("skipped")]
    waits, since = [], None
    for e in events:
        if e.get("what") == "mic_wait":
            if e["on"]:
                since = e["t"] - t0
            elif since is not None:
                waits.append((since, e["t"] - t0))

    def whose(a, b):
        shares = {"me": 0.0, "them": 0.0, "voice": 0.0}
        for who, at, dur in turns:
            shares[who] += max(0.0, min(b, at + dur) - max(a, at))
        for lo, hi in voice:
            shares["voice"] += max(0.0, min(b, hi) - max(a, lo))
        return max(shares, key=shares.get)

    sent = {"me": 0, "them": 0, "voice": 0}
    print(f"== {d.name}: {(d / 'log.txt').read_text()}")
    for e in sorted((e for e in events if e["ev"] == "commit" and e.get("phase") == "report"
                     and e.get("channel") == "out"), key=lambda e: e["seq"]):
        a, b = e["t_src_start"] - t0, (e.get("t_src_end") or e["t_commit"]) - t0
        sent[w := whose(a, b)] += 1
        print(f"  {a:5.1f}-{b:5.1f} s  {w:5}  {e['text_src'][:50]!r} -> {e.get('text_tgt', '')[:50]!r}")
    mine = sum(dur for who, at, dur in turns if who == "me")
    lost = sum(max(0.0, min(at + dur, hi) - max(at, lo)) for who, at, dur in turns if who == "me" for lo, hi in waits)
    print(f"  clauses my mic sent, by whose words: {sent}; Spanish voice: {len(voice)} played, skipped {skipped}; "
          f"my speech the mic waited through: {lost:.1f} of {mine:.1f} s")
    return sent


def main() -> int:
    from interprete import utf8_output

    utf8_output()
    for name, flag in (("open", []), ("gated", ["--speakers"])):
        subprocess.run([sys.executable, __file__, "--run-one", str(OUT / name), *flag], check=True)
    without, with_gate = report(OUT / "open"), report(OUT / "gated")
    echo = lambda sent: sent["them"] + sent["voice"]
    ok = echo(with_gate) == 0 and echo(without) > 0
    print(f"R: {'PASS' if ok else 'FAIL'}: their words or the Spanish voice sent back, {echo(without)} without the "
          f"gate, {echo(with_gate)} with it")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--run-one" in sys.argv:
        run_one(Path(sys.argv[sys.argv.index("--run-one") + 1]), "--speakers" in sys.argv)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)                  # the keyboard hook thread cannot be stopped, as in the app's own __main__
    sys.exit(main())
