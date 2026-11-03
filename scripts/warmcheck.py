"""Pre-call check: everything a call needs, tested for real, in under a minute. Never changes a setting.

    uv run python scripts/warmcheck.py

FAIL blocks the call; WARN does not. It tells you what to change; you change it. It speaks English,
as the README that sends you here does; the app itself speaks Spanish to its user.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import pyaudiowpatch as pyaudio
import websockets
from pycaw.constants import EDataFlow
from pycaw.utils import AudioUtilities

from interprete import config
from interprete.audio import devices
from interprete.audio.device_restore import endpoint_volume
from interprete.mt.spark import Spark
from interprete.tts.mms import MmsVoice
from interprete.video.virtual_cam import HEIGHT, WIDTH, measure_webcam, open_virtual_camera

HEADPHONE_HINTS = ("auricular", "headphone", "headset", "bose", "airpods", "buds", "audífono", "audifono", "manos libres")
failures = 0


def report(level: str, what: str, detail: str = "") -> None:
    global failures
    failures += level == "FAIL"
    print(f"{level:6} {what}{': ' + detail if detail else ''}")


def check_devices(s: config.Settings) -> None:
    try:
        d = devices.find(s.mic_name, "input", ("CABLE",))
        report("OK", "physical microphone", f"{d.name} [{d.hostapi}] {d.default_samplerate} Hz")
    except LookupError as exc:
        report("FAIL", "physical microphone", str(exc))
    for name, kind in ((s.cable_name, "output"), ("CABLE Output", "input")):
        try:
            d = devices.find(name, kind)
            report("OK", name, f"[{d.hostapi}] {d.default_samplerate} Hz")
        except LookupError:
            report("FAIL", name, "not found: install VB-CABLE and restart Windows")

    vol = endpoint_volume(s.cable_name)
    if vol is None:
        report("WARN", "CABLE Input volume", "could not be read")
    elif vol[0] < 0.95 or vol[1]:
        report("WARN", "CABLE Input volume", f"{vol[0] * 100:.0f} %{' and muted' if vol[1] else ''}: they will hear "
                                             "you very low. Raise it to 100 in Settings > System > Sound > CABLE Input")
    else:
        report("OK", "CABLE Input volume", f"{vol[0] * 100:.0f} %")
    vol = endpoint_volume("CABLE Output", EDataFlow.eCapture)
    if vol is not None and (vol[0] < 0.95 or vol[1]):
        report("WARN", "CABLE Output volume", f"{vol[0] * 100:.0f} %{' and muted' if vol[1] else ''}: raise it to 100")

    out = AudioUtilities.GetSpeakers().FriendlyName or ""
    if "CABLE" in out:
        report("FAIL", "default output", f"{out}: the subtitles would hear your own English. Pick your headphones")
    elif any(h in out.lower() for h in HEADPHONE_HINTS):
        report("OK", "default output", out)
    else:
        report("WARN", "default output", f"{out} does not look like headphones: with speakers the mic hears "
                                         "the other side and translates their voice back")
    with pyaudio.PyAudio() as pa:
        try:
            d = pa.get_default_wasapi_loopback()
            report("OK", "loopback", f"{d['name']} {int(d['defaultSampleRate'])} Hz")
        except (OSError, LookupError) as exc:
            report("FAIL", "loopback", repr(exc))


def check_camera(s: config.Settings) -> None:
    """Only with RTT_DUB=1: opens the webcam and the driver for a moment, keeps nothing."""
    try:
        fps = measure_webcam(s.camera_name)
        report("OK" if fps >= 24 else "WARN", "webcam", f"{s.camera_name}, {fps:.0f} fps at {WIDTH}x{HEIGHT}"
               + ("" if fps >= 24 else ": more light, and no other app holding the webcam"))
    except (RuntimeError, OSError) as exc:
        report("FAIL", "webcam", str(exc))
    try:
        open_virtual_camera().close()
        report("OK", "virtual camera", "OBS driver available: pick OBS Virtual Camera in the call")
    except RuntimeError as exc:
        report("FAIL", "virtual camera", f"{exc} (winget install OBSProject.OBSStudio)")


async def check_mms(s: config.Settings) -> None:
    """The first run downloads the model; after that it needs no network."""
    t0 = time.perf_counter()
    try:
        voice = MmsVoice(s.mms_model, 48000, speaking_rate=s.mms_speaking_rate)
        await voice.start()
        r = await voice.say("Testing the voice.")
        report("OK" if r.pcm else "FAIL", "MMS-TTS", f"{voice.name}: ready in {time.perf_counter() - t0:.0f} s, "
               f"a sentence in {r.t_ready - r.t_request:.2f} s")
    except Exception as exc:
        report("FAIL", "MMS-TTS", repr(exc)[:200])


async def check_meta(key: str) -> None:
    try:
        r = await Spark(key, timeout_s=8.0).translate("La entrevista comienza ahora.")
        report("OK", "Muse Spark", f"{r.t_end - r.t_start:.2f} s, {r.translation}")
    except Exception as exc:
        report("FAIL", "Muse Spark", repr(exc)[:200])
    try:
        async with websockets.connect(config.ASR_URL, max_size=None) as ws:
            await ws.send(json.dumps({
                "mode": "ENDPOINTING", "authorization": {"accessToken": f"Bearer {key}"}, "audioEncoding": "PCM_16KHZ",
                "model": config.ASR_MODEL, "partialMode": "CUMULATIVE", "emitAudioProgress": False,
            }))
            ack = json.loads(await asyncio.wait_for(ws.recv(), 6.0))
            report("OK" if "sessionId" in ack else "FAIL", "Muse Voice Transcribe", "session open" if "sessionId" in ack else str(ack)[:200])
            await ws.send(json.dumps({"type": "endStream"}))
    except Exception as exc:
        report("FAIL", "Muse Voice Transcribe", repr(exc)[:200])


async def main() -> int:
    s = config.load()
    check_devices(s)
    if s.dub:
        check_camera(s)
    await check_mms(s)
    if s.api_key:
        report("OK", "MODEL_API_KEY")
        await check_meta(s.api_key)
    else:
        report("FAIL", "MODEL_API_KEY", "not in .env: copy .env.example to .env and put your key there")
    print("\nIn the call: microphone = CABLE Output, unmuted, noise cancellation off.")
    print("READY FOR A CALL" if not failures else f"{failures} FAIL(S): fix them before the call")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
