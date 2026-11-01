from types import SimpleNamespace

import numpy as np
import pytest

from interprete.audio import capture
from interprete.audio.capture import MicCapture


def fed(monkeypatch, times):
    mic = MicCapture()
    mic.rate = 48000
    for t in times:
        monkeypatch.setattr(capture, "block_start", lambda *_: t)
        mic._callback(np.zeros((960, 1), np.float32), 960, None, SimpleNamespace(input_overflow=False))
    return mic


def test_steady_blocks_lose_nothing(monkeypatch):
    assert fed(monkeypatch, [10.00, 10.02, 10.04]).lost_s == pytest.approx(0.0)


def test_a_gap_in_the_callbacks_is_lost_time(monkeypatch):
    assert fed(monkeypatch, [10.00, 10.02, 10.24, 10.26]).lost_s == pytest.approx(0.2)


def test_a_mic_that_never_ran_lost_nothing():
    assert MicCapture().lost_s == 0.0
