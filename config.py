"""
config.py - Constantes e persistencia.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "Papagaio Transcritor"
APP_VERSION = "4.0"
PACKAGE_NAME = "papagaio_transcritor"


def get_app_data_dir() -> Path:
    env_override = os.environ.get("PAPAGAIO_DATA_DIR")
    candidates = []
    if env_override:
        candidates.append(Path(env_override))
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "PapagaioTranscritor")
    if os.environ.get("APPDATA"):
        candidates.append(Path(os.environ["APPDATA"]) / "PapagaioTranscritor")
    candidates.append(Path.cwd() / ".papagaio_transcritor_data")

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except Exception:
            continue
    return Path.cwd()


def get_output_dir() -> Path:
    env_override = os.environ.get("PAPAGAIO_OUTPUT_DIR")
    output_dir = Path(env_override) if env_override else (Path.cwd() / "output")
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


APP_DATA_DIR = get_app_data_dir()
CONFIG_FILE = APP_DATA_DIR / "config.json"
CACHE_FILE = "_cache.json"
OUTPUT_DIR = get_output_dir()

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
SUPPORTED_EXTS = VIDEO_EXTS | AUDIO_EXTS

LANGS = {
    "Portugues (pt)": "pt",
    "English (en)": "en",
    "Espanol (es)": "es",
    "Francais (fr)": "fr",
    "Deutsch (de)": "de",
    "Italiano (it)": "it",
    "Japanese (ja)": "ja",
    "Korean (ko)": "ko",
    "Chinese (zh)": "zh",
}

GEMINI_DEFAULT_TRANSCRIPTION_MODEL = "gemini-flash-latest"
GEMINI_DEFAULT_SUMMARY_MODEL = "gemini-flash-latest"

PROVIDER_GEMINI = "gemini"
PROVIDER_WHISPER = "whisper"
PROVIDERS = {
    PROVIDER_GEMINI: "Gemini (nuvem)",
    PROVIDER_WHISPER: "Whisper local",
}
DEFAULT_PROVIDER = PROVIDER_GEMINI

# Whisper roda offline; o resumo continua no Gemini quando houver API key salva.
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
WHISPER_DEFAULT_MODEL = "small"
WHISPER_DEFAULT_DEVICE = "auto"  # auto | cpu | cuda
WHISPER_DEFAULT_COMPUTE_TYPE = "int8"
WHISPER_SAMPLE_RATE = 16000
# Sobrescreva com PAPAGAIO_DIARIZATION_MODEL se quiser outro checkpoint
# (ex.: pyannote/speaker-diarization-community-1, do pyannote.audio 4.x).
DIARIZATION_MODEL = os.environ.get("PAPAGAIO_DIARIZATION_MODEL") or "pyannote/speaker-diarization-3.1"

MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # limite do File API do Gemini por arquivo


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(data: dict):
    try:
        CONFIG_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
