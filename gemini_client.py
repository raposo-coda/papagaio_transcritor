"""
gemini_client.py - Transcricao e resumo via Google Gemini API.
"""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

try:
    from .config import GEMINI_DEFAULT_SUMMARY_MODEL, GEMINI_DEFAULT_TRANSCRIPTION_MODEL
    from .logger import log
    from .models import GeminiConfig, NormalizedTranscriptResult, TranscriptUtterance
except ImportError:
    from config import GEMINI_DEFAULT_SUMMARY_MODEL, GEMINI_DEFAULT_TRANSCRIPTION_MODEL  # type: ignore
    from logger import log  # type: ignore
    from models import GeminiConfig, NormalizedTranscriptResult, TranscriptUtterance  # type: ignore

_UPLOAD_POLL_SECONDS = 2
_UPLOAD_TIMEOUT_SECONDS = 600
_RETRY_ATTEMPTS = 4
_RETRY_BASE_DELAY_SECONDS = 5

# Tempo maximo esperando UMA resposta do Gemini. Sem limite, uma conexao que o
# Google deixa pendurada (aberta, mas sem resposta) prende o job para sempre - e,
# com ele, o lock que impede qualquer outra transcricao. Ja aconteceu: 1h15 de
# audio ficou mais de uma hora parada. A transcricao normal de horas de audio
# volta em poucos minutos, entao 20 min sobra com folga.
_REQUEST_TIMEOUT_SECONDS = 20 * 60

# Estourar o tempo limite custa caro (20 min cada vez): so uma segunda chance.
_TIMEOUT_ATTEMPTS = 2


def _timeout_options():
    from google.genai import types

    return types.HttpOptions(timeout=_REQUEST_TIMEOUT_SECONDS * 1000)  # em milissegundos


def _call_with_retry(fn, label: str):
    import httpx
    from google.genai import errors

    timeouts = 0
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            return fn()
        except errors.ServerError as exc:
            if attempt == _RETRY_ATTEMPTS:
                raise
            delay = _RETRY_BASE_DELAY_SECONDS * attempt
            log.warning(f"  [gemini] {label} indisponivel (tentativa {attempt}/{_RETRY_ATTEMPTS}): {exc}. Tentando de novo em {delay}s.")
            time.sleep(delay)
        except httpx.TimeoutException:
            timeouts += 1
            minutos = _REQUEST_TIMEOUT_SECONDS // 60
            if timeouts >= _TIMEOUT_ATTEMPTS or attempt == _RETRY_ATTEMPTS:
                raise RuntimeError(
                    f"O Gemini nao respondeu em {minutos} min ({label.lower()}), {timeouts} vez(es) seguidas. "
                    "Tente de novo mais tarde ou use o modo local."
                ) from None
            log.warning(f"  [gemini] {label}: sem resposta do Gemini em {minutos} min. Tentando de novo.")

_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".flv": "video/x-flv",
    ".wmv": "video/x-ms-wmv",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
}


def _guess_mime_type(file_path: Path) -> str:
    mime_type = _MIME_TYPES.get(file_path.suffix.lower())
    if not mime_type:
        raise RuntimeError(f"Extensao sem mime type mapeado: {file_path.suffix}")
    return mime_type

_TRANSCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "language_code": {"type": "string"},
        "utterances": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string"},
                    "start_seconds": {"type": "number"},
                    "end_seconds": {"type": "number"},
                    "text": {"type": "string"},
                },
                "required": ["speaker", "start_seconds", "end_seconds", "text"],
            },
        },
    },
    "required": ["utterances"],
}


def _package_available() -> bool:
    return importlib.util.find_spec("google.genai") is not None


def validate_environment(config: GeminiConfig) -> list[str]:
    errors = []
    if not _package_available():
        errors.append("Pacote Python ausente: pip install google-genai")
    if not config.api_key.strip():
        errors.append("API key do Gemini obrigatoria. Gere uma em aistudio.google.com.")
    return errors


def _client(config: GeminiConfig):
    from google import genai

    return genai.Client(api_key=config.api_key)


def _upload_and_wait(client, file_path: Path, mime_type: str | None = None):
    from google.genai import types

    log.info(f"  [gemini] Enviando arquivo: {file_path.name}")
    mime_type = mime_type or _guess_mime_type(file_path)
    uploaded = client.files.upload(
        file=str(file_path),
        config=types.UploadFileConfig(mime_type=mime_type),
    )

    waited = 0
    while getattr(uploaded.state, "name", uploaded.state) == "PROCESSING":
        if waited >= _UPLOAD_TIMEOUT_SECONDS:
            raise RuntimeError(f"Timeout aguardando processamento do arquivo no Gemini: {file_path.name}")
        time.sleep(_UPLOAD_POLL_SECONDS)
        waited += _UPLOAD_POLL_SECONDS
        uploaded = client.files.get(name=uploaded.name)

    state_name = getattr(uploaded.state, "name", uploaded.state)
    if state_name == "FAILED":
        raise RuntimeError(f"Gemini falhou ao processar o arquivo: {file_path.name}")

    log.ok(f"  [gemini] Arquivo pronto: {file_path.name}")
    return uploaded


def _transcription_prompt(lang_code: str) -> str:
    lang_hint = (
        f"O idioma do audio/video e '{lang_code}'. Transcreva nesse idioma."
        if lang_code
        else "Detecte o idioma automaticamente e transcreva nesse mesmo idioma."
    )
    return (
        "Voce e um sistema de transcricao. Ouca o arquivo de audio/video anexado e produza uma "
        "transcricao verbatim completa, dividida em falas.\n\n"
        f"{lang_hint}\n\n"
        "Para cada fala, identifique o falante (use rotulos como 'Falante 1', 'Falante 2' se nao "
        "souber o nome; use o nome se for mencionado na conversa), o instante de inicio e fim em "
        "segundos, e o texto falado. Nao pule nenhum trecho. Nao resuma, transcreva literalmente.\n\n"
        "Baseie-se exclusivamente no audio real do arquivo anexado, nunca nestas instrucoes. Se o "
        "arquivo nao contiver fala alguma (silencio, musica sem letra, ruido, tom de teste), retorne "
        "'utterances' como uma lista vazia."
    )


def transcribe_path(file_path: Path, lang_code: str, config: GeminiConfig, mime_type: str | None = None) -> NormalizedTranscriptResult:
    from google.genai import types

    client = _client(config)
    uploaded = _upload_and_wait(client, file_path, mime_type=mime_type)

    model = config.transcription_model or GEMINI_DEFAULT_TRANSCRIPTION_MODEL
    log.info(f"  [gemini] Transcrevendo com modelo: {model}")

    response = _call_with_retry(
        lambda: client.models.generate_content(
            model=model,
            contents=[uploaded, _transcription_prompt(lang_code)],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_TRANSCRIPTION_SCHEMA,
                http_options=_timeout_options(),
            ),
        ),
        label="Transcricao",
    )

    data = json.loads(response.text)
    raw_utterances = data.get("utterances") or []

    utterances = [
        TranscriptUtterance(
            speaker=str(item.get("speaker") or "Falante"),
            start_ms=int(float(item.get("start_seconds") or 0) * 1000),
            end_ms=int(float(item.get("end_seconds") or 0) * 1000),
            text=(item.get("text") or "").strip(),
        )
        for item in raw_utterances
    ]
    full_text = "\n".join(item.text for item in utterances if item.text)
    duration = max((item.end_ms for item in utterances), default=0) / 1000

    try:
        client.files.delete(name=uploaded.name)
    except Exception:
        pass

    log.ok(f"  [gemini] Transcricao concluida: {len(utterances)} fala(s)")

    return NormalizedTranscriptResult(
        source_path=file_path,
        provider_id="gemini",
        provider_label="Gemini",
        model=model,
        text=full_text,
        audio_duration_seconds=duration,
        language_code=data.get("language_code") or lang_code,
        utterances=utterances,
    )


def _build_summary_prompt(transcripts: list[NormalizedTranscriptResult], context_prompt: str) -> str:
    blocks = []
    if len(transcripts) > 1:
        blocks.append(
            f"As transcricoes abaixo pertencem a {len(transcripts)} arquivos do mesmo contexto. "
            "Considere tudo como uma unica sessao."
        )
    for index, transcript in enumerate(transcripts, start=1):
        blocks.append(
            f"--- Arquivo {index}: {transcript.source_path.name} ---\n{transcript.text.strip() or '_Sem texto_'}"
        )

    context_block = f"Contexto fornecido pelo usuario:\n{context_prompt.strip()}\n\n" if context_prompt.strip() else ""
    blocks.append(
        f"{context_block}"
        "Analise as transcricoes acima e produza um relatorio estruturado em Markdown.\n\n"
        "Se o contexto pedir um formato proprio, siga esse formato. Caso contrario, use:\n"
        "1. Resumo Executivo\n"
        "2. Pontos-chave\n"
        "3. Decisoes e Acoes\n"
        "4. Participantes\n"
        "5. Conclusao\n\n"
        "Seja objetivo e use portugues claro."
    )
    return "\n\n".join(blocks)


def generate_summary(transcripts: list[NormalizedTranscriptResult], context_prompt: str, config: GeminiConfig) -> str:
    errors = validate_environment(config)
    if errors:
        for error in errors:
            log.warning(f"  [gemini] {error}")
        return _fallback_summary_all(transcripts)

    model = config.summary_model or GEMINI_DEFAULT_SUMMARY_MODEL
    prompt = _build_summary_prompt(transcripts, context_prompt)
    log.info(f"  [gemini] Gerando resumo com modelo: {model} | ~{len(prompt.split())} palavras")

    try:
        from google.genai import types

        client = _client(config)
        response = _call_with_retry(
            lambda: client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(http_options=_timeout_options()),
            ),
            label="Resumo",
        )
        text = (response.text or "").strip()
        if text:
            log.ok("  [gemini] Resumo gerado com sucesso")
            return text
        log.warning("  [gemini] Resposta vazia do Gemini. Usando fallback local.")
    except Exception as exc:
        log.exception("  [gemini] Falha ao gerar resumo", exc=exc)

    return _fallback_summary_all(transcripts)


def _fallback_summary_all(transcripts: list[NormalizedTranscriptResult]) -> str:
    if not transcripts:
        return "_Resumo indisponivel: nenhuma transcricao encontrada._"

    lines = [
        "## Resumo Consolidado (gerado localmente)",
        "",
        "> O Gemini nao pode gerar o resumo. O texto abaixo foi montado a partir do conteudo transcrito.",
        "",
    ]
    for index, transcript in enumerate(transcripts, start=1):
        if len(transcripts) > 1:
            lines.extend([f"### Arquivo {index} - {transcript.source_path.name}", ""])
        words = transcript.text.split()
        snippet = " ".join(words[:300])
        if len(words) > 300:
            snippet += "..."
        lines.extend(["**Trecho inicial:**", "", f"> {snippet or '_Sem conteudo_'}", ""])
    return "\n".join(lines)
