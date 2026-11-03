import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from interprete import app
from interprete.audio import speaker
from interprete.audio.devices import Device
from interprete.audio.speaker import GATE_TAIL_S, Gate, SpeakerVoice
from interprete.tts.spoken import spoken_es

DEVICE = Device(0, "Headphones", "Windows WASAPI", 16000, 0, 2)


@pytest.fixture
def clock(monkeypatch):
    t = [10.0]
    monkeypatch.setattr(speaker, "now", lambda: t[0])
    return t


def pcm(seconds, rate=16000, value=0.5):
    return (np.full(int(seconds * rate), value) * 32767).astype("<i2").tobytes()


def test_the_gate_hands_silence_of_the_same_length_while_the_voice_speaks():
    got = []
    gate = Gate(lambda t, block: got.append((t, block)), lambda t: 1.0 <= t < 2.0)
    for t in (0.5, 1.5, 2.5):
        gate(t, b"\x01\x02" * 4)
    assert got == [(0.5, b"\x01\x02" * 4), (1.5, bytes(8)), (2.5, b"\x01\x02" * 4)]
    assert gate.blocked == 1


def test_the_voice_is_deaf_while_it_speaks_and_for_a_tail_after(clock):
    voice = SpeakerVoice(device=DEVICE, open_stream=lambda cb: None)
    assert not voice.deaf(10.0)
    end = voice.play(pcm(2.0), 16000)
    assert end == pytest.approx(12.0)
    assert voice.play(pcm(1.0), 16000) == pytest.approx(13.0)      # a second clause waits for the first
    assert voice.deaf(12.9) and voice.deaf(13.0 + GATE_TAIL_S - 0.01) and not voice.deaf(13.0 + GATE_TAIL_S + 0.01)
    assert voice.behind_s() == pytest.approx(3.0)
    clock[0] = 10.5
    voice.hush()                                                    # F6: what has not played is dropped
    assert voice.behind_s() == 0.0 and voice.deaf(10.5 + GATE_TAIL_S - 0.01)


def test_it_plays_the_clauses_back_to_back_then_silence():
    voice = SpeakerVoice(device=DEVICE, open_stream=lambda cb: None)
    voice.play(pcm(0.001, value=0.5), 16000)                        # 16 samples
    voice.play(pcm(0.001, value=-0.25), 16000)
    out = np.ones((40, 1), np.float32)
    voice._fill(out, 40, None, None)
    assert np.allclose(out[:16, 0], 0.5, atol=1e-3) and np.allclose(out[16:32, 0], -0.25, atol=1e-3)
    assert not out[32:].any()


def test_a_clause_at_another_rate_is_resampled_to_the_headphones():
    voice = SpeakerVoice(device=DEVICE, open_stream=lambda cb: None)
    voice.play(pcm(0.5, rate=48000), 48000)
    assert sum(len(x) for x in voice._q) == pytest.approx(8000, abs=8)


def test_spanish_keeps_its_letters_and_says_numbers_in_words():
    assert spoken_es("En 2026 bajamos un 40% el año.") == "en dos mil veintiséis bajamos un cuarenta por ciento el año"
    assert spoken_es("Somos 2.500; ¿web?") == "somos dos mil quinientos ueb"


def run_worker(chain, items):
    async def go():
        for item in items:
            chain.q_say.put_nowait(item)
        task = asyncio.create_task(app.Incoming.say_worker(chain))
        await asyncio.sleep(0.05)
        task.cancel()
    asyncio.run(go())


def worker_chain(*, on=True, behind=0.0):
    played, events = [], []

    async def say(text):
        return SimpleNamespace(pcm=b"\x00\x01", sample_rate=16000, t_end=1.0, t_request=0.5, duration_s=0.4)

    quiet = lambda *a, **k: None
    return SimpleNamespace(
        q_say=asyncio.Queue(), voice_on=on, voice_es=SimpleNamespace(say=say),
        speaker=SimpleNamespace(behind_s=lambda: behind, play=lambda p, r: played.append((p, r)) or 2.0),
        ui=SimpleNamespace(call_start=quiet, call_end=quiet), emit=lambda ev, **f: events.append((ev, f)),
    ), played, events


def test_their_clauses_are_said_in_order_while_the_voice_is_on():
    chain, played, events = worker_chain()
    run_worker(chain, [(1, "Hola."), (2, "¿Qué tal?")])
    assert played == [(b"\x00\x01", 16000), (b"\x00\x01", 16000)]


def test_off_or_too_far_behind_they_are_only_read():
    chain, played, _ = worker_chain(on=False)
    run_worker(chain, [(1, "Hola.")])
    assert played == []
    chain, played, events = worker_chain(behind=speaker.MAX_BEHIND_S + 1)
    run_worker(chain, [(1, "Hola.")])
    assert played == [] and events == [("tts", {"channel": "in", "seq": 1, "skipped": "behind"})]


def test_f6_turns_the_voice_off_and_drops_what_has_not_played():
    hushed, said = [], []
    chain = SimpleNamespace(voice_on=True, speaker=SimpleNamespace(hush=lambda: hushed.append(1)),
                            emit=lambda ev, **f: None, ui=SimpleNamespace(mine=said.append))
    app.Incoming.toggle_voice(chain)
    assert chain.voice_on is False and hushed == [1] and "apagada" in said[-1]
    app.Incoming.toggle_voice(chain)
    assert chain.voice_on is True and hushed == [1]
