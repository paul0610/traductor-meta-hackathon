"""What the button and the metrics line show, apart from Qt so the chains can say it and the tests can check it.

The words are English; `texts.t` gives them in Spanish with RTT_UI_LANG=es."""

from __future__ import annotations

from enum import Enum

import numpy as np

from .texts import t

SYNC_OK_S = 0.5                # gate S
SYNC_WARN_S = 1.0              # past this a clause counts as late
LAG_OK_S = 4.0                 # 60 s gate
LAG_WARN_S = 6.0
CABLE = {"OFF": "OFF", "SILENT": "STANDBY", "PASSTHROUGH": "RAW VOICE", "TTS": "VOICE", "DUB": "DUB", "MUTE": "MUTED"}


class Phase(str, Enum):
    LOADING = "LOADING"
    READY = "READY"            # nothing is captured yet: no audio leaves this PC
    STARTING = "CONNECTING"    # the ASR and the camera are on their way; what is said meanwhile waits in the queue
    LIVE = "LIVE"
    LISTENING = "MIC ON HOLD"  # on speakers the PC is playing, so the mic waits (`audio/room.py`)
    PAUSED = "PAUSED"          # they took the floor
    MUTED = "MUTED"            # F8: nothing reaches the other side
    RAW = "RAW VOICE"          # F9
    STOPPING = "STOPPING"
    OFF = "OFF"


SOUNDS = {Phase.READY: "ready", Phase.LIVE: "live", Phase.PAUSED: "floor", Phase.MUTED: "mute", Phase.RAW: "raw",
          Phase.STOPPING: "stop"}


def sound_for(before: Phase, after: Phase) -> str | None:
    """Only a change makes a sound, and getting the floor back is not news. Nor is the mic waiting: on speakers
    it comes and goes with every sentence, and a cue there would be one more sound for it to wait out."""
    before, after = (Phase.LIVE if p is Phase.LISTENING else p for p in (before, after))
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


def running(*, raw: bool, muted: bool, held: bool, connected: bool, waiting: bool = False) -> Phase:
    """What the other side is really getting goes first: raw voice or silence outrank anything else going on."""
    if raw:
        return Phase.RAW
    if muted:
        return Phase.MUTED
    if held:
        return Phase.PAUSED
    if not connected:
        return Phase.STARTING
    return Phase.LISTENING if waiting else Phase.LIVE


def metrics_line(*, lags: list[float], sync_s: float, delay_s: float, late: bool, corrections: int, theirs: bool,
                 cable: str, minutes: float, notes: str = "") -> str:
    lag = f"{lags[-1]:.1f}s p95 {np.percentile(lags, 95):.1f}s" if lags else "--"
    sync = ""
    if delay_s > 0:
        sync = f" │ SYNC {sync_s:+.1f}s (D {delay_s:.1f}s)"
        if late:
            sync += " " + t("LATE: restart with D {d:.1f}").format(d=delay_s + 0.5)
    return (f"LAG {lag}{sync} │ CORR {corrections} │ {t('TURN')}: {t('them') if theirs else t('me')} │ ES→EN "
            f"{t(CABLE[cable])}{notes} │ META {minutes:.0f} min")
