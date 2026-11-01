"""Which voice speaks: MMS by default, and nothing behind it, so a default run only ever calls Meta models.

`RTT_TTS_ENGINE=pocket` brings back the local Pocket voice, with Edge behind it if `RTT_TTS_BACKUP=edge`.
Neither is imported, let alone called, unless asked for; `uv sync --extra pocket` installs them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from ..config import Settings
from ..hedge import hedged
from .mms import MmsVoice
from .shaping import TtsResult

if TYPE_CHECKING:
    from .edge_session import EdgeVoice
    from .own_voice import Store
    from .pocket import PocketVoice

BACKUP_AFTER_S = 3.0           # the worst local clause is 1.7 s, and one may wait behind another


class WithBackup:
    """Edge takes a clause the local voice fails or holds too long, and every clause if the model does not load."""

    def __init__(self, primary: PocketVoice, backup: EdgeVoice, *, emit: Callable[..., None] | None = None) -> None:
        self.primary = primary
        self.backup = backup
        self.down = False
        self._emit = emit or (lambda ev, **f: None)

    @property
    def name(self) -> str:
        return self.primary.name

    @property
    def reconnects(self) -> int:
        return self.backup.reconnects

    @property
    def warning(self) -> str:
        return " │ VOZ DE RESPALDO" if self.down else self.primary.warning

    async def start(self) -> None:
        await self.backup.start()
        try:
            await self.primary.start()
        except Exception as exc:
            self.down = True
            self._emit("tts", primary_error=repr(exc)[:300])

    async def close(self) -> None:
        await self.primary.close()
        await self.backup.close()

    def rate_for(self, text: str, src_s: float | None) -> int:
        return self.primary.rate_for(text, src_s)

    async def say(self, text: str, *, rate_pct: int = 0, on_hedge: Callable[[], None] | None = None) -> TtsResult:
        if self.down:
            return await self.backup.say(text, rate_pct=rate_pct, on_hedge=on_hedge)
        return await hedged(lambda: self.primary.say(text, rate_pct=rate_pct),
                            lambda: self.backup.say(text, rate_pct=rate_pct), BACKUP_AFTER_S, on_hedge)


def local_voice(voice) -> PocketVoice | None:
    """The one that can take a clone, once it loaded. MMS has one speaker and cannot."""
    local = voice.primary if isinstance(voice, WithBackup) else voice
    return local if hasattr(local, "clone") and local.tts is not None else None


def make_voice(s: Settings, out_rate: int, *, size: int, emit: Callable[..., None] | None = None,
               store: Store | None = None):
    if s.tts_engine == "mms":
        return MmsVoice(s.mms_model, out_rate, speaking_rate=s.mms_speaking_rate)
    from .edge_session import EdgeVoice
    from .own_voice import Store
    from .pocket import PocketVoice

    edge = EdgeVoice(s.tts_voice, out_rate, size=size, emit=emit)
    if s.tts_engine == "edge":
        return edge
    store = store or Store()
    saved = store.read(s.pocket_model)
    own = saved is not None and saved.active
    pocket = PocketVoice(s.pocket_model, str(store.state) if own else s.pocket_voice, out_rate, gain_db=s.pocket_gain_db,
                         pace=saved.pace if own else None, own=own)
    return WithBackup(pocket, edge, emit=emit) if s.tts_backup == "edge" else pocket
