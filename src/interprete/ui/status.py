"""What the button shows, apart from Qt so the chains can say it and the tests can check it."""

from __future__ import annotations

from enum import Enum

SYNC_OK_S = 0.5                # gate S
SYNC_WARN_S = 1.0              # past this a clause counts as late
LAG_OK_S = 4.0                 # 60 s gate
LAG_WARN_S = 6.0


class Phase(str, Enum):
    LOADING = "CARGANDO"
    READY = "LISTO"            # nothing is captured yet: no audio leaves this PC
    LIVE = "EN VIVO"
    PAUSED = "PAUSADO"         # they took the floor
    RAW = "VOZ CRUDA"          # F9
    OFF = "APAGADO"


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
