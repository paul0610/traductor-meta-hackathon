"""Metrics: one JSONL line per event, one writer (SPEC §4).

`emit()` is safe from any thread (audio callbacks included) and never blocks:
it puts a dict on a SimpleQueue that a single writer thread drains. Every line
carries `ev`, `t` (perf_counter at emit) and the event's fields.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ..clock import now

_STOP = object()


class Metrics:
    def __init__(self, path: Path | str | None = None, *, run_name: str = "run") -> None:
        if path is None:
            stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            path = Path("logs") / f"{run_name}_{stamp}.jsonl"
        self.path = Path(path)
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._writer, name="metrics", daemon=True)
        self.ok = True
        self._thread.start()
        # anchor perf_counter to wall time once per run, so logs can be lined up with recordings
        self.emit("run", wall=datetime.now().isoformat(timespec="milliseconds"), argv=sys.argv)

    def emit(self, ev: str, **fields: Any) -> None:
        self._q.put({"ev": ev, "t": now(), **fields})

    def close(self) -> None:
        self._q.put(_STOP)
        self._thread.join(timeout=2)

    def _writer(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            f = open(self.path, "a", encoding="utf-8")
        except OSError as exc:
            self.ok = False
            print(f"[metrics] disabled, cannot open {self.path}: {exc}", file=sys.stderr)
            f = None
        while True:
            item = self._q.get()
            if item is _STOP:
                break
            if f is None:
                continue
            try:
                f.write(json.dumps(item, ensure_ascii=False, default=_default) + "\n")
                f.flush()
            except OSError as exc:
                self.ok = False
                print(f"[metrics] write failed, continuing without metrics: {exc}", file=sys.stderr)
                f = None
        if f is not None:
            f.close()


def _default(o: Any) -> Any:
    if hasattr(o, "value"):          # Enums
        return o.value
    return repr(o)


def read(path: Path | str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
