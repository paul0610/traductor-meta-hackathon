"""Incremental policy: irreversible clause commits from the stable prefix of the partials (SPEC §2, LA-2).

A word is stable once two consecutive CUMULATIVE partials agree on it (local
agreement, n = 2). Over the stable words a clause is cut at punctuation, before a
conjunction, after MAX_WORDS or after MAX_CLAUSE_S of speech; `speechEnd` and
`speechComplete` flush what is left. What is committed is never revised: a later
partial that disagrees only counts as `corrections_blocked`.

`t_src_start` follows SPEC §7.4: the first clause of a turn takes the turn's
`speechStart`; the next ones take the audio position of the partial that first
carried their first word, minus one word (`asr_ts` does not exist in realtime).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from ..contracts import AsrEvent, AsrKind, Channel, Commit
from ..metrics.asr_stats import lcp

MIN_WORDS = 3                   # punctuation or a conjunction never cuts a shorter clause
MAX_WORDS = 8                   # SPEC asks 6 to 10; D's arithmetic asks for the short end
MAX_CLAUSE_S = 2.0
WORD_S = 0.3                    # a word has already been spoken when a partial first shows it
CLOSES_CLAUSE = re.compile(r"[,.;:?!…]$")
CONJUNCTIONS = frozenset({
    "y", "e", "o", "u", "pero", "porque", "aunque", "cuando", "mientras", "entonces", "pues", "si", "como", "donde",
    "and", "but", "because", "so", "although", "though", "when", "while", "if", "or", "which",
})
# A forced cut (words, time) backs up to before the last of these: Spanish puts the adjective after
# the noun, and "para empresas | medianas" came out as "for companies | medium-sized".
SOFT_OPENERS = CONJUNCTIONS | frozenset({
    "a", "al", "de", "del", "en", "con", "por", "para", "sin", "sobre", "que", "sino", "entre", "desde", "hasta",
    "to", "of", "in", "with", "for", "on", "at", "from", "that", "by",
})


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word).lower()


@dataclass(slots=True)
class _Turn:
    t_start: float | None = None
    t_end: float | None = None
    t_partial_first: float | None = None
    words: list[str] = field(default_factory=list)          # latest partial
    norm: list[str] = field(default_factory=list)           # the same, normalized, compared across partials
    first_seen: list[float] = field(default_factory=list)   # when each word first showed up, local clock
    committed: list[str] = field(default_factory=list)
    conflict: list[str] | None = None                       # the revision already counted
    commit_id: int = 0


class Incremental:
    def __init__(self, channel: Channel, to_local: Callable[[float | None], float | None],
                 *, lat_asr_s: float = 0.3, emit: Callable[..., None] | None = None) -> None:
        self.channel = channel
        self.to_local = to_local
        self.lat_asr_s = lat_asr_s
        self._emit = emit or (lambda ev, **f: None)
        self._turns: dict[str, _Turn] = {}
        self._seq = 0
        self.corrections_blocked = 0

    def take_seq(self) -> int:
        """Next emission slot, also for what does not come from the ASR (the initial notice)."""
        seq, self._seq = self._seq, self._seq + 1
        return seq

    def _key(self, ev: AsrEvent) -> str:
        return f"{ev.session_id}:{ev.turn_id}"

    def _position(self, ev: AsrEvent) -> float:
        local = self.to_local(ev.t_asr_s)
        return local if local is not None else ev.t_recv - self.lat_asr_s

    def _note_revision(self, turn: _Turn, norm: list[str]) -> None:
        """Committed words the ASR changed afterwards: counted once per new reading, never acted on."""
        done = [_norm(w) for w in turn.committed]
        seen = norm[:len(done)]
        if len(seen) < len(done) or seen == done:
            turn.conflict = None
        elif seen != turn.conflict:
            turn.conflict = seen
            self.corrections_blocked += 1

    def _make(self, turn: _Turn, ev: AsrEvent, n: int, boundary: str, t_end: float | None) -> Commit:
        start = len(turn.committed)
        clause = turn.words[start:start + n]
        if start == 0 and turn.t_start is not None:
            t_src_start, source = turn.t_start, "speech_start"
        else:
            t_src_start, source = turn.first_seen[start] - WORD_S, "partial_arrival"
        if t_end is None:
            t_end = turn.first_seen[start + n - 1]
        commit = Commit(
            utterance_id=f"{self.channel.value}:{ev.session_id}:{ev.turn_id}", commit_id=turn.commit_id,
            seq=self.take_seq(), channel=self.channel, text_src=" ".join(clause), prefix_src=" ".join(turn.committed),
            boundary=boundary, t_src_start=t_src_start, t_src_end=t_end, t_src_source=source, t_commit=ev.t_recv,
        )
        turn.committed += clause
        turn.commit_id += 1
        self._emit("commit", channel=self.channel.value, seq=commit.seq, utterance_id=commit.utterance_id,
                   commit_id=commit.commit_id, boundary=boundary, text_src=commit.text_src, t_src_start=t_src_start,
                   t_src_end=t_end, t_src_source=source, t_commit=commit.t_commit, t_partial_first=turn.t_partial_first,
                   n_words=len(clause))
        return commit

    def _cut(self, turn: _Turn, stable: int, t: float) -> tuple[int, str] | None:
        """How many stable words the next clause takes, if it can close now."""
        start = len(turn.committed)
        new = turn.words[start:stable]
        for i, word in enumerate(new):
            if i + 1 >= MIN_WORDS and CLOSES_CLAUSE.search(word):
                return i + 1, "punct"
            if i >= MIN_WORDS and _norm(word) in CONJUNCTIONS:
                return i, "conj"                     # the conjunction opens the next clause
        if len(new) >= MAX_WORDS:
            return self._back_up(new[:MAX_WORDS]), "words"
        if len(new) >= MIN_WORDS and t - turn.first_seen[start] >= MAX_CLAUSE_S:
            return self._back_up(new), "time"
        return None

    @staticmethod
    def _back_up(new: list[str]) -> int:
        for i in range(len(new) - 1, MIN_WORDS - 1, -1):
            if _norm(new[i]) in SOFT_OPENERS:
                return i
        return len(new)

    def _on_partial(self, turn: _Turn, ev: AsrEvent) -> Commit | None:
        if turn.t_partial_first is None:
            turn.t_partial_first = ev.t_recv
            if turn.t_start is not None:             # recalibrated on every turn (SPEC §7.4)
                self.lat_asr_s = max(0.0, ev.t_recv - turn.t_start)
        words = ev.text.split()
        norm = [_norm(w) for w in words]
        t = self._position(ev)
        stable = lcp(turn.norm, norm)
        turn.first_seen = turn.first_seen[:stable] + [t] * (len(words) - stable)
        turn.words, turn.norm = words, norm
        self._note_revision(turn, norm)
        if stable <= len(turn.committed):
            return None
        cut = self._cut(turn, stable, t)
        return None if cut is None else self._make(turn, ev, cut[0], cut[1], None)

    def _flush(self, turn: _Turn, ev: AsrEvent, boundary: str) -> Commit | None:
        n = len(turn.words) - len(turn.committed)
        return None if n <= 0 else self._make(turn, ev, n, boundary, turn.t_end)

    def on_event(self, ev: AsrEvent) -> Commit | None:
        if ev.kind not in (AsrKind.SPEECH_START, AsrKind.PARTIAL, AsrKind.SPEECH_END, AsrKind.SPEECH_COMPLETE):
            return None
        turn = self._turns.setdefault(self._key(ev), _Turn())
        if ev.kind is AsrKind.SPEECH_START:
            turn.t_start = self._position(ev)
        elif ev.kind is AsrKind.PARTIAL:
            return self._on_partial(turn, ev)
        elif ev.kind is AsrKind.SPEECH_END:
            turn.t_end = self._position(ev)
            return self._flush(turn, ev, "speechEnd")
        else:
            del self._turns[self._key(ev)]
            words = ev.text.split()
            if not words:
                return None
            norm = [_norm(w) for w in words]
            self._note_revision(turn, norm)
            turn.words, turn.norm = words, norm
            t = self._position(ev)
            turn.first_seen += [t] * (len(words) - len(turn.first_seen))
            return self._flush(turn, ev, "speechComplete")
        return None
