"""Watchdog and guaranteed cleanup (SPEC §3, §7.6)."""

from __future__ import annotations

import atexit
import signal
import threading
from typing import Callable

from .audio.cable_mixer import CableMixer, State
from .policy.scheduler import Scheduler

WATCHDOG_S = 2.0


class Watchdog:
    def __init__(self, mixer: CableMixer, sched: Scheduler, *, limit_s: float = WATCHDOG_S,
                 emit: Callable[..., None] | None = None) -> None:
        self.mixer = mixer
        self.sched = sched
        self.limit_s = limit_s
        self._emit = emit or (lambda ev, **f: None)
        self.trips = 0

    def _trip(self, what: str, **fields) -> str:
        self.trips += 1
        self._emit("state", what="watchdog", why=what, trips=self.trips, **fields)
        return what

    def check(self, t: float) -> str | None:
        """One look at the cable; returns what tripped, if anything. Pure with respect to time."""
        m = self.mixer
        if m.state is State.OFF:
            return None
        stalled_s = t - m.t_callback
        if stalled_s > self.limit_s:
            self.sched.flush()
            m.reopen()
            return self._trip("mixer_callback", stalled_s=stalled_s)
        overdue_s = self.sched.overdue(t)
        if overdue_s > self.limit_s:
            self.sched.flush()
            m.release("watchdog")
            return self._trip("scheduler", overdue_s=overdue_s)
        idle = m.tts_idle_since
        if m.state is State.TTS and not m.busy() and idle is not None and t - idle > self.limit_s:
            # a slow clause is not cancelled: it stays in flight and retakes the cable when ready
            m.release("watchdog")
            return self._trip("tts_idle", idle_s=t - idle)
        return None


class ExitGuard:
    """Runs `cleanup` exactly once, however the process ends: `finally`, atexit, Ctrl-C or a closed console."""

    def __init__(self, cleanup: Callable[[], None], on_signal: Callable[[], None]) -> None:
        self._cleanup = cleanup
        self._lock = threading.Lock()
        self._done = False
        atexit.register(self.run)
        for s in (signal.SIGINT, signal.SIGTERM, signal.SIGBREAK):   # SIGBREAK = console window closed
            signal.signal(s, lambda *_: on_signal())

    def run(self) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True
        self._cleanup()
