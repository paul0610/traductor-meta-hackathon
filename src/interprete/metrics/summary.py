"""p50/p95 per route from one run's JSONL, and the gate verdicts (SPEC §4, §5).

    uv run python -m interprete.metrics.summary logs/app_20261030_120000.jsonl
"""

from __future__ import annotations

import json
import sys
from collections import Counter

import numpy as np

from .jsonl import read

GATE_MAX_S = 4.0


def _stats(xs: list[float]) -> dict:
    if not xs:
        return dict(n=0, mean=None, p50=None, p95=None)
    return dict(n=len(xs), mean=round(float(np.mean(xs)), 3), p50=round(float(np.percentile(xs, 50)), 3),
                p95=round(float(np.percentile(xs, 95)), 3))


def _diffs(rows: list[dict], end: str, start: str) -> list[float]:
    return [r[end] - r[start] for r in rows if r.get(end) is not None and r.get(start) is not None]


def _asr(events: list[dict], channel: str) -> dict:
    evs = [e for e in events if e["ev"] == "asr" and e.get("channel") == channel]
    partials = [e for e in evs if e.get("kind") == "partial"]
    by_turn: dict = {}
    for e in partials:
        by_turn.setdefault(e.get("turn_id"), []).append(e["t_recv"])
    gaps = [float(d) for ts in by_turn.values() for d in np.diff(ts)]
    return dict(sessions=sum(e.get("kind") == "session" for e in evs),
                reconnects=sum(e.get("kind") == "reconnect" for e in evs),
                lag_s=_stats([e["lag_s"] for e in partials if e.get("lag_s") is not None]),
                partial_interval_s=_stats(gaps))


def summarize(events: list[dict]) -> dict:
    rows = [e for e in events if e["ev"] == "commit" and e.get("phase") == "report"]
    out = [r for r in rows if r.get("channel") == "out"]
    inc = [r for r in rows if r.get("channel") == "in"]
    ends = [e for e in events if e["ev"] == "play" and e.get("phase") == "end"]
    written = sum(e.get("bytes_written", 0) for e in ends)
    expected = sum(e.get("bytes_expected", 0) for e in ends)
    plays = [e for e in events if e["ev"] == "play"]
    src_to_tts = _diffs(out, "t_tts_first", "t_src_start")
    f9 = [e["f9_latency_s"] for e in events if "f9_latency_s" in e]
    return dict(
        out=dict(
            commits=len(out), played=sum(r.get("t_play_start") is not None for r in out),
            commit_to_mt_s=_stats(_diffs(out, "t_mt", "t_commit")),
            mt_to_tts_first_s=_stats(_diffs(out, "t_tts_first", "t_mt")),
            commit_to_first_pcm_s=_stats(_diffs(out, "t_play_start", "t_commit")),
            speech_end_to_first_pcm_s=_stats(_diffs(out, "t_play_start", "t_src_end")),
            endpoint_delay_s=_stats(_diffs(out, "t_commit", "t_src_end")),
            src_start_to_tts_first_s=_stats(src_to_tts),
            d_estimate_s=round(float(np.percentile(src_to_tts, 95)) + 0.3, 2) if src_to_tts else None,
            bytes_pct=round(100 * written / expected, 2) if expected else None,
            cable_gaps=max((e.get("cable_gaps", 0) for e in events), default=0),
            seq_skipped=max((e.get("seq_skipped") or 0 for e in plays), default=0),
            tombstones=sum(bool(e.get("tombstone")) for e in plays),
            corrections_blocked=max((e.get("corrections_blocked", 0) for e in events), default=0),
            boundaries=dict(Counter(e["boundary"] for e in events if e["ev"] == "commit" and e.get("channel") == "out"
                                    and "boundary" in e)),
            f9_latency_s=_stats(f9),
            # "tts_idle" only means the next clause is slow: the cable rests, nothing was lost
            watchdog_trips=sum(e.get("what") == "watchdog" and e.get("why") != "tts_idle" for e in events),
            asr=_asr(events, "out"),
        ),
        incoming=dict(
            commits=len(inc), subtitles=sum(r.get("t_shown") is not None for r in inc),
            commit_to_subtitle_s=_stats(_diffs(inc, "t_shown", "t_commit")),
            speech_end_to_subtitle_s=_stats(_diffs(inc, "t_shown", "t_src_end")),
            endpoint_delay_s=_stats(_diffs(inc, "t_commit", "t_src_end")),
            asr=_asr(events, "in"),
        ),
    )


def integrity_failures(s: dict) -> list[str]:
    o = s["out"]
    fails = []
    if o["played"] < o["commits"]:
        fails.append(f"lost clauses: {o['played']} of {o['commits']} played")
    if o["cable_gaps"]:
        fails.append(f"cable gaps: {o['cable_gaps']}")
    if o["bytes_pct"] is not None and o["bytes_pct"] < 100:
        fails.append(f"bytes on the cable: {o['bytes_pct']} %")
    return fails


def gate_60s(s: dict) -> list[str]:
    """Failures of the 60 s gate: commit → first PCM mean and p95 ≤ 4 s, 0 gaps, 100 % of the bytes.

    `corrections_blocked` stays out: a blocked correction is by definition one nobody heard.
    """
    o = s["out"]
    c2p = o["commit_to_first_pcm_s"]
    if not c2p["n"]:
        return ["no clause reached the cable"]
    fails = integrity_failures(s)
    if c2p["mean"] > GATE_MAX_S:
        fails.append(f"commit → first PCM mean {c2p['mean']} s > {GATE_MAX_S} s")
    if c2p["p95"] > GATE_MAX_S:
        fails.append(f"commit → first PCM p95 {c2p['p95']} s > {GATE_MAX_S} s")
    return fails


def gate_a(s: dict) -> list[str]:
    """Failures of gate A: both directions worked during the run and the cable stayed clean."""
    fails = integrity_failures(s)
    if not s["out"]["played"]:
        fails.append("outgoing: no clause reached the cable")
    if not s["incoming"]["subtitles"]:
        fails.append("incoming: no subtitle was shown")
    return fails


if __name__ == "__main__":
    print(json.dumps(summarize(read(sys.argv[1])), indent=2, ensure_ascii=False))
