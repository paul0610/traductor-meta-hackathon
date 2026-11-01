import asyncio
import json
from types import SimpleNamespace

import pytest

from interprete.mt import spark as spark_module
from interprete.mt.spark import EmptyTranslation, Spark


class FakeCompletions:
    """Each call takes the next (seconds, text) of `script`; an exception in place of text is raised."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.systems = []

    async def create(self, **kw):
        wait, text = self.script.pop(0)
        self.calls += 1
        self.systems.append(kw["messages"][0]["content"])
        await asyncio.sleep(wait)
        if isinstance(text, Exception):
            raise text
        message = SimpleNamespace(content=json.dumps({"translation": text}))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def with_script(script):
    spark = Spark("key")
    fake = FakeCompletions(script)
    spark.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return spark, fake


def test_spanglish_rules_go_only_from_spanish():
    spark, fake = with_script([(0.0, "Good morning"), (0.0, "Buenos días")])
    asyncio.run(spark.translate("Buenos días"))
    asyncio.run(spark.translate("Good morning", src="en", tgt="es"))
    assert "cómo se dice" in fake.systems[0]
    assert "cómo se dice" not in fake.systems[1]


def test_only_english_is_asked_to_write_numbers_in_words():
    spark, fake = with_script([(0.0, "Good morning"), (0.0, "Buenos días")])
    asyncio.run(spark.translate("Buenos días"))
    asyncio.run(spark.translate("Good morning", src="en", tgt="es"))
    assert "forty percent" in fake.systems[0]
    assert "forty percent" not in fake.systems[1]


def test_a_clean_answer_takes_one_call():
    spark, fake = with_script([(0.0, "Good morning")])
    assert asyncio.run(spark.translate("Buenos días")).translation == "Good morning"
    assert fake.calls == 1


def test_an_empty_answer_is_asked_again():
    spark, fake = with_script([(0.0, "  "), (0.0, "that they already have")])
    assert asyncio.run(spark.translate("que ya tienen.")).translation == "that they already have"
    assert fake.calls == 2


def test_two_empty_answers_are_an_error():
    spark, _ = with_script([(0.0, ""), (0.0, "")])
    with pytest.raises(EmptyTranslation):
        asyncio.run(spark.translate("eh"))


def test_a_failed_call_is_asked_again_at_once():
    spark, fake = with_script([(0.0, ConnectionError("gone")), (0.0, "three teams")])
    assert asyncio.run(spark.translate("a tres equipos")).translation == "three teams"
    assert fake.calls == 2


def test_a_stuck_call_races_a_second_one(monkeypatch):
    monkeypatch.setattr(spark_module, "HEDGE_AFTER_S", 0.01)
    hedges = []
    spark, fake = with_script([(5.0, "too late"), (0.0, "three teams")])
    r = asyncio.run(spark.translate("a tres equipos", on_hedge=lambda: hedges.append(1)))
    assert r.translation == "three teams"
    assert hedges == [1]
    assert fake.calls == 2
