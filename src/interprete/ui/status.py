"""What the button and the metrics line show, apart from Qt so the chains can say it and the tests can check it."""

from __future__ import annotations

from enum import Enum

import numpy as np

SYNC_OK_S = 0.5                # gate S
SYNC_WARN_S = 1.0              # past this a clause counts as late
LAG_OK_S = 4.0                 # 60 s gate
LAG_WARN_S = 6.0
CABLE = {"OFF": "APAGADO", "SILENT": "EN ESPERA", "PASSTHROUGH": "VOZ CRUDA", "TTS": "VOZ", "DUB": "DOBLAJE", "MUTE": "MUDO"}


class Phase(str, Enum):
    LOADING = "CARGANDO"
    READY = "LISTO"            # nothing is captured yet: no audio leaves this PC
    STARTING = "CONECTANDO"    # the ASR and the camera are on their way; what is said meanwhile waits in the queue
    LIVE = "EN VIVO"
    PAUSED = "PAUSADO"         # they took the floor
    MUTED = "MUDO"             # F8: nothing reaches the other side
    RAW = "VOZ CRUDA"          # F9
    STOPPING = "APAGANDO"
    OFF = "APAGADO"


SOUNDS = {Phase.READY: "ready", Phase.LIVE: "live", Phase.PAUSED: "floor", Phase.MUTED: "mute", Phase.RAW: "raw",
          Phase.STOPPING: "stop"}


def sound_for(before: Phase, after: Phase) -> str | None:
    """Only a change makes a sound, and getting the floor back is not news."""
    if before is after or (before is Phase.PAUSED and after is Phase.LIVE):
        return None
    return "back" if before in (Phase.MUTED, Phase.RAW) and after is Phase.LIVE else SOUNDS.get(after)


class Health(str, Enum):
    NONE = "none"
    OK = "ok"
    WARN = "warn"
    BAD = "bad"


def health(lag_s: float | None, sync_s: float, delay_s: float) -> Health:
    """With a dub what matters is how far the voice is from the lips, otherwise how late it arrives."""
    if lag_s is None:
        return Health.NONE
    value, ok, warn = (abs(sync_s), SYNC_OK_S, SYNC_WARN_S) if delay_s > 0 else (lag_s, LAG_OK_S, LAG_WARN_S)
    return Health.OK if value <= ok else Health.WARN if value <= warn else Health.BAD


def running(*, raw: bool, muted: bool, held: bool, connected: bool) -> Phase:
    """What the other side is really getting goes first: raw voice or silence outrank anything else going on."""
    if raw:
        return Phase.RAW
    if muted:
        return Phase.MUTED
    if held:
        return Phase.PAUSED
    return Phase.LIVE if connected else Phase.STARTING


def metrics_line(*, lags: list[float], sync_s: float, delay_s: float, late: bool, corrections: int, theirs: bool,
                 cable: str, minutes: float, notes: str = "") -> str:
    lag = f"{lags[-1]:.1f}s p95 {np.percentile(lags, 95):.1f}s" if lags else "--"
    sync = ""
    if delay_s > 0:
        sync = f" │ SYNC {sync_s:+.1f}s (D {delay_s:.1f}s)"
        if late:
            sync += f" TARDE: reinicia con D {delay_s + 0.5:.1f}"
    return (f"LAG {lag}{sync} │ CORR {corrections} │ TURNO: {'ellos' if theirs else 'yo'} │ ES→EN {CABLE[cable]}{notes}"
            f" │ META {minutes:.0f} min")
