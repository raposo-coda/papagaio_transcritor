"""
pipeline.py - Orquestrador do pipeline e construtores de Markdown.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

try:
    from . import local_client
    from .config import MODE_LOCAL
    from .converter import check_ffmpeg, convert_to_mp4, convert_to_wav16k
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
    import local_client  # type: ignore
    from config import MODE_LOCAL  # type: ignore
    from converter import check_ffmpeg, convert_to_mp4, convert_to_wav16k  # type: ignore
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


def _tem_diarizacao(transcript: NormalizedTranscriptResult) -> bool:
    """A transcricao saiu com falantes separados?

    Prefere o flag gravado pelo provider, mas cai para os proprios rotulos - assim
    entradas antigas de cache, gravadas antes do flag existir, ainda acertam.
    """
    if (transcript.raw_metadata or {}).get("diarization"):
        return True
    rotulos = {item.speaker for item in transcript.utterances}
    return len(rotulos) > 1 or bool(rotulos - {"Falante", ""})


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

    if transcript.provider_id == local_client.PROVIDER_ID:
        aviso = [
            "> Transcrito **offline, nesta maquina**. Nenhum audio, video ou texto foi enviado",
            "> para a internet.",
        ]
        if _tem_diarizacao(transcript):
            aviso.extend(
                [
                    "> Os falantes foram separados pela voz, tambem offline. O aplicativo distingue",
                    "> vozes diferentes, mas nao sabe os nomes das pessoas - troque `Falante 1`,",
                    "> `Falante 2`... pelos nomes reais se quiser.",
                ]
            )
        else:
            aviso.append(
                "> Sem separacao por falante nesta execucao: os trechos aparecem em ordem "
                "cronologica sob um rotulo unico."
            )
        lines.extend(aviso + [""])

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

    if request.mode == MODE_LOCAL:
        lines.extend(
            [
                "> **Processado em modo local.** A transcricao rodou inteiramente neste computador,",
                "> com o modelo Whisper. Nenhum arquivo e nenhum texto saiu da maquina.",
                "",
                "---",
                "",
            ]
        )

    if request.context_prompt.strip():
        lines.extend(["## Contexto Fornecido", "", f"> {request.context_prompt.strip()}", "", "---", ""])

    linhas_extra = []
    if request.mode == MODE_LOCAL:
        linha_transcricao = f"| Transcricao | `whisper-{request.local.model_size}` (local, {request.local.device}) |"
        linha_resumo = "| Resumo | panorama estatistico local (sem IA) |"
        diarizou = any(_tem_diarizacao(item) for item in transcripts)
        if diarizou:
            quantos = request.local.num_speakers
            como = f"{quantos} falantes (definido por voce)" if quantos else "numero automatico"
            linhas_extra.append(f"| Separacao de falantes | local, offline - {como} |")
        else:
            linhas_extra.append("| Separacao de falantes | nao aplicada |")
    else:
        linha_transcricao = f"| Transcricao | `{request.gemini.transcription_model}` (Google Gemini) |"
        linha_resumo = f"| Resumo | `{request.gemini.summary_model}` (Google Gemini) |"

    lines.extend(
        [
            "## Metadados Gerais",
            "",
            "| Campo | Valor |",
            "|---|---|",
            f"| Modo de processamento | {'Local (offline)' if request.mode == MODE_LOCAL else 'Nuvem (Google Gemini)'} |",
            f"| Arquivos processados | {len(transcripts)} |",
            f"| Idioma solicitado | `{request.lang_code or 'auto'}` |",
            f"| Duracao total | {fmt_time(total_duration)} |",
            f"| Total de palavras | {total_words:,} |",
            linha_transcricao,
            linha_resumo,
            *linhas_extra,
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

    titulo_resumo = (
        "## Panorama do Conteudo (local)" if request.mode == MODE_LOCAL else "## Analise e Resumo Unificado"
    )
    lines.extend(["", "---", "", titulo_resumo, "", summary.strip(), "", "---", ""])

    if len(transcripts) > 1:
        lines.extend(["## Relatorios Individuais", ""])
        for transcript in transcripts:
            lines.append(f"- [{transcript.source_path.name}]({transcript.source_path.stem}.md)")
        lines.extend(["", "---", ""])

    lines.append("_Gerado automaticamente por Papagaio Transcritor_")
    return "\n".join(lines)


def _is_local(request: PipelineRequest) -> bool:
    return request.mode == MODE_LOCAL


def _cache_identity(request: PipelineRequest) -> tuple[str, str]:
    """
    (provider, modelo) usados na chave de cache - separa nuvem de local.

    A diarizacao entra no identificador porque muda o resultado: sem isso,
    ligar/desligar os falantes ou trocar a quantidade devolveria o transcript
    antigo do cache.
    """
    if _is_local(request):
        if not request.local.diarize:
            sufixo = "+nodiar"
        elif request.local.num_speakers and request.local.num_speakers > 0:
            sufixo = f"+diar{request.local.num_speakers}"
        else:
            sufixo = "+diarauto"
        return local_client.PROVIDER_ID, f"whisper-{request.local.model_size}{sufixo}"
    return "gemini", request.gemini.transcription_model


def _validate_runtime(request: PipelineRequest):
    if _is_local(request):
        errors = local_client.validate_environment(request.local)
        if not check_ffmpeg():
            errors.append("ffmpeg nao encontrado. Necessario para preparar os arquivos antes da transcricao.")
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

    provider, modelo = _cache_identity(request)
    cache_key = build_cache_key(file_path, modelo, provider=provider)
    cache_record = cache.get(cache_key)
    if cache_record:
        log.ok(f"  [cache] Reutilizando transcricao existente para {file_path.name}")
        return transcript_from_cache(cache_record, file_path)

    if _is_local(request):
        # O Whisper e a separacao de falantes trabalham em 16 kHz mono. Preparar
        # o audio direto nesse formato evita reencodar video que seria descartado.
        converted_path = convert_to_wav16k(file_path, tmpdir)
        transcript = local_client.transcribe_path(converted_path, request.lang_code, request.local)
    else:
        converted_path, mime_type = convert_to_mp4(file_path, tmpdir)
        transcript = transcribe_path(converted_path, request.lang_code, request.gemini, mime_type=mime_type)

    transcript.source_path = file_path
    return transcript


def run_pipeline(request: PipelineRequest, on_done, on_error):
    try:
        log.start_session(request.title or "sessao")
        log.info(f"Pipeline iniciado: {len(request.file_paths)} arquivo(s)")
        if _is_local(request):
            log.ok(
                f"MODO LOCAL: transcricao offline com whisper-{request.local.model_size} "
                f"em {request.local.device.upper()}. Nenhum audio, video ou texto sai deste computador."
            )
            if request.local.diarize:
                quantos = request.local.num_speakers
                alvo = f"{quantos} falantes" if quantos else "numero automatico de falantes"
                log.info(f"Separacao de falantes: ligada, offline ({alvo}).")
            else:
                log.info("Separacao de falantes: desligada.")
            log.info("Resumo: panorama estatistico local (sem IA, sem rede).")
        else:
            log.warning(
                "MODO NUVEM: cada arquivo sera enviado ao Google Gemini para ser transcrito."
            )
            log.info(f"Transcricao: gemini | modelo={request.gemini.transcription_model}")
            log.info(f"Resumo: gemini | modelo={request.gemini.summary_model}")
        log.info(f"Pasta de saida: {request.output_dir}")

        _validate_runtime(request)

        session_dir = get_session_dir(request.output_dir, request.title, request.job_id)
        cache = load_cache(session_dir)
        transcripts: list[NormalizedTranscriptResult] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            for index, file_path in enumerate(request.file_paths, start=1):
                transcript = _process_file(file_path, index, len(request.file_paths), request, cache, Path(tmpdir))
                transcripts.append(transcript)

                md_path = session_dir / f"{file_path.stem}.md"
                md_path.write_text(build_file_markdown(transcript), encoding="utf-8")
                log.ok(f"  [salvo] {md_path.name}")

                provider, modelo = _cache_identity(request)
                cache_key = build_cache_key(file_path, modelo, provider=provider)
                cache[cache_key] = transcript_to_cache(transcript)
                save_cache(session_dir, cache)

        log.info(f"\n{'=' * 50}")
        if _is_local(request):
            log.info("Montando panorama consolidado localmente (sem IA)...")
            summary = local_client.generate_summary(transcripts, request.context_prompt)
        else:
            log.info("Gerando resumo consolidado...")
            summary = generate_summary(transcripts, request.context_prompt, request.gemini)

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
