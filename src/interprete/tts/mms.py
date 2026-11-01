"""MMS-TTS on this machine: Meta's open weights, so listening, translating and speaking are all Meta's.

VITS, one speaker, 16 kHz. It has no streaming: a clause comes back whole, so its first PCM is its
last, and that wait goes straight into D. The revision is pinned and the weights come from
safetensors, so what runs is what was measured. The model holds no state between clauses, but one
clause at a time keeps the cores for the call.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Callable

import numpy as np
import soxr

from ..clock import now
from .shaping import TtsResult, isochrony_pct, trim_silence
from .spoken import spoken
from .stretch import wsola

RATE = 16000
REVISIONS = {"facebook/mms-tts-eng": "c71de0fe7204c83f1c10820a7d696d0b450048ba",
             "facebook/mms-tts-spa": "833b96c0278c85cf09776d565ba8f1cb2819f7ec"}
THREADS = 4                    # a third less time per clause than 2; 6 saves 10 % more, and the call needs those cores
SPEAKING_RATE = 1.45           # forty characters in 1.95 s, as the voice gate S passed with; at 1.3 and 2.10 s it did not
DURATION_NOISE = 0.0           # its default, 0.8, makes a clause 6 % longer on average and no more predictable
FULL_SCALE = 0.99
WARM_TEXT = "Good morning, thanks for having me."
PACE = {                       # speaking rate: seconds per character and per clause, trimmed, 58 clauses
    1.0: (0.0532, 0.30),
    1.15: (0.0509, 0.25),
    1.3: (0.0460, 0.26),
    1.45: (0.0426, 0.24),
}


def load_model(model: str, threads: int) -> Callable[[str, float], np.ndarray]:
    import torch
    from transformers import AutoTokenizer, VitsModel
    from transformers.utils import logging

    logging.disable_progress_bar()
    torch.set_num_threads(threads)
    revision = REVISIONS[model]
    tokenizer = AutoTokenizer.from_pretrained(model, revision=revision)
    net = VitsModel.from_pretrained(model, revision=revision, use_safetensors=True).eval()
    net.noise_scale_duration = DURATION_NOISE

    def speak(text: str, speaking_rate: float) -> np.ndarray:
        with torch.inference_mode():
            return net(**tokenizer(text, return_tensors="pt"), speaking_rate=speaking_rate).waveform[0].numpy()

    return speak


class MmsVoice:
    reconnects = 0
    warning = ""

    def __init__(self, model: str, out_rate: int, *, speaking_rate: float = SPEAKING_RATE, threads: int = THREADS,
                 load: Callable = load_model) -> None:
        self.model = model
        self.out_rate = out_rate
        self.speaking_rate = speaking_rate
        self.threads = threads
        self.load = load
        self.speak: Callable[[str, float], np.ndarray] | None = None
        self.pace = PACE[min(PACE, key=lambda r: abs(r - speaking_rate))]
        self._one_at_a_time = threading.Lock()

    @property
    def name(self) -> str:
        return f"mms:{self.model.rsplit('/', 1)[-1]}"

    async def start(self) -> None:
        """A short clause goes first: the first one pays for loading what the rest reuse."""
        self.speak = await asyncio.to_thread(self.load, self.model, self.threads)
        await self.say(WARM_TEXT)

    async def close(self) -> None:
        pass

    def rate_for(self, text: str, src_s: float | None) -> int:
        return isochrony_pct(spoken(text), src_s, *self.pace)

    def _synth(self, text: str, rate_pct: int, t_request: float) -> TtsResult:
        words = spoken(text)
        with self._one_at_a_time:
            x = self.speak(words, self.speaking_rate) if words else np.zeros(0, np.float32)
        t_end = now()
        if rate_pct:
            x = wsola(x, 1 + rate_pct / 100, RATE)
        y, lead, tail = trim_silence(soxr.resample(x, RATE, self.out_rate), self.out_rate)
        peak = float(np.abs(y).max()) if len(y) else 1.0
        y = y * min(1.0, FULL_SCALE / max(peak, 1e-6))
        pcm = (y * 32767.0).astype("<i2").tobytes()
        return TtsResult(pcm, self.out_rate, len(y) / self.out_rate, t_request, t_end, t_end, now(), 0.0, lead, tail, 0)

    async def say(self, text: str, *, rate_pct: int = 0, on_hedge: Callable[[], None] | None = None) -> TtsResult:
        return await asyncio.to_thread(self._synth, text, rate_pct, now())
