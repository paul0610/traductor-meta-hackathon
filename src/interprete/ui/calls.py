"""What Meta's models are doing right now, for the panel on screen: one row per call, the latest at the bottom.

Judges have to see the live model calls in the demo video, and the console in a corner is easy to miss.
A row starts when a call goes out and ends with what it returned, or with its failure: Muse Voice
Transcribe hearing a turn, Muse Spark translating a clause, MMS-TTS speaking it. Qt-free, so the tests
read the rows; `widget.MetaPanel` only paints them.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..clock import now

KEEP = 10                      # a few clauses through all three models, and the turns they came from
TEXT_CHARS = 46
COLUMN_X = (0, 2, 24, 30, 41)  # where icon, model, route, clock and text start, in characters
ROW_CHARS = COLUMN_X[-1] + TEXT_CHARS
ICON = {"live": "●", "done": "✓", "failed": "✗"}


@dataclass
class Call:
    model: str
    route: str
    text: str
    t_start: float
    timed: bool = True             # a turn being heard has no latency to show
    t_end: float | None = None
    failed: bool = False

    @property
    def state(self) -> str:
        return "failed" if self.failed else "live" if self.t_end is None else "done"

    def clock(self, t: float) -> str:
        if self.failed:
            return "failed"
        if not self.timed:
            return "listening" if self.t_end is None else "heard"
        return f"{t - self.t_start:5.1f} s…" if self.t_end is None else f"{self.t_end - self.t_start:5.1f} s"


def clip(text: str, chars: int = TEXT_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= chars else text[:chars - 1] + "…"


class CallLog:
    def __init__(self, keep: int = KEEP) -> None:
        self.keep = keep
        self.calls: dict[tuple, Call] = {}

    def start(self, key: tuple, model: str, route: str = "", text: str = "", *, timed: bool = True) -> None:
        self.calls.pop(key, None)
        self.calls[key] = Call(model, route, text, now(), timed)
        while len(self.calls) > self.keep:
            del self.calls[next(iter(self.calls))]

    def update(self, key: tuple, text: str) -> None:
        if key in self.calls:
            self.calls[key].text = text

    def end(self, key: tuple, text: str | None = None, *, failed: bool = False) -> None:
        """A call that already ended, or left the screen, stays as it is."""
        call = self.calls.get(key)
        if call is None or call.t_end is not None:
            return
        call.t_end, call.failed = now(), failed
        if text is not None:
            call.text = text

    def live(self) -> bool:
        return any(c.t_end is None for c in self.calls.values())

    def cells(self, t: float | None = None) -> list[tuple[str, tuple[str, str, str, str, str]]]:
        """(state, (icon, model, route, clock, text)), oldest first. The panel draws each at its COLUMN_X: the icons
        come from a fallback font whose widths differ, so spaces alone do not keep the columns straight."""
        t = now() if t is None else t
        return [(c.state, (ICON[c.state], c.model, c.route, c.clock(t), clip(c.text))) for c in self.calls.values()]

    def rows(self, t: float | None = None) -> list[tuple[str, str]]:
        """(state, line), oldest first, the cells padded to their columns."""
        widths = [b - a for a, b in zip(COLUMN_X, COLUMN_X[1:])]
        return [(state, "".join(cell.ljust(w) for cell, w in zip(cells, widths)) + cells[-1])
                for state, cells in self.cells(t)]


class HeardTurn:
    """One row per turn Muse Voice Transcribe hears, from its first partial to its end."""

    def __init__(self, ui, channel: str, lang: str) -> None:
        self.ui = ui
        self.channel = channel
        self.lang = lang
        self.turn = 0
        self.open = False

    def partial(self, text: str) -> None:
        key = ("asr", self.channel, self.turn)
        if self.open:
            self.ui.call_update(key, text)
        else:
            self.ui.call_start(key, "Muse Voice Transcribe", self.lang, text, timed=False)
            self.open = True

    def complete(self, text: str) -> None:
        if not self.open:
            self.partial(text)
        self.ui.call_end(("asr", self.channel, self.turn), text)
        self.open = False
        self.turn += 1
