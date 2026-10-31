import numpy as np

from interprete.tts.stretch import GRAIN_S, wsola

RATE = 24000


def tone(hz, seconds):
    t = np.arange(int(RATE * seconds)) / RATE
    return np.sin(2 * np.pi * hz * t).astype(np.float32)


def pitch(x):
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.argmax(spectrum) * RATE / len(x)


def test_the_audio_gets_shorter_by_the_speed():
    assert abs(len(wsola(tone(150, 2.0), 1.25, RATE)) / RATE - 1.6) < 0.05


def test_the_pitch_stays():
    assert abs(pitch(wsola(tone(150, 2.0), 1.25, RATE)) - 150) < 2


def test_grains_join_without_a_click():
    x = tone(150, 2.0)
    edge = int(GRAIN_S * RATE)
    assert np.abs(np.diff(wsola(x, 1.25, RATE)[edge:-edge])).max() < 1.5 * np.abs(np.diff(x)).max()


def test_a_clip_too_short_to_cut_comes_back_as_it_is():
    x = tone(150, 0.02)
    assert wsola(x, 1.25, RATE) is x
