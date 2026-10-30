from types import SimpleNamespace

from interprete.audio.cable_mixer import State
from interprete.contracts import Channel, PcmChunk
from interprete.policy.scheduler import Scheduler
from interprete.safety import ExitGuard, Watchdog


class FakeMixer:
    def __init__(self, state=State.SILENT, t_callback=100.0):
        self.state = state
        self.t_callback = t_callback
        self.tts_idle_since = None
        self.reopened = 0
        self.released = []
        self.cleared = 0
        self.is_busy = False

    def busy(self):
        return self.is_busy

    def reopen(self):
        self.reopened += 1

    def release(self, why):
        self.released.append(why)

    def play(self, chunk, start_at):
        pass

    def clear(self):
        self.cleared += 1


def chunk(seq=0):
    return PcmChunk(utterance_id="out:s:0", commit_id=0, seq=seq, channel=Channel.OUT, pcm=b"\x00\x00",
                    sample_rate=48000, duration_s=1.0, t_src_start=0.0, t_src_end=None, deadline=0.0)


def make(**kw):
    m = FakeMixer(**kw)
    s = Scheduler(m)
    return m, s, Watchdog(m, s)


def test_quiet_when_healthy():
    m, s, w = make()
    assert w.check(101.0) is None
    assert m.reopened == 0 and m.released == []


def test_off_is_never_watched():
    m, s, w = make(state=State.OFF, t_callback=0.0)
    assert w.check(100.0) is None


def test_dead_callback_reopens_the_stream_and_flushes():
    m, s, w = make()
    assert w.check(102.5) == "mixer_callback"
    assert m.reopened == 1 and m.cleared == 1


def test_a_slow_clause_only_gives_the_cable_back_and_stays_in_flight():
    m, s, w = make(state=State.TTS)
    m.tts_idle_since = 100.0
    s.expect(0, "out:s:0", 99.0, 98.0)
    m.t_callback = 102.9
    assert w.check(103.0) == "tts_idle"
    assert m.released == ["watchdog"]
    assert s.in_flight() == 1 and not s.is_cancelled("out:s:0")


def test_audio_still_playing_is_not_idle():
    m, s, w = make(state=State.TTS)
    m.tts_idle_since = 100.0
    m.is_busy = True
    m.t_callback = 102.9
    assert w.check(103.0) is None


def test_scheduler_that_stopped_stepping_is_flushed():
    m, s, w = make()
    s.submit(chunk(), t=100.0)            # ready, but nobody called step()
    m.t_callback = 102.9
    assert w.check(103.0) == "scheduler"
    assert m.released == ["watchdog"] and m.cleared == 1


def test_a_chunk_just_arrived_is_not_overdue_even_if_its_deadline_is_long_past():
    m, s, w = make()                      # D = 0: t_src_start is seconds old when the TTS finishes
    s.submit(chunk(), t=103.0)
    m.t_callback = 102.9
    assert w.check(103.0) is None
    assert m.released == [] and m.cleared == 0


def test_exit_guard_runs_cleanup_once():
    calls = []
    g = ExitGuard(lambda: calls.append(1), lambda: None)
    g.run()
    g.run()
    assert calls == [1]
