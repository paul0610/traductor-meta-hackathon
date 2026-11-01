"""Where a phrase opens, heard on the microphone itself (SPEC §7.4).

Mid-turn the ASR says nothing about a pause: words surface in bursts, up to 2 s after
they were said, so their arrival cannot place a phrase the lips have already started.
The mic can, to a block, on the same clock as the video. This runs in the audio thread
and the policy reads `onsets` from the loop: appending to a deque and copying it are atomic.
"""

from __future__ import annotations

from collections import deque

import numpy as np

BLOCK_S = 0.02
QUIET_S = 0.25                  # of quiet before speech counts as opening a phrase
SPEECH_S = 0.1                  # loud for this long: a click opens nothing
HISTORY_S = 60.0                # the room and the voice are read from this much of the past
FIRST_READING_S = 2.0
MIN_RANGE_DB = 12.0             # voice this close to the room gives no threshold worth trusting


class SpeechOnsets:
    def __init__(self) -> None:
        self.onsets: deque[float] = deque(maxlen=256)
        self._levels: deque[float] = deque(maxlen=round(HISTORY_S / BLOCK_S))
        self._threshold: float | None = None
        self._blocks = 0
        self._quiet = 0
        self._loud = 0
        self._t_loud = 0.0

    def _read_room(self) -> None:
        room, voice = np.percentile(self._levels, [20, 95])
        self._threshold = (room + voice) / 2 if voice - room >= MIN_RANGE_DB else None      # halfway between them in dB

    def feed(self, t_capture: float, block: np.ndarray) -> None:
        level = 10 * np.log10(float(np.mean(block ** 2)) + 1e-12)
        self._levels.append(level)
        self._blocks += 1
        if self._blocks % round(1.0 / BLOCK_S) == 0 and self._blocks >= round(FIRST_READING_S / BLOCK_S):
            self._read_room()
        if self._threshold is None or level <= self._threshold:
            self._quiet += 1
            self._loud = 0
            return
        if self._loud == 0:
            self._t_loud = t_capture
        self._loud += 1
        if self._loud >= round(SPEECH_S / BLOCK_S):
            if self._quiet >= round(QUIET_S / BLOCK_S):
                self.onsets.append(self._t_loud)
            self._quiet = 0
