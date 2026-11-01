import asyncio
import json
import threading
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from interprete.config import Settings
from interprete.tts import own_voice
from interprete.tts.backup import WithBackup, local_voice, make_voice
from interprete.tts.edge_session import EdgeVoice
from interprete.tts.own_voice import (FORGET, MAX_S, MAX_SPEED, MIN_S, NO_ACCESS, OFFER, OWN, PACE_TEXTS, READ, STANDARD, TERMS_URL,
                                      TYPICAL_CHARS, TYPICAL_S, Enrollment, Store, clip_speed, fit_pace, review)
from interprete.tts.pocket import PACE, PocketVoice

RATE = 24000
OUT = 48000
MIC = 48000


class Chunk:
    def __init__(self, x):
        self.x = x

    def numpy(self):
        return self.x


class FakeTts:
    """One tenth of a second per character unless told, so the pace of a clone is known."""

    sample_rate = RATE

    def __init__(self, clones=True, per_char=0.1):
        self.has_voice_cloning = clones
        self.per_char = per_char
        self.prompts = []

    def get_state_for_audio_prompt(self, audio, truncate=False):
        self.prompts.append(audio)
        return f"state of {audio}"

    def generate_audio_stream(self, state, text, *, copy_state, stop):
        self.state_used = state
        yield Chunk(np.full(int(RATE * (self.per_char * len(text) + 0.5)), 0.4, np.float32))


def voice(tts=None, **kw):
    v = PocketVoice("english", "michael", OUT, load=lambda *a: (tts or FakeTts(), "catalog"),
                    export=lambda state, path: path.write_text("state"), **kw)
    asyncio.run(v.start())
    return v


def speech(seconds, level=0.3, room=0.001, rate=MIC):
    """A second of room at each end, and in between a tone that pauses like someone reading."""
    n = int(seconds * rate)
    t = np.arange(n) / rate
    x = np.full(n, room, np.float32)
    talking = (t % 0.6 < 0.4) & (t >= 1) & (t < seconds - 1)
    x[talking] = (level * np.sin(2 * np.pi * 180 * t[talking])).astype(np.float32)
    return x


class FakeTake:
    def __init__(self, x=None, rate=MIC, fails=False):
        self.x = speech(14) if x is None else x
        self.rate = rate
        self.fails = fails
        self.stopped = 0

    def __call__(self, mic_name):
        return self

    def start(self):
        if self.fails:
            raise RuntimeError("no mic")

    def stop(self):
        self.stopped += 1
        return self.x, self.rate


def enrollment(store, v=None, **kw):
    e = Enrollment(v or voice(), store, "michael", "Realtek", **{"take": FakeTake(), "play": lambda p: None, **kw})
    return e


def recorded(store, v=None, **kw):
    """Up to the moment the person has to decide."""
    e = enrollment(store, v, **kw)

    async def go():
        await e.script()
        await e.record()
        await e.finish()

    asyncio.run(go())
    return e


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "voice")


def test_with_nothing_saved_the_card_offers(store):
    e = enrollment(store)
    e.opening()
    assert e.prompt is OFFER


def test_saying_not_now_closes_the_card(store):
    e = enrollment(store)
    e.opening()
    asyncio.run(e.later())
    assert e.prompt is None


def test_on_free_weights_it_says_what_is_missing_and_where(store):
    pages = []
    e = enrollment(store, voice(FakeTts(clones=False)), browse=pages.append)
    asyncio.run(e.script())
    assert e.prompt is NO_ACCESS
    asyncio.run(e.terms())
    assert pages == [TERMS_URL]


def test_the_text_opens_with_the_consent():
    assert own_voice.TO_READ.startswith("This is my own voice, and I agree")
    assert own_voice.TO_READ in READ.body


def test_a_take_is_cloned_and_measured_and_nothing_is_kept_yet(store):
    e = recorded(store)
    assert e.prompt.title == "Así suenas en inglés"
    assert e.busy
    assert store.sample.exists() and store.state.exists() and store.preview.exists()
    assert store.read("english") is None
    assert e.voice.state == f"state of {store.sped_up}"


def test_the_pace_of_the_clone_is_measured_on_what_it_said(store):
    e = recorded(store)
    assert abs(e._pace[0] - 0.1) < 0.002
    assert abs(e._pace[1] - 0.5) < 0.1


def test_a_slow_reader_is_cloned_again_from_a_faster_clip_and_the_clip_is_not_kept(store):
    e = recorded(store)
    assert e.voice.tts.prompts[-2:] == [store.sample, store.sped_up]
    assert store.sample.exists() and not store.sped_up.exists()


def test_a_reader_at_a_normal_pace_is_cloned_once(store):
    tts = FakeTts(per_char=0.03)
    e = recorded(store, voice(tts))
    assert tts.prompts[-1] == store.sample
    assert tts.prompts.count(store.sped_up) == 0


def test_the_clip_goes_as_fast_as_the_pace_needs_and_no_further():
    assert clip_speed((0.0, TYPICAL_S)) == 1.0
    assert clip_speed((0.0, TYPICAL_S * 0.8)) == 1.0
    assert clip_speed((0.0, TYPICAL_S * 1.2)) == pytest.approx(1.2)
    assert clip_speed((0.1, 0.5)) == MAX_SPEED
    assert clip_speed((TYPICAL_S / TYPICAL_CHARS, 0.0)) == pytest.approx(1.0)


def test_keeping_it_saves_the_voice_and_its_pace_and_consent(store):
    e = recorded(store)
    calls = []
    e.on_voice = lambda: calls.append(1)
    asyncio.run(e.keep())
    assert e.prompt is OWN
    assert calls == [1]
    assert e.voice.own and e.voice.voice == str(store.state) and e.voice.pace == e._pace
    assert e.voice.name == "pocket:english:own_voice"
    assert e.voice.warning == " │ TU VOZ"
    saved = store.read("english")
    assert saved.active and saved.pace == pytest.approx(e._pace)
    assert json.loads(store.meta.read_text(encoding="utf-8"))["consent"] == own_voice.TO_READ


def test_discarding_it_leaves_nothing_and_goes_back_to_the_standard_voice(store):
    e = recorded(store)
    asyncio.run(e.discard())
    assert e.prompt is OFFER
    assert not e.voice.own and e.voice.voice == "michael" and e.voice.pace == PACE[("english", "michael")]
    assert list(store.folder.iterdir()) == []


def test_a_preview_starts_with_the_real_recording(store):
    plays = []
    e = recorded(store, play=plays.append)
    asyncio.run(e.play())
    assert plays == [store.preview]
    x, rate = sf.read(store.preview, dtype="float32")
    assert rate == OUT
    assert len(x) / rate > own_voice.PREVIEW_REAL_S


def test_a_short_take_is_refused_and_keeps_nothing(store):
    e = recorded(store, take=FakeTake(speech(MIN_S - 2)))
    assert e.prompt.title == "Hay que grabarla otra vez"
    assert not e.busy
    assert not store.sample.exists()
    assert e.voice.state == "catalog"


def test_a_clone_that_fails_leaves_the_voice_it_had_and_nothing_on_disk(store):
    v = voice()

    async def broken(*a):
        v.state = "half a clone"
        raise OSError("no room on disk")

    v.clone = broken
    e = recorded(store, v)
    assert e.prompt.title == "Hay que grabarla otra vez"
    assert v.voice == "michael" and v.state == "state of michael"
    assert list(store.folder.iterdir()) == []


def test_a_mic_that_does_not_open_says_so(store):
    e = enrollment(store, take=FakeTake(fails=True))
    asyncio.run(e.record())
    assert e.prompt.title == "Hay que grabarla otra vez"


def test_the_recording_stops_by_itself(store, monkeypatch):
    monkeypatch.setattr(own_voice, "MAX_S", 0.01)
    take = FakeTake()
    e = enrollment(store, take=take)

    async def go():
        await e.record()
        await asyncio.wait_for(e._ticker, 5.0)

    asyncio.run(go())
    assert take.stopped == 1
    assert e.prompt.title == "Así suenas en inglés"


def test_closing_while_recording_lets_the_mic_go(store):
    take = FakeTake()
    e = enrollment(store, take=take)

    async def go():
        await e.record()
        e.close()

    asyncio.run(go())
    assert take.stopped == 1
    assert e.prompt is None and not e.busy


def test_closing_before_deciding_leaves_nothing_on_disk(store):
    e = recorded(store)
    e.close()
    assert list(store.folder.iterdir()) == []


def test_a_kept_voice_opens_as_own_and_can_step_aside(store):
    e = recorded(store)
    asyncio.run(e.keep())
    again = enrollment(store)
    again.opening()
    assert again.prompt is OWN
    asyncio.run(again.standard())
    assert again.prompt is STANDARD
    assert not again.voice.own
    assert store.read("english").active is False
    next_run = enrollment(store)
    next_run.opening()
    assert next_run.prompt is STANDARD
    asyncio.run(next_run.own())
    assert next_run.voice.own and next_run.prompt is OWN
    assert store.read("english").active


def test_forgetting_asks_first_and_can_be_called_off(store):
    e = recorded(store)
    asyncio.run(e.keep())
    asyncio.run(e.act("ask_forget"))
    assert e.prompt is FORGET
    assert store.read("english") is not None
    asyncio.run(e.act("back"))
    assert e.prompt is OWN


def test_forgetting_a_kept_voice_deletes_every_file(store):
    e = recorded(store)
    asyncio.run(e.keep())
    asyncio.run(e.forget())
    assert e.prompt is OFFER
    assert list(store.folder.iterdir()) == []


def test_a_state_never_goes_to_other_weights(store):
    e = recorded(store)
    asyncio.run(e.keep())
    assert store.read("english_18m_2026-10") is None


def test_half_a_voice_is_no_voice(store):
    e = recorded(store)
    asyncio.run(e.keep())
    store.state.unlink()
    assert store.read("english") is None


def test_review_cuts_the_silence_at_the_ends_and_sets_the_peak():
    clean, problems = review(speech(14), MIC)
    assert problems == []
    assert 11.5 <= len(clean) / MIC <= 12.8
    assert abs(np.abs(clean).max() - own_voice.PEAK) < 0.01


def test_review_warns_about_clipping_and_room_noise():
    assert any("Saturó" in p for p in review(speech(14, level=1.0), MIC)[1])
    assert any("ruido" in p for p in review(speech(14, room=0.03), MIC)[1])


def test_review_refuses_a_take_with_no_voice_or_no_audio():
    assert review(np.zeros(0, np.float32), MIC)[0] is None
    assert review(speech(14, room=0.2), MIC)[0] is None


def test_the_recording_never_exceeds_what_pocket_reads():
    assert MAX_S == 30


def test_the_pace_fit_recovers_a_known_voice():
    chars = [len(t) for t in PACE_TEXTS]
    per_char, per_clause = fit_pace(chars, [0.04 * c + 0.3 for c in chars])
    assert per_char == pytest.approx(0.04)
    assert per_clause == pytest.approx(0.3)


def settings(**over):
    return SimpleNamespace(**{**dict(tts_engine="pocket", tts_backup="edge", tts_voice="en-US-GuyNeural", pocket_model="english",
                                     pocket_voice="michael", pocket_gain_db=3.0), **over})


def saved_voice(store, active=True):
    store.folder.mkdir(parents=True)
    store.state.write_text("state")
    store.write("english", (0.05, 0.2))
    if not active:
        store.activate(False)


def test_a_new_run_speaks_with_the_saved_voice_at_its_pace(store):
    saved_voice(store)
    made = make_voice(settings(), OUT, size=1, store=store)
    assert isinstance(made, WithBackup)
    assert made.primary.voice == str(store.state) and made.primary.own
    assert made.primary.pace == (0.05, 0.2)
    assert made.warning == " │ TU VOZ"


def test_a_voice_put_aside_is_not_used(store):
    saved_voice(store, active=False)
    made = make_voice(settings(), OUT, size=1, store=store)
    assert made.primary.voice == "michael" and not made.primary.own


def test_with_nothing_saved_the_catalog_voice_speaks(store):
    made = make_voice(settings(), OUT, size=1, store=store)
    assert made.primary.voice == "michael" and made.warning == ""


def test_the_network_voice_has_no_clone(store):
    assert isinstance(make_voice(settings(tts_engine="edge"), OUT, size=1, store=store), EdgeVoice)


def test_only_a_loaded_local_voice_can_take_a_clone(store):
    made = make_voice(settings(), OUT, size=1, store=store)
    assert local_voice(made) is None
    made.primary.tts = FakeTts()
    assert local_voice(made) is made.primary


def test_a_voice_is_not_swapped_in_the_middle_of_a_clause():
    v = voice()
    done = threading.Event()

    def swap():
        asyncio.run(v.switch("george"))
        done.set()

    with v._one_at_a_time:
        threading.Thread(target=swap, daemon=True).start()
        assert not done.wait(0.05)
    assert done.wait(1.0)
    assert v.voice == "george"
