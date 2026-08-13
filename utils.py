"""
utils.py - Helpers de sessao, cache e formatacao, independentes de provider.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    from .config import CACHE_FILE
    from .logger import log
    from .models import NormalizedTranscriptResult, TranscriptUtterance
except ImportError:
    from config import CACHE_FILE  # type: ignore
    from logger import log  # type: ignore
    from models import NormalizedTranscriptResult, TranscriptUtterance  # type: ignore


def fmt_time(seconds: float) -> str:
    total = int(seconds or 0)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def make_safe_name(title: str, fallback: str = "sessao") -> str:
    safe = "".join(char if char.isalnum() or char in " _-" else "_" for char in title).strip()
    safe = safe[:60].strip().replace(" ", "_")
    return safe or fallback


def get_session_dir(output_dir: Path, title: str) -> Path:
    session = output_dir / make_safe_name(title, fallback="sessao")
    session.mkdir(parents=True, exist_ok=True)
    log.debug(f"Pasta de sessao: {session}")
    return session


def load_cache(session_dir: Path) -> dict:
    cache_path = session_dir / CACHE_FILE
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            log.debug(f"Cache carregado: {len(data)} entrada(s)")
            return data
        except Exception as exc:
            log.warning(f"Falha ao ler cache: {exc}")
    return {}


def save_cache(session_dir: Path, cache: dict):
    cache_path = session_dir / CACHE_FILE
    try:
        cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
        log.debug(f"Cache salvo: {len(cache)} entrada(s)")
    except Exception as exc:
        log.warning(f"Falha ao salvar cache: {exc}")


def build_cache_key(file_path: Path, model: str) -> str:
    stat = file_path.stat()
    basis = "|".join(
        [
            "gemini",
            model,
            str(file_path.resolve()).lower(),
            str(stat.st_size),
            str(int(stat.st_mtime)),
        ]
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def transcript_to_cache(transcript: NormalizedTranscriptResult) -> dict:
    return {
        "provider_id": transcript.provider_id,
        "provider_label": transcript.provider_label,
        "model": transcript.model,
        "text": transcript.text,
        "audio_duration_seconds": transcript.audio_duration_seconds,
        "language_code": transcript.language_code,
        "utterances": [
            {
                "speaker": item.speaker,
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "text": item.text,
            }
            for item in transcript.utterances
        ],
    }


def transcript_from_cache(data: dict, source_path: Path) -> NormalizedTranscriptResult:
    return NormalizedTranscriptResult(
        source_path=source_path,
        provider_id=data.get("provider_id", "gemini"),
        provider_label=data.get("provider_label", "Gemini"),
        model=data.get("model", ""),
        text=data.get("text", ""),
        audio_duration_seconds=float(data.get("audio_duration_seconds") or 0),
        language_code=data.get("language_code", ""),
        utterances=[
            TranscriptUtterance(
                speaker=item.get("speaker", ""),
                start_ms=int(item.get("start_ms") or 0),
                end_ms=int(item.get("end_ms") or 0),
                text=item.get("text", ""),
            )
            for item in data.get("utterances") or []
        ],
    )
