import asyncio
import json

from interprete.asr import meta_realtime
from interprete.asr.meta_realtime import QUEUE_BLOCKS, MetaRealtimeAsr
from interprete.audio.capture import QueueFeeder
from interprete.contracts import Channel

BLOCK = bytes(640)              # 20 ms at 16 kHz


class Socket:
    """Takes all it is given, like the server under its 5 s limit, and stays quiet until released."""

    def __init__(self):
        self.audio = 0
        self.release = asyncio.Event()

    async def send(self, data):
        self.audio += isinstance(data, bytes)

    async def recv(self):
        return json.dumps({"sessionId": "S1"})

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self.release.wait()
        raise StopAsyncIteration

    async def close(self):
        pass


def test_what_piled_up_before_the_session_leaves_at_once(monkeypatch):
    """Whatever stays in the queue here stays for the whole call, and every clause arrives that much later."""
    async def go():
        q = asyncio.Queue(maxsize=QUEUE_BLOCKS)
        feeder = QueueFeeder(asyncio.get_running_loop(), q)
        for i in range(400):                     # the microphone opened 8 s before the session
            feeder._offer((i * 0.02, BLOCK))
        ws = Socket()

        async def connect(*args, **kwargs):
            return ws

        monkeypatch.setattr(meta_realtime.websockets, "connect", connect)
        stop = asyncio.Event()
        task = asyncio.create_task(MetaRealtimeAsr(Channel.OUT, "key")._session(q, lambda ev: None, stop))
        await asyncio.sleep(0.05)
        left = q.qsize()
        stop.set()
        ws.release.set()
        await task
        return left, ws.audio

    left, sent = asyncio.run(go())
    assert left == 0
    assert sent == QUEUE_BLOCKS
