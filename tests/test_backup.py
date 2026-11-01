import asyncio
import dataclasses

import pytest

from interprete import config
from interprete.tts import backup
from interprete.tts.backup import WithBackup, make_voice
from interprete.tts.edge_session import EdgeVoice
from interprete.tts.pocket import PocketVoice


class FakeVoice:
    name = "fake"
    reconnects = 3
    warning = ""

    def __init__(self, answer, *, wait=0.0, fails=False, load_fails=False):
        self.answer = answer
        self.wait = wait
        self.fails = fails
        self.load_fails = load_fails
        self.started = 0
        self.closed = 0
        self.said = []

    async def start(self):
        self.started += 1
        if self.load_fails:
            raise OSError("no weights")

    async def close(self):
        self.closed += 1

    def rate_for(self, text, src_s):
        return 25

    async def say(self, text, *, rate_pct=0, on_hedge=None):
        self.said.append((text, rate_pct))
        await asyncio.sleep(self.wait)
        if self.fails:
            raise RuntimeError("no")
        return self.answer


def test_the_local_voice_speaks_and_the_backup_stays_quiet():
    local, edge = FakeVoice("local"), FakeVoice("edge")
    assert asyncio.run(WithBackup(local, edge).say("hello", rate_pct=25)) == "local"
    assert local.said == [("hello", 25)]
    assert edge.said == []


def test_a_failure_goes_to_the_backup_at_once():
    local, edge = FakeVoice("local", fails=True), FakeVoice("edge")
    assert asyncio.run(WithBackup(local, edge).say("hello", rate_pct=25)) == "edge"
    assert edge.said == [("hello", 25)]


def test_a_slow_clause_races_the_backup(monkeypatch):
    monkeypatch.setattr(backup, "BACKUP_AFTER_S", 0.01)
    hedges = []
    local, edge = FakeVoice("local", wait=1.0), FakeVoice("edge")
    r = asyncio.run(WithBackup(local, edge).say("hello", on_hedge=lambda: hedges.append(1)))
    assert r == "edge"
    assert hedges == [1]


def test_a_model_that_does_not_load_leaves_the_backup_in_charge():
    local, edge = FakeVoice("local", load_fails=True), FakeVoice("edge")
    events = []
    voice = WithBackup(local, edge, emit=lambda ev, **f: events.append((ev, f)))

    async def go():
        await voice.start()
        return await voice.say("hello")

    assert asyncio.run(go()) == "edge"
    assert local.said == []
    assert "RESPALDO" in voice.warning
    assert "primary_error" in events[0][1]


def test_start_and_close_reach_both_voices():
    local, edge = FakeVoice("local"), FakeVoice("edge")
    voice = WithBackup(local, edge)

    async def go():
        await voice.start()
        await voice.close()

    asyncio.run(go())
    assert (local.started, edge.started, local.closed, edge.closed) == (1, 1, 1, 1)
    assert voice.warning == ""


def test_the_pace_is_the_local_voice_s():
    assert WithBackup(FakeVoice("local"), FakeVoice("edge")).rate_for("hello", 1.0) == 25


@pytest.mark.parametrize("engine, spare, kind", [("pocket", "edge", WithBackup), ("pocket", "none", PocketVoice),
                                                 ("edge", "edge", EdgeVoice), ("edge", "none", EdgeVoice)])
def test_the_settings_pick_the_voice(engine, spare, kind):
    s = dataclasses.replace(config.load(), tts_engine=engine, tts_backup=spare)
    assert isinstance(make_voice(s, 48000, size=3), kind)
