"""Pre-call check: everything a call needs, tested for real, in under a minute. Never changes a setting.

    uv run python scripts/warmcheck.py

FALLA blocks the call; AVISO does not. It tells you what to change; you change it.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time

import numpy as np
import pyaudiowpatch as pyaudio
import soundfile
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
    failures += level == "FALLA"
    print(f"{level:6} {what}{': ' + detail if detail else ''}")


def check_devices(s: config.Settings) -> None:
    try:
        d = devices.find(s.mic_name, "input", ("CABLE",))
        report("OK", "micrófono físico", f"{d.name} [{d.hostapi}] {d.default_samplerate} Hz")
    except LookupError as exc:
        report("FALLA", "micrófono físico", str(exc))
    for name, kind in ((s.cable_name, "output"), ("CABLE Output", "input")):
        try:
            d = devices.find(name, kind)
            report("OK", name, f"[{d.hostapi}] {d.default_samplerate} Hz")
        except LookupError:
            report("FALLA", name, "no aparece; ¿VB-CABLE instalado?")

    vol = endpoint_volume(s.cable_name)
    if vol is None:
        report("AVISO", "volumen de CABLE Input", "no se pudo leer")
    elif vol[0] < 0.95 or vol[1]:
        report("AVISO", "volumen de CABLE Input", f"{vol[0] * 100:.0f} %{' y mudo' if vol[1] else ''}: te oirán muy bajo. "
                                                  "Súbelo a 100 en Configuración > Sonido > Salida > CABLE Input")
    else:
        report("OK", "volumen de CABLE Input", f"{vol[0] * 100:.0f} %")
    vol = endpoint_volume("CABLE Output", EDataFlow.eCapture)
    if vol is not None and (vol[0] < 0.95 or vol[1]):
        report("AVISO", "volumen de CABLE Output", f"{vol[0] * 100:.0f} %{' y mudo' if vol[1] else ''}: súbelo a 100")

    out = AudioUtilities.GetSpeakers().FriendlyName or ""
    if "CABLE" in out:
        report("FALLA", "salida predeterminada", f"{out}: el loopback capturaría tu propia traducción")
    elif any(h in out.lower() for h in HEADPHONE_HINTS):
        report("OK", "salida predeterminada", out)
    else:
        report("AVISO", "salida predeterminada", f"{out} no parece un auricular: con parlantes el mic oye al otro "
                                                 "y traduce su voz de vuelta")
    with pyaudio.PyAudio() as pa:
        try:
            d = pa.get_default_wasapi_loopback()
            report("OK", "loopback", f"{d['name']} {int(d['defaultSampleRate'])} Hz")
        except (OSError, LookupError) as exc:
            report("FALLA", "loopback", repr(exc))


def check_decoder() -> None:
    version = soundfile.__libsndfile_version__
    major, minor = (int(x) for x in re.match(r"(\d+)\.(\d+)", version).groups())
    report("OK" if (major, minor) >= (1, 1) else "FALLA", "libsndfile (decodifica el MP3 de Edge)", version)


def check_camera(s: config.Settings) -> None:
    """Only with RTT_DUB=1: opens the webcam and the driver for a moment, keeps nothing."""
    try:
        fps = measure_webcam(s.camera_name)
        report("OK" if fps >= 24 else "AVISO", "webcam", f"{s.camera_name}, {fps:.0f} fps a {WIDTH}x{HEIGHT}")
    except (RuntimeError, OSError) as exc:
        report("FALLA", "webcam", str(exc))
    try:
        open_virtual_camera().close()
        report("OK", "cámara virtual", "driver de OBS disponible; elige OBS Virtual Camera en la llamada")
    except RuntimeError as exc:
        report("FALLA", "cámara virtual", f"{exc}. Instala OBS Studio una vez: winget install OBSProject.OBSStudio")


async def check_mms(s: config.Settings) -> None:
    """The first run downloads the model; after that it needs no network."""
    t0 = time.perf_counter()
    try:
        voice = MmsVoice(s.mms_model, 48000, speaking_rate=s.mms_speaking_rate)
        await voice.start()
        r = await voice.say("Testing the voice.")
        report("OK" if r.pcm else "FALLA", "MMS-TTS", f"{voice.name}: lista en {time.perf_counter() - t0:.0f} s, "
               f"frase en {r.t_ready - r.t_request:.2f} s")
    except Exception as exc:
        report("FALLA", "MMS-TTS", repr(exc)[:200])


async def check_pocket(s: config.Settings) -> None:
    """The first run downloads the model; after that it needs no network."""
    from interprete.tts.pocket import PocketVoice

    t0 = time.perf_counter()
    try:
        voice = PocketVoice(s.pocket_model, s.pocket_voice, 48000, gain_db=s.pocket_gain_db)
        await voice.start()
        r = await voice.say("Testing the voice.")
        report("OK" if r.pcm else "FALLA", "Pocket TTS", f"{s.pocket_model}, {s.pocket_voice}: lista en "
               f"{time.perf_counter() - t0:.0f} s, frase en {r.t_ready - r.t_request:.2f} s")
    except Exception as exc:
        report("AVISO" if s.tts_backup == "edge" else "FALLA", "Pocket TTS", repr(exc)[:200])


async def check_edge(s: config.Settings) -> None:
    from interprete.tts.edge import synthesize

    trouble = "AVISO" if s.tts_engine == "pocket" else "FALLA"       # only the backup: the call can go on
    try:
        r = await asyncio.wait_for(synthesize("Testing the voice.", s.tts_voice, 48000), 6.0)
        x = np.frombuffer(r.pcm, "<i2")
        report("OK" if len(x) else trouble, "Edge TTS", f"{s.tts_voice}, primer byte {r.t_first - r.t_request:.2f} s")
    except Exception as exc:
        report(trouble, "Edge TTS", repr(exc)[:200])


async def check_tts(s: config.Settings) -> None:
    if s.tts_engine == "mms":
        await check_mms(s)
    if s.tts_engine == "pocket":
        await check_pocket(s)
    if s.tts_engine == "edge" or s.tts_backup == "edge":
        await check_edge(s)


async def check_meta(key: str) -> None:
    try:
        r = await Spark(key, timeout_s=8.0).translate("La entrevista comienza ahora.")
        report("OK", "Muse Spark", f"{r.t_end - r.t_start:.2f} s, {r.translation}")
    except Exception as exc:
        report("FALLA", "Muse Spark", repr(exc)[:200])
    try:
        async with websockets.connect(config.ASR_URL, max_size=None) as ws:
            await ws.send(json.dumps({
                "mode": "ENDPOINTING", "authorization": {"accessToken": f"Bearer {key}"}, "audioEncoding": "PCM_16KHZ",
                "model": config.ASR_MODEL, "partialMode": "CUMULATIVE", "emitAudioProgress": False,
            }))
            ack = json.loads(await asyncio.wait_for(ws.recv(), 6.0))
            report("OK" if "sessionId" in ack else "FALLA", "Muse Voice Transcribe", "sesión abierta" if "sessionId" in ack else str(ack)[:200])
            await ws.send(json.dumps({"type": "endStream"}))
    except Exception as exc:
        report("FALLA", "Muse Voice Transcribe", repr(exc)[:200])


async def main() -> int:
    s = config.load()
    check_devices(s)
    if "edge" in (s.tts_engine, s.tts_backup):
        check_decoder()
    if s.dub:
        check_camera(s)
    await check_tts(s)
    if s.api_key:
        report("OK", "MODEL_API_KEY")
        await check_meta(s.api_key)
    else:
        report("FALLA", "MODEL_API_KEY", "no está en .env")
    print("\nRecuerda: en la llamada, micrófono = CABLE Output, sin mute, sin cancelación de ruido.")
    print("LISTO PARA LLAMADA" if not failures else f"{failures} FALLA(S): corrige antes de entrar")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
