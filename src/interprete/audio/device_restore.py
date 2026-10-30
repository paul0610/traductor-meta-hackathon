"""Windows audio endpoints through Core Audio (pycaw), never SoundVolumeView.

F10 puts the physical mic back as the default capture device. The name is the
one the capture actually opened, so a laptop's jack mic with the same vendor in
its name is never picked. Roles are all three (console, multimedia,
communications): call apps listen to the last one.

`endpoint_volume` only reads: a low Windows slider on `CABLE Input` scales the
whole translated voice before the call app sees it, and no log would show it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

import comtypes
from pycaw.constants import DEVICE_STATE, EDataFlow, ERole
from pycaw.utils import AudioUtilities

from ..clock import now

ROLES = (ERole.eConsole, ERole.eMultimedia, ERole.eCommunications)
TIMEOUT_S = 1.0


@dataclass(frozen=True, slots=True)
class Restore:
    ok: bool
    changed: bool
    elapsed_s: float
    detail: str


def _on_com_thread(fn: Callable[[], Any]) -> Any:
    """COM is per thread, and a stuck endpoint call must not stall the caller. Returns the exception on failure."""
    box: list[Any] = []

    def work() -> None:
        comtypes.CoInitialize()         # never uninitialized here, the thread just ends
        try:
            box.append(fn())
        except (OSError, comtypes.COMError) as exc:
            box.append(exc)

    t = threading.Thread(target=work, name="core-audio", daemon=True)
    t.start()
    t.join(TIMEOUT_S)
    return box[0] if box else TimeoutError(f"no answer in {TIMEOUT_S} s")


def _set_default(name: str, force: bool, only_from: str | None) -> tuple[bool, bool, str]:
    active = AudioUtilities.GetAllDevices(EDataFlow.eCapture.value, DEVICE_STATE.ACTIVE.value)
    found = [d for d in active if (d.FriendlyName or "").startswith(name)]      # MME names arrive cut at 31 characters
    if not found:
        return False, False, f"no active capture endpoint named {name!r}"
    target = found[0]
    enum = AudioUtilities.GetDeviceEnumerator()
    defaults = [enum.GetDefaultAudioEndpoint(EDataFlow.eCapture.value, r.value).GetId() for r in ROLES]
    if only_from is not None:
        names = {d.id: d.FriendlyName or "" for d in active}
        if not any(only_from in names.get(i, "") for i in defaults):
            return True, False, f"default is not {only_from}, left alone"
    missing = any(i != target.id for i in defaults)
    if not missing and not force:
        return True, False, f"already default: {target.FriendlyName}"
    AudioUtilities.SetDefaultDevice(target.id, roles=list(ROLES))
    return True, missing, target.FriendlyName


def restore_mic(name: str, *, force: bool = False, only_from: str | None = None) -> Restore:
    """Blocks at most TIMEOUT_S.

    `only_from` acts only if the current default's name contains it: at exit we
    undo the cable, never a headset the user chose. `force` writes even if nothing
    changes, which measures the call without side effects.
    """
    t0 = now()
    res = _on_com_thread(lambda: _set_default(name, force, only_from))
    if isinstance(res, Exception):
        return Restore(False, False, now() - t0, repr(res)[:300])
    ok, changed, detail = res
    return Restore(ok, changed, now() - t0, detail)


def endpoint_volume(name: str, flow: EDataFlow = EDataFlow.eRender) -> tuple[float, bool] | None:
    """Windows slider (0–1) and mute of the first active endpoint named `name`; None if it cannot be read."""

    def read() -> tuple[float, bool] | None:
        for d in AudioUtilities.GetAllDevices(flow.value, DEVICE_STATE.ACTIVE.value):
            if (d.FriendlyName or "").startswith(name):
                v = d.EndpointVolume
                return float(v.GetMasterVolumeLevelScalar()), bool(v.GetMute())
        return None

    res = _on_com_thread(read)
    return None if isinstance(res, Exception) else res
