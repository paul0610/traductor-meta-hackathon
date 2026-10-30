"""ASR characterization from one recorded run: what the policy and the deadline math need (SPEC §4, §7.4).

Inputs: the run's `asr` events (with `t_recv`, `t_asr_s`, `lag_s`) and the 16 kHz
audio actually sent, aligned to the local clock (`t0` = t_capture of sample 0).
Speech onsets/offsets come from a local energy VAD on that audio: with
headphones the mic only hears me, so energy is a fair ground truth for
"when I started / stopped talking".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

FRAME_S = 0.02


@dataclass(slots=True)
class Segment:
    start: float
    end: float


def vad_segments(x: np.ndarray, rate: int, t0: float, *, rel_db: float = 15.0,
                 min_gap_s: float = 0.25, min_len_s: float = 0.15) -> list[Segment]:
    hop = int(rate * FRAME_S)
    n = len(x) // hop
    if n == 0:
        return []
    frames = x[: n * hop].astype(np.float64).reshape(n, hop)
    db = 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-12)
    floor = np.percentile(db, 10)
    voiced = db > max(floor + rel_db, -55.0)
    segs: list[Segment] = []
    i = 0
    while i < n:
        if voiced[i]:
            j = i
            while j < n and voiced[j]:
                j += 1
            segs.append(Segment(t0 + i * FRAME_S, t0 + j * FRAME_S))
            i = j
        else:
            i += 1
    merged: list[Segment] = []
    for s in segs:
        if merged and s.start - merged[-1].end < min_gap_s:
            merged[-1].end = s.end
        else:
            merged.append(s)
    return [s for s in merged if s.end - s.start >= min_len_s]


def words(text: str, normalize: bool) -> list[str]:
    if normalize:
        text = re.sub(r"[^\w\s']", " ", text.lower())
    return text.split()


def lcp(a: list[str], b: list[str]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def prefix_revisions(partials: list[str], normalize: bool) -> int:
    """How often an LA-2 stable prefix (common to two consecutive partials) was later changed.

    Mirrors what an LA-2 policy would commit: the committed prefix only grows;
    a later partial that does not start with it is a revision the policy would
    have had to block (`corrections_blocked`).
    """
    committed: list[str] = []
    prev: list[str] | None = None
    revisions = 0
    for p in partials:
        w = words(p, normalize)
        if committed and w[: len(committed)] != committed:
            revisions += 1
            committed = committed[: lcp(committed, w)]
        if prev is not None:
            stable = prev[: lcp(prev, w)]
            if len(stable) > len(committed):
                committed = stable
        prev = w
    return revisions


def pct(xs: list[float], q: float) -> float | None:
    return round(float(np.percentile(xs, q)), 3) if xs else None


def summarize(events: list[dict], segments: list[Segment]) -> dict:
    by_turn: dict[str, list[dict]] = {}
    for e in events:
        if e.get("turn_id") is not None:
            by_turn.setdefault(e["turn_id"], []).append(e)

    lag_partial = [e["lag_s"] for e in events if e.get("kind") == "partial" and e.get("lag_s") is not None]
    lag_all = [e["lag_s"] for e in events if e.get("lag_s") is not None]
    intervals, first_partial_lat, start_lat, start_pos_bias = [], [], [], []
    end_delay, complete_delay, end_pos_delay = [], [], []
    rev_raw = rev_norm = 0
    final_vs_last = 0
    n_partials = 0

    def onset_before(t: float) -> Segment | None:
        cands = [s for s in segments if s.start <= t + 0.05]
        return cands[-1] if cands else None

    def offset_before(t: float, after: float) -> Segment | None:
        cands = [s for s in segments if s.end <= t and s.end > after]
        return cands[-1] if cands else None

    for turn, evs in by_turn.items():
        partials = [e for e in evs if e["kind"] == "partial"]
        n_partials += len(partials)
        ts = [e["t_recv"] for e in partials]
        intervals += list(np.diff(ts))
        texts = [e.get("text") or "" for e in partials]
        rev_raw += prefix_revisions(texts, normalize=False)
        rev_norm += prefix_revisions(texts, normalize=True)
        start = next((e for e in evs if e["kind"] == "speechStart"), None)
        t_turn0 = start["t_recv"] if start else (ts[0] if ts else None)
        seg = onset_before(t_turn0) if t_turn0 is not None else None
        if seg is not None:
            if start:
                start_lat.append(start["t_recv"] - seg.start)
                if start.get("t_asr_s") is not None and start.get("lag_s") is not None:
                    start_pos_bias.append((start["t_recv"] - start["lag_s"]) - seg.start)
            if ts:
                first_partial_lat.append(ts[0] - seg.start)
        t_after = seg.start if seg is not None else -1e9
        for kind, sink in (("speechEnd", end_delay), ("speechComplete", complete_delay)):
            e = next((x for x in evs if x["kind"] == kind), None)
            if e is None:
                continue
            off = offset_before(e["t_recv"], t_after)
            if off is not None:
                sink.append(e["t_recv"] - off.end)
                if kind == "speechEnd" and e.get("lag_s") is not None:
                    end_pos_delay.append((e["t_recv"] - e["lag_s"]) - off.end)
        complete = next((x for x in evs if x["kind"] == "speechComplete"), None)
        if complete and texts and words(complete.get("text") or "", True) != words(texts[-1], True):
            final_vs_last += 1

    return dict(
        turns=len(by_turn), partials=n_partials, vad_segments=len(segments),
        lag_partial_p50=pct(lag_partial, 50), lag_partial_p95=pct(lag_partial, 95),
        lag_all_p50=pct(lag_all, 50), lag_all_p95=pct(lag_all, 95),
        partial_interval_p50=pct(intervals, 50), partial_interval_p95=pct(intervals, 95),
        first_partial_after_onset_p50=pct(first_partial_lat, 50), first_partial_after_onset_p95=pct(first_partial_lat, 95),
        speech_start_after_onset_p50=pct(start_lat, 50), speech_start_after_onset_p95=pct(start_lat, 95),
        speech_start_position_vs_onset_p50=pct(start_pos_bias, 50),
        prefix_revisions_raw=rev_raw, prefix_revisions_normalized=rev_norm,
        speech_end_delay_p50=pct(end_delay, 50), speech_end_delay_p95=pct(end_delay, 95),
        speech_end_position_vs_offset_p50=pct(end_pos_delay, 50),
        speech_complete_delay_p50=pct(complete_delay, 50), speech_complete_delay_p95=pct(complete_delay, 95),
        final_differs_from_last_partial=final_vs_last,
    )
