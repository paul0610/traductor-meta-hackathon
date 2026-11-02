import asyncio
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

from interprete import replay
from interprete.replay import DEMO, Replay, Session, Speakers, load
from interprete.tts.shaping import TtsResult

RATE = 48000


def track(path: Path, seconds: float, level: float = 0.5) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.full(int(16000 * seconds), level) * 32767).astype("<i2").tobytes())


def session_folder(tmp_path: Path) -> Path:
    events = [
        dict(ev="state", what="fixture_start", t_start=0.0),
        dict(ev="state", what="fixture_start_in", t_start=0.0),
        dict(ev="asr", channel="in", kind="partial", t_recv=0.05, text="Hello"),
        dict(ev="asr", channel="in", kind="speechComplete", t_recv=0.10, text="Hello there."),
        dict(ev="commit", channel="in", seq=0, commit_id=0, utterance_id="in:0", text_src="Hello there.", t_commit=0.10,
             t_src_start=0.0, t_src_end=0.1),
        dict(ev="mt", channel="in", seq=0, t=0.15, t_mt=0.15, text_tgt="Hola."),
        dict(ev="asr", channel="out", kind="partial", t_recv=0.20, text="Buenos"),
        dict(ev="commit", channel="out", seq=0, commit_id=0, utterance_id="out:1", text_src="Buenos días.", t_commit=0.25,
             t_src_start=0.15, t_src_end=0.25),
        dict(ev="commit", channel="out", seq=1, commit_id=1, utterance_id="out:1", text_src="este", t_commit=0.30,
             t_src_start=0.25, t_src_end=0.30),
        dict(ev="commit", channel="out", seq=2, commit_id=2, utterance_id="out:1", text_src="Gracias.", t_commit=0.35,
             t_src_start=0.30, t_src_end=0.35),
        dict(ev="mt", channel="out", seq=0, t=0.30, t_mt=0.30, text_tgt="Good morning."),
        dict(ev="mt", channel="out", seq=1, t=0.36, failed="filler"),
        dict(ev="mt", channel="out", seq=2, t=0.40, t_mt=0.40, text_tgt="Thank you."),
    ]
    (tmp_path / "session.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    (tmp_path / "about.json").write_text(json.dumps(dict(recorded="2026-11-02T08:00:00", commit="abc1234", delay_s=0.0)),
                                         encoding="utf-8")
    track(tmp_path / "out.wav", 0.3)
    track(tmp_path / "in.wav", 0.2)
    return tmp_path


class FakeVoice:
    def __init__(self):
        self.said = []

    async def start(self):
        pass

    def rate_for(self, text, src_s):
        return 0

    async def say(self, text, *, rate_pct=0):
        self.said.append(text)
        pcm = (np.full(int(RATE * 0.1), 0.3) * 32767).astype("<i2").tobytes()
        return TtsResult(pcm, RATE, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


class FakeSink:
    rate = RATE

    def __init__(self):
        self.chunks, self.tracks = [], []

    def play(self, chunk, start_at):
        self.chunks.append((chunk.seq, start_at))

    def clear(self):
        pass

    def add(self, samples, start_at, gain):
        self.tracks.append((len(samples), start_at, gain))

    def busy(self):
        return False


class FakeUi:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name, *a))


def test_a_session_loads_with_its_tracks_and_where_they_start(tmp_path):
    s = load(session_folder(tmp_path))
    assert s.delay_s == 0.0
    assert set(s.tracks) == {"out", "in"}
    assert s.tracks["out"][1] == 0.0 and len(s.tracks["out"][0]) == 4800
    assert abs(s.length - 0.4) < 1e-9


def test_the_replay_speaks_in_order_skips_what_was_not_said_and_shows_both_sides(tmp_path):
    voice, sink, ui = FakeVoice(), FakeSink(), FakeUi()
    r = Replay(load(session_folder(tmp_path)), voice, sink, ui, lead_s=0.05, tail_s=0.0)
    asyncio.run(r.run())
    assert voice.said == ["Good morning.", "Thank you."]
    assert [seq for seq, _ in sink.chunks] == [0, 2]                 # the filler is a tombstone, not a stall
    assert sink.chunks[0][1] < sink.chunks[1][1]
    assert r.played == [0, 2]
    assert ("their_final", "Hello there.") in ui.calls and ("their_translation", "Hola.") in ui.calls
    assert ("mine", "Buenos días.", "Good morning.") in ui.calls
    gains = sorted(g for _, _, g in sink.tracks)
    assert len(gains) == 2 and gains[0] < gains[1]                     # my Spanish under, their English up


def test_speakers_place_each_sound_at_its_time_and_join_blocks_without_a_slip():
    t = [100.0]
    sink = Speakers(file=Path("unused.wav"), rate=1000, clock=lambda: t[0])
    ramp = np.arange(1, 31, dtype=np.float32) / 100
    sink.add(ramp, 100.015, 1.0)                                       # starts 15 samples into the first 10-sample block
    out = np.zeros((10, 1), np.float32)
    got = []
    for _ in range(5):
        sink._callback(out, 10, None, None)
        got.append(out[:, 0].copy())
        t[0] += 0.0103                                                 # a clock that drifts never shifts a started sound
    x = np.concatenate(got)
    assert np.all(x[:15] == 0)
    assert np.array_equal(x[15:45], ramp)
    assert not sink.busy()


def test_the_shipped_demo_is_complete_and_says_where_it_came_from():
    s = load(DEMO)
    assert set(s.tracks) == {"out", "in"}
    about = s.about
    assert about["commit"] and about["recorded"].startswith("2026-")
    assert about["measured"]["out"]["played"] == about["measured"]["out"]["commits"] > 10
    spoken = [e for e in s.events if e["ev"] == "mt" and e["channel"] == "out" and e.get("text_tgt")]
    assert len(spoken) == about["measured"]["out"]["played"]
    assert "commit" in replay.describe(s) and about["commit"] in replay.describe(s)


def test_the_replay_needs_nothing_that_only_windows_has():
    code = ("import sys\n"
            "for name in ('winsound', 'pycaw', 'comtypes', 'pyaudiowpatch', 'keyboard', 'cv2', 'pyvirtualcam'):\n"
            "    sys.modules[name] = None\n"
            "import interprete.replay\n"
            "print('ok')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-800:]


def test_a_folder_without_a_session_says_what_it_needs(tmp_path):
    try:
        load(tmp_path)
    except SystemExit as exc:
        assert "session.jsonl" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_console_subtitles_skip_partials(capsys):
    ui = replay.ConsoleUi()
    ui.their_partial("Hel")
    ui.mine("Buenos")
    ui.their_final("Hello there.")
    ui.mine("Buenos días.", "Good morning.")
    out = capsys.readouterr().out
    assert "Hel\n" not in out and "Buenos\n" not in out
    assert "Hello there." in out and "Good morning." in out


def test_an_empty_session_has_no_length_and_no_delay():
    s = Session(Path("."), [], {})
    assert s.delay_s == 0.0 and s.length == 0.0 and s.tracks == {}
