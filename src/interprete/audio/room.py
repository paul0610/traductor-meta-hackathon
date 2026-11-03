"""Speakers instead of headphones: what the PC plays reaches the microphone, so the mic waits while it plays.

The app takes my mic straight from the device, not through the call app, so the call app's echo
cancelling never touches it. On speakers the mic would hear the other side and the Spanish voice, and
translate them back to the other side in English. The loopback is exactly what the speakers play, so
its level says when the mic could be hearing them: from then until a short tail after, the mic hands
on silence of the same length (`MicCapture.gate`), to the ASR, the live voice and the dub alike. So it
goes in turns, like a walkie-talkie: what I say while the call sounds is lost, and the button says
MIC EN ESPERA meanwhile. Headphones need none of it, and keep talking over the other side possible.
"""

from __future__ import annotations

from collections import deque
from typing import Callable

import numpy as np

from .devices import looks_like_headphones

RATE = 16000                    # the loopback's feed is already the ASR's
TAIL_S = 0.6                    # the room still rings, and Bluetooth speakers play behind the loopback
LOUD_DBFS = -55.0               # under this, nothing the mic picks up comes out as words
OVER_FLOOR_DB = 12.0            # this far over the quietest moment of the last seconds: something is playing
FLOOR_S = 5                     # seconds the quietest moment is looked for in, so a steady hum is not the call
SILENCE_DBFS = -100.0           # before the first reading: anything over LOUD_DBFS counts


def dbfs(block: bytes) -> float:
    x = np.frombuffer(block, "<i2").astype(np.float32)
    rms = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
    return 20 * float(np.log10(max(rms, 1.0) / 32768))


def speakers_mode(setting: str, output: str) -> bool:
    """`RTT_SPEAKERS`: 1 or 0 decide; auto, the default, takes any output not named like headphones for speakers.

    Wired headphones on the jack often keep the speakers' name: they then go in turns too, which is safe.
    """
    if setting in ("0", "1"):
        return setting == "1"
    return not looks_like_headphones(output)


class Room:
    """`hear` takes every loopback block before anything gates it; `deaf(t)` closes the mic."""

    def __init__(self, voice: Callable[[float], bool] | None = None) -> None:
        self.voice = voice or (lambda t: False)      # the Spanish voice: closes the mic before its sound leaves
        self.loud_until = float("-inf")
        self._second = 0
        self._quietest = SILENCE_DBFS
        self._minima: deque[float] = deque([SILENCE_DBFS], maxlen=FLOOR_S)

    def hear(self, t_capture: float, block: bytes) -> None:
        level = dbfs(block)
        second = int(t_capture)
        if second != self._second:
            self._minima.append(self._quietest)
            self._second, self._quietest = second, level
        else:
            self._quietest = min(self._quietest, level)
        if level > max(LOUD_DBFS, min(self._quietest, *self._minima) + OVER_FLOOR_DB):
            self.loud_until = max(self.loud_until, t_capture + len(block) / 2 / RATE)

    def deaf(self, t: float) -> bool:
        return t < self.loud_until + TAIL_S or self.voice(t)

    def tap(self, feed: Callable[[float, bytes], None]) -> Callable[[float, bytes], None]:
        """The loopback's feed, heard on the way."""
        def heard(t_capture: float, block: bytes) -> None:
            self.hear(t_capture, block)
            feed(t_capture, block)
        return heard
