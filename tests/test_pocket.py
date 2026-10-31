import asyncio
import time

import numpy as np
import pytest

from interprete.tts.edge import S_PER_CHAR, S_PER_CLAUSE
from interprete.tts.pocket import FULL_SCALE, PACE, WARM_TEXT, PocketVoice, max_audio_s

RATE = 24000
OUT = 48000
CHUNK = RATE // 10


class Chunk:
    def __init__(self, x):
        self.x = x

    def numpy(self):
        return self.x


class FakeTts:
    """Yields flat audio; `endless` plays a clause that lost its end of speech."""

    sample_rate = RATE

    def __init__(self, level=0.5, chunks=5, endless=False, wait=0.0):
        self.level = level
        self.chunks = chunks
        self.endless = endless
        self.wait = wait
        self.texts = []
        self.running = 0
        self.most = 0
        self.stopped = False

    def generate_audio_stream(self, state, text, *, copy_state, stop):
        assert copy_state, "the voice state is reused for every clause"
        self.texts.append(text)
        self.running += 1
        self.most = max(self.most, self.running)
        try:
            for _ in range(100_000 if self.endless else self.chunks):
                if stop.is_set():
                    self.stopped = True
                    return
                time.sleep(self.wait)
                yield Chunk(np.full(CHUNK, self.level, np.float32))
        finally:
            self.running -= 1


def started(tts, **kw):
    voice = PocketVoice("english", "michael", OUT, load=lambda *a: (tts, "state"), **kw)

    async def go():
        await voice.start()
        tts.texts.clear()

    asyncio.run(go())
    return voice


def samples(r):
    return np.frombuffer(r.pcm, "<i2").astype(np.float32) / 32768.0


def test_start_loads_and_warms_up_with_a_short_clause():
    tts = FakeTts()
    voice = PocketVoice("english", "michael", OUT, load=lambda *a: (tts, "state"))
    asyncio.run(voice.start())
    assert tts.texts == [WARM_TEXT]


def test_a_clause_comes_back_at_the_cable_rate():
    r = asyncio.run(started(FakeTts()).say("Good morning."))
    assert r.sample_rate == OUT
    assert abs(r.duration_s - 0.5) < 0.1
    assert len(r.pcm) == 2 * round(r.duration_s * OUT)
    assert r.t_request <= r.t_first <= r.t_end <= r.t_ready


def test_the_gain_raises_the_level():
    r = asyncio.run(started(FakeTts(level=0.1), gain_db=6.0).say("Good morning."))
    assert abs(np.median(samples(r)) - 0.1995) < 0.01


def test_a_loud_clause_is_not_clipped():
    r = asyncio.run(started(FakeTts(level=0.9), gain_db=6.0).say("Good morning."))
    assert np.abs(samples(r)).max() <= FULL_SCALE + 0.001
    assert np.median(samples(r)) > 0.8                # it still comes out loud, just without the flat tops


def test_a_clause_that_lost_its_end_of_speech_is_cut():
    tts = FakeTts(endless=True)
    text = "I have five years."
    r = asyncio.run(started(tts).say(text))
    assert tts.stopped
    assert r.duration_s <= max_audio_s(text) + 2 * CHUNK / RATE


def test_clauses_go_one_at_a_time():
    tts = FakeTts(wait=0.02)
    voice = started(tts)

    async def go():
        await asyncio.gather(voice.say("one"), voice.say("two"))

    asyncio.run(go())
    assert tts.most == 1
    assert sorted(tts.texts) == ["one", "two"]


def test_a_rate_shortens_the_clause():
    normal = asyncio.run(started(FakeTts(chunks=20)).say("Good morning."))
    fast = asyncio.run(started(FakeTts(chunks=20)).say("Good morning.", rate_pct=25))
    assert abs(fast.duration_s / normal.duration_s - 0.8) < 0.05


def test_a_cancelled_clause_stops_generating():
    tts = FakeTts(endless=True, wait=0.01)
    voice = started(tts)

    async def go():
        task = asyncio.create_task(voice.say("A clause nobody needs any more."))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(100):
            if tts.stopped:
                return
            await asyncio.sleep(0.01)

    asyncio.run(go())
    assert tts.stopped


def test_a_load_failure_reaches_the_caller():
    def broken(*a):
        raise OSError("no weights")

    with pytest.raises(OSError):
        asyncio.run(PocketVoice("english", "michael", OUT, load=broken).start())


def test_a_measured_voice_uses_its_own_pace():
    voice = PocketVoice("english", "george", OUT)
    assert voice.pace == PACE[("english", "george")]


def test_an_unmeasured_voice_falls_back_on_edge_pace():
    assert PocketVoice("english", "nobody", OUT).pace == (S_PER_CHAR, S_PER_CLAUSE)
