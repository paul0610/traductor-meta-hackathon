from interprete.clock import StreamClock
from interprete.contracts import AsrEvent, AsrKind, Channel
from interprete.policy.consecutivo import Consecutive


def ev(kind, t_recv, t_asr_s=None, turn="0", text=""):
    return AsrEvent(Channel.OUT, kind, t_recv, "S1", turn_id=turn, text=text, t_asr_s=t_asr_s)


def test_commit_per_speech_complete_with_mapped_times():
    clock = StreamClock()
    clock.start_session("S1")
    clock.mark_first_block(100.0)
    p = Consecutive(Channel.OUT, clock.to_local)
    assert p.on_event(ev(AsrKind.SPEECH_START, 101.4, 1.2)) is None
    assert p.on_event(ev(AsrKind.PARTIAL, 101.6, 1.4, text="hola")) is None
    assert p.on_event(ev(AsrKind.SPEECH_END, 103.5, 3.3)) is None
    c = p.on_event(ev(AsrKind.SPEECH_COMPLETE, 103.9, 3.6, text=" hola, ¿qué tal? "))
    assert c.seq == 0 and c.commit_id == 0
    assert c.utterance_id == "out:S1:0"
    assert c.text_src == "hola, ¿qué tal?"
    assert c.t_src_start == 101.2 and c.t_src_source == "speech_start"
    assert c.t_src_end == 103.3
    assert c.t_commit == 103.9


def test_seq_increments_and_empty_turns_are_skipped():
    clock = StreamClock()
    p = Consecutive(Channel.OUT, clock.to_local, lat_asr_s=0.3)
    assert p.on_event(ev(AsrKind.SPEECH_COMPLETE, 5.0, turn="0", text="  ")) is None
    p.on_event(ev(AsrKind.SPEECH_START, 6.0, turn="1"))
    c1 = p.on_event(ev(AsrKind.SPEECH_COMPLETE, 8.0, turn="1", text="uno"))
    c2 = p.on_event(ev(AsrKind.SPEECH_COMPLETE, 9.0, turn="2", text="dos"))
    assert (c1.seq, c2.seq) == (0, 1)
    assert c1.t_src_start == 5.7                 # no stream mapping: arrival minus lat_asr
    assert c2.t_src_source == "partial_arrival"
