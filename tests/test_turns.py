from interprete.contracts import AsrEvent, AsrKind, Channel
from interprete.turns.floor import MIN_SPOKEN_S, SILENT_S, Floor


def ev(kind, t, text=""):
    return AsrEvent(channel=Channel.IN, kind=kind, t_recv=t, session_id="s", turn_id="1", text=text, t_asr_s=None,
                    speaker=None, raw={})


def spoken(floor, *lines):
    for t, text in lines:
        floor.on_event(ev(AsrKind.PARTIAL, t, text))


def test_a_turn_takes_the_floor():
    floor = Floor()
    floor.on_event(ev(AsrKind.SPEECH_START, 10.0))
    spoken(floor, (11.5, "Could you tell me"), (11.6, "Could you tell me a"))
    assert floor.theirs(11.7)


def test_a_yeah_does_not():
    floor = Floor()
    floor.on_event(ev(AsrKind.SPEECH_START, 10.0))
    spoken(floor, (11.0, "Yeah"), (12.0, "Yeah, I see."), (12.5, "That sounds great."))
    assert not floor.theirs(12.6)


def test_fast_words_alone_are_not_a_turn():
    floor = Floor()
    floor.on_event(ev(AsrKind.SPEECH_START, 10.0))
    spoken(floor, (10.0 + MIN_SPOKEN_S - 0.1, "one two three four"))
    assert not floor.theirs(10.9)


def test_it_comes_back_when_they_stop():
    floor = Floor()
    floor.on_event(ev(AsrKind.SPEECH_START, 10.0))
    spoken(floor, (11.5, "Could you tell me"))
    floor.on_event(ev(AsrKind.SPEECH_END, 13.0))
    assert not floor.theirs(13.1)


def test_a_partial_without_speech_start_still_counts():
    floor = Floor()
    spoken(floor, (10.0, "Could"), (11.5, "Could you tell me"))
    assert floor.theirs(11.6)


def test_a_silent_asr_cannot_hold_the_floor():
    floor = Floor()
    floor.on_event(ev(AsrKind.SPEECH_START, 10.0))
    spoken(floor, (11.5, "Could you tell me"))
    assert floor.theirs(11.5 + SILENT_S - 0.1)
    assert not floor.theirs(11.5 + SILENT_S + 0.1)
