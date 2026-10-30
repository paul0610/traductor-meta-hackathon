import pytest

from interprete.contracts import Channel, PcmChunk
from interprete.policy.scheduler import (
    HANDOFF_LEAD_S,
    MT_TIMEOUT_S,
    SKIP_GRACE_S,
    TTS_TIMEOUT_S,
    Scheduler,
)


class FakeSink:
    def __init__(self):
        self.played: list[tuple[int, float]] = []
        self.cleared = 0

    def play(self, chunk, start_at):
        self.played.append((chunk.seq, start_at))

    def clear(self):
        self.cleared += 1


def chunk(seq, sched, t_src_start=0.0, dur=1.0, utt="out:s:0"):
    return PcmChunk(
        utterance_id=utt, commit_id=seq, seq=seq, channel=Channel.OUT,
        pcm=b"\x00\x00" * int(48000 * dur), sample_rate=48000, duration_s=dur,
        t_src_start=t_src_start, t_src_end=None, deadline=sched.deadline_for(t_src_start),
    )


def make(delay=0.0):
    sink = FakeSink()
    events = []
    s = Scheduler(sink, delay, emit=lambda ev, **f: events.append((ev, f)))
    return s, sink, events


def test_out_of_order_tts_plays_in_seq_order_back_to_back():
    s, sink, _ = make()
    s.submit(chunk(1, s, dur=1.0))
    # seq 0 never registered: its skip horizon starts when a later seq shows up
    assert s.step(10.0) == pytest.approx(10.0 + MT_TIMEOUT_S + TTS_TIMEOUT_S + SKIP_GRACE_S)
    assert sink.played == []
    s.submit(chunk(0, s, dur=2.0))
    due = s.step(10.0)
    assert sink.played == [(0, 10.0)]
    assert due == pytest.approx(12.0 - HANDOFF_LEAD_S)   # next handed to the mixer just before 0 ends
    s.step(due)
    assert sink.played == [(0, 10.0), (1, 12.0)]   # 1 starts exactly when 0 ends


def test_tombstone_unblocks_next_seq():
    s, sink, events = make()
    s.submit(chunk(1, s))
    s.tombstone(0, "mt_timeout")
    s.step(5.0)
    assert sink.played == [(1, 5.0)]
    assert ("play", {"seq": 0, "tombstone": "mt_timeout"}) in events


def test_d0_missing_seq_waits_for_both_timeouts_before_skipping():
    s, sink, _ = make()
    s.expect(0, "out:s:0", t_commit=1.0, t_src_start=0.0)
    s.expect(1, "out:s:0", t_commit=1.5, t_src_start=0.5)
    s.submit(chunk(1, s, t_src_start=0.5))
    horizon = 1.0 + MT_TIMEOUT_S + TTS_TIMEOUT_S + SKIP_GRACE_S
    assert s.step(3.0) == pytest.approx(horizon)   # with D = 0 a 1 s grace would drop live clauses
    assert sink.played == []
    s.step(horizon)
    assert s.seq_skipped == 1
    assert sink.played == [(1, horizon)]
    s.submit(chunk(0, s))                           # arrives after being skipped: dropped, never played
    s.step(horizon + 1)
    assert [p[0] for p in sink.played] == [1]


def test_d_positive_holds_until_deadline_and_hands_off_early_by_lead():
    s, sink, _ = make(delay=5.5)
    s.submit(chunk(0, s, t_src_start=10.0))
    due = s.step(12.0)
    assert due == pytest.approx(15.5 - HANDOFF_LEAD_S)
    assert sink.played == []
    s.step(due)
    assert sink.played == [(0, 15.5)]               # start_at is the deadline itself


def test_d_positive_late_chunk_plays_on_arrival_with_positive_sync_err():
    s, sink, _ = make(delay=5.5)
    c = chunk(0, s, t_src_start=10.0)
    s.submit(c)
    s.step(16.0)
    assert sink.played == [(0, 16.0)]
    assert c.sync_err_s == pytest.approx(0.5)


def test_d_positive_missing_seq_skipped_one_second_after_deadline():
    s, sink, _ = make(delay=5.5)
    s.expect(0, "out:s:0", t_commit=12.0, t_src_start=10.0)
    s.submit(chunk(1, s, t_src_start=11.0))
    assert s.step(15.0) == pytest.approx(15.5 + SKIP_GRACE_S)
    s.step(16.5)
    assert s.seq_skipped == 1
    assert sink.played == [(1, 16.5)]


def test_cancelled_utterance_is_dropped_and_order_continues():
    s, sink, _ = make()
    s.submit(chunk(0, s, utt="out:s:0"))
    s.submit(chunk(1, s, utt="out:s:1"))
    s.cancel_utterance("out:s:0")
    s.step(3.0)
    assert sink.played == [(1, 3.0)]


def test_yield_current_drops_rest_of_playing_utterance():
    s, sink, _ = make()
    s.submit(chunk(0, s, utt="out:s:7"))
    s.step(1.0)
    assert s.yield_current() == "out:s:7"
    s.submit(chunk(1, s, utt="out:s:7"))
    s.submit(chunk(2, s, utt="out:s:8"))
    s.step(2.0)
    assert sink.played == [(0, 1.0), (2, 2.0)]


def test_flush_clears_mixer_and_drops_in_flight_work():
    s, sink, _ = make()
    s.expect(0, "out:s:0", t_commit=0.0, t_src_start=0.0)
    s.submit(chunk(1, s, utt="out:s:0"))
    s.flush()
    assert sink.cleared == 1
    s.submit(chunk(0, s, utt="out:s:0"))            # in-flight work of the flushed utterance
    s.step(1.0)
    assert sink.played == []
    s.submit(chunk(2, s, utt="out:s:1"))            # new speech after F9 still plays
    s.step(2.0)
    assert sink.played == [(2, 2.0)]


def test_in_flight_and_backlog():
    s, sink, _ = make()
    s.expect(0, "u", 0.0, 0.0)
    s.expect(1, "u", 0.0, 0.0)
    assert s.in_flight() == 2
    s.submit(chunk(0, s, dur=2.0, utt="u"))
    s.step(1.0)
    assert s.in_flight() == 1
    assert s.backlog_s(1.5) == pytest.approx(1.5)
