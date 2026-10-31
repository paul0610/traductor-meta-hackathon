import numpy as np

from interprete.video.clap import audio_claps, offsets, video_claps

RATE = 48000
FPS = 30


def audio_with(times, seconds=12.0):
    rng = np.random.default_rng(0)
    x = rng.normal(0, 0.002, int(seconds * RATE)).astype(np.float32)
    for t in times:
        i = int(t * RATE)
        x[i: i + 480] += 0.8 * np.exp(-np.arange(480) / 100.0).astype(np.float32)
    return x


def video_with(contacts, seconds=12.0):
    """Hands speed up for 0.15 s, stop dead at each contact."""
    t = np.arange(int(seconds * FPS)) / FPS
    motion = np.full(len(t), 0.2)
    for c in contacts:
        moving = (t > c - 0.15) & (t <= c)
        motion[moving] = 2.0 + 20.0 * (t[moving] - (c - 0.15))
    return t, motion


def test_each_clap_is_found_at_its_attack():
    found = audio_claps(audio_with([2.0, 4.5, 7.0]), RATE, 100.0)
    assert np.allclose(found, [102.0, 104.5, 107.0], atol=0.01)


def test_an_echo_is_not_a_second_clap():
    assert len(audio_claps(audio_with([2.0, 2.3]), RATE, 0.0)) == 1


def test_silence_has_no_claps():
    assert audio_claps(np.zeros(RATE, np.float32), RATE, 0.0) == []


def test_room_noise_has_no_claps():
    assert audio_claps(audio_with([]), RATE, 0.0) == []


def test_the_contact_is_where_the_hands_stop():
    found = video_claps(*video_with([2.0, 4.5, 7.0]))
    assert np.allclose(found, [2.0, 4.5, 7.0], atol=2 / FPS)


def test_nothing_moving_finds_nothing():
    t = np.arange(90) / FPS
    assert video_claps(t, np.zeros(90)) == []
    assert video_claps(t, np.random.default_rng(0).normal(0.5, 0.05, 90)) == []


def test_offset_is_audio_minus_video():
    assert np.allclose(offsets([2.0, 4.5], [1.9, 4.4]), [0.1, 0.1])


def test_a_clap_out_of_frame_does_not_count():
    assert offsets([2.0, 9.0], [2.1]) == [2.0 - 2.1]
