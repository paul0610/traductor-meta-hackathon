import numpy as np

from interprete.metrics.asr_stats import prefix_revisions, summarize, vad_segments


def test_stable_growth_has_no_revisions():
    assert prefix_revisions(["yo", "yo creo", "yo creo que", "yo creo que sí."], normalize=True) == 0


def test_change_after_two_partials_counts_once():
    assert prefix_revisions(["yo creo", "yo creo que", "yo pienso que", "yo pienso que sí"], normalize=True) == 1


def test_punctuation_only_change_is_revision_raw_but_not_normalized():
    p = ["the bug", "the bug by", "the bug. By fixed"]
    assert prefix_revisions(p, normalize=False) == 1
    assert prefix_revisions(p, normalize=True) == 0


def test_vad_finds_two_bursts_aligned_to_t0():
    rate = 16000
    x = np.zeros(rate * 3, np.float32) + np.random.default_rng(0).normal(0, 1e-4, rate * 3).astype(np.float32)
    x[int(0.5 * rate):int(1.0 * rate)] += 0.3 * np.sin(np.arange(int(0.5 * rate)) * 0.2)
    x[int(2.0 * rate):int(2.4 * rate)] += 0.3 * np.sin(np.arange(int(0.4 * rate)) * 0.2)
    segs = vad_segments(x, rate, t0=100.0)
    assert len(segs) == 2
    assert abs(segs[0].start - 100.5) < 0.03 and abs(segs[0].end - 101.0) < 0.03
    assert abs(segs[1].end - 102.4) < 0.03


def test_summarize_endpoint_delays():
    from interprete.metrics.asr_stats import Segment
    segs = [Segment(10.0, 12.0)]
    events = [
        dict(kind="speechStart", turn_id="0", t_recv=10.3, t_asr_s=0.2, lag_s=0.1),
        dict(kind="partial", turn_id="0", t_recv=10.5, t_asr_s=0.4, lag_s=0.1, text="hola"),
        dict(kind="partial", turn_id="0", t_recv=10.7, t_asr_s=0.6, lag_s=0.1, text="hola qué"),
        dict(kind="speechEnd", turn_id="0", t_recv=12.6, t_asr_s=2.5, lag_s=0.1),
        dict(kind="speechComplete", turn_id="0", t_recv=12.9, t_asr_s=2.8, lag_s=0.1, text="hola qué tal"),
    ]
    s = summarize(events, segs)
    assert s["speech_end_delay_p50"] == 0.6
    assert s["speech_complete_delay_p50"] == 0.9
    assert s["first_partial_after_onset_p50"] == 0.5
    assert s["partial_interval_p50"] == 0.2
