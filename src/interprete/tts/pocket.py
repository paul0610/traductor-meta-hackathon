"""Pocket TTS on this machine: a voice with no network to stall on.

Edge's bad windows delay one clause by seconds and the clauses behind it with it; this
voice takes the same time on a bad afternoon (SPEC §8). The model is not thread-safe, so
clauses go one at a time, and at twice real time the next one barely waits. It has no
speed control, so `rate_pct` becomes a time stretch. A clause can lose its end of speech
and run on in silence up to the model's maximum, up to 8 s, so generation stops at twice
what the text could need.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Callable

import numpy as np
import soxr

from ..clock import now
from .edge import S_PER_CHAR, S_PER_CLAUSE, TtsResult, isochrony_pct, trim_silence
from .stretch import wsola

THREADS = 2                    # 4 costs twice the cores for 10 % less time; the call needs them too
MAX_S_PER_CHAR = 0.05          # slower than every voice measured: with MAX_S_PER_CLAUSE, no real clause reaches the cap
MAX_S_PER_CLAUSE = 0.8
MAX_OVER = 2.0
FULL_SCALE = 0.99
WARM_TEXT = "Good morning."

PACE: dict[tuple[str, str], tuple[float, float]] = {        # seconds per character and per clause, 219 clauses at rate 0
    ("english", "michael"): (0.0382, 0.35),
    ("english", "george"): (0.0373, 0.29),
    ("english_18m_2026-10", "george"): (0.0401, 0.39),
}


def load_model(model: str, voice: str, threads: int):
    import torch
    from pocket_tts import TTSModel

    torch.set_num_threads(threads)
    tts = TTSModel.load_model(language=model)
    return tts, tts.get_state_for_audio_prompt(voice)


def export_state(state, path: Path) -> None:
    from pocket_tts import export_model_state

    export_model_state(state, path)


def max_audio_s(text: str) -> float:
    return (MAX_S_PER_CHAR * len(text) + MAX_S_PER_CLAUSE) * MAX_OVER


class PocketVoice:
    reconnects = 0

    def __init__(self, model: str, voice: str, out_rate: int, *, gain_db: float = 0.0, threads: int = THREADS,
                 load: Callable = load_model, export: Callable = export_state,
                 pace: tuple[float, float] | None = None, own: bool = False) -> None:
        self.model = model
        self.voice = voice
        self.out_rate = out_rate
        self.gain = 10 ** (gain_db / 20)
        self.threads = threads
        self.load = load
        self.export = export
        self.tts = None
        self.state = None
        self.own = own                               # the person's clone: the notice has to say so
        self.pace = pace or PACE.get((model, voice), (S_PER_CHAR, S_PER_CLAUSE))
        self._one_at_a_time = threading.Lock()

    @property
    def name(self) -> str:
        return f"pocket:{self.model}:{Path(self.voice).stem}"

    @property
    def warning(self) -> str:
        return " │ TU VOZ" if self.own else ""

    @property
    def can_clone(self) -> bool:
        """False on the free weights, which is what a machine that never accepted the terms loads."""
        return getattr(self.tts, "has_voice_cloning", False)

    async def start(self) -> None:
        """Loading takes 15 s and the first clause is slower than the rest, so a short one goes first."""
        self.tts, self.state = await asyncio.to_thread(self.load, self.model, self.voice, self.threads)
        await self.say(WARM_TEXT)

    async def close(self) -> None:
        pass

    def _prompt(self, audio: str | Path) -> object:
        with self._one_at_a_time:
            return self.tts.get_state_for_audio_prompt(audio, truncate=True)

    async def clone(self, sample: Path, path: Path) -> None:
        """Speaks with the voice of `sample` from here on, and leaves the state in `path` for the next run."""
        state = await asyncio.to_thread(self._prompt, sample)
        await asyncio.to_thread(self.export, state, path)
        self.state = state

    async def switch(self, voice: str, *, pace: tuple[float, float] | None = None, own: bool = False) -> None:
        self.state = await asyncio.to_thread(self._prompt, voice)
        self.voice, self.own = voice, own
        self.pace = pace or PACE.get((self.model, voice), (S_PER_CHAR, S_PER_CLAUSE))

    def rate_for(self, text: str, src_s: float | None) -> int:
        return isochrony_pct(text, src_s, *self.pace)

    def _generate(self, text: str, stop: threading.Event) -> tuple[np.ndarray, float | None]:
        rate = self.tts.sample_rate
        cap = int(max_audio_s(text) * rate)
        parts: list[np.ndarray] = []
        t_first = None
        n = 0
        for chunk in self.tts.generate_audio_stream(self.state, text, copy_state=True, stop=stop):
            t_first = t_first or now()
            parts.append(chunk.numpy())
            n += len(parts[-1])
            if n > cap:
                stop.set()
        return np.concatenate(parts), t_first

    def _synth(self, text: str, rate_pct: int, stop: threading.Event, t_request: float) -> TtsResult:
        with self._one_at_a_time:
            x, t_first = self._generate(text, stop)
        t_end = now()
        rate = self.tts.sample_rate
        if rate_pct:
            x = wsola(x, 1 + rate_pct / 100, rate)
        y, lead, tail = trim_silence(soxr.resample(x, rate, self.out_rate), self.out_rate)
        peak = float(np.abs(y).max()) if len(y) else 1.0
        y = y * min(self.gain, FULL_SCALE / max(peak, 1e-6))  # never clip: a loud clause rises less than the rest
        pcm = (y * 32767.0).astype("<i2").tobytes()
        return TtsResult(pcm, self.out_rate, len(y) / self.out_rate, t_request, t_first, t_end, now(), 0.0, lead, tail, 0)

    async def say(self, text: str, *, rate_pct: int = 0, on_hedge: Callable[[], None] | None = None) -> TtsResult:
        stop = threading.Event()
        try:
            return await asyncio.to_thread(self._synth, text, rate_pct, stop, now())
        except asyncio.CancelledError:               # the thread goes on: this ends its clause at the next frame
            stop.set()
            raise
