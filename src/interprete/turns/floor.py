"""Who has the floor. Their speech takes it from my voice once it is a turn, not a "yeah" while they listen.

On the incoming fixture four words arrive 1.4 s after `speechStart`, and "I see." or
"That sounds great." stay short of them. The ASR can go silent mid-turn, and a floor
nobody gives back would leave my voice held for the rest of the call, so it expires.
"""

from __future__ import annotations

from ..contracts import AsrEvent, AsrKind

MIN_WORDS = 4
MIN_SPOKEN_S = 1.0
SILENT_S = 4.0                 # no word from the ASR for this long: they stopped, or the link did


class Floor:
    def __init__(self) -> None:
        self._t_start: float | None = None
        self._t_last = 0.0
        self._theirs = False

    def on_event(self, ev: AsrEvent) -> None:
        if ev.kind is AsrKind.SPEECH_START:
            self._t_start = ev.t_recv
            self._t_last = ev.t_recv
        elif ev.kind is AsrKind.PARTIAL:
            if self._t_start is None:
                self._t_start = ev.t_recv
            self._t_last = ev.t_recv
            if len(ev.text.split()) >= MIN_WORDS and ev.t_recv - self._t_start >= MIN_SPOKEN_S:
                self._theirs = True
        elif ev.kind in (AsrKind.SPEECH_END, AsrKind.SPEECH_COMPLETE):
            self._t_start = None
            self._theirs = False

    def theirs(self, t: float) -> bool:
        return self._theirs and t - self._t_last < SILENT_S
