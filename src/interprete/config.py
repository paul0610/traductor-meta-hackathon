"""Settings from the environment (.env at the repo root, never committed)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

API_BASE = "https://api.meta.ai/v1"
ASR_URL = "wss://api.meta.ai/v1/asr/realtime"
ASR_MODEL = "muse-voice-transcribe-1.0"
SPARK_MODEL = "muse-spark-1.3"


@dataclass(frozen=True, slots=True)
class Settings:
    api_key: str | None
    tts_voice: str
    cable_name: str
    mic_name: str
    delay_s: float
    voice_under_db: float
    av_offset_ms: float
    overlay_hide_from_capture: bool
    mode: str
    dub: bool
    camera_name: str


def load() -> Settings:
    return Settings(
        api_key=os.environ.get("MODEL_API_KEY") or None,
        tts_voice=os.environ.get("RTT_TTS_VOICE", "en-US-GuyNeural"),
        cable_name=os.environ.get("RTT_CABLE_NAME", "CABLE Input"),
        mic_name=os.environ.get("RTT_MIC_NAME", "Realtek"),
        delay_s=float(os.environ.get("RTT_DELAY_S", "6.0")),
        voice_under_db=float(os.environ.get("RTT_VOICE_UNDER_DB", "-20")),
        av_offset_ms=float(os.environ.get("RTT_AV_OFFSET_MS", "0")),
        overlay_hide_from_capture=os.environ.get("RTT_OVERLAY_HIDE_FROM_CAPTURE", "0") == "1",
        mode=os.environ.get("RTT_MODE", "incremental"),
        dub=os.environ.get("RTT_DUB", "0") == "1",
        camera_name=os.environ.get("RTT_CAMERA_NAME", "HD UVC"),
    )


def require_api_key() -> str:
    key = load().api_key
    if not key:
        raise SystemExit(f"MODEL_API_KEY is not set. Put it in {ROOT / '.env'} (see .env.example).")
    return key
