"""Output scheduler: plays PcmChunks in `seq` order, never before their deadline (SPEC §7.5.1).

TTS jobs finish out of order and emitted audio cannot be corrected, so the
emission order is the commit order (`seq`). The deadline `t_src_start + D` only
means "not before"; with D = 0 it is already in the past, i.e. "now, in order".
`mt` and `tts` deliver either a chunk or a tombstone for every seq, so a dropped
clause never stalls the clauses behind it.

The decision logic lives in `step(now)`, a pure function of the clock, so it is
tested without audio or real time; `run()` drives it on the event loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Protocol

from ..clock import now as clock_now
from ..contracts import PcmChunk

MT_TIMEOUT_S = 4.0
TTS_TIMEOUT_S = 4.0
SKIP_GRACE_S = 1.0            # a missing seq without tombstone is skipped this long after its horizon
HANDOFF_LEAD_S = 0.10         # hand a chunk to the mixer this early; the mixer starts it at start_at


class Sink(Protocol):
    """The mixer as seen by the scheduler."""

    def play(self, chunk: PcmChunk, start_at: float) -> None: ...
    def clear(self) -> None: ...


@dataclass(slots=True)
class _Expected:
    t_commit: float
    deadline: float
    utterance_id: str


class Scheduler:
    def __init__(
        self,
        sink: Sink,
        delay_s: float = 0.0,
        *,
        emit: Callable[..., None] | None = None,
        first_seq: int = 0,
    ) -> None:
        self.sink = sink
        self.delay_s = delay_s
        self._emit = emit or (lambda ev, **f: None)
        self._next_seq = first_seq
        self._ready: dict[int, PcmChunk] = {}
        self._tombs: dict[int, str] = {}
        self._expected: dict[int, _Expected] = {}
        self._first_seen_later: dict[int, float] = {}   # seq -> when a later seq was first known (seq never expected)
        self._cancelled: set[str] = set()
        self._prev_end = 0.0                            # perf_counter when the last handed chunk ends
        self._current_utterance: str | None = None
        self._wake = asyncio.Event()
        self.seq_skipped = 0
        self.dropped = 0

    # --- producers --------------------------------------------------------------

    def expect(self, seq: int, utterance_id: str, t_commit: float, t_src_start: float) -> None:
        """Register a commit as soon as it exists, so a missing seq has a known skip horizon."""
        if seq >= self._next_seq:
            self._expected[seq] = _Expected(t_commit, t_src_start + self.delay_s, utterance_id)

    def deadline_for(self, t_src_start: float) -> float:
        return t_src_start + self.delay_s

    def submit(self, chunk: PcmChunk) -> None:
        if chunk.seq < self._next_seq:
            self._drop(chunk.seq, "late", chunk.utterance_id)
            return
        if chunk.utterance_id in self._cancelled:
            # resolve the seq as a tombstone, or the queue would stall until the skip horizon
            self.tombstone(chunk.seq, "cancelled")
            return
        self._ready[chunk.seq] = chunk
        self._wake.set()

    def tombstone(self, seq: int, reason: str = "") -> None:
        if seq >= self._next_seq:
            self._tombs[seq] = reason
            self._wake.set()

    # --- control (F9, turns) ------------------------------------------------------

    def cancel_utterance(self, utterance_id: str) -> None:
        self._cancelled.add(utterance_id)
        self._wake.set()

    def is_cancelled(self, utterance_id: str) -> bool:
        return utterance_id in self._cancelled

    def yield_current(self) -> str | None:
        """Turn yield: the chunk already handed to the mixer finishes, the rest of its utterance is dropped."""
        utt = self._current_utterance
        if utt is not None:
            self.cancel_utterance(utt)
        return utt

    def flush(self) -> None:
        """F9: drop everything queued and in flight, clear the mixer."""
        for c in self._ready.values():
            self._cancelled.add(c.utterance_id)
        for e in self._expected.values():
            self._cancelled.add(e.utterance_id)
        if self._current_utterance is not None:
            self._cancelled.add(self._current_utterance)
        self.sink.clear()
        self._prev_end = 0.0
        self._wake.set()

    # --- introspection ---------------------------------------------------------------

    def in_flight(self) -> int:
        """Commits registered but not yet played, tombstoned or skipped."""
        pending = set(self._expected) | set(self._ready) | set(self._tombs)
        return sum(1 for s in pending if s >= self._next_seq)

    def backlog_s(self, now: float) -> float:
        """Audio still to play: what the mixer holds plus chunks waiting here."""
        queued = max(0.0, self._prev_end - now)
        return queued + sum(c.duration_s for c in self._ready.values())

    # --- decision logic -----------------------------------------------------------

    def _skip_horizon(self, seq: int, now: float) -> float | None:
        """When a missing seq (no chunk, no tombstone) may be skipped; None = wait for an event."""
        exp = self._expected.get(seq)
        if exp is not None:
            if self.delay_s > 0:
                return exp.deadline + SKIP_GRACE_S
            # D = 0: the deadline is already past at birth; give MT and TTS their full timeouts first.
            return exp.t_commit + MT_TIMEOUT_S + TTS_TIMEOUT_S + SKIP_GRACE_S
        later_known = any(s > seq for s in self._ready) or any(s > seq for s in self._tombs)
        if not later_known:
            return None
        first = self._first_seen_later.setdefault(seq, now)
        return first + MT_TIMEOUT_S + TTS_TIMEOUT_S + SKIP_GRACE_S

    def _advance(self) -> None:
        self._expected.pop(self._next_seq, None)
        self._first_seen_later.pop(self._next_seq, None)
        self._next_seq += 1

    def _drop(self, seq: int, reason: str, utterance_id: str | None = None) -> None:
        self.dropped += 1
        self._emit("play", seq=seq, utterance_id=utterance_id, dropped=reason)

    def step(self, now: float) -> float | None:
        """Advance as far as possible at time `now`.

        Returns the clock time of the next timed action, or None when only a
        producer event (submit, tombstone, cancel) can unblock the queue.
        """
        while True:
            seq = self._next_seq
            if seq in self._tombs:
                reason = self._tombs.pop(seq)
                self._ready.pop(seq, None)
                self._emit("play", seq=seq, tombstone=reason or True)
                self._advance()
                continue

            chunk = self._ready.get(seq)
            if chunk is not None:
                if chunk.utterance_id in self._cancelled:
                    del self._ready[seq]
                    self._drop(seq, "cancelled", chunk.utterance_id)
                    self._advance()
                    continue
                start_at = max(chunk.deadline, self._prev_end, now)
                if now < start_at - HANDOFF_LEAD_S:
                    return start_at - HANDOFF_LEAD_S
                del self._ready[seq]
                chunk.t_play_start = start_at
                self.sink.play(chunk, start_at)
                self._prev_end = start_at + chunk.duration_s
                self._current_utterance = chunk.utterance_id
                self._emit(
                    "play",
                    seq=seq,
                    utterance_id=chunk.utterance_id,
                    commit_id=chunk.commit_id,
                    t_src_start=chunk.t_src_start,
                    deadline=chunk.deadline,
                    t_play_start=start_at,
                    sync_err_s=chunk.sync_err_s,
                    delay_s=self.delay_s,
                    duration_s=chunk.duration_s,
                    t_tts_first=chunk.t_tts_first,
                    backlog_s=self.backlog_s(now),
                    seq_skipped=self.seq_skipped,
                )
                self._advance()
                continue

            exp = self._expected.get(seq)
            if exp is not None and exp.utterance_id in self._cancelled:
                # cancelled before delivery: its chunk or tombstone will be dropped on arrival
                self._drop(seq, "cancelled", exp.utterance_id)
                self._advance()
                continue

            horizon = self._skip_horizon(seq, now)
            if horizon is None:
                return None
            if now < horizon:
                return horizon
            self.seq_skipped += 1
            self._emit("play", seq=seq, seq_skipped=self.seq_skipped, skipped=True)
            self._advance()

    async def run(self) -> None:
        while True:
            self._wake.clear()
            due = self.step(clock_now())
            timeout = None if due is None else max(0.0, due - clock_now())
            try:
                await asyncio.wait_for(self._wake.wait(), timeout)
            except TimeoutError:
                pass
