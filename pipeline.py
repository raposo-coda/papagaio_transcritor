"""
pipeline.py — Orquestrador do pipeline e construtores de Markdown
"""

import tempfile
from datetime import datetime
from pathlib import Path

import assemblyai as aai

from .config import VIDEO_EXTS
from .logger import log
from .transcriber import (
    check_ffmpeg, get_session_dir, load_cache, save_cache,
    process_file, fmt_time,
)
from .summarizer import generate_summary


# ── Markdown por arquivo ──────────────────────────────────────────────────────

def build_file_markdown(transcript: aai.Transcript, file_path: Path, lang: str) -> str:
    dur      = fmt_time(transcript.audio_duration or 0)
    words    = len((transcript.text or "").split())
    speakers = len({u.speaker for u in (transcript.utterances or [])})

    lines = [
        f"# Transcricao: {file_path.name}", "",
        "---", "",
        "## Metadados", "",
        "| Campo | Valor |", "|---|---|",
        f"| Arquivo | `{file_path.name}` |",
        f"| Idioma detectado | `{lang}` |",
        f"| Duracao | {dur} |",
        f"| Palavras | {words:,} |",
        f"| Falantes | {speakers} |",
        "", "---", "",
    ]

    if transcript.chapters:
        lines += ["## Capitulos", ""]
        for ch in transcript.chapters:
            s = fmt_time(ch.start / 1000)
            e = fmt_time(ch.end / 1000)
            lines += [f"### `{s}` → `{e}` — {ch.headline}", "", f"> {ch.summary}", ""]
        lines += ["---", ""]

    lines += ["## Transcricao Completa", ""]
    if transcript.utterances:
        cur = None
        for utt in transcript.utterances:
            spk = f"Falante {utt.speaker}"
            ts  = fmt_time(utt.start / 1000)
            if spk != cur:
                cur = spk
                lines.append(f"\n**{spk}** — `{ts}`\n")
            lines.append(utt.text)
    else:
        lines.append(transcript.text or "_Sem texto._")

    lines += ["", "---", ""]

    if transcript.entities:
        lines += ["## Entidades Detectadas", ""]
        emap: dict = {}
        for e in transcript.entities:
            emap.setdefault(e.entity_type, set()).add(e.text)
        for et, vals in sorted(emap.items()):
            lines.append(f"- **{et}**: {', '.join(sorted(vals))}")
        lines += ["", "---", ""]

    lines.append("_Gerado por TranscritorIA + AssemblyAI_")
    return "\n".join(lines)


# ── Markdown consolidado ──────────────────────────────────────────────────────

def build_consolidated_markdown(
    transcripts: list,
    file_paths: list,
    summary: str,
    context_prompt: str,
    lang: str,
    title: str = "",
) -> str:
    now         = datetime.now().strftime("%d/%m/%Y %H:%M")
    n           = len(transcripts)
    total_dur   = sum(t.audio_duration or 0 for t in transcripts)
    total_words = sum(len((t.text or "").split()) for t in transcripts)
    heading     = title.strip() or f"Relatorio Consolidado — {n} arquivo(s)"

    lines = [
        f"# {heading}",
        f"_Gerado em {now}_", "",
        "---", "",
    ]

    if context_prompt.strip():
        lines += ["## Contexto Fornecido", "", f"> {context_prompt.strip()}", "", "---", ""]

    lines += [
        "## Metadados Gerais", "",
        "| Campo | Valor |", "|---|---|",
        f"| Arquivos processados | {n} |",
        f"| Idioma | `{lang}` |",
        f"| Duracao total | {fmt_time(total_dur)} |",
        f"| Total de palavras | {total_words:,} |",
        "", "---", "",
        "## Arquivos Incluidos", "",
    ]
    for i, (t, p) in enumerate(zip(transcripts, file_paths), 1):
        dur   = fmt_time(t.audio_duration or 0)
        words = len((t.text or "").split())
        lines.append(f"{i}. **{p.name}** — {dur}, {words:,} palavras")

    lines += [
        "", "---", "",
        "## Analise e Resumo Unificado (IA)", "",
        summary.strip(), "",
        "---", "",
    ]

    if n > 1:
        lines += ["## Relatorios Individuais", ""]
        for p in file_paths:
            lines.append(f"- [{p.name}]({p.stem}.md)")
        lines += ["", "---", ""]

    lines.append("_Gerado automaticamente por TranscritorIA + AssemblyAI_")
    return "\n".join(lines)


# ── Orquestrador principal ────────────────────────────────────────────────────

def run_pipeline(
    file_paths: list,
    lang_code: str,
    api_key: str,
    context_prompt: str,
    title: str,
    output_dir: Path,
    on_done,
    on_error,
    provider_id: str = "lemur",
    ai_model: str = "",
    ai_key: str = "",
    ai_url: str = "",
):
    """
    Orquestra todo o pipeline em uma thread separada:
      1. Verifica ffmpeg
      2. Cria/reutiliza pasta de sessão
      3. Para cada arquivo: checa cache → extrai áudio → transcreve → salva MD
      4. Gera resumo consolidado via provider de IA
      5. Salva _consolidado.md
    """
    try:
        log.start_session(title or "sessao")
        log.info(f"Pipeline iniciado: {len(file_paths)} arquivo(s)")
        log.info(f"Provider IA: {provider_id} | Modelo: {ai_model or 'default'}")
        log.info(f"Pasta de saida: {output_dir}")

        if not check_ffmpeg():
            raise RuntimeError(
                "ffmpeg nao encontrado.\n"
                "Ubuntu/Debian:  sudo apt install ffmpeg\n"
                "macOS:          brew install ffmpeg\n"
                "Windows:        https://ffmpeg.org/download.html"
            )

        session_dir = get_session_dir(output_dir, title or "sessao")
        cache       = load_cache(session_dir)

        existing = [f for f in cache if f in {fp.stem for fp in file_paths}]
        if existing:
            log.info(f"Cache: {len(existing)} arquivo(s) ja transcritos: {existing}")

        transcripts = []
        n = len(file_paths)

        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, fp in enumerate(file_paths, 1):
                transcript = process_file(fp, idx, n, api_key, session_dir, cache, tmpdir)
                transcripts.append(transcript)

                # Salvar MD individual
                md_path    = session_dir / (fp.stem + ".md")
                md_content = build_file_markdown(transcript, fp, lang_code)
                md_path.write_text(md_content, encoding="utf-8")
                log.ok(f"  [salvo] {md_path.name}")

                # Atualizar cache
                cache[fp.stem] = transcript.id
                save_cache(session_dir, cache)
                log.debug(f"  [cache] id={transcript.id} salvo para '{fp.stem}'")

        # Resumo consolidado
        log.info(f"\n{'='*50}")
        log.info("Gerando resumo consolidado...")
        summary = generate_summary(
            transcripts, context_prompt,
            provider_id=provider_id,
            ai_model=ai_model,
            ai_key=ai_key,
            ai_url=ai_url,
        )

        consolidated = build_consolidated_markdown(
            transcripts, file_paths, summary, context_prompt, lang_code, title
        )
        out = session_dir / "_consolidado.md"
        out.write_text(consolidated, encoding="utf-8")
        log.ok(f"Consolidado salvo: {out}")

        if log.log_file:
            log.info(f"Log completo em: {log.log_file}")

        on_done(session_dir, out)

    except Exception as exc:
        log.exception("Pipeline falhou", exc=exc)
        on_error(str(exc))
