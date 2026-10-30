"""Synthetic voices for runs without a person: Spanish "me" and English "them", as 16 kHz WAVs in logs/.

    uv run python scripts/make_fixtures.py

They measure the real ASR, Spark, TTS and cable path. They do not replace a human
voice: Edge speech is cleaner and steadier than a person, so its ASR numbers are a floor.
Next to each WAV goes a `.words.json` with when every word really starts, the
ground truth that `scripts/gate_sync.py` compares `t_src_start` against.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import edge_tts

from interprete.audio.fixture import RATE, save_wav
from interprete.tts.edge import TtsResult, finish

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
EN = [
    "Hello, thanks for joining us today.",
    "Could you tell me a little about your background and what you are working on right now?",
    "That sounds great. What was the hardest part of that project?",
    "I see. And how did you handle the disagreement between the two teams?",
    "We are a small team, about eight people, spread across three time zones.",
    "Do you have any questions for me before we finish?",
]


async def speak(text: str, voice: str, rate_pct: int) -> tuple[TtsResult, list[tuple[str, float]]]:
    """One line, and when each of its words starts in the trimmed audio."""
    mp3 = bytearray()
    marks = []
    comm = edge_tts.Communicate(text, voice, rate=f"{rate_pct:+d}%", boundary="WordBoundary")
    async for part in comm.stream():
        if part["type"] == "audio":
            mp3 += part["data"]
        elif part["type"] == "WordBoundary":
            marks.append((part["text"], part["offset"] / 1e7))
    r = await finish(bytes(mp3), RATE, 0.0, None, 0.0)
    return r, [(w, t - r.trimmed_lead_s) for w, t in marks]


async def build(lines: list[str], voice: str, pause_s: float, rate_pct: int = 0) -> tuple[bytes, list[dict]]:
    gap = bytes(int(RATE * pause_s) * 2)
    pcm = bytearray(gap)
    words = []
    for line in lines:
        r, marks = await speak(line, voice, rate_pct)
        t0 = len(pcm) / 2 / RATE
        words += [dict(text=w, t=round(t0 + t, 3)) for w, t in marks]
        pcm += r.pcm + gap
    return bytes(pcm), words


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pause-es", type=float, default=0.8, help="silence between lines of the monologue, seconds")
    ap.add_argument("--pause-en", type=float, default=2.5, help="silence between the other side's turns, seconds")
    args = ap.parse_args()
    for name, lines, voice, pause, rate in (("fixture_es", ES, "es-PE-AlexNeural", args.pause_es, 0),
                                            ("fixture_es_fast", ES, "es-PE-AlexNeural", args.pause_es, 35),
                                            ("fixture_en", EN, "en-GB-SoniaNeural", args.pause_en, 0)):
        pcm, words = await build(lines, voice, pause, rate)
        path = Path("logs") / f"{name}.wav"
        save_wav(path, pcm)
        path.with_suffix(".words.json").write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
        print(f"{path}: {len(pcm) / 2 / RATE:.1f} s, {len(words)} words, {voice}")


if __name__ == "__main__":
    asyncio.run(main())
