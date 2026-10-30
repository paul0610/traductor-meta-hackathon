import numpy as np

from interprete.audio.delay import DelayLine

RATE = 48000
FRAMES = 480


def ramp(start, n):
    return np.arange(start, start + n, dtype=np.float32)


def test_output_is_silent_then_exactly_the_input_d_later():
    d = DelayLine(RATE, 0.1)
    got = []
    for k in range(30):
        d.write(ramp(k * FRAMES, FRAMES))
        got.append(d.read(FRAMES))
    x = np.concatenate(got)
    first = np.flatnonzero(x)[0]
    assert first == d.frames - FRAMES + 1                  # sample 0 is 0: the first nonzero is sample 1
    assert np.array_equal(x[first:], np.arange(1, len(x) - first + 1, dtype=np.float32))


def test_a_faster_writer_is_pulled_back_to_the_delay():
    d = DelayLine(RATE, 0.1)
    for k in range(200):
        d.write(ramp(0, FRAMES + 20))                      # 4 % fast
        d.read(FRAMES)
    assert d.jumps > 0
    assert abs(len(d._ring) - d.frames) <= d._slack + FRAMES


def test_a_stalled_writer_waits_for_the_level_instead_of_shrinking_the_delay():
    d = DelayLine(RATE, 0.1)
    for k in range(20):
        d.write(ramp(0, FRAMES))
        d.read(FRAMES)
    for _ in range(10):                                    # mic stalled for 100 ms
        d.read(FRAMES)
    assert d.jumps == 1
    assert np.all(d.read(FRAMES) == 0.0)
    for k in range(20):
        d.write(np.ones(FRAMES, np.float32))
        x = d.read(FRAMES)
    assert np.all(x == 1.0)
    assert abs(len(d._ring) - d.frames) <= FRAMES


def test_clear_starts_over_with_silence():
    d = DelayLine(RATE, 0.05)
    for _ in range(8):
        d.write(np.ones(FRAMES, np.float32))
        d.read(FRAMES)
    d.clear()
    d.write(np.ones(FRAMES, np.float32))
    assert np.all(d.read(FRAMES) == 0.0)
