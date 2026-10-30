from interprete.metrics.summary import gate_60s, gate_a, gate_s, summarize
from interprete.metrics.truth import clause_truth


def out_row(seq, lag):
    return dict(ev="commit", phase="report", channel="out", seq=seq, t_commit=10.0, t_mt=11.0, t_src_start=7.0,
                t_src_end=9.5, t_tts_first=12.0, t_play_start=10.0 + lag)


def in_row(seq):
    return dict(ev="commit", phase="report", channel="in", seq=seq, t_commit=20.0, t_src_end=19.5, t_shown=21.0)


def end(written=100, expected=100, gaps=0):
    return dict(ev="play", phase="end", bytes_written=written, bytes_expected=expected, cable_gaps=gaps)


def test_routes_are_split_by_channel():
    s = summarize([out_row(0, 2.0), out_row(1, 3.0), in_row(0), end()])
    assert s["out"]["commits"] == 2 and s["incoming"]["commits"] == 1
    assert s["out"]["commit_to_first_pcm_s"]["mean"] == 2.5
    assert s["out"]["src_start_to_tts_first_s"]["p50"] == 5.0
    assert s["out"]["d_estimate_s"] == 5.3
    assert s["incoming"]["commit_to_subtitle_s"]["p50"] == 1.0
    assert s["out"]["bytes_pct"] == 100.0


def test_gate_60s_passes_on_clean_fast_run():
    assert gate_60s(summarize([out_row(0, 2.0), out_row(1, 3.0), end()])) == []


def test_gate_60s_fails_on_slow_p95_gaps_and_lost_bytes():
    fails = gate_60s(summarize([out_row(0, 3.0), out_row(1, 6.0), end(90, 100, 2)]))
    assert any("p95" in f for f in fails)
    assert any("gaps" in f for f in fails)
    assert any("bytes" in f for f in fails)


def test_a_clause_that_never_played_fails_both_gates():
    lost = dict(ev="commit", phase="report", channel="out", seq=2, t_commit=10.0)
    events = [out_row(0, 2.0), lost, in_row(0), end()]
    assert any("lost clauses" in f for f in gate_60s(summarize(events)))
    assert any("lost clauses" in f for f in gate_a(summarize(events)))


def test_gate_60s_needs_something_on_the_cable():
    assert gate_60s(summarize([])) == ["no clause reached the cable"]


def test_gate_a_needs_both_directions():
    assert gate_a(summarize([out_row(0, 2.0), in_row(0), end()])) == []
    assert any("incoming" in f for f in gate_a(summarize([out_row(0, 2.0), end()])))
    assert any("outgoing" in f for f in gate_a(summarize([in_row(0)])))


def dubbed(seq, late):
    return dict(ev="commit", phase="report", channel="out", seq=seq, t_commit=10.0, t_src_start=7.0,
                t_play_start=12.5 + late)


RUN = dict(ev="run", delay_s=5.5, voice_under_db=-20.0, delay_jumps=0)


def test_gate_s_passes_when_clauses_meet_their_deadlines():
    assert gate_s(summarize([dubbed(0, 0.0), dubbed(1, 0.3), end(), RUN])) == []


def test_gate_s_fails_on_spread_and_on_a_clause_a_second_late():
    fails = gate_s(summarize([dubbed(0, 0.0), dubbed(1, 1.2), end(), RUN]))
    assert any("p95" in f for f in fails)
    assert any("past their deadline" in f for f in fails)


def test_gate_s_needs_a_dub():
    assert gate_s(summarize([out_row(0, 2.0), end()])) == ["no dubbed clause in this run"]


def test_clause_truth_finds_the_first_word_even_if_the_asr_changed_others():
    words = [dict(text=w, t=float(i)) for i, w in enumerate("Tengo cinco años de experiencia".split())]
    rows = [dict(seq=3, text_src="Tengo 5 años,"), dict(seq=4, text_src="de experiencia.")]
    assert clause_truth(rows, words, 100.0) == {3: 100.0, 4: 103.0}


def test_clause_truth_leaves_out_a_clause_whose_first_word_changed():
    words = [dict(text=w, t=float(i)) for i, w in enumerate("cinco años de experiencia".split())]
    assert clause_truth([dict(seq=0, text_src="5 años"), dict(seq=1, text_src="de experiencia")], words, 0.0) == {1: 2.0}
