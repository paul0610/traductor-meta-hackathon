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
    "Buenos días, muchas gracias por recibirme hoy.",
    "Tengo cinco años de experiencia construyendo sistemas de datos para empresas medianas.",
    "En mi último trabajo reduje el tiempo de los reportes de dos días a veinte minutos.",
    "Lo más difícil fue coordinar a tres equipos que nunca habían trabajado juntos.",
    "Me interesa este puesto porque quiero trabajar con clientes de otros países.",
    "¿Podrías contarme cómo está organizado el equipo?",
    "Si tienen una pregunta técnica, con gusto la respondo ahora mismo.",
]
EN = [
    "Hello, thanks for joining us today.",
    "Could you tell me a little about your background and what you are working on right now?",
    "That sounds great. What was the hardest part of that project?",
    "I see. And how did you handle the disagreement between the two teams?",
    "We are a small team, about eight people, spread across three time zones.",
    "Do you have any questions for me before we finish?",
]


async def build(lines: list[str], voice: str, pause_s: float) -> bytes:
    gap = bytes(int(RATE * pause_s) * 2)
    pcm = bytearray(gap)
    for line in lines:
        pcm += (await synthesize(line, voice, RATE)).pcm + gap
    return bytes(pcm)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pause", type=float, default=2.5, help="silence between lines, seconds")
    args = ap.parse_args()
    for name, lines, voice in (("fixture_es", ES, "es-PE-AlexNeural"), ("fixture_en", EN, "en-GB-SoniaNeural")):
        pcm = await build(lines, voice, args.pause)
        path = Path("logs") / f"{name}.wav"
        save_wav(path, pcm)
        print(f"{path}: {len(pcm) / 2 / RATE:.1f} s, {voice}")


if __name__ == "__main__":
    asyncio.run(main())
