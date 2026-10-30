"""How far `t_src_start` is from when a clause really started, on a fixture whose word times are known.

The ASR gives no word times in realtime, so `t_src_start` is an estimate (SPEC §7.4)
and it decides whether voice and lips meet. Gate S only compares the scheduler
against that estimate; this compares the estimate against the truth.
"""

from __future__ import annotations

import difflib
import re


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word).lower()


def clause_truth(rows: list[dict], words: list[dict], t_fixture: float) -> dict[int, float]:
    """seq -> local time of the clause's first word. A clause whose first word the ASR changed is left out."""
    rows = sorted(rows, key=lambda r: r["seq"])
    said = [(_norm(w), r["seq"], i == 0) for r in rows for i, w in enumerate(r["text_src"].split())]
    real = [_norm(w["text"]) for w in words]
    sm = difflib.SequenceMatcher(None, [w for w, _, _ in said], real, autojunk=False)
    out: dict[int, float] = {}
    for m in sm.get_matching_blocks():
        for k in range(m.size):
            _, seq, first = said[m.a + k]
            if first:
                out[seq] = t_fixture + words[m.b + k]["t"]
    return out
