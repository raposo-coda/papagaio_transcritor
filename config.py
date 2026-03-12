"""
config.py — Constantes, configuração e persistência
"""

import json
from pathlib import Path

# ── App ───────────────────────────────────────────────────────────────────────
APP_NAME    = "TranscritorIA"
APP_VERSION = "2.1"
CONFIG_FILE = Path.home() / ".transcritor_config.json"

# ── Arquivos suportados ───────────────────────────────────────────────────────
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
SUPPORTED_EXTS = VIDEO_EXTS | AUDIO_EXTS

# ── Idiomas ───────────────────────────────────────────────────────────────────
LANGS = {
    "Português (pt)": "pt",
    "English (en)":   "en",
    "Español (es)":   "es",
    "Français (fr)":  "fr",
    "Deutsch (de)":   "de",
    "Italiano (it)":  "it",
    "日本語 (ja)":     "ja",
    "한국어 (ko)":     "ko",
    "中文 (zh)":       "zh",
}

# ── Cores da UI ───────────────────────────────────────────────────────────────
COLORS = {
    "bg":       "#1e1e2e",
    "surface":  "#2a2a3e",
    "border":   "#3d3d5c",
    "accent":   "#7c6af7",
    "accent2":  "#5a9cf5",
    "success":  "#4caf82",
    "warning":  "#f5a623",
    "error":    "#f56060",
    "text":     "#cdd6f4",
    "subtext":  "#a6adc8",
    "muted":    "#6c7086",
}

# ── Provedores de IA ──────────────────────────────────────────────────────────
PROVIDERS = {
    "AssemblyAI LeMUR": {
        "id":            "lemur",
        "needs_key":     False,
        "needs_url":     False,
        "default_model": "(automático)",
        "hint":          "Usa a mesma key da AssemblyAI. Grátis no plano atual.",
    },
    "Anthropic (Claude)": {
        "id":            "anthropic",
        "needs_key":     True,
        "needs_url":     False,
        "default_model": "claude-haiku-4-5",
        "hint":          "pip install anthropic  |  console.anthropic.com",
    },
    "OpenAI (ChatGPT)": {
        "id":            "openai",
        "needs_key":     True,
        "needs_url":     False,
        "default_model": "gpt-4o-mini",
        "hint":          "pip install openai  |  platform.openai.com",
    },
    "Ollama (local)": {
        "id":            "ollama",
        "needs_key":     False,
        "needs_url":     True,
        "default_model": "llama3",
        "hint":          "Requer Ollama rodando localmente  |  ollama.com",
    },
    "OpenAI-compatible": {
        "id":            "openai_compat",
        "needs_key":     True,
        "needs_url":     True,
        "default_model": "mistral",
        "hint":          "Qualquer endpoint /v1/chat/completions (Groq, Together, LM Studio...)",
    },
}


# ── Persistência ──────────────────────────────────────────────────────────────

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
