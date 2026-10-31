import asyncio
import json
from types import SimpleNamespace

from interprete.mt.context import Conversation, load_glossary
from interprete.mt.spark import Spark


def test_clauses_of_one_speaker_join_a_line():
    c = Conversation()
    c.add("Me", "I have five years")
    c.add("Me", "of experience")
    c.add("Them", "That sounds great.")
    assert c.text() == "Me: I have five years of experience\nThem: That sounds great."


def test_the_oldest_lines_leave_first():
    c = Conversation(max_chars=40)
    c.add("Me", "a" * 20)
    c.add("Them", "b" * 10)
    c.add("Me", "c" * 15)
    assert "a" not in c.text()
    assert len(c.text()) <= 40


def test_the_last_line_stays_even_if_it_is_too_long():
    c = Conversation(max_chars=10)
    c.add("Me", "x" * 50)
    assert c.text() == "Me: " + "x" * 50


def test_the_count_stays_true_after_lines_join_and_leave():
    c = Conversation(max_chars=100)
    for i in range(200):
        c.add("Me" if i % 3 else "Them", "word " * 3)
    assert len(c.text()) <= 100


def test_a_missing_glossary_is_empty(tmp_path):
    assert load_glossary(tmp_path / "nothing.txt") == {}


def test_glossary_lines(tmp_path):
    path = tmp_path / "glossary.txt"
    path.write_text("# my terms\ntablero = dashboard\n\nRimac   # a name\n", encoding="utf-8")
    assert load_glossary(path) == {"tablero": "dashboard", "Rimac": "Rimac"}


class Fake:
    def __init__(self):
        self.messages = None

    async def create(self, **kw):
        self.messages = kw["messages"]
        message = SimpleNamespace(content=json.dumps({"translation": "ok"}))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


def spark_with(glossary=None):
    spark = Spark("key", glossary=glossary)
    fake = Fake()
    spark.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return spark, fake


def test_the_conversation_reaches_spark():
    spark, fake = spark_with()
    asyncio.run(spark.translate("Sí", context="Them: Do you know SQL?"))
    assert "Them: Do you know SQL?" in fake.messages[0]["content"]


def test_no_conversation_adds_nothing():
    spark, fake = spark_with()
    asyncio.run(spark.translate("Sí"))
    assert "Conversation so far" not in fake.messages[0]["content"]


def test_the_glossary_reaches_spark():
    spark, fake = spark_with({"tablero": "dashboard"})
    asyncio.run(spark.translate("el tablero"))
    assert '"tablero" as "dashboard"' in fake.messages[0]["content"]


def test_the_glossary_goes_both_ways():
    spark, fake = spark_with({"tablero": "dashboard"})
    asyncio.run(spark.translate("the dashboard", src="en", tgt="es"))
    assert '"dashboard" as "tablero"' in fake.messages[0]["content"]
