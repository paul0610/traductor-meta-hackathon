import numpy as np

from interprete.audio.onsets import BLOCK_S, SpeechOnsets
from interprete.audio.recorder import Recorder


def heard(segments, seconds=8.0):
    """A quiet room with speech in `segments`, block by block, as the mic hands it over."""
    rng = np.random.default_rng(0)
    detector = SpeechOnsets()
    for i in range(int(seconds / BLOCK_S)):
        t = i * BLOCK_S
        loud = any(a <= t < b for a, b in segments)
        detector.feed(100.0 + t, rng.normal(0, 0.1 if loud else 0.001, 320).astype(np.float32))
    return list(detector.onsets)


def test_a_phrase_opens_where_speech_comes_back_after_a_pause():
    assert np.allclose(heard([(0.5, 1.5), (3.0, 4.0), (5.0, 6.0)]), [103.0, 105.0], atol=0.03)


def test_a_breath_inside_speech_opens_nothing():
    assert np.allclose(heard([(0.5, 1.5), (3.0, 4.0), (4.1, 5.0)]), [103.0], atol=0.03)


def test_a_click_opens_nothing():
    assert np.allclose(heard([(0.5, 1.5), (3.0, 3.04), (5.0, 6.0)]), [105.0], atol=0.03)


def test_a_room_with_no_voice_opens_nothing():
    assert heard([]) == []


def recorded(blocks):
    rec = Recorder.__new__(Recorder)
    rec.rate = 1000
    rec.blocks = [(t, np.ones(10, np.float32)) for t in blocks]
    return rec.signal()


def test_a_lost_stretch_stays_on_the_file_as_silence():
    t0, x = recorded([5.00, 5.01, 5.02, 5.22, 5.23])
    assert t0 == 5.00
    assert len(x) == 240
    assert x[30:220].sum() == 0 and x[220] == 1


def test_callbacks_that_only_wobble_add_nothing():
    _, x = recorded([5.00, 5.012, 5.019, 5.031, 5.04])
    assert len(x) == 50
