"""Synthetic voices for runs without a person: Spanish "me" and English "them", as 16 kHz WAVs in logs/.

    uv run python scripts/make_fixtures.py

They measure the real ASR, Spark, TTS and cable path. They do not replace a human
voice: Edge speech is cleaner and steadier than a person, so its ASR numbers are a floor.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from interprete.audio.fixture import RATE, save_wav
from interprete.tts.edge import synthesize

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


async def build(lines: list[str], voice: str, pause_s: float, rate_pct: int = 0) -> bytes:
    gap = bytes(int(RATE * pause_s) * 2)
    pcm = bytearray(gap)
    for line in lines:
        pcm += (await synthesize(line, voice, RATE, rate_pct=rate_pct)).pcm + gap
    return bytes(pcm)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pause-es", type=float, default=0.8, help="silence between lines of the monologue, seconds")
    ap.add_argument("--pause-en", type=float, default=2.5, help="silence between the other side's turns, seconds")
    args = ap.parse_args()
    for name, lines, voice, pause, rate in (("fixture_es", ES, "es-PE-AlexNeural", args.pause_es, 0),
                                            ("fixture_es_fast", ES, "es-PE-AlexNeural", args.pause_es, 35),
                                            ("fixture_en", EN, "en-GB-SoniaNeural", args.pause_en, 0)):
        pcm = await build(lines, voice, pause, rate)
        path = Path("logs") / f"{name}.wav"
        save_wav(path, pcm)
        print(f"{path}: {len(pcm) / 2 / RATE:.1f} s, {voice}")


if __name__ == "__main__":
    asyncio.run(main())
