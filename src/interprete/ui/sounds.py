"""Short cues for what the button shows, so a call does not need eyes on it.

Made here from plain tones: no files and no new dependency. They play on the Windows default
output, the headphones, and never on the cable, so the other side does not hear them. The
loopback does take them to the ASR, which is why they stay short, soft and without speech.
"""

from __future__ import annotations

import io
import threading
import wave
import winsound
from typing import Callable

import numpy as np

RATE = 44100
NOTE_S = 0.32
PEAK = 0.22                    # under a call, not over it
A3, B3, E4, C5, D5, E5, G5, A5, B5 = 220, 247, 330, 523, 587, 659, 784, 880, 988
CUES: dict[str, tuple[tuple[int, float], ...]] = {    # (note, when)
    "ready": ((E5, 0.0), (A5, 0.09)),                 # it can start
    "live": ((C5, 0.0), (E5, 0.08), (G5, 0.16)),      # rising: the other side is getting me
    "stop": ((G5, 0.0), (C5, 0.1)),
    "mute": ((E4, 0.0), (B3, 0.08)),                  # falling and low: nothing goes out
    "back": ((B3, 0.0), (E4, 0.08)),                  # the same, undone
    "raw": ((A5, 0.0), (A5, 0.13)),                   # twice, and the same note: this one needs attention
    "floor": ((D5, 0.0),),                            # they took the floor: one soft note
    "record": ((B5, 0.0),),
    "done": ((E5, 0.0), (B5, 0.08)),
}


def pluck(freq: float) -> np.ndarray:
    """A soft mallet: a body that rings, a brief shine on top, and an attack that does not click."""
    t = np.arange(int(RATE * NOTE_S)) / RATE
    body = np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.11)
    shine = 0.18 * np.sin(2 * np.pi * freq * 3.9 * t) * np.exp(-t / 0.03)
    return (body + shine) * np.minimum(t / 0.004, 1.0)


def cue(notes: tuple[tuple[int, float], ...]) -> np.ndarray:
    x = np.zeros(int(RATE * (max(at for _, at in notes) + NOTE_S)))
    for freq, at in notes:
        start = int(at * RATE)
        x[start:start + int(RATE * NOTE_S)] += pluck(freq)
    return x * (PEAK / np.abs(x).max())


def wav(x: np.ndarray) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return out.getvalue()


def play_memory(data: bytes) -> None:
    winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_NODEFAULT)


class Sounds:
    def __init__(self, player: Callable[[bytes], None] = play_memory) -> None:
        self.cues = {name: wav(cue(notes)) for name, notes in CUES.items()}
        self.player = player

    def play(self, name: str) -> None:
        """Off the loop: Windows only plays from memory by blocking."""
        threading.Thread(target=self.player, args=(self.cues[name],), name="cue", daemon=True).start()
