"""What Spark knows about the call: everything both sides have said, and a glossary.

Spark takes 0.95 s with no conversation, 1.10 s with 32 000 characters and 2.06 s with
128 000, one call at 16.9 s (Oct 31, 8 calls each). So the conversation stops there,
some 40 minutes of talk, and the oldest lines leave first. It is kept in English,
what both sides end up hearing, so both directions read the same thing.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

MAX_CHARS = 32_000


class Conversation:
    def __init__(self, max_chars: int = MAX_CHARS) -> None:
        self.max_chars = max_chars
        self._lines: deque[list[str]] = deque()      # [who, what they said]
        self._chars = 0

    def add(self, who: str, text: str) -> None:
        """Clauses of the same speaker join one line: the labels would cost more than they say."""
        if self._lines and self._lines[-1][0] == who:
            self._lines[-1][1] += f" {text}"
            self._chars += len(text) + 1
        else:
            self._lines.append([who, text])
            self._chars += len(who) + len(text) + 3
        while self._chars > self.max_chars and len(self._lines) > 1:
            old_who, old_text = self._lines.popleft()
            self._chars -= len(old_who) + len(old_text) + 3

    def text(self) -> str:
        return "\n".join(f"{who}: {said}" for who, said in self._lines)


def load_glossary(path: Path) -> dict[str, str]:
    """`term = translation` per line, `#` for comments. A term alone is a name: it stays as it is."""
    if not path.exists():
        return {}
    terms: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        term, _, translation = line.partition("=")
        terms[term.strip()] = translation.strip() or term.strip()
    return terms
