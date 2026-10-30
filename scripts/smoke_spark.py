"""Smoke test: 10 ES→EN translations with Muse Spark, structured JSON, minimum reasoning; p50/p95.

The first call (connection setup) is reported apart from the warm ones.

    uv run python scripts/smoke_spark.py [--effort minimal] [--also-none]
"""

from __future__ import annotations

import argparse
import asyncio

import numpy as np
import openai

from interprete import config
from interprete.metrics.jsonl import Metrics
from interprete.mt.spark import Spark

SENTENCES = [
    "Buenos días, gracias por la oportunidad de conversar hoy.",
    "Tengo cinco años de experiencia construyendo pipelines de datos.",
    "Eh, o sea, en mi último trabajo lideré un equipo de cuatro personas.",
    "Lo más difícil fue coordinar con tres equipos en distintos husos horarios.",
    "Reducimos la latencia del sistema en un cuarenta por ciento.",
    "¿Podrías repetir la última parte de la pregunta, por favor?",
    "Me interesa este rol porque quiero trabajar en productos de inteligencia artificial.",
    "Usamos Python, FastAPI y Postgres, y desplegamos en AWS con Docker.",
    "Este, mi fortaleza es que aprendo rápido y me adapto al feedback.",
    "Yo creo que el deadline es tight, pero podemos entregar el MVP el viernes.",
]


async def run(effort: str, metrics: Metrics) -> dict:
    spark = Spark(config.require_api_key(), effort=effort, timeout_s=15.0)
    lat, rtoks = [], []
    for i, s in enumerate(SENTENCES):
        r = await spark.translate(s)
        dt = r.t_end - r.t_start
        lat.append(dt)
        rtoks.append(r.reasoning_tokens)
        metrics.emit("mt", effort=effort, i=i, text_src=s, text_tgt=r.translation, t_mt=dt,
                     completion_tokens=r.completion_tokens, reasoning_tokens=r.reasoning_tokens)
        print(f"  [{effort}] {dt:.3f}s  rt={r.reasoning_tokens}  {s}  ->  {r.translation}")
    warm = np.array(lat[1:])
    return dict(effort=effort, cold_s=round(lat[0], 3), warm_p50_s=round(float(np.median(warm)), 3),
                warm_p95_s=round(float(np.percentile(warm, 95)), 3), all_p50_s=round(float(np.median(lat)), 3),
                all_p95_s=round(float(np.percentile(lat, 95)), 3), reasoning_tokens=rtoks)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--effort", default="minimal")
    ap.add_argument("--also-none", action="store_true", help='A/B against reasoning_effort="none"')
    args = ap.parse_args()
    metrics = Metrics(run_name="smoke_spark")
    summaries = [await run(args.effort, metrics)]
    if args.also_none:
        try:
            summaries.append(await run("none", metrics))
        except openai.APIError as exc:
            print(f'  reasoning_effort="none" rejected: {exc}')
            summaries.append({"effort": "none", "error": str(exc)[:200]})
    for s in summaries:
        metrics.emit("run", summary=s)
        print(s)
    metrics.close()


if __name__ == "__main__":
    asyncio.run(main())
