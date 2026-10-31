"""Muse Voice Transcribe realtime client (`wss://api.meta.ai/v1/asr/realtime`).

Protocol (docs + official cookbook 06_muse_voice, read 2026-10-29): first text
frame is the handshake; the ack is a bare `{"sessionId"}`; then binary PCM16
mono frames up; JSON events down, each with an integer `audioProcessedMs`
(processing progress, not word timestamps). End with `{"type":"endStream"}` and
read until close 1000. The server closes with 1008 if audio runs more than 5 s
ahead or below real time for 10 s, and with 1011 at 60 min.

Raw JSON never leaves this module: events come out as `AsrEvent`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable

import websockets

from .. import config
from ..clock import StreamClock, now
from ..contracts import AsrEvent, AsrKind, Channel

SAMPLE_RATES = {"PCM_16KHZ": 16000, "PCM_24KHZ": 24000}
MAX_AHEAD_S = 4.0                # stay clearly under the server's 5 s backlog limit
QUEUE_BLOCKS = 150               # 3 s at 20 ms, under MAX_AHEAD_S: audio the sender holds back never catches up (measured 1.18 s for good)
ROTATE_AFTER_S = 55 * 60         # sessions are capped at 60 min; rotate before
_KINDS = {
    "speechStart": AsrKind.SPEECH_START, "speechEnd": AsrKind.SPEECH_END,
    "speechComplete": AsrKind.SPEECH_COMPLETE, "speaker": AsrKind.SPEAKER,
    "audioProgress": AsrKind.PROGRESS, "error": AsrKind.ERROR,
}


class AsrError(RuntimeError):
    pass


class MetaRealtimeAsr:
    def __init__(self, channel: Channel, api_key: str, *, mode: str = "ENDPOINTING",
                 encoding: str = "PCM_16KHZ", language_bias: list[str] | None = None,
                 keywords: list[str] | None = None, emit_progress: bool = True,
                 emit: Callable[..., None] | None = None, url: str = config.ASR_URL) -> None:
        self.channel = channel
        self.api_key = api_key
        self.mode = mode
        self.encoding = encoding
        self.rate = SAMPLE_RATES[encoding]
        self.language_bias = language_bias
        self.keywords = keywords
        self.emit_progress = emit_progress
        self.url = url
        self._emit = emit or (lambda ev, **f: None)
        self.clock = StreamClock()
        self.token_form: str | None = None      # "bearer" or "bare": which accessToken form the server took
        self._turn: str | None = None
        self.audio_sent_s = 0.0
        self.t_down: float | None = None        # when the link dropped; None while up and before the first session

    def _handshake(self, bearer: bool) -> str:
        h = {
            "mode": self.mode,
            "authorization": {"accessToken": f"Bearer {self.api_key}" if bearer else self.api_key},
            "audioEncoding": self.encoding,
            "model": config.ASR_MODEL,
            "partialMode": "CUMULATIVE",
            "emitAudioProgress": self.emit_progress,
        }
        if self.keywords:
            h["keywords"] = self.keywords
        if self.language_bias:
            h["languageBias"] = self.language_bias
        return json.dumps(h)

    async def _connect(self):
        """Open a session. Docs show `Bearer <key>`, the cookbook sends the bare key: try both."""
        forms = [True, False] if self.token_form != "bare" else [False]
        last = None
        for bearer in forms:
            ws = await websockets.connect(self.url, max_size=None)
            await ws.send(self._handshake(bearer))
            try:
                ack = json.loads(await ws.recv())
            except websockets.exceptions.ConnectionClosed as exc:
                last = f"closed {exc.code} {exc.reason}"
                continue
            if "sessionId" in ack and "type" not in ack:
                self.token_form = "bearer" if bearer else "bare"
                return ws, ack["sessionId"]
            last = ack.get("message", str(ack))
            await ws.close()
        raise AsrError(f"handshake rejected: {last}")

    def _normalize(self, frame: dict, t_recv: float, session_id: str) -> AsrEvent:
        typ = frame.get("type")
        ms = frame.get("audioProcessedMs")
        t_asr_s = ms / 1000.0 if isinstance(ms, (int, float)) else None
        if typ == "transcript":
            kind = AsrKind.FINAL if frame.get("final") else AsrKind.PARTIAL
        else:
            kind = _KINDS.get(typ, AsrKind.ERROR)
        turn = frame.get("turnId")
        if typ == "speechStart":
            self._turn = None if turn is None else str(turn)
        turn_id = str(turn) if turn is not None else self._turn   # transcripts belong to the latest speechStart
        return AsrEvent(
            channel=self.channel, kind=kind, t_recv=t_recv, session_id=session_id,
            turn_id=turn_id, text=frame.get("transcript", "") or "", t_asr_s=t_asr_s,
            speaker=frame.get("label"), raw=frame,
        )

    async def run(self, pcm_q: asyncio.Queue, on_event: Callable[[AsrEvent], None],
                  stop: asyncio.Event | None = None) -> None:
        """Stream `(t_capture, pcm16_bytes)` items from `pcm_q`; call `on_event` for every event.

        Reconnects with backoff on errors and rotates the session before the 60 min cap.
        """
        stop = stop or asyncio.Event()
        backoff = 0.5
        while not stop.is_set():
            try:
                await self._session(pcm_q, on_event, stop)
                backoff = 0.5
            except (OSError, websockets.exceptions.WebSocketException, AsrError) as exc:
                self.t_down = self.t_down or now()
                self._emit("asr", channel=self.channel, kind="reconnect", error=str(exc)[:300], backoff_s=backoff)
                if isinstance(exc, AsrError) and "handshake" in str(exc):
                    raise
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 8.0)

    async def _session(self, pcm_q: asyncio.Queue, on_event, stop: asyncio.Event) -> None:
        ws, session_id = await self._connect()
        self.clock.start_session(session_id)
        self._turn = None
        self.t_down = None
        t_open = now()
        self._emit("asr", channel=self.channel, kind="session", session_id=session_id,
                   token_form=self.token_form, mode=self.mode, encoding=self.encoding)
        on_event(AsrEvent(self.channel, AsrKind.PROGRESS, t_open, session_id, raw={"type": "session"}))

        async def sender() -> None:
            bytes_per_s = self.rate * 2
            sent_s = 0.0
            t_first_send = None
            while not stop.is_set() and now() - t_open < ROTATE_AFTER_S:
                try:
                    t_capture, pcm = await asyncio.wait_for(pcm_q.get(), 0.5)
                except TimeoutError:
                    if t_first_send is not None:           # a stalled source must not fall below real time (1008)
                        await ws.send(bytes(bytes_per_s // 2))
                        sent_s += 0.5
                    continue
                if t_first_send is None:
                    t_first_send = now()
                    self.clock.mark_first_block(t_capture)
                await ws.send(pcm)
                sent_s += len(pcm) / bytes_per_s
                self.audio_sent_s = sent_s
                ahead = sent_s - (now() - t_first_send)
                if ahead > MAX_AHEAD_S:                    # file/fixture input: never run > 5 s ahead
                    await asyncio.sleep(ahead - MAX_AHEAD_S)
            await ws.send(json.dumps({"type": "endStream"}))

        send_task = asyncio.create_task(sender())
        try:
            async for message in ws:
                t_recv = now()
                if isinstance(message, bytes):
                    continue
                frame = json.loads(message)
                ev = self._normalize(frame, t_recv, session_id)
                if ev.kind is not AsrKind.PROGRESS:
                    self._emit("asr", channel=self.channel, kind=ev.kind.value, turn_id=ev.turn_id,
                               t_recv=t_recv, t_asr_s=ev.t_asr_s, lag_s=self.clock.lag(t_recv, ev.t_asr_s),
                               text=ev.text if ev.kind in (AsrKind.FINAL, AsrKind.SPEECH_COMPLETE, AsrKind.PARTIAL) else None,
                               session_id=session_id, error=frame.get("message"))
                on_event(ev)
                if ev.kind is AsrKind.ERROR:
                    raise AsrError(frame.get("message", "asr error"))
        except websockets.exceptions.ConnectionClosed as exc:
            if exc.code != 1000:
                raise AsrError(f"closed {exc.code} {exc.reason}") from exc
        finally:
            send_task.cancel()
            await asyncio.gather(send_task, return_exceptions=True)
            await ws.close()
