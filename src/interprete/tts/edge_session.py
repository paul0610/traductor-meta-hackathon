"""Edge TTS over open WebSockets: one connection per concurrent synthesis, kept open between clauses.

`edge_tts` opens a connection per request. That handshake is most of a clause's
synthesis (1.1 s of 1.3 s), and it is what Edge rate-limits: one new connection
every 0.75 s failed 52 times out of 80 with NoAudioReceived. The same protocol
over an open socket gives first audio in 0.43 s. Measured 30/10, SPEC §8.

Speaks the same wire protocol as `edge_tts` and reuses its helpers; only the
connection's lifetime changes. Anything that goes wrong on a socket closes it,
because a turn cut halfway cannot be reused, and a fresh one opens in the
background. A clause that a socket does not answer in time races the spare socket
and a one-shot `synthesize` together, so a clause never depends on one connection:
on a bad afternoon Edge stalls a turn, closes it at 2.5 s, and may do it twice in a row.
"""

from __future__ import annotations

import asyncio
from typing import Callable

import aiohttp
from edge_tts.communicate import (
    _SSL_CTX,
    connect_id,
    date_to_string,
    escape,
    mkssml,
    remove_incompatible_characters,
    ssml_headers_plus_data,
)
from edge_tts.constants import SEC_MS_GEC_VERSION, WSS_HEADERS, WSS_URL
from edge_tts.data_classes import TTSConfig
from edge_tts.drm import DRM
from edge_tts.exceptions import NoAudioReceived

from ..clock import now
from ..hedge import hedged
from .edge import TtsResult, finish, synthesize

HEDGE_AFTER_S = 1.2             # a healthy socket answers a whole clause in 1 s at worst
RECONNECT_S = 2.0
RETRY_S = 0.05
KEEPALIVE_S = 10.0              # an idle socket dies within 60 s; a WS ping does not stop it, a resent config does
AUDIO_CONFIG = (
    "X-Timestamp:{ts}\r\nContent-Type:application/json; charset=utf-8\r\nPath:speech.config\r\n\r\n"
    '{{"context":{{"synthesis":{{"audio":{{"metadataoptions":{{"sentenceBoundaryEnabled":"false",'
    '"wordBoundaryEnabled":"false"}},"outputFormat":"audio-24khz-48kbitrate-mono-mp3"}}}}}}}}\r\n'
)


class Socket:
    """One open connection; carries one turn at a time."""

    def __init__(self, voice: str) -> None:
        self.voice = voice
        self.session: aiohttp.ClientSession | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None

    @property
    def closed(self) -> bool:
        return self.ws is None or self.ws.closed

    async def open(self) -> None:
        await self.close()
        self.session = aiohttp.ClientSession(trust_env=True)
        self.ws = await self.session.ws_connect(
            f"{WSS_URL}&ConnectionId={connect_id()}&Sec-MS-GEC={DRM.generate_sec_ms_gec()}"
            f"&Sec-MS-GEC-Version={SEC_MS_GEC_VERSION}",
            compress=15, headers=DRM.headers_with_muid(WSS_HEADERS), ssl=_SSL_CTX,
        )
        await self.ws.send_str(AUDIO_CONFIG.format(ts=date_to_string()))

    async def close(self) -> None:
        ws, session, self.ws, self.session = self.ws, self.session, None, None
        if ws is not None:
            await ws.close()
        if session is not None:
            await session.close()

    async def keep_alive(self) -> None:
        await self.ws.send_str(AUDIO_CONFIG.format(ts=date_to_string()))

    async def speak(self, text: str, rate_pct: int) -> tuple[bytes, float | None, float]:
        """One turn. Returns the MP3, when its first byte came and when the turn ended."""
        cfg = TTSConfig(self.voice, f"{rate_pct:+d}%", "+0%", "+0Hz", "SentenceBoundary")
        ssml = mkssml(cfg, escape(remove_incompatible_characters(text)))
        await self.ws.send_str(ssml_headers_plus_data(connect_id(), date_to_string(), ssml))
        mp3 = bytearray()
        t_first = None
        while True:
            msg = await self.ws.receive()
            if msg.type is aiohttp.WSMsgType.TEXT:
                if "Path:turn.end" in msg.data:
                    break
            elif msg.type is aiohttp.WSMsgType.BINARY:
                body = msg.data[2 + int.from_bytes(msg.data[:2], "big"):]
                if body:
                    t_first = t_first or now()
                    mp3 += body
            else:
                raise ConnectionError(f"edge socket: {msg.type.name}")
        if not mp3:
            raise NoAudioReceived("turn ended without audio")
        return bytes(mp3), t_first, now()


class EdgeVoice:
    def __init__(self, voice: str, out_rate: int, *, size: int = 2, emit: Callable[..., None] | None = None,
                 make_socket: Callable[[str], Socket] = Socket) -> None:
        self.voice = voice
        self.out_rate = out_rate
        self.size = size
        self.make_socket = make_socket
        self._emit = emit or (lambda ev, **f: None)
        self._free: asyncio.Queue[Socket] = asyncio.Queue()
        self._reopening: set[asyncio.Task] = set()
        self._keeper: asyncio.Task | None = None
        self._sockets: list[Socket] = []
        self.reconnects = 0

    async def start(self) -> None:
        """Opens every socket before the first clause needs one; a failure just leaves it to `_reopen`."""
        self._sockets = [self.make_socket(self.voice) for _ in range(self.size)]
        for s in self._sockets:
            self._replace(s)
        self._keeper = asyncio.create_task(self._keep_alive())

    async def close(self) -> None:
        tasks = [*self._reopening, *([self._keeper] if self._keeper is not None else [])]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*(s.close() for s in self._sockets), return_exceptions=True)

    async def _keep_alive(self) -> None:
        while True:
            await asyncio.sleep(KEEPALIVE_S)
            for _ in range(self._free.qsize()):      # only idle sockets: a turn in progress must not be touched
                sock = self._free.get_nowait()
                try:
                    await sock.keep_alive()
                except (OSError, aiohttp.ClientError, AttributeError) as exc:
                    self._emit("tts", socket_error=repr(exc)[:200])
                    self.reconnects += 1
                    self._replace(sock)
                else:
                    self._free.put_nowait(sock)

    def _replace(self, sock: Socket) -> None:
        task = asyncio.create_task(self._reopen(sock))
        self._reopening.add(task)
        task.add_done_callback(self._reopening.discard)

    async def _reopen(self, sock: Socket) -> None:
        while True:
            try:
                await sock.open()
                self._free.put_nowait(sock)
                return
            except (OSError, aiohttp.ClientError, TimeoutError) as exc:
                self._emit("tts", socket_error=repr(exc)[:200])
                await asyncio.sleep(RECONNECT_S)

    async def _over_socket(self, text: str, rate_pct: int) -> TtsResult:
        return await self._turn(await self._free.get(), text, rate_pct)

    async def _rescue(self, text: str, rate_pct: int) -> TtsResult:
        """A stalled clause: the spare socket answers in 0.3 s, a new connection in over 1 s but on its own luck."""
        def one_shot():
            return synthesize(text, self.voice, self.out_rate, rate_pct=rate_pct)

        try:
            sock = self._free.get_nowait()
        except asyncio.QueueEmpty:
            return await one_shot()
        return await hedged(lambda: self._turn(sock, text, rate_pct), one_shot, 0.0)

    async def _turn(self, sock: Socket, text: str, rate_pct: int) -> TtsResult:
        t_request = now()
        try:
            if sock.closed:                          # the server dropped it while idle
                self.reconnects += 1
                await sock.open()
            mp3, t_first, t_end = await sock.speak(text, rate_pct)
        except BaseException:                        # also cancellation: a turn cut halfway cannot be reused
            self.reconnects += 1
            self._replace(sock)
            raise
        self._free.put_nowait(sock)
        return await finish(mp3, self.out_rate, t_request, t_first, t_end)

    async def say(self, text: str, *, rate_pct: int = 0, on_hedge: Callable[[], None] | None = None) -> TtsResult:
        """Keeps trying until the caller's timeout gives up: Edge can refuse everything for seconds and come back."""
        while True:
            try:
                return await hedged(lambda: self._over_socket(text, rate_pct),
                                    lambda: self._rescue(text, rate_pct), HEDGE_AFTER_S, on_hedge)
            except Exception as exc:
                self._emit("tts", socket_error=repr(exc)[:200])
                await asyncio.sleep(RETRY_S)
