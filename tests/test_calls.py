import pytest

from interprete.ui import calls
from interprete.ui.calls import CallLog, HeardTurn


@pytest.fixture
def clock(monkeypatch):
    t = [100.0]
    monkeypatch.setattr(calls, "now", lambda: t[0])
    return t


def test_a_call_counts_its_seconds_while_out_and_keeps_them_when_it_returns(clock):
    log = CallLog()
    log.start(("mt", "out", 1), "Muse Spark", "es→en", "Claro, en mi último proyecto")
    clock[0] = 100.8
    (state, live), = log.rows()
    assert state == "live" and live.startswith("● Muse Spark ") and "es→en" in live
    assert "0.8 s…" in live and live.endswith("Claro, en mi último proyecto")
    clock[0] = 101.3
    log.end(("mt", "out", 1), "Sure, in my last project")
    clock[0] = 109.0
    (state, done), = log.rows()
    assert state == "done" and done.startswith("✓ ") and "1.3 s " in done and done.endswith("Sure, in my last project")
    assert live.index("0.8 s") == done.index("1.3 s")       # the seconds stay in one column
    assert done[calls.COLUMN_X[1]:].startswith("Muse Spark") and done[calls.COLUMN_X[4]:] == "Sure, in my last project"
    assert not log.live()


def test_a_failure_says_so_and_an_ended_call_stays_as_it_ended(clock):
    log = CallLog()
    log.start("k", "Muse Spark", "es→en", "una cláusula")
    log.end("k", "TimeoutError: clause dropped", failed=True)
    log.end("k", "late answer")
    (state, line), = log.rows()
    assert state == "failed" and line.startswith("✗ ") and "failed" in line
    assert line.endswith("TimeoutError: clause dropped")


def test_only_the_last_rows_stay_and_long_text_is_cut(clock):
    log = CallLog(keep=2)
    for i in range(3):
        log.start(i, "MMS-TTS, this CPU", "en", "word " * 30)
    assert [k for k in log.calls] == [1, 2]
    assert all(len(line) <= calls.ROW_CHARS for _, line in log.rows())
    assert log.rows()[0][1].endswith("…")
    log.end("gone")                                       # it left the screen: nothing to end


def test_a_heard_turn_is_one_row_from_its_first_partial_to_its_end(clock):
    seen = []

    class Ui:
        def __getattr__(self, name):
            return lambda *a, **k: seen.append((name, a, k))

    turn = HeardTurn(Ui(), "out", "es")
    turn.partial("Claro")
    turn.partial("Claro, en mi")
    turn.complete("Claro, en mi último proyecto.")
    turn.partial("Y")
    assert [(name, a[0]) for name, a, k in seen] == [
        ("call_start", ("asr", "out", 0)), ("call_update", ("asr", "out", 0)), ("call_end", ("asr", "out", 0)),
        ("call_start", ("asr", "out", 1))]
    assert seen[0][2] == {"timed": False}


def test_a_turn_being_heard_shows_listening_then_heard(clock):
    log = CallLog()
    log.start("t", "Muse Voice Transcribe", "en", "How did you", timed=False)
    assert "listening" in log.rows()[0][1]
    log.end("t", "How did you handle it?")
    assert "heard" in log.rows()[0][1] and log.rows()[0][1].endswith("How did you handle it?")
