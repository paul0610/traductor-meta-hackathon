"""Muse Spark translation with structured JSON output and minimum reasoning.

OpenAI-SDK compatible endpoint (`https://api.meta.ai/v1`). `reasoning_effort`
runs none < minimal < low < medium < high; the cookbook (01_api_fundamentals/
06_reasoning_tokens) says "none" is not reliably available on the public
endpoint yet, so "minimal" is the default and "none" is tried only on request.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from openai import AsyncOpenAI

from .. import config
from ..clock import now
from ..hedge import hedged

HEDGE_AFTER_S = 2.0            # p99 of 1060 clauses is 2.3 s; a stuck call is what loses a clause at the 4 s limit
LANG = {"es": "Spanish", "en": "English"}

SYSTEM = (
    "You are a professional simultaneous interpreter in a live video call. "
    "Translate what the speaker just said from {src} into natural spoken {tgt}. "
    "The speaker may mix {src} and {tgt} (code-switching): render everything in {tgt}. "
    "Drop fillers and false starts (eh, este, o sea, um), keep the meaning, names and numbers exact. "
    "Keep the speaker's order where possible, short clauses joined by conjunctions; "
    "do not add explanations. Return only the JSON object."
)
FRAGMENT = (
    " You receive the speech clause by clause as it is said. Translate only the new fragment so it continues "
    "what you already interpreted: never repeat it, never complete the sentence, and add no final period "
    "unless the fragment ends with one. A fragment without a final period may stop in the middle of a phrase and go "
    "on in the next one, so read it as the start of something longer: \"Tengo cinco años\" may go on \"de experiencia\", "
    "so it is \"I have five years\", not an age."
)

SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "translation",
        "schema": {
            "type": "object",
            "properties": {"translation": {"type": "string"}},
            "required": ["translation"],
            "additionalProperties": False,
        },
    },
}


class EmptyTranslation(Exception):
    pass


@dataclass(slots=True)
class MtResult:
    translation: str
    t_start: float
    t_end: float
    effort: str
    completion_tokens: int | None
    reasoning_tokens: int | None


class Spark:
    def __init__(self, api_key: str, *, model: str = config.SPARK_MODEL, effort: str = "minimal",
                 timeout_s: float = 4.0) -> None:
        self.client = AsyncOpenAI(base_url=config.API_BASE, api_key=api_key, timeout=timeout_s, max_retries=0)
        self.model = model
        self.effort = effort

    async def translate(self, text: str, *, src: str = "es", tgt: str = "en", prefix: str = "",
                        history: list[tuple[str, str]] | None = None, fragment: bool = False,
                        on_hedge: Callable[[], None] | None = None) -> MtResult:
        """A failed or empty answer is asked again at once, and a slow one races a second call."""
        system = SYSTEM.format(src=LANG[src], tgt=LANG[tgt]) + (FRAGMENT if fragment else "")
        messages = [{"role": "system", "content": system}]
        for said, translated in history or []:
            messages.append({"role": "user", "content": said})
            messages.append({"role": "assistant", "content": json.dumps({"translation": translated})})
        content = text if not prefix else f"[already interpreted earlier in this sentence: {prefix}]\n{text}"
        messages.append({"role": "user", "content": content})
        return await hedged(lambda: self._call(messages), lambda: self._call(messages), HEDGE_AFTER_S, on_hedge)

    async def _call(self, messages: list[dict]) -> MtResult:
        t_start = now()
        resp = await self.client.chat.completions.create(
            model=self.model, messages=messages, response_format=SCHEMA, reasoning_effort=self.effort,
        )
        t_end = now()
        out = json.loads(resp.choices[0].message.content)["translation"].strip()
        if not out:
            raise EmptyTranslation
        usage = resp.usage
        details = getattr(usage, "completion_tokens_details", None) if usage else None
        return MtResult(out, t_start, t_end, self.effort,
                        usage.completion_tokens if usage else None,
                        getattr(details, "reasoning_tokens", None) if details else None)
