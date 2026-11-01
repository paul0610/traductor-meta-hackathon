import asyncio

import pytest

pytest.importorskip("edge_tts")

from interprete.tts import edge_session  # noqa: E402
from interprete.tts.edge_session import EdgeVoice  # noqa: E402


class FakeSocket:
    """Stands in for one WebSocket; `script` decides what each turn does."""

    made: list["FakeSocket"] = []
    script: list = []

    def __init__(self, voice):
        self.voice = voice
        self.opens = 0
        self.closed = True
        self.turns = []
        self.kept = 0
        self.dies_on_keep_alive = False
        FakeSocket.made.append(self)

    async def open(self):
        self.opens += 1
        self.closed = False

    async def close(self):
        self.closed = True

    async def keep_alive(self):
        self.kept += 1
        if self.dies_on_keep_alive:
            self.dies_on_keep_alive = False
            raise ConnectionResetError("gone")

    async def speak(self, text, rate_pct):
        self.turns.append((text, rate_pct))
        step = FakeSocket.script.pop(0) if FakeSocket.script else None
        if isinstance(step, Exception):
            raise step
        if isinstance(step, float):
            await asyncio.sleep(step)
        return b"mp3", 1.0, 2.0


async def fake_finish(mp3, rate, t_request, t_first, t_end):
    return "over the socket"


async def fake_oneshot(text, voice, rate, rate_pct=0):
    return "one shot"


def run(coro, monkeypatch, script=()):
    FakeSocket.made, FakeSocket.script = [], list(script)
    monkeypatch.setattr(edge_session, "finish", fake_finish)
    monkeypatch.setattr(edge_session, "synthesize", fake_oneshot)
    monkeypatch.setattr(edge_session, "HEDGE_AFTER_S", 0.02)
    return asyncio.run(coro())


def test_sockets_open_before_the_first_clause_and_are_reused(monkeypatch):
    async def main():
        v = EdgeVoice("v", 48000, size=2, make_socket=FakeSocket)
        await v.start()
        await asyncio.sleep(0.001)
        for i in range(5):
            assert await v.say(f"clause {i}") == "over the socket"
        await v.close()
        return v

    v = run(main, monkeypatch)
    assert [s.opens for s in FakeSocket.made] == [1, 1]
    assert sum(len(s.turns) for s in FakeSocket.made) == 5
    assert v.reconnects == 0


def test_idle_sockets_are_kept_alive_and_a_dead_one_is_replaced(monkeypatch):
    async def main():
        monkeypatch.setattr(edge_session, "KEEPALIVE_S", 0.005)
        v = EdgeVoice("v", 48000, size=1, make_socket=FakeSocket)
        await v.start()
        await asyncio.sleep(0.03)
        assert FakeSocket.made[0].kept >= 2
        FakeSocket.made[0].dies_on_keep_alive = True
        await asyncio.sleep(0.02)
        assert await v.say("after the silence") == "over the socket"
        await v.close()
        return v

    v = run(main, monkeypatch)
    assert FakeSocket.made[0].opens == 2
    assert v.reconnects == 1


def test_rate_reaches_the_socket(monkeypatch):
    async def main():
        v = EdgeVoice("v", 48000, size=1, make_socket=FakeSocket)
        await v.start()
        await v.say("hi", rate_pct=25)
        await v.close()

    run(main, monkeypatch)
    assert FakeSocket.made[0].turns == [("hi", 25)]


def test_a_failed_turn_is_rescued_and_the_socket_is_replaced(monkeypatch):
    async def main():
        v = EdgeVoice("v", 48000, size=1, make_socket=FakeSocket)
        await v.start()
        assert await v.say("one") in ("one shot", "over the socket")
        await asyncio.sleep(0.01)
        assert await v.say("two") == "over the socket"
        await v.close()

    run(main, monkeypatch, [ConnectionError("gone")])
    assert FakeSocket.made[0].opens == 2


def test_the_one_shot_saves_a_clause_when_the_spare_fails_too(monkeypatch):
    async def main():
        v = EdgeVoice("v", 48000, size=2, make_socket=FakeSocket)
        await v.start()
        await asyncio.sleep(0.001)
        assert await v.say("twice unlucky") == "one shot"
        await asyncio.sleep(0.01)
        await v.close()

    run(main, monkeypatch, [ConnectionError("closed"), ConnectionError("closed")])
    assert sum(s.opens for s in FakeSocket.made) == 4    # both replaced


def test_a_stalled_socket_loses_the_race_and_is_not_reused(monkeypatch):
    async def main():
        v = EdgeVoice("v", 48000, size=1, make_socket=FakeSocket)
        await v.start()
        assert await v.say("slow") == "one shot"
        await asyncio.sleep(0.01)
        await v.close()

    run(main, monkeypatch, [5.0])
    assert FakeSocket.made[0].opens == 2


def test_a_clause_keeps_trying_while_edge_refuses_everything(monkeypatch):
    async def no(*a, **k):
        raise ConnectionError("closed")

    async def main():
        monkeypatch.setattr(edge_session, "synthesize", no)
        monkeypatch.setattr(edge_session, "RETRY_S", 0.001)
        v = EdgeVoice("v", 48000, size=2, make_socket=FakeSocket)
        await v.start()
        await asyncio.sleep(0.001)
        assert await v.say("persistent") == "over the socket"
        await v.close()

    run(main, monkeypatch, [ConnectionError("closed")] * 5)


def test_a_stalled_turn_goes_to_the_spare_socket_before_any_new_connection(monkeypatch):
    async def main():
        v = EdgeVoice("v", 48000, size=2, make_socket=FakeSocket)
        await v.start()
        await asyncio.sleep(0.001)
        assert await v.say("slow") == "over the socket"
        await asyncio.sleep(0.01)
        await v.close()

    run(main, monkeypatch, [5.0])
    assert sum(len(s.turns) for s in FakeSocket.made) == 2
    assert sum(s.opens for s in FakeSocket.made) == 3    # the stalled one is replaced


def test_a_socket_dropped_while_idle_reopens_on_use(monkeypatch):
    async def main():
        v = EdgeVoice("v", 48000, size=1, make_socket=FakeSocket)
        await v.start()
        await asyncio.sleep(0.001)
        FakeSocket.made[0].closed = True
        assert await v.say("after a silence") == "over the socket"
        await v.close()
        return v

    v = run(main, monkeypatch)
    assert v.reconnects == 1
