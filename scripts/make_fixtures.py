"""Synthetic voices for runs without a person: Spanish "me" and English "them", as 16 kHz WAVs in logs/.

    uv run python scripts/make_fixtures.py

They measure the real ASR, Spark, voice and cable path, on Meta's own MMS-TTS voices, and a clean
clone makes them with this one command. They do not replace a human voice: synthetic speech is
cleaner and steadier than a person, so its ASR numbers are a floor.
Next to each WAV goes a `.words.json` with when every word really starts, the ground truth that
`scripts/gate_sync.py` compares `t_src_start` against. Those times are the model's own: VITS
decides how many frames each letter lasts before it makes a sound, so nothing is aligned afterwards.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import torch
from transformers import AutoTokenizer, VitsModel
from transformers.utils import logging

from interprete.audio.fixture import RATE, save_wav
from interprete.tts.mms import REVISIONS
from interprete.tts.shaping import trim_silence

HOP = 256                      # waveform samples per VITS frame
COMMA_S = 0.22                 # the tokenizer drops punctuation, so a comma is a pause put here by hand
MODELS = {"es": "facebook/mms-tts-spa", "en": "facebook/mms-tts-eng"}

ES = [
    "Buenos días, muchas gracias por recibirme hoy, porque sé que tienen la agenda muy llena esta semana.",
    "Tengo cinco años de experiencia construyendo sistemas de datos para empresas medianas, y en mi último trabajo "
    "reduje el tiempo de los reportes de dos días a veinte minutos.",
    "Lo más difícil no fue la parte técnica, sino coordinar a tres equipos que nunca habían trabajado juntos, "
    "porque cada uno tenía sus propias prioridades y sus propios plazos.",
    "Me interesa este puesto porque quiero trabajar con clientes de otros países, y también porque creo que puedo "
    "ayudar al equipo a tomar mejores decisiones con los datos que ya tienen.",
    "Si tienen alguna pregunta técnica, con gusto la respondo ahora mismo, y si prefieren puedo mostrarles "
    "un ejemplo real de cómo resolvimos un problema parecido el año pasado.",
]
SPANGLISH = [         # English that falls into Spanish where the speaker gets stuck, the case the outgoing chain must clean
    "Good morning, thanks for having me. I have five years of experience building data systems, "
    "{pero la verdad es que} my English is not perfect.",
    "In my last job I worked with, {cómo se dice, los equipos de ventas,} and we reduced the reporting time a lot.",
    "The hardest part was, {eh, coordinar a tres equipos que nunca habían trabajado juntos.}",
    "So I learned to, {no sé, escuchar primero y después proponer,} and that really helped the project.",
    "I want this position because I like working with clients, {sobre todo con clientes de otros países.}",
    "If you have questions about the, the {base de datos,} I can show you a real example.",
]
DEMO = [             # the session behind `python -m interprete.replay`: they ask in English, I answer in Spanish
    ("en", "Hi, thanks for joining us today. Could you tell me a little about yourself?"),
    ("es", "Buenos días, gracias por recibirme. Tengo cinco años de experiencia construyendo sistemas de datos para "
           "empresas medianas, y en mi último trabajo reduje el tiempo de los reportes de dos días a veinte minutos."),
    ("en", "That's impressive. What was the hardest part of that project?"),
    ("es", "Lo más difícil no fue la parte técnica, sino coordinar a tres equipos que nunca habían trabajado juntos. "
           "Aprendí a escuchar primero y después proponer."),
    ("en", "Great, thank you. Do you have any questions for us?"),   # "Great." alone was heard "Brave."
    ("es", "Sí, me gustaría saber cómo es un día normal en el equipo, y qué esperan lograr en los primeros seis meses."),
]
DEMO_LEAD_S = 1.0
DEMO_AFTER_THEM_S = 1.5        # I answer once they finish
DEMO_AFTER_ME_S = 6.0          # they wait for my English, which trails my Spanish by a few seconds
EN = [
    "Hello, thanks for joining us today.",
    "Could you tell me a little about your background and what you are working on right now?",
    "That sounds great. What was the hardest part of that project?",
    "I see. And how did you handle the disagreement between the two teams?",
    "We are a small team, about eight people, spread across three time zones.",
    "Do you have any questions for me before we finish?",
]


class Speaker:
    def __init__(self, model: str) -> None:
        self.tokenizer = AutoTokenizer.from_pretrained(model, revision=REVISIONS[model])
        self.net = VitsModel.from_pretrained(model, revision=REVISIONS[model], use_safetensors=True).eval()
        self.net.duration_predictor.register_forward_hook(self._keep)

    def _keep(self, module, inputs, log_duration) -> None:
        self.log_duration = log_duration

    def say(self, text: str, speaking_rate: float) -> tuple[np.ndarray, list[tuple[str, float]]]:
        """One stretch of speech with no comma in it, and when each of its words starts."""
        ids = self.tokenizer(text, return_tensors="pt")
        with torch.inference_mode():
            x = self.net(**ids, speaking_rate=speaking_rate).waveform[0].numpy()
        frames = np.ceil(np.exp(self.log_duration[0, 0].numpy()) / speaking_rate)     # as the model works them out
        starts = np.concatenate([[0], np.cumsum(frames)]) * HOP / RATE
        tokens = self.tokenizer.convert_ids_to_tokens(ids.input_ids[0])
        marks: list[tuple[str, float]] = []
        word = ""
        for i in range(1 if self.tokenizer.add_blank else 0, len(tokens), 2 if self.tokenizer.add_blank else 1):
            if tokens[i] == " ":
                word = ""
            elif not word:
                word = tokens[i]
                marks.append((word, float(starts[i])))
            else:
                word += tokens[i]
                marks[-1] = (word, marks[-1][1])
        return x, marks


def stretches(line: str) -> list[tuple[str, str]]:
    """(language, text) without commas, in order. Braces mark the Spanish inside an English line."""
    parts = re.split(r"(\{[^}]*\})", line)
    return [("es" if part.startswith("{") else "en" if "{" in line else None, piece)
            for part in parts for piece in re.split(r"[,.]\s*", part.strip("{} ")) if piece]


def build(lines: list[str], language: str, speakers: dict[str, Speaker], pause_s: float, speaking_rate: float) -> tuple[bytes, list[dict]]:
    pcm = [np.zeros(int(RATE * pause_s), np.float32)]
    t = pause_s
    words = []
    for line in lines:
        for lang, text in stretches(line):
            x, marks = speakers[lang or language].say(text, speaking_rate)
            y, lead, _ = trim_silence(x, RATE)
            words += [dict(text=w, t=round(t + at - lead, 3)) for w, at in marks]
            pcm += [y, np.zeros(int(RATE * COMMA_S), np.float32)]
            t += len(y) / RATE + COMMA_S
        pcm.append(np.zeros(int(RATE * pause_s), np.float32))
        t += pause_s
    return (np.clip(np.concatenate(pcm), -1, 1) * 32767).astype("<i2").tobytes(), words


def build_demo(speakers: dict[str, Speaker]) -> tuple[bytes, bytes]:
    """The interview as two tracks of one length: what I say on one, what they say on the other, never at once.

    After my turn they wait for my English to end, so the replay shows turns, not collisions.
    """
    tracks = {"es": [], "en": []}
    t = DEMO_LEAD_S
    for lang, line in DEMO:
        start = int(t * RATE)
        y = []
        for _, text in stretches(line):
            x, _ = speakers[lang].say(text, 1.0)
            y += [trim_silence(x, RATE)[0], np.zeros(int(RATE * COMMA_S), np.float32)]
        tracks[lang].append((start, np.concatenate(y)))
        t += sum(len(part) for part in y) / RATE + (DEMO_AFTER_ME_S if lang == "es" else DEMO_AFTER_THEM_S)
    total = int(t * RATE)
    out = []
    for lang in ("es", "en"):
        x = np.zeros(total, np.float32)
        for start, y in tracks[lang]:
            x[start:start + len(y)] = y
        out.append((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return out[0], out[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pause-es", type=float, default=0.8, help="silence between lines of the monologue, seconds")
    ap.add_argument("--pause-en", type=float, default=2.5, help="silence between the other side's turns, seconds")
    ap.add_argument("--only", default=None, help="build just this fixture")
    ap.add_argument("--demo", action="store_true", help="only the interview behind demo/, as logs/demo_es.wav and demo_en.wav")
    args = ap.parse_args()
    logging.disable_progress_bar()
    speakers = {lang: Speaker(model) for lang, model in MODELS.items()}
    if args.demo:
        for lang, pcm in zip(("es", "en"), build_demo(speakers)):
            save_wav(Path("logs") / f"demo_{lang}.wav", pcm)
        print(f"logs/demo_es.wav, logs/demo_en.wav: {len(pcm) / 2 / RATE:.1f} s, {len(DEMO)} turns")
        return
    for name, lines, language, pause, rate in (("fixture_es", ES, "es", args.pause_es, 1.0),
                                               ("fixture_es_fast", ES, "es", args.pause_es, 1.35),
                                               ("fixture_spanglish", SPANGLISH, "en", args.pause_es, 1.0),
                                               ("fixture_en", EN, "en", args.pause_en, 1.0)):
        if args.only not in (None, name):
            continue
        pcm, words = build(lines, language, speakers, pause, rate)
        path = Path("logs") / f"{name}.wav"
        save_wav(path, pcm)
        path.with_suffix(".words.json").write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        print(f"{path}: {len(pcm) / 2 / RATE:.1f} s, {len(words)} words, {language}")


if __name__ == "__main__":
    main()
