"""The mic, D seconds later: the voice under the dub, in step with the delayed video (SPEC §4.1).

Written from the mic callback and read from the cable callback, two devices with
two clocks. The fill level IS the delay, so it is the only thing watched: drift or
a stalled device shows up as a level off target and the reader jumps back to it.
Under the TTS a jump is inaudible; a delay that wanders away from the video is not.
"""

from __future__ import annotations

import numpy as np

from .ring import Ring

SLACK_S = 0.04                  # a mic block plus a cable block: normal movement of the level


class DelayLine:
    def __init__(self, rate: int, delay_s: float) -> None:
        self.frames = int(rate * delay_s)
        self._slack = int(rate * SLACK_S)
        self._ring = Ring(self.frames + int(rate))
        self._flowing = False
        self.jumps = 0

    def write(self, x: np.ndarray) -> None:
        self._ring.write(x)

    def clear(self) -> None:
        """Whatever is inside was already heard live: start over, D seconds of silence."""
        self._ring.clear()
        self._flowing = False

    def read(self, n: int) -> np.ndarray:
        """`n` frames captured D seconds ago; silence until the line has filled."""
        level = len(self._ring)
        if level > self.frames + self._slack:
            self._ring.drop_to(self.frames)
            self.jumps += 1
        elif level < self.frames - self._slack and self._flowing:
            self._flowing = False
            self.jumps += 1
        if level >= self.frames:
            self._flowing = True
        x = self._ring.read(n) if self._flowing else np.empty(0, np.float32)
        return x if len(x) == n else np.concatenate([np.zeros(n - len(x), np.float32), x])
