"""Small thread-safe float32 ring buffer between an input callback and an output callback."""

from __future__ import annotations

import threading

import numpy as np


class Ring:
    def __init__(self, capacity: int) -> None:
        self._buf = np.zeros(capacity, dtype=np.float32)
        self._cap = capacity
        self._r = 0
        self._n = 0
        self._lock = threading.Lock()
        self.overflow_frames = 0

    def __len__(self) -> int:
        return self._n

    def write(self, x: np.ndarray) -> None:
        with self._lock:
            n = len(x)
            if n >= self._cap:
                x = x[-self._cap:]
                n = self._cap
            free = self._cap - self._n
            if n > free:                                   # drop the oldest, never block the producer
                drop = n - free
                self._r = (self._r + drop) % self._cap
                self._n -= drop
                self.overflow_frames += drop
            w = (self._r + self._n) % self._cap
            first = min(n, self._cap - w)
            self._buf[w:w + first] = x[:first]
            self._buf[:n - first] = x[first:]
            self._n += n

    def read(self, n: int) -> np.ndarray:
        """Up to `n` frames; fewer if the ring holds less."""
        with self._lock:
            n = min(n, self._n)
            first = min(n, self._cap - self._r)
            out = np.empty(n, dtype=np.float32)
            out[:first] = self._buf[self._r:self._r + first]
            out[first:] = self._buf[:n - first]
            self._r = (self._r + n) % self._cap
            self._n -= n
            return out

    def drop_to(self, keep: int) -> int:
        """Discard the oldest frames so at most `keep` remain (bounds passthrough latency)."""
        with self._lock:
            drop = max(0, self._n - keep)
            self._r = (self._r + drop) % self._cap
            self._n -= drop
            return drop

    def clear(self) -> None:
        with self._lock:
            self._r = 0
            self._n = 0
