"""
pipeline.py - Orquestrador do pipeline e construtores de Markdown.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

try:
    from . import whisper_client
    from .config import PROVIDER_WHISPER
    from .converter import check_ffmpeg, convert_to_mp4
    from .gemini_client import generate_summary, transcribe_path, validate_environment
    from .logger import log
    from .models import NormalizedTranscriptResult, PipelineRequest
    from .utils import (
        build_cache_key,
        fmt_time,
        get_session_dir,
        load_cache,
        save_cache,
        transcript_from_cache,
        transcript_to_cache,
    )
except ImportError:
    import whisper_client  # type: ignore
    from config import PROVIDER_WHISPER  # type: ignore
    from converter import check_ffmpeg, convert_to_mp4  # type: ignore
    from gemini_client import generate_summary, transcribe_path, validate_environment  # type: ignore
    from logger import log  # type: ignore
    from models import NormalizedTranscriptResult, PipelineRequest  # type: ignore
    from utils import (  # type: ignore
        build_cache_key,
        fmt_time,
        get_session_dir,
        load_cache,
        save_cache,
        transcript_from_cache,
        transcript_to_cache,
    )


def build_file_markdown(transcript: NormalizedTranscriptResult) -> str:
    words = len((transcript.text or "").split())
    speakers = len({item.speaker for item in transcript.utterances})
    lines = [
        f"# Transcricao: {transcript.source_path.name}",
        "",
        "---",
        "",
        "## Metadados",
        "",
        "| Campo | Valor |",
        "|---|---|",
        f"| Arquivo | `{transcript.source_path.name}` |",
        f"| Idioma detectado | `{transcript.language_code or 'n/d'}` |",
        f"| Duracao | {fmt_time(transcript.audio_duration_seconds)} |",
        f"| Palavras | {words:,} |",
        f"| Falantes | {speakers or 0} |",
        f"| Provider de transcricao | `{transcript.provider_label}` |",
        f"| Modelo | `{transcript.model or 'default'}` |",
        "",
        "---",
        "",
    ]

    lines.extend(["## Transcricao Completa", ""])
    if transcript.utterances:
        current_speaker = None
        for item in transcript.utterances:
            speaker = item.speaker or "Falante"
            if speaker != current_speaker:
                current_speaker = speaker
                lines.append(f"\n**{speaker}** - `{fmt_time(item.start_ms / 1000)}`\n")
            lines.append(item.text)
    else:
        lines.append(transcript.text or "_Sem texto._")

    lines.extend(["", "---", ""])
    lines.append("_Gerado por Papagaio Transcritor_")
    return "\n".join(lines)


def build_consolidated_markdown(
    transcripts: list[NormalizedTranscriptResult],
    summary: str,
    request: PipelineRequest,
) -> str:
    total_duration = sum(item.audio_duration_seconds or 0 for item in transcripts)
    total_words = sum(len((item.text or "").split()) for item in transcripts)
    heading = request.title.strip() or f"Relatorio Consolidado - {len(transcripts)} arquivo(s)"
    lines = [
        f"# {heading}",
        f"_Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}_",
        "",
        "---",
        "",
    ]

    if request.context_prompt.strip():
        lines.extend(["## Contexto Fornecido", "", f"> {request.context_prompt.strip()}", "", "---", ""])

    lines.extend(
        [
            "## Metadados Gerais",
            "",
            "| Campo | Valor |",
            "|---|---|",
            f"| Arquivos processados | {len(transcripts)} |",
            f"| Idioma solicitado | `{request.lang_code or 'auto'}` |",
            f"| Duracao total | {fmt_time(total_duration)} |",
            f"| Total de palavras | {total_words:,} |",
            f"| Provider de transcricao | `{transcripts[0].provider_label if transcripts else request.provider}` |",
            f"| Modelo de transcricao | `{transcription_model_for(request) or 'default'}` |",
            f"| Modelo de resumo | `{request.gemini.summary_model if request.summary_enabled else 'desativado'}` |",
            "",
            "---",
            "",
            "## Arquivos Incluidos",
            "",
        ]
    )
    for index, transcript in enumerate(transcripts, start=1):
        lines.append(
            f"{index}. **{transcript.source_path.name}** - {fmt_time(transcript.audio_duration_seconds)}, "
            f"{len((transcript.text or '').split()):,} palavras"
        )

    if summary.strip():
        lines.extend(["", "---", "", "## Analise e Resumo Unificado", "", summary.strip(), "", "---", ""])
    else:
        lines.extend(["", "---", "", "_Geracao de resumo desativada nesta sessao._", "", "---", ""])

    if len(transcripts) > 1:
        lines.extend(["## Relatorios Individuais", ""])
        for transcript in transcripts:
            lines.append(f"- [{transcript.source_path.name}]({transcript.source_path.stem}.md)")
        lines.extend(["", "---", ""])

    lines.append("_Gerado automaticamente por Papagaio Transcritor_")
    return "\n".join(lines)


def transcription_model_for(request: PipelineRequest) -> str:
    """Modelo efetivamente usado na transcricao, conforme o provider escolhido."""
    if request.provider == PROVIDER_WHISPER:
        return request.whisper.model
    return request.gemini.transcription_model


def _validate_runtime(request: PipelineRequest):
    if request.provider == PROVIDER_WHISPER:
        # O Whisper decodifica o audio com PyAV, entao nao depende do binario do ffmpeg.
        # O resumo continua no Gemini quando houver API key; sem ela, generate_summary
        # ja cai sozinho no fallback local.
        errors = whisper_client.validate_environment(request.whisper)
    else:
        errors = validate_environment(request.gemini)
        if not check_ffmpeg():
            errors.append("ffmpeg nao encontrado. Necessario para normalizar os arquivos antes do envio ao Gemini.")
    if errors:
        raise RuntimeError("\n".join(errors))


def _process_file(
    file_path: Path,
    idx: int,
    total: int,
    request: PipelineRequest,
    cache: dict,
    tmpdir: Path,
) -> NormalizedTranscriptResult:
    log.info(f"\n{'=' * 50}")
    log.info(f"Arquivo [{idx}/{total}]: {file_path.name}")
    log.info(f"{'=' * 50}")

    cache_key = build_cache_key(file_path, request.provider, transcription_model_for(request))
    cache_record = cache.get(cache_key)
    if cache_record:
        log.ok(f"  [cache] Reutilizando transcricao existente para {file_path.name}")
        return transcript_from_cache(cache_record, file_path)

    if request.provider == PROVIDER_WHISPER:
        transcript = whisper_client.transcribe_path(file_path, request.lang_code, request.whisper)
    else:
        converted_path, mime_type = convert_to_mp4(file_path, tmpdir)
        transcript = transcribe_path(converted_path, request.lang_code, request.gemini, mime_type=mime_type)
    transcript.source_path = file_path
    return transcript


def run_pipeline(request: PipelineRequest, on_done, on_error):
    try:
        log.start_session(request.title or "sessao")
        log.info(f"Pipeline iniciado: {len(request.file_paths)} arquivo(s)")
        log.info(f"Transcricao: {request.provider} | modelo={transcription_model_for(request)}")
        if request.provider == PROVIDER_WHISPER:
            log.info(f"Diarizacao: {'pyannote' if request.whisper.diarization else 'desligada'}")
        if request.summary_enabled:
            log.info(f"Resumo: gemini | modelo={request.gemini.summary_model}")
        else:
            log.info("Resumo: desativado")
        log.info(f"Pasta de saida: {request.output_dir}")

        _validate_runtime(request)

        session_dir = get_session_dir(request.output_dir, request.title or "sessao")
        cache = load_cache(session_dir)
        transcripts: list[NormalizedTranscriptResult] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            for index, file_path in enumerate(request.file_paths, start=1):
                transcript = _process_file(file_path, index, len(request.file_paths), request, cache, Path(tmpdir))
                transcripts.append(transcript)

                md_path = session_dir / f"{file_path.stem}.md"
                md_path.write_text(build_file_markdown(transcript), encoding="utf-8")
                log.ok(f"  [salvo] {md_path.name}")

                cache_key = build_cache_key(file_path, request.provider, transcription_model_for(request))
                cache[cache_key] = transcript_to_cache(transcript)
                save_cache(session_dir, cache)

        log.info(f"\n{'=' * 50}")
        if request.summary_enabled:
            log.info("Gerando resumo consolidado...")
            summary = generate_summary(transcripts, request.context_prompt, request.gemini)
        else:
            log.info("Resumo desativado; gerando so o indice consolidado.")
            summary = ""

        consolidated_path = session_dir / "_consolidado.md"
        consolidated_path.write_text(
            build_consolidated_markdown(transcripts, summary, request),
            encoding="utf-8",
        )
        log.ok(f"Consolidado salvo: {consolidated_path}")

        if log.log_file:
            log.info(f"Log completo em: {log.log_file}")

        on_done(session_dir, consolidated_path)
    except Exception as exc:
        log.exception("Pipeline falhou", exc=exc)
        on_error(str(exc))
