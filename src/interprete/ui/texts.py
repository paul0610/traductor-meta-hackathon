"""Every word the interface shows, in English unless `RTT_UI_LANG=es` asks for Spanish.

English by default, because whoever meets the app first may not speak Spanish, the hackathon's judges
included; Spanish for its user, one setting away. The code writes the English and `t` looks up the Spanish,
so a text missing here shows in English instead of failing. The subtitles are the translation itself, and
the Meta panel names models and timings: neither goes through here.
"""

from __future__ import annotations

LANG = "en"

ES = {
    # the button
    "LOADING": "CARGANDO",
    "READY": "LISTO",
    "CONNECTING": "CONECTANDO",
    "LIVE": "EN VIVO",
    "MIC ON HOLD": "MIC EN ESPERA",
    "PAUSED": "PAUSADO",
    "MUTED": "MUDO",
    "RAW VOICE": "VOZ CRUDA",
    "STOPPING": "APAGANDO",
    "OFF": "APAGADO",
    # the metrics line
    "STANDBY": "EN ESPERA",
    "VOICE": "VOZ",
    "DUB": "DOBLAJE",
    "TURN": "TURNO",
    "them": "ellos",
    "me": "yo",
    "LATE: restart with D {d:.1f}": "TARDE: reinicia con D {d:.1f}",
    "ASR DOWN": "ASR CAÍDO",
    "SUBTITLES DOWN": "SUBTÍTULOS CAÍDOS",
    "EN→ES VOICE": "VOZ ES",
    "SPEAKERS": "PARLANTES",
    "NO METRICS": "SIN MÉTRICAS",
    "NO DELAYED CAMERA": "SIN CÁMARA RETRASADA",
    "HEADPHONES NOT DEFAULT": "AUDÍFONOS NO PREDETERMINADOS",
    "ERROR {task}: raw voice on the cable": "ERROR {task}: voz cruda en el cable",
    "ERROR {task}: no subtitles": "ERROR {task}: sin subtítulos",
    "DUB D {d:.1f}s": "DOBLAJE D {d:.1f}s",
    "NO DUB": "SIN DOBLAJE",
    "SPEAKERS: IN TURNS": "PARLANTES: POR TURNOS",
    "Loading…": "Cargando…",
    "Connecting…": "Conectando…",
    "Off": "Apagado",
    "Off: the voice did not load": "Apagado: no se pudo cargar la voz",
    # the band
    "Loading the voice…": "Cargando la voz…",
    "Only English reaches the other side.": "Al otro lado solo llega inglés.",
    "On speakers, speak when the button says LIVE.": "Con parlantes, habla cuando el botón diga EN VIVO.",
    "F7 notice · F8 mute · F9 raw voice · F10 physical mic": "F7 aviso · F8 mudo · F9 voz cruda · F10 mic físico",
    "Spanish voice on.": "Voz en español encendida.",
    "Spanish voice off: subtitles only.": "Voz en español apagada: solo subtítulos.",
    "The voice did not load. The first run downloads it and needs internet.":
        "No se pudo cargar la voz. La primera vez se descarga y necesita internet.",
    "Stopping…": "Apagando…",
    "Off. Physical microphone restored.": "Apagado. Micrófono físico restaurado.",
    "Off. Could not restore the microphone: check it in Windows' Sound settings.":
        "Apagado. No pude restaurar el micrófono: revísalo en Sonido de Windows.",
    "THEY ARE TALKING TO YOU": "TE ESTÁN HABLANDO",
    "Not said: {text}": "Sin decir: {text}",
    "(opening notice)": "(aviso inicial)",
    # the console
    "F7 notice · F8 mute · F9 raw voice on/off · F10 physical mic · Ctrl-C quit":
        "F7 aviso · F8 mudo · F9 voz cruda on/off · F10 mic físico · Ctrl-C salir",
    "camera {name} -> OBS Virtual Camera, {s:.2f} s late": "cámara {name} -> OBS Virtual Camera, {s:.2f} s tarde",
    "WARNING: no delayed camera, the other side sees your lips {s:.1f} s before the voice: {exc}":
        "AVISO: sin cámara retrasada, el otro lado ve tus labios {s:.1f} s antes que la voz: {exc}",
    "WARNING: {cable} is at {pct:.0f} %{muted} in Windows: the other side will hear you very low. "
    "Raise it to 100 in Sound > Output > CABLE Input.":
        "AVISO: {cable} está al {pct:.0f} %{muted} en Windows: el otro lado te oirá muy bajo. "
        "Súbelo a 100 en Sonido > Salida > CABLE Input.",
    " and muted": " y mudo",
    "WARNING: Python is at {pct:.0f} % in Windows' volume mixer: the other side will hear you very low. "
    "Raise it to 100 in System > Sound > Volume mixer, with the app running.":
        "AVISO: Python está al {pct:.0f} % en el mezclador de volumen de Windows: el otro lado te oirá muy bajo. "
        "Súbelo a 100 en Sistema > Sonido > Mezclador de volumen, con la app en marcha.",
    "WARNING: {phones} is connected, but Windows plays on {output}: the call, its subtitles and the Spanish voice "
    "follow Windows' default output. Make {phones} the default in Settings > System > Sound > Output, leave the "
    "meeting app's speaker on its default, and start again.":
        "AVISO: {phones} está conectado, pero Windows suena por {output}: la llamada, sus subtítulos y la voz en "
        "español siguen la salida predeterminada de Windows. Elige {phones} como predeterminada en Configuración > "
        "Sistema > Sonido > Salida, deja el altavoz de la app de reuniones en el predeterminado y vuelve a empezar.",
    "FAILED": "FALLÓ",
}


def t(text: str) -> str:
    """`text` in the interface's language."""
    return ES.get(text, text) if LANG == "es" else text


def set_lang(lang: str) -> None:
    global LANG
    LANG = "es" if lang.strip().lower().startswith("es") else "en"
