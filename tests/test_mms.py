import asyncio
import time
from pathlib import Path

import numpy as np

from interprete.tts.mms import PACE, RATE, WARM_TEXT, MmsVoice
from interprete.tts.shaping import FAST_RATE_PCT
from interprete.tts.spoken import spoken

OUT = 48000


class FakeModel:
    """Flat audio at `level`, with the silence VITS leaves around a clause."""

    def __init__(self, level=0.5, seconds=1.0, wait=0.0):
        self.level = level
        self.seconds = seconds
        self.wait = wait
        self.calls = []
        self.running = 0
        self.most = 0

    def load(self, model, threads):
        self.loaded = (model, threads)
        return self.speak

    def speak(self, text, speaking_rate):
        self.calls.append((text, speaking_rate))
        self.running += 1
        self.most = max(self.most, self.running)
        time.sleep(self.wait)
        self.running -= 1
        pad = np.zeros(RATE // 5, np.float32)
        return np.concatenate([pad, np.full(int(RATE * self.seconds), self.level, np.float32), pad])


def voice(model, **kw):
    v = MmsVoice("facebook/mms-tts-eng", OUT, load=model.load, **kw)
    asyncio.run(v.start())
    return v


def test_start_warms_up_on_a_clause():
    model = FakeModel()
    v = voice(model, threads=3)
    assert model.loaded == ("facebook/mms-tts-eng", 3)
    assert model.calls[0][0] == spoken(WARM_TEXT)
    assert v.name == "mms:mms-tts-eng"


def test_a_clause_comes_out_at_the_cable_rate_without_its_silence():
    r = asyncio.run(voice(FakeModel()).say("Good morning."))
    assert r.sample_rate == OUT
    assert 1.0 <= r.duration_s < 1.15
    assert r.trimmed_lead_s > 0.1 and r.trimmed_tail_s > 0.1
    assert len(r.pcm) == round(r.duration_s * OUT) * 2


def test_what_it_cannot_say_is_written_out_first():
    model = FakeModel()
    asyncio.run(voice(model).say("We grew 40% with Núñez"))
    assert model.calls[-1][0] == "We grew forty percent with Nunyez"


def test_rate_pct_shortens_the_clause_and_the_base_rate_goes_to_the_model():
    model = FakeModel(seconds=2.0)
    v = voice(model, speaking_rate=1.15)
    plain = asyncio.run(v.say("Good morning."))
    fast = asyncio.run(v.say("Good morning.", rate_pct=25))
    assert model.calls[-1][1] == 1.15
    assert fast.duration_s < plain.duration_s * 0.85


def test_a_clause_with_nothing_to_say_never_reaches_the_model():
    model = FakeModel()
    v = voice(model)
    before = len(model.calls)
    r = asyncio.run(v.say("..."))
    assert len(model.calls) == before
    assert r.pcm == b"" and r.duration_s == 0.0


def test_a_loud_clause_does_not_clip():
    r = asyncio.run(voice(FakeModel(level=1.4)).say("Good morning."))
    assert np.abs(np.frombuffer(r.pcm, "<i2")).max() <= 32767 * 0.99 + 1


def test_clauses_go_one_at_a_time():
    model = FakeModel(wait=0.02)
    v = voice(model)

    async def two():
        await asyncio.gather(v.say("One."), v.say("Two."))

    asyncio.run(two())
    assert model.most == 1


def test_pace_follows_the_speaking_rate_and_the_text_as_said():
    model = FakeModel()
    assert voice(model, speaking_rate=1.3).pace == PACE[1.3]
    assert voice(model, speaking_rate=1.2).pace == PACE[1.15]
    v = voice(model)
    assert v.rate_for("Good morning.", None) == 0
    assert v.rate_for("About 120", 0.9) == FAST_RATE_PCT        # "about one hundred and twenty" is much longer than it looks


def test_no_other_voice_is_locked():
    lock = (Path(__file__).parents[1] / "uv.lock").read_text(encoding="utf-8")
    assert "pocket-tts" not in lock and "edge-tts" not in lock
