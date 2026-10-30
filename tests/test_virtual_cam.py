import threading
import time

import numpy as np
import pytest

from interprete.video import virtual_cam
from interprete.video.virtual_cam import FrameDelay, VirtualCam, dshow_index


def frame(v):
    return np.full((2, 2, 3), v, np.uint8)


def test_pick_gives_the_newest_frame_at_least_d_old_and_drops_older_ones():
    d = FrameDelay(5.0)
    for t in (1.0, 2.0, 3.0):
        d.push(t, frame(int(t)))
    assert d.pick(5.9) is None                          # 1.0 is only 4.9 old
    assert d.pick(7.5)[0] == 2.0
    assert len(d) == 2
    assert d.pick(100.0)[0] == 3.0                      # the last frame stays: a dead webcam freezes, never black


def test_clear_starts_the_delay_over():
    d = FrameDelay(1.0)
    d.push(0.0, frame(1))
    d.clear()
    assert d.pick(10.0) is None


class FakeCap:
    def __init__(self):
        self.released = False
        self.n = 0

    def read(self):
        time.sleep(0.001)
        self.n += 1
        return True, frame(self.n % 250 + 1)

    def release(self):
        self.released = True


class FakeOut:
    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, f):
        self.sent.append(f)

    def sleep_until_next_frame(self):
        time.sleep(0.001)

    def close(self):
        self.closed = True


CARD = np.zeros((2, 2, 3), np.uint8)


def make(delay, live=lambda: False):
    out, cap = FakeOut(), FakeCap()
    v = VirtualCam(delay, live, make_webcam=lambda name: cap, make_output=lambda: out, card=CARD)
    return v, out, cap


def wait_for(cond, s=2.0):
    t_end = time.perf_counter() + s
    while not cond() and time.perf_counter() < t_end:
        time.sleep(0.002)
    assert cond()


def test_the_card_goes_out_until_a_frame_is_old_enough_then_the_delayed_video():
    v, out, cap = make(0.05)
    v.start()
    wait_for(lambda: any(f is not CARD for f in out.sent))
    assert out.sent[0] is CARD
    v.stop()
    assert cap.released and out.closed


def test_live_jumps_to_the_newest_frame_and_coming_back_shows_the_card_again():
    live = threading.Event()
    v, out, cap = make(0.2, live.is_set)
    v.start()
    wait_for(lambda: v.lag_s >= 0.2)
    live.set()
    wait_for(lambda: 0 < v.lag_s < 0.05)
    live.clear()
    wait_for(lambda: len(v.frames) < 50)
    out.sent.clear()
    wait_for(lambda: len(out.sent) >= 3)
    assert out.sent[0] is CARD
    v.stop()


def test_no_driver_starts_no_thread_and_no_webcam():
    cap = FakeCap()

    def no_driver():
        raise RuntimeError("virtual camera output could not be started")

    v = VirtualCam(1.0, lambda: False, make_webcam=lambda name: cap, make_output=no_driver, card=CARD)
    with pytest.raises(RuntimeError):
        v.start()
    assert v._threads == [] and cap.n == 0


def test_a_webcam_that_fails_closes_the_driver():
    out = FakeOut()

    def no_webcam(name):
        raise RuntimeError("in use")

    v = VirtualCam(1.0, lambda: False, make_webcam=no_webcam, make_output=lambda: out, card=CARD)
    with pytest.raises(RuntimeError):
        v.start()
    assert out.closed


def test_the_webcam_is_found_by_name_in_directshow_order(monkeypatch):
    class R:
        stderr = '[in#0] "USB2.0 IR UVC WebCam" (video)\n[in#0] "Mic" (audio)\n[in#0] "USB2.0 HD UVC WebCam" (video)\n'

    monkeypatch.setattr(virtual_cam.subprocess, "run", lambda *a, **k: R)
    assert dshow_index("HD UVC") == 1
    with pytest.raises(RuntimeError):
        dshow_index("Logitech")
