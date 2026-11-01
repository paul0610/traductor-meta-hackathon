"""Your own voice on the English side: a short recording that never leaves this PC.

Zero-shot: Pocket reads the clip once and every clause starts from the state it leaves, so a
clone runs at the speed of a catalog voice, whatever the length of the clip (SPEC §10). Only
making one needs the gated weights: the exported state also runs on the free ones. The text
to read opens with the consent, so the recording is its own proof, and all of it stays in
logs/voice, which git ignores. A clone speaks at the pace of its clip, and someone reading a
foreign language reads slowly: a clause longer than its Spanish delays every clause behind
it, so a slow clip is sped up before cloning, up to a limit, instead of stretching each clause.
"""

from __future__ import annotations

import asyncio
import json
import time
import webbrowser
import winsound
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf
import soxr

from ..audio.capture import MicCapture
from ..clock import now
from .pocket import PocketVoice
from .stretch import wsola

DIR = Path("logs/voice")
TERMS_URL = "https://huggingface.co/kyutai/pocket-tts"
MAX_S = 30                     # Pocket reads no more
MIN_S = 8                      # under this the clone comes out thin
PEAK = 0.7                     # about -3 dB: the voice under the clone keeps the level of a catalog voice
SPEECH_DB = 25                 # how far under the loudest speech a frame still counts as speech
MIN_ROOM_DB = 20               # less than this over the room, and the clone copies the room too
FRAME_S = 0.02
TYPICAL_CHARS = 40             # a clause of this length says the pace in one number
TYPICAL_S = 1.9                # what michael takes: the sync was tuned on it
MAX_SPEED = 1.3                # past this the clip stops sounding like a person reading
CUE_S = 0.4                    # the sound that says it is recording
PREVIEW_REAL_S = 6
PREVIEW_GAP_S = 0.6
TO_READ = ("This is my own voice, and I agree to make a copy of it for my personal interpreter. "
           "Today I want to talk about a simple idea. When we work with people in other countries, "
           "we should understand each other without waiting. Please take your time, ask me anything, "
           "and tell me what you really think about the project.")
PACE_TEXTS = ("Good morning",
              "Thanks for joining the call",
              "We finished the report yesterday",
              "but nobody has read it yet",
              "I think the main problem is the database",
              "Could you send me the numbers before the end of the week",
              "We should talk about it with the whole team next Monday morning",
              "and I would like to understand what the client really needs from us",
              "Yes, that works for me",
              "I have five years of experience building data systems for medium-sized companies")
PREVIEW = (4, 6, 9)            # which of those the person hears


@dataclass(frozen=True, slots=True)
class Saved:
    pace: tuple[float, float]
    active: bool


class Store:
    """Three files, or none: a voice without its pace would break the sync."""

    def __init__(self, folder: Path = DIR) -> None:
        self.folder = folder
        self.sample = folder / "own_voice.wav"
        self.state = folder / "own_voice.safetensors"
        self.meta = folder / "own_voice.json"
        self.preview = folder / "own_voice_preview.wav"
        self.sped_up = folder / "own_voice_sped_up.wav"

    def read(self, model: str) -> Saved | None:
        """A state only fits the weights that made it."""
        if not self.meta.exists() or not self.state.exists():
            return None
        meta = json.loads(self.meta.read_text(encoding="utf-8"))
        if meta["model"] != model:
            return None
        return Saved(tuple(meta["pace"]), meta["active"])

    def write(self, model: str, pace: tuple[float, float]) -> None:
        self.meta.write_text(json.dumps({"model": model, "pace": list(pace), "active": True, "consent": TO_READ,
                                         "recorded": time.strftime("%Y-%m-%d %H:%M")}, indent=2), encoding="utf-8")

    def activate(self, active: bool) -> None:
        meta = json.loads(self.meta.read_text(encoding="utf-8"))
        self.meta.write_text(json.dumps({**meta, "active": active}, indent=2), encoding="utf-8")

    def forget(self) -> None:
        for path in (self.sample, self.state, self.meta, self.preview, self.sped_up):
            path.unlink(missing_ok=True)


def fit_pace(chars: list[int], seconds: list[float]) -> tuple[float, float]:
    """Seconds per character and per clause, the two numbers isochrony needs from a voice."""
    per_char, per_clause = np.polyfit(chars, seconds, 1)
    return float(per_char), max(float(per_clause), 0.0)


def clip_speed(pace: tuple[float, float]) -> float:
    """How much faster the clip should go for the clone to keep up, as measured: a third faster clip, a quarter less time."""
    typical = pace[0] * TYPICAL_CHARS + pace[1]
    return float(np.clip(typical / TYPICAL_S, 1.0, MAX_SPEED))


def review(x: np.ndarray, rate: int) -> tuple[np.ndarray | None, list[str]]:
    """Without the silences at the ends and with its peak in place; None if it cannot make a clone."""
    size = int(rate * FRAME_S)
    n = len(x) // size
    if n == 0:
        return None, ["No se grabó audio."]
    db = 20 * np.log10(np.sqrt((x[:n * size].reshape(n, size) ** 2).mean(axis=1)) + 1e-9)
    room, voice = np.percentile(db, [20, 95])
    on = np.flatnonzero(db > voice - SPEECH_DB)
    y = x[max(on[0] - 10, 0) * size:min(on[-1] + 20, n) * size]
    if len(y) / rate < MIN_S:
        return None, [f"Dura {len(y) / rate:.0f} s y hacen falta al menos {MIN_S}: lee todo el texto."]
    if voice - room < 12:
        return None, ["Casi no se distingue tu voz del ruido de la sala."]
    problems = []
    if np.abs(x).max() >= 0.98:
        problems.append("Saturó: habla algo más lejos del micrófono.")
    if voice - room < MIN_ROOM_DB:
        problems.append("Hay ruido de fondo y el clon lo copiará.")
    return y * (PEAK / max(float(np.abs(y).max()), 1e-6)), problems


class Take:
    """The microphone, only while the person reads."""

    def __init__(self, mic_name: str) -> None:
        self.blocks: list[np.ndarray] = []
        self.mic = MicCapture(mic_name)
        self.mic.add_consumer(lambda t, block: self.blocks.append(block))

    def start(self) -> None:
        self.mic.start()

    def stop(self) -> tuple[np.ndarray, int]:
        rate = self.mic.rate
        self.mic.stop()
        x = np.concatenate(self.blocks) if self.blocks else np.zeros(0, np.float32)
        return x[:MAX_S * rate], rate


@dataclass(frozen=True, slots=True)
class Prompt:
    title: str
    body: str
    actions: tuple[tuple[str, str], ...] = ()        # (label, method of Enrollment)
    busy: bool = False                               # the round button waits
    suggests: bool = True                            # the first action is the one to take


OFFER = Prompt("¿Quieres que te oigan con tu voz?",
               "Opcional. Lees medio minuto en inglés y la traducción suena como tú. La grabación no sale de esta PC.",
               (("Clonar mi voz", "script"), ("Ahora no", "later")))
NO_ACCESS = Prompt("Falta un permiso para clonar",
                   "Acepta las condiciones de Pocket TTS en Hugging Face, inicia sesión con uvx hf auth login "
                   "y vuelve a abrir la app. Sin eso sigue sonando la voz estándar.",
                   (("Abrir la página", "terms"), ("Ahora no", "later")))
READ = Prompt("Lee esto en inglés, con calma",
              f"{TO_READ}\n\nGraba solo tu propia voz: al leerlo autorizas esta copia.",
              (("Empezar a grabar", "record"), ("Cancelar", "later")))
WORKING = Prompt("Clonando tu voz…", "Tarda unos 30 s. Nada sale de esta PC.", busy=True)
OWN = Prompt("Hablas con tu voz clonada", "Al empezar, el otro lado oirá un aviso de que es una copia.",
             (("Voz estándar", "standard"), ("Borrar mi voz", "ask_forget")), suggests=False)
STANDARD = Prompt("Tu voz clonada está guardada", "Ahora suena la voz estándar.",
                  (("Usar mi voz", "own"), ("Borrar mi voz", "ask_forget")))
FORGET = Prompt("¿Borrar tu voz?", "Se borran la grabación y el clon de esta PC. Para volver a usarla habrá que grabarla otra vez.",
                (("Borrar", "forget"), ("Cancelar", "back")), suggests=False)


def recording(seconds: float) -> Prompt:
    return Prompt(f"Grabando · {seconds:.0f} s", TO_READ, (("Terminar", "finish"),), busy=True)


def retry(problems: list[str]) -> Prompt:
    return Prompt("Hay que grabarla otra vez", " ".join(problems), (("Grabar otra vez", "script"), ("Ahora no", "later")))


def listen(problems: list[str]) -> Prompt:
    body = " ".join(["Suena primero tu grabación y después el clon.", *problems])
    return Prompt("Así suenas en inglés", body, (("Usar mi voz", "keep"), ("Escuchar", "play"), ("Descartar", "discard")), busy=True)


def play_wav(path: Path) -> None:
    winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)


class Enrollment:
    """The steps behind the card. Each action is named by a method, and every step says what to show next."""

    def __init__(self, voice: PocketVoice, store: Store, standard: str, mic_name: str, *, take: Callable = Take,
                 play: Callable = play_wav, browse: Callable = webbrowser.open, emit: Callable[..., None] | None = None,
                 on_voice: Callable[[], None] = lambda: None) -> None:
        self.voice = voice
        self.store = store
        self.standard_voice = standard
        self.mic_name = mic_name
        self.make_take = take
        self.play_wav = play
        self.browse = browse
        self.emit = emit or (lambda ev, **f: None)
        self.on_voice = on_voice
        self.on_prompt: Callable[[Prompt | None], None] = lambda p: None
        self.chime: Callable[[str], None] = lambda name: None
        self.prompt: Prompt | None = None
        self._take: Take | None = None
        self._ticker: asyncio.Task | None = None
        self._pace: tuple[float, float] | None = None

    @property
    def busy(self) -> bool:
        return self.prompt is not None and self.prompt.busy

    def show(self, prompt: Prompt | None) -> None:
        self.prompt = prompt
        self.on_prompt(prompt)

    def opening(self) -> None:
        saved = self.store.read(self.voice.model)
        self.show(OFFER if saved is None else OWN if saved.active else STANDARD)

    async def act(self, key: str) -> None:
        await getattr(self, key)()

    async def later(self) -> None:
        self.show(None)

    async def script(self) -> None:
        self.show(READ if self.voice.can_clone else NO_ACCESS)

    async def terms(self) -> None:
        self.browse(TERMS_URL)

    async def record(self) -> None:
        take = self.make_take(self.mic_name)
        self.show(recording(0))
        self.chime("record")
        await asyncio.sleep(CUE_S)                   # the cue must not end up in the clip
        try:
            await asyncio.to_thread(take.start)
        except RuntimeError as exc:
            self.emit("state", what="own_voice", step="mic_failed", error=repr(exc)[:300])
            self.show(retry(["No pude abrir el micrófono."]))
            return
        self._take = take
        self.emit("state", what="own_voice", step="record")
        self._ticker = asyncio.ensure_future(self._tick(now()))

    async def _tick(self, t0: float) -> None:
        while now() - t0 < MAX_S:
            self.show(recording(now() - t0))
            await asyncio.sleep(0.5)
        await self.finish()

    async def finish(self) -> None:
        if self._ticker is not asyncio.current_task() and self._ticker is not None:
            self._ticker.cancel()
        take, self._take = self._take, None
        x, rate = take.stop()
        self.chime("done")
        self.show(WORKING)
        clean, problems = review(x, rate)
        if clean is None:
            self.emit("state", what="own_voice", step="rejected", detail=problems)
            self.show(retry(problems))
            return
        self.store.folder.mkdir(parents=True, exist_ok=True)
        sf.write(self.store.sample, clean, rate, subtype="PCM_16")
        try:
            await self.clone(clean, rate)
        except Exception as exc:                     # the app can still start, with the voice it had
            await self.voice.switch(self.standard_voice)
            self.store.forget()
            self.emit("state", what="own_voice", step="failed", error=repr(exc)[:300])
            self.show(retry(["No pude clonarla."]))
            return
        self.show(listen(problems))

    async def clone(self, clean: np.ndarray, rate: int) -> None:
        """The pace comes from the clauses it says here, and the person hears three of them."""
        t0 = now()
        await self.voice.clone(self.store.sample, self.store.state)
        said = await self._say_all()
        speed = clip_speed(self._pace)
        if speed > 1.0:
            sf.write(self.store.sped_up, wsola(clean, speed, rate), rate, subtype="PCM_16")
            await self.voice.clone(self.store.sped_up, self.store.state)
            self.store.sped_up.unlink()
            said = await self._say_all()
        out = self.voice.out_rate
        gap = np.zeros(int(out * PREVIEW_GAP_S), np.float32)
        real = soxr.resample(clean[:PREVIEW_REAL_S * rate], rate, out).astype(np.float32)
        spoken = [np.frombuffer(said[i].pcm, "<i2").astype(np.float32) / 32768.0 for i in PREVIEW]
        parts = [real, gap, gap]
        for x in spoken:
            parts += [x, gap]
        sf.write(self.store.preview, np.concatenate(parts), out, subtype="PCM_16")
        self.emit("state", what="own_voice", step="cloned", seconds=len(clean) / rate, took_s=now() - t0, pace=self._pace,
                  clip_speed=speed)

    async def _say_all(self) -> list:
        said = [await self.voice.say(text) for text in PACE_TEXTS]
        self._pace = fit_pace([len(t) for t in PACE_TEXTS], [r.duration_s for r in said])
        return said

    async def play(self) -> None:
        self.play_wav(self.store.preview)

    async def keep(self) -> None:
        self.store.write(self.voice.model, self._pace)
        await self.voice.switch(str(self.store.state), pace=self._pace, own=True)
        self.emit("state", what="own_voice", step="kept")
        self.on_voice()
        self.show(OWN)

    async def discard(self) -> None:
        await self.forget()

    async def ask_forget(self) -> None:
        self.show(FORGET)

    async def back(self) -> None:
        self.opening()

    async def forget(self) -> None:
        await self.voice.switch(self.standard_voice)
        self.store.forget()
        self.emit("state", what="own_voice", step="forgotten")
        self.on_voice()
        self.show(OFFER)

    async def standard(self) -> None:
        await self.voice.switch(self.standard_voice)
        self.store.activate(False)
        self.on_voice()
        self.show(STANDARD)

    async def own(self) -> None:
        saved = self.store.read(self.voice.model)
        await self.voice.switch(str(self.store.state), pace=saved.pace, own=True)
        self.store.activate(True)
        self.on_voice()
        self.show(OWN)

    def close(self) -> None:
        """The app is leaving this screen: a take in progress or never approved does not stay on disk."""
        if self._ticker is not None:
            self._ticker.cancel()
        if self._take is not None:
            self._take.stop()
            self._take = None
        if self.prompt is not None and self.prompt.busy:
            self.store.forget()
        self.show(None)
