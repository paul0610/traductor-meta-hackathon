"""Consecutive policy: one Commit per `speechComplete` (SPEC §2, the reliable base).

`t_src_start` follows SPEC §7.4: the turn's `speechStart` position on the local
clock (`audioProcessedMs` mapped through the stream clock), else its arrival
minus `lat_asr`. The realtime ASR has no word timestamps, so `asr_ts` is never
claimed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..contracts import AsrEvent, AsrKind, Channel, Commit


@dataclass(slots=True)
class _Turn:
    t_start: float | None = None
    source: str = "partial_arrival"
    t_end: float | None = None
    t_partial_first: float | None = None


class Consecutive:
    def __init__(self, channel: Channel, to_local: Callable[[float | None], float | None],
                 *, lat_asr_s: float = 0.3, emit: Callable[..., None] | None = None) -> None:
        self.channel = channel
        self.to_local = to_local
        self.lat_asr_s = lat_asr_s
        self._emit = emit or (lambda ev, **f: None)
        self._turns: dict[str, _Turn] = {}
        self._seq = 0
        self._anon = 0
        self.corrections_blocked = 0        # nothing is committed before the final text

    def take_seq(self) -> int:
        """Next emission slot, also for what does not come from the ASR (the initial notice)."""
        seq, self._seq = self._seq, self._seq + 1
        return seq

    def _turn(self, ev: AsrEvent) -> tuple[str, _Turn]:
        key = ev.turn_id if ev.turn_id is not None else f"anon{self._anon}"
        return key, self._turns.setdefault(f"{ev.session_id}:{key}", _Turn())

    def on_event(self, ev: AsrEvent) -> Commit | None:
        if ev.kind is AsrKind.SPEECH_START:
            _, turn = self._turn(ev)
            local = self.to_local(ev.t_asr_s)
            turn.t_start, turn.source = ((local, "speech_start") if local is not None
                                         else (ev.t_recv - self.lat_asr_s, "speech_start"))
        elif ev.kind is AsrKind.PARTIAL:
            _, turn = self._turn(ev)
            if turn.t_partial_first is None:
                turn.t_partial_first = ev.t_recv
        elif ev.kind is AsrKind.SPEECH_END:
            _, turn = self._turn(ev)
            local = self.to_local(ev.t_asr_s)
            turn.t_end = local if local is not None else ev.t_recv - self.lat_asr_s
        elif ev.kind is AsrKind.SPEECH_COMPLETE:
            key, turn = self._turn(ev)
            self._turns.pop(f"{ev.session_id}:{key}", None)
            if ev.turn_id is None:
                self._anon += 1
            text = ev.text.strip()
            if not text:
                return None
            t_start = turn.t_start
            source = turn.source
            if t_start is None:
                t_start = (turn.t_partial_first or ev.t_recv) - self.lat_asr_s
                source = "partial_arrival"
            commit = Commit(
                utterance_id=f"{self.channel.value}:{ev.session_id}:{key}",
                commit_id=0, seq=self.take_seq(), channel=self.channel, text_src=text, prefix_src="",
                boundary="speechComplete", t_src_start=t_start, t_src_end=turn.t_end,
                t_src_source=source, t_commit=ev.t_recv,
            )
            self._emit("commit", channel=self.channel.value, seq=commit.seq, utterance_id=commit.utterance_id, commit_id=0,
                       boundary=commit.boundary, text_src=text, t_src_start=t_start, t_src_end=turn.t_end,
                       t_src_source=source, t_commit=commit.t_commit, t_partial_first=turn.t_partial_first)
            return commit
        return None
