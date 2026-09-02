"""
utils.py - Helpers de sessao, cache e formatacao, independentes de provider.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
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


def ascii_safe_stem(stem: str, fallback: str = "arquivo") -> str:
    """Versao ASCII de um nome de arquivo.

    O SDK do Gemini manda o nome do arquivo no header HTTP
    `X-Goog-Upload-File-Name`, e o httpx exige headers em ASCII: um acento no
    nome do arquivo derruba o upload com UnicodeEncodeError.
    """
    normalized = unicodedata.normalize("NFKD", stem)
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    safe = "".join(
        char if char.isascii() and (char.isalnum() or char in "._- ") else "_"
        for char in without_marks
    ).strip(" ._-")

    if safe == stem:
        return safe
    # O nome mudou, entao dois arquivos distintos podem colidir ("cafe" e "cafe"
    # acentuado viram o mesmo). Um sufixo deterministico evita que um
    # temporario sobrescreva o outro e a transcricao saia trocada.
    digest = hashlib.sha1(stem.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"{safe}_{digest}" if safe else f"{fallback}_{digest}"


def make_safe_name(title: str, fallback: str = "sessao") -> str:
    safe = "".join(char if char.isalnum() or char in " _-" else "_" for char in title).strip()
    safe = safe[:60].strip().replace(" ", "_")
    return safe or fallback


def get_session_dir(output_dir: Path, title: str, job_id: str = "") -> Path:
    """
    Pasta da sessao, derivada do titulo.

    Repetir o mesmo titulo de proposito reaproveita a pasta e o cache - e assim
    que o cache economiza reprocessamento. Ja quando o titulo esta em branco (ou
    so tem simbolos), execucoes sem relacao nenhuma cairiam todas na mesma pasta
    "sessao" e se misturariam; nesse caso o job_id desempata.
    """
    nome = make_safe_name(title, fallback="")
    if not nome:
        nome = f"sessao_{job_id}" if job_id else "sessao"
    session = output_dir / nome
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


def _file_fingerprint(file_path: Path) -> str:
    """Hash do conteudo, lido em blocos para nao carregar o arquivo na memoria."""
    digest = hashlib.sha1(usedforsecurity=False)  # identidade de cache, nao seguranca
    with file_path.open("rb") as handle:
        for bloco in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def build_cache_key(file_path: Path, model: str, provider: str = "gemini") -> str:
    """
    Chave derivada do CONTEUDO do arquivo, nao do caminho.

    Todo envio pela interface web cai numa pasta temporaria com nome novo, entao
    caminho e data de modificacao mudam a cada execucao - chavear por eles fazia
    o cache nunca acertar. O conteudo e o que de fato determina a transcricao, e
    ler alguns MB por segundo custa muito menos que retranscrever.
    """
    basis = "|".join(
        [
            provider,
            model,
            str(file_path.stat().st_size),
            _file_fingerprint(file_path),
        ]
    )
    return hashlib.sha1(basis.encode("utf-8"), usedforsecurity=False).hexdigest()


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
        # Preserva o flag de diarizacao e o dispositivo: sem isso, um acerto de
        # cache reexibe os avisos errados no relatorio.
        "raw_metadata": dict(transcript.raw_metadata or {}),
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
        raw_metadata=dict(data.get("raw_metadata") or {}),
    )
