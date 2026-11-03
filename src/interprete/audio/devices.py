"""Audio endpoint lookup by name (never by index: indices shift when devices come and go).

Host API preference is WASAPI, then the shared duplicates of the same device
(DirectSound, MME). WDM-KS is never used: on this PC the Realtek array's WDM-KS
endpoint fails intermittently (0x492) and the jack mic only exists there.
"""

from __future__ import annotations

from dataclasses import dataclass

import sounddevice as sd

HOSTAPI_ORDER = ("Windows WASAPI", "Windows DirectSound", "MME")
MME_NAME = 31
HEADPHONE_HINTS = ("auricular", "headphone", "headset", "bose", "airpods", "buds", "audífono", "audifono", "manos libres")


@dataclass(frozen=True, slots=True)
class Device:
    index: int
    name: str
    hostapi: str
    default_samplerate: int
    max_input_channels: int
    max_output_channels: int

    @property
    def is_wasapi(self) -> bool:
        return self.hostapi == "Windows WASAPI"


def looks_like_headphones(name: str) -> bool:
    """A guess from the name: Windows does not say what kind of device an output is."""
    return any(h in name.lower() for h in HEADPHONE_HINTS)


def default_mic() -> str:
    """Windows' default input, cut to what MME keeps of a name so its duplicates match too. Never the cable."""
    for api in sd.query_hostapis():
        if api["name"] == HOSTAPI_ORDER[0] and api["default_input_device"] >= 0:
            name = sd.query_devices(api["default_input_device"])["name"]
            return "" if "CABLE" in name.upper() else name[:MME_NAME]
    return ""


def candidates(name_sub: str, kind: str, exclude: tuple[str, ...] = ()) -> list[Device]:
    """All endpoints whose name contains `name_sub`, best host API first. `kind` is "input" or "output".

    An input with no name is the default microphone: a machine that is not this one has no mic called Realtek.
    """
    if kind == "input" and not name_sub:
        name_sub = default_mic()
    apis = sd.query_hostapis()
    found: list[Device] = []
    for i, d in enumerate(sd.query_devices()):
        api = apis[d["hostapi"]]["name"]
        if api not in HOSTAPI_ORDER:
            continue
        channels = d["max_input_channels"] if kind == "input" else d["max_output_channels"]
        if channels <= 0:
            continue
        name = d["name"]
        if name_sub.lower() not in name.lower() or any(x.lower() in name.lower() for x in exclude):
            continue
        found.append(Device(i, name, api, int(d["default_samplerate"]),
                            d["max_input_channels"], d["max_output_channels"]))
    found.sort(key=lambda dev: HOSTAPI_ORDER.index(dev.hostapi))
    return found


def find(name_sub: str, kind: str, exclude: tuple[str, ...] = ()) -> Device:
    found = candidates(name_sub, kind, exclude)
    if not found:
        raise LookupError(f"no {kind} endpoint matching {name_sub!r} (WASAPI/DirectSound/MME)")
    return found[0]


def stream_extra(dev: Device):
    """WASAPI shared mode with Windows' own conversion, so mono/rate mismatches never fail to open."""
    return sd.WasapiSettings(auto_convert=True) if dev.is_wasapi else None


def default_output_name() -> str:
    return sd.query_devices(kind="output")["name"]
