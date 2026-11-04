import asyncio
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
import pytest

from interprete import app
from interprete.audio import speaker
from interprete.audio.capture import MicCapture
from interprete.audio.devices import Device
from interprete.audio.fixture import replay
from interprete.audio.onsets import SpeechOnsets
from interprete.audio.room import FLOOR_S, LOUD_DBFS, TAIL_S, Room, dbfs, speakers_mode
from interprete.ui.status import Phase, running, sound_for

BLOCK_S = 0.02


def block(dbfs_level: float | None, n: int = 320) -> bytes:
    """20 ms at 16 kHz: a tone at this RMS level, or digital silence."""
    if dbfs_level is None:
        return bytes(2 * n)
    amp = 32768 * 10 ** (dbfs_level / 20) * np.sqrt(2)
    return (amp * np.sin(2 * np.pi * 440 * np.arange(n) / 16000)).astype("<i2").tobytes()


def test_levels_are_read_in_dbfs():
    assert dbfs(block(None)) == pytest.approx(-90.3, abs=0.1)
    assert dbfs(block(-20)) == pytest.approx(-20, abs=0.2)
    assert dbfs(b"") == pytest.approx(-90.3, abs=0.1)


@pytest.mark.parametrize("setting, output, on", [
    ("auto", "Altavoces (Realtek(R) Audio)", True),
    ("auto", "Auriculares (WH-1000XM4)", False),
    ("auto", "Headphones (Realtek(R) Audio)", False),
    ("auto", "", True),                              # no name to go by: the safe way
    ("0", "Altavoces (Realtek(R) Audio)", False),     # headphones on the jack that keep the speakers' name
    ("1", "Auriculares (WH-1000XM4)", True),
])
def test_speakers_unless_the_output_is_named_like_headphones(setting, output, on):
    assert speakers_mode(setting, output) is on


def test_the_mic_waits_from_the_first_loud_block_until_a_tail_after_the_last():
    room = Room()
    room.hear(100.0, block(None))
    assert not room.deaf(100.0)
    room.hear(100.02, block(-30))
    assert room.deaf(100.03)
    room.hear(100.04, block(None))
    assert room.deaf(100.04 + TAIL_S - 0.01) and not room.deaf(100.04 + TAIL_S + 0.01)


def test_what_is_too_quiet_to_come_back_as_words_leaves_the_mic_open():
    room = Room()
    for k in range(10):
        room.hear(100 + k * BLOCK_S, block(LOUD_DBFS - 3))
    assert not room.deaf(100.3)


def test_the_spanish_voice_closes_the_mic_before_its_sound_even_leaves():
    room = Room(voice=lambda t: 5.0 <= t < 7.0)
    assert room.deaf(5.5) and not room.deaf(7.5)


def test_a_steady_hum_stops_closing_the_mic_but_the_call_over_it_still_does():
    room = Room()
    t = 100.0
    for _ in range(int((FLOOR_S + 1) / BLOCK_S)):    # a fan in their room, through the call, for six seconds
        room.hear(t, block(-45))
        t += BLOCK_S
    room.hear(t, block(-45))
    assert not room.deaf(t + TAIL_S + BLOCK_S)
    room.hear(t + 1.0, block(-25))                   # and now they speak
    assert room.deaf(t + 1.0 + BLOCK_S)


def test_the_tap_hears_each_block_and_hands_it_on_untouched():
    room, got = Room(), []
    feed = room.tap(lambda t, b: got.append((t, b)))
    loud = block(-20)
    feed(100.0, loud)
    assert got == [(100.0, loud)] and room.deaf(100.1)


def mic(gate=None):
    m = MicCapture()
    m.rate = 16000
    m.gate = gate
    seen = []
    m.add_consumer(lambda t, b: seen.append(b.copy()))
    return m, seen


def test_every_mic_consumer_gets_silence_of_the_same_length_while_the_room_says_so():
    m, seen = mic(gate=lambda t: True)
    m._callback(np.full((320, 1), 0.3, np.float32), 320, None, SimpleNamespace(input_overflow=False))
    assert len(seen[0]) == 320 and not seen[0].any() and m.gated == 1
    m, seen = mic()
    m._callback(np.full((320, 1), 0.3, np.float32), 320, None, SimpleNamespace(input_overflow=False))
    assert np.allclose(seen[0], 0.3) and m.gated == 0


def test_a_gated_stretch_counts_as_quiet_and_stays_out_of_the_room_reading():
    onsets = SpeechOnsets()
    rng = np.random.default_rng(0)
    t = 0.0
    for k in range(200):                             # 4 s: the room, then a voice, so there is a threshold
        x = rng.normal(0, 0.001 if k < 100 else 0.1, 320).astype(np.float32)
        onsets.feed(t, x)
        t += BLOCK_S
    before = list(onsets._levels)
    for _ in range(20):                              # the mic gated, 0.4 s
        onsets.feed(t, np.zeros(320, np.float32))
        t += BLOCK_S
    assert list(onsets._levels) == before
    n = len(onsets.onsets)
    for _ in range(10):                              # and I speak as it opens
        onsets.feed(t, rng.normal(0, 0.1, 320).astype(np.float32))
        t += BLOCK_S
    assert len(onsets.onsets) == n + 1


def test_the_mic_fixture_waits_too_when_a_run_asks_for_it():
    async def go():
        q, stop = asyncio.Queue(), asyncio.Event()
        await replay(block(-20) * 3, q, stop, gate=lambda t: True)
        chunks = [q.get_nowait()[1] for _ in range(q.qsize())]
        assert len(chunks) == 3 and all(c == bytes(640) for c in chunks)
    asyncio.run(go())


@pytest.mark.parametrize("flags, phase", [
    (dict(raw=False, muted=False, held=False, connected=True, waiting=True), Phase.LISTENING),
    (dict(raw=False, muted=False, held=True, connected=True, waiting=True), Phase.PAUSED),
    (dict(raw=False, muted=True, held=False, connected=True, waiting=True), Phase.MUTED),
    (dict(raw=False, muted=False, held=False, connected=False, waiting=True), Phase.STARTING),
])
def test_the_button_says_when_the_mic_waits(flags, phase):
    assert running(**flags) is phase


@pytest.mark.parametrize("before, after, name", [
    (Phase.LIVE, Phase.LISTENING, None),             # every sentence on speakers: no cue, or the mic waits for it
    (Phase.LISTENING, Phase.LIVE, None),
    (Phase.PAUSED, Phase.LISTENING, None),
    (Phase.STARTING, Phase.LISTENING, "live"),
    (Phase.LISTENING, Phase.PAUSED, "floor"),
    (Phase.MUTED, Phase.LISTENING, "back"),
])
def test_the_mic_waiting_makes_no_sound(before, after, name):
    assert sound_for(before, after) == name


def test_i_took_the_floor_with_a_partial_or_when_the_mic_opened_a_phrase():
    out = SimpleNamespace(t_spoke=float("-inf"), speech=SimpleNamespace(onsets=[]))
    assert not app.Outgoing.spoke_after(out, 10.0)
    out.speech.onsets = [9.0, 10.4]
    assert app.Outgoing.spoke_after(out, 10.0) and not app.Outgoing.spoke_after(out, 10.5)
    out.t_spoke = 11.0
    assert app.Outgoing.spoke_after(out, 10.5)


def say_chain(spoke_after):
    played, events, ended = [], [], []

    async def say(text):
        return SimpleNamespace(pcm=b"\x00\x01", sample_rate=16000, t_end=1.0, t_request=0.5, duration_s=0.4)

    return SimpleNamespace(
        q_say=asyncio.Queue(), voice_on=True, voice_es=SimpleNamespace(say=say), spoke_after=spoke_after,
        speaker=SimpleNamespace(behind_s=lambda: 0.0, play=lambda p, r: played.append(p) or 2.0),
        ui=SimpleNamespace(call_start=lambda *a, **k: None, call_end=lambda key, text=None, **k: ended.append(text)),
        emit=lambda ev, **f: events.append(f),
    ), played, events, ended


def run_say(chain, items, t=8.0):
    async def go():
        for item in items:
            chain.q_say.put_nowait(item)
        task = asyncio.create_task(app.Incoming.say_worker(chain))
        await asyncio.sleep(0.05)
        task.cancel()
    with patch.object(app, "now", lambda: t):
        asyncio.run(go())


def test_once_i_answered_their_clause_is_only_read():
    chain, played, events, _ = say_chain(lambda t: t < 5.0)
    run_say(chain, [(1, "¿Y en producción?", 4.0), (2, "¿Cuánto tiempo?", 6.0)])
    assert len(played) == 1 and events[0] == {"channel": "in", "seq": 1, "skipped": "floor"}


def test_if_i_begin_while_it_is_being_made_it_is_not_played():
    answers = iter([False, True])
    chain, played, events, ended = say_chain(lambda t: next(answers))
    run_say(chain, [(1, "¿Y en producción?", 4.0)])
    assert played == [] and events == [{"channel": "in", "seq": 1, "skipped": "floor"}]
    assert ended == ["you took the floor: only read"]


def test_a_clause_that_would_start_too_late_after_they_said_it_is_only_read():
    chain, played, events, _ = say_chain(lambda t: False)
    run_say(chain, [(1, "a nosotros hoy.", 1.0), (2, "¿Podrías decirme?", 5.0)], t=1.0 + speaker.MAX_LATE_S + 0.5)
    assert len(played) == 1 and events[0] == {"channel": "in", "seq": 1, "skipped": "late"}


def fake_app(*, flag=False, wav=None, setting="auto"):
    events = []
    out, inc = SimpleNamespace(room=None), SimpleNamespace(room=None, speaker=None)
    return SimpleNamespace(out=out, inc=inc, args=SimpleNamespace(speakers=flag, wav_in=wav, wav_out=wav),
                           settings=SimpleNamespace(speakers=setting), emit=lambda ev, **f: events.append(f)), events


@pytest.fixture
def output(monkeypatch):
    name = ["Altavoces (Realtek(R) Audio)"]
    monkeypatch.setattr(app.devices, "default_output", lambda: Device(21, name[0], "Windows WASAPI", 48000, 0, 2))
    monkeypatch.setattr(app.devices, "headphones_elsewhere", lambda output: None)     # this PC's jack is not the test's
    return name


def test_on_speakers_both_chains_share_one_room(output):
    a, events = fake_app()
    r = app.App.speakers(a)
    assert isinstance(r, Room) and a.out.room is r and a.inc.room is r
    assert events == [{"what": "speakers", "on": True, "output": output[0], "setting": "auto"}]
    a.inc.speaker = SimpleNamespace(deaf=lambda t: True)
    assert r.deaf(0.0)                               # the Spanish voice closes it too


def test_headphones_and_fixtures_need_no_room_unless_asked(output):
    output[0] = "Auriculares (WH-1000XM4)"
    a, _ = fake_app()
    assert app.App.speakers(a) is None and a.out.room is None
    a, _ = fake_app(wav="fixture.wav")
    assert app.App.speakers(a) is None
    a, _ = fake_app(wav="fixture.wav", flag=True)
    assert isinstance(app.App.speakers(a), Room)
    a, _ = fake_app()
    a.inc = None                                     # nothing hears the loopback: nothing to close the mic with
    assert app.App.speakers(a) is None


@pytest.mark.parametrize("room, said", [(Room(), True), (None, False)])
def test_the_line_says_speakers(room, said):
    chain = SimpleNamespace(lags=[], sync_err=0.0, delay_s=0.0, sync_late=0, policy=None, floor=None,
                            mixer=SimpleNamespace(state=SimpleNamespace(value="SILENT")), cable_warning="",
                            output_warning="", video_warning="", t_start=0.0, asr=None, incoming=None,
                            metrics=SimpleNamespace(ok=True), fault="", room=room)
    assert ("SPEAKERS" in app.Outgoing.bar_text(chain, 1.0)) is said


def test_headphones_plugged_in_while_windows_plays_on_the_speakers_are_named(output, monkeypatch, capsys):
    monkeypatch.setattr(app.devices, "headphones_elsewhere", lambda out: "Auriculares (Realtek(R) Audio)")
    a, events = fake_app()
    a.out.output_warning = ""
    app.App.speakers(a)
    assert {"what": "headphones_elsewhere", "headphones": "Auriculares (Realtek(R) Audio)", "output": output[0]} in events
    assert a.out.output_warning == " │ HEADPHONES NOT DEFAULT"
    assert "Auriculares (Realtek(R) Audio) is connected, but Windows plays on Altavoces" in capsys.readouterr().err


@pytest.mark.parametrize("default, others, found", [
    ("Altavoces (Realtek(R) Audio)", ["Auriculares (Realtek(R) Audio)"], "Auriculares (Realtek(R) Audio)"),
    ("Auriculares (Realtek(R) Audio)", ["Altavoces (Realtek(R) Audio)"], None),      # already on them
    ("Altavoces (Realtek(R) Audio)", ["CABLE Input (VB-Audio Virtual Cable)"], None),
    ("Altavoces (Realtek(R) Audio)", ["Varios micrófonos (Realtek(R) Audio)"], None),  # an input
    ("", ["Auriculares (Realtek(R) Audio)"], None),
])
def test_headphones_elsewhere_looks_only_at_other_wasapi_outputs(monkeypatch, default, others, found):
    devs = [{"name": default, "max_output_channels": 2}] + [
        {"name": n, "max_output_channels": 0 if "micr" in n else 2} for n in others]
    monkeypatch.setattr(app.devices.sd, "query_hostapis",
                        lambda: [{"name": "MME", "devices": [0]}, {"name": "Windows WASAPI", "devices": list(range(len(devs)))}])
    monkeypatch.setattr(app.devices.sd, "query_devices", lambda i: devs[i])
    assert app.devices.headphones_elsewhere(default) == found
