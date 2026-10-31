import numpy as np
import pytest

from interprete.video.clap import audio_claps, contact, offsets

RATE = 48000
FPS = 30


def audio_with(times, seconds=12.0):
    rng = np.random.default_rng(0)
    x = rng.normal(0, 0.002, int(seconds * RATE)).astype(np.float32)
    for t in times:
        i = int(t * RATE)
        x[i: i + 480] += 0.8 * np.exp(-np.arange(480) / 100.0).astype(np.float32)
    return x


def video_with(contacts, seconds=12.0, fps=FPS):
    """Hands speed up for 0.15 s and stop dead at each contact, somewhere between two frames."""
    t = np.arange(int(seconds * fps)) / fps
    motion = np.full(len(t), 0.0005)
    for c in contacts:
        moving = (t > c - 0.15) & (t - 1 / fps < c)
        motion[moving] = 0.02 + 0.2 * (t[moving] - (c - 0.15))
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


@pytest.mark.parametrize("fps", [30, 14])
def test_the_contact_is_within_half_a_frame(fps):
    for c in (2.0, 4.52, 7.01):
        t, motion = video_with([c], fps=fps)
        assert abs(contact(t, motion, c + 0.1) - c) <= 0.5 / fps + 1e-9


def test_the_title_card_and_getting_into_position_are_not_claps():
    t, motion = video_with([3.0, 6.0])
    motion[t == 1.0] = 1.0
    motion[(t > 1.3) & (t < 1.9)] = 0.3
    assert np.allclose(offsets([3.0, 6.0], t, motion), [0.0, 0.0], atol=1 / FPS)


def test_nothing_moving_finds_nothing():
    t = np.arange(90) / FPS
    assert contact(t, np.zeros(90), 1.5) is None
    assert contact(t, np.random.default_rng(0).normal(0.002, 0.001, 90), 1.5) is None


def test_hands_that_never_stop_are_no_contact():
    t = np.arange(90) / FPS
    assert contact(t, np.full(90, 0.05), 1.5) is None


def test_a_clap_at_the_edge_of_the_video_is_no_contact():
    t = np.arange(90) / FPS
    assert contact(t, np.full(90, 0.05), 50.0) is None


def test_offset_is_audio_minus_video():
    assert np.allclose(offsets([2.1, 4.6], *video_with([2.0, 4.5])), [0.1, 0.1], atol=1 / FPS)


def test_a_clap_out_of_frame_does_not_count():
    assert len(offsets([2.0, 9.0], *video_with([2.0]))) == 1
