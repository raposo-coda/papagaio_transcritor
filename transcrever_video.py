#!/usr/bin/env python3
"""
transcrever_video.py
====================
Extrai áudio de vídeo, transcreve na nuvem (AssemblyAI),
realiza diarização de falantes e gera resumo em Markdown.

Dependências:
    pip install assemblyai

Sistema:
    ffmpeg deve estar instalado (sudo apt install ffmpeg)

Uso:
    python transcrever_video.py <arquivo_de_video> [--lang pt]
    python transcrever_video.py reuniao.mp4 --lang pt
    python transcrever_video.py lecture.mkv --lang en

Obter API key gratuita (5h/mês + LeMUR):
    https://www.assemblyai.com/
"""

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import timedelta

# ---------------------------------------------------------------------------
# Verificação de dependência
# ---------------------------------------------------------------------------
try:
    import assemblyai as aai
except ImportError:
    print("[ERRO] Instale a dependência: pip install assemblyai")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
SUPPORTED_VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v"}
SUPPORTED_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}

LANG_MAP = {
    "pt": "pt",
    "en": "en",
    "es": "es",
    "fr": "fr",
    "de": "de",
    "it": "it",
    "ja": "ja",
    "ko": "ko",
    "zh": "zh",
}


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def check_ffmpeg():
    """Verifica se o ffmpeg está disponível no sistema."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def format_timestamp(seconds: float) -> str:
    """Converte segundos em HH:MM:SS."""
    td = timedelta(seconds=int(seconds))
    total_seconds = int(td.total_seconds())
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Extração de áudio
# ---------------------------------------------------------------------------

def extract_audio(video_path: Path, output_path: Path) -> Path:
    """
    Usa ffmpeg para extrair e converter o áudio do vídeo.
    Saída: mono 16kHz WAV (formato ideal para STT, menor tamanho).
    """
    print(f"[1/4] Extraindo áudio de '{video_path.name}'...")

    cmd = [
        "ffmpeg",
        "-y",                     # sobrescrever sem perguntar
        "-i", str(video_path),    # entrada
        "-vn",                    # sem vídeo
        "-acodec", "pcm_s16le",   # WAV 16-bit
        "-ar", "16000",           # 16 kHz (suficiente para voz)
        "-ac", "1",               # mono
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERRO] ffmpeg falhou:\n{result.stderr}")
        sys.exit(1)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"    Áudio extraído: {output_path.name} ({size_mb:.1f} MB)")
    return output_path


# ---------------------------------------------------------------------------
# Transcrição + Diarização via AssemblyAI
# ---------------------------------------------------------------------------

def transcribe(audio_path: Path, language_code: str, api_key: str) -> aai.Transcript:
    """
    Envia o áudio para a AssemblyAI e aguarda a transcrição.
    Speaker diarization habilitada por padrão.
    """
    print("[2/4] Enviando áudio para AssemblyAI...")
    aai.settings.api_key = api_key

    config = aai.TranscriptionConfig(
        language_code=language_code,
        speaker_labels=True,          # diarização
        speakers_expected=None,       # detectar automaticamente
        punctuate=True,
        format_text=True,
        auto_chapters=True,           # capítulos automáticos (tópicos)
        entity_detection=True,        # extrai entidades (nomes, datas, etc.)
        sentiment_analysis=False,     # desativado para economizar quota
    )

    transcriber = aai.Transcriber()
    print("[3/4] Transcrevendo na nuvem (aguarde)...")
    transcript = transcriber.transcribe(str(audio_path), config=config)

    if transcript.status == aai.TranscriptStatus.error:
        print(f"[ERRO] Transcrição falhou: {transcript.error}")
        sys.exit(1)

    print("    Transcrição concluída.")
    return transcript


# ---------------------------------------------------------------------------
# Geração do resumo com LeMUR (IA generativa da AssemblyAI)
# ---------------------------------------------------------------------------

def generate_summary_lemur(transcript: aai.Transcript) -> str:
    """
    Usa o LeMUR da AssemblyAI para gerar um resumo estruturado.
    Incluído no plano gratuito com limite mensal.
    """
    print("[4/4] Gerando resumo com LeMUR (IA)...")

    prompt = (
        "Analise esta transcrição e produza um relatório estruturado em Markdown contendo:\n"
        "1. **Resumo Executivo** – parágrafo curto com o tema central\n"
        "2. **Pontos-chave** – lista dos principais assuntos discutidos\n"
        "3. **Decisões e Ações** – compromissos, decisões ou próximos passos mencionados\n"
        "4. **Participantes** – perfil de cada falante identificado (se houver)\n"
        "5. **Entidades Mencionadas** – nomes, datas, locais ou organizações relevantes\n"
        "6. **Conclusão** – encerramento sintético\n\n"
        "Use Markdown limpo, sem blocos de código. Seja direto e objetivo."
    )

    try:
        result = transcript.lemur.task(
            prompt=prompt,
            final_model=aai.LemurModel.claude3_5_haiku,
            max_output_size=2000,
        )
        return result.response
    except Exception as e:
        print(f"    [AVISO] LeMUR indisponível ({e}). Gerando resumo local.")
        return _fallback_summary(transcript)


def _fallback_summary(transcript: aai.Transcript) -> str:
    """Resumo básico gerado localmente caso LeMUR falhe."""
    chapters = transcript.chapters or []
    entities = transcript.entities or []

    lines = ["## Resumo (gerado localmente)\n"]

    if chapters:
        lines.append("### Tópicos identificados\n")
        for ch in chapters:
            ts = format_timestamp(ch.start / 1000)
            lines.append(f"- `{ts}` — **{ch.headline}**: {ch.summary}")

    if entities:
        lines.append("\n### Entidades Mencionadas\n")
        entity_map: dict = {}
        for e in entities:
            entity_map.setdefault(e.entity_type, []).append(e.text)
        for etype, values in entity_map.items():
            unique = list(dict.fromkeys(values))
            lines.append(f"- **{etype}**: {', '.join(unique)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Formatação do Markdown final
# ---------------------------------------------------------------------------

def build_markdown(
    transcript: aai.Transcript,
    summary: str,
    video_path: Path,
    language: str,
) -> str:
    """Monta o documento Markdown completo."""

    duration_s = (transcript.audio_duration or 0)
    duration_fmt = format_timestamp(duration_s)
    word_count = len((transcript.text or "").split())

    # ── Cabeçalho ──────────────────────────────────────────────────────────
    lines = [
        f"# Transcrição: {video_path.name}",
        "",
        "---",
        "",
        "## Metadados",
        "",
        f"| Campo | Valor |",
        f"|---|---|",
        f"| Arquivo | `{video_path.name}` |",
        f"| Idioma | `{language}` |",
        f"| Duração | {duration_fmt} |",
        f"| Palavras transcritas | {word_count:,} |",
        f"| Falantes detectados | {_count_speakers(transcript)} |",
        "",
        "---",
        "",
    ]

    # ── Resumo gerado por IA ───────────────────────────────────────────────
    lines += [
        "## Análise e Resumo (IA)",
        "",
        summary.strip(),
        "",
        "---",
        "",
    ]

    # ── Capítulos automáticos ─────────────────────────────────────────────
    if transcript.chapters:
        lines += ["## Capítulos / Tópicos", ""]
        for ch in transcript.chapters:
            ts_start = format_timestamp(ch.start / 1000)
            ts_end = format_timestamp(ch.end / 1000)
            lines.append(f"### `{ts_start}` → `{ts_end}` — {ch.headline}")
            lines.append("")
            lines.append(f"> {ch.summary}")
            lines.append("")
        lines += ["---", ""]

    # ── Transcrição com diarização ────────────────────────────────────────
    lines += ["## Transcrição Completa", ""]

    if transcript.utterances:
        current_speaker = None
        for utt in transcript.utterances:
            ts = format_timestamp(utt.start / 1000)
            speaker = f"Falante {utt.speaker}"
            if speaker != current_speaker:
                current_speaker = speaker
                lines.append(f"\n**{speaker}** — `{ts}`\n")
            lines.append(utt.text)
    else:
        # Sem diarização: texto corrido
        lines.append(transcript.text or "_Nenhum texto transcrito._")

    lines += ["", "---", ""]

    # ── Entidades ─────────────────────────────────────────────────────────
    if transcript.entities:
        lines += ["## Entidades Detectadas", ""]
        entity_map: dict = {}
        for e in transcript.entities:
            entity_map.setdefault(e.entity_type, set()).add(e.text)
        for etype, values in sorted(entity_map.items()):
            lines.append(f"- **{etype}**: {', '.join(sorted(values))}")
        lines += ["", "---", ""]

    lines += [
        "_Gerado automaticamente por `transcrever_video.py` + AssemblyAI_",
    ]

    return "\n".join(lines)


def _count_speakers(transcript: aai.Transcript) -> int:
    if not transcript.utterances:
        return 0
    return len({u.speaker for u in transcript.utterances})


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Transcreve vídeo/áudio com diarização e gera resumo Markdown.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("arquivo", help="Caminho para o arquivo de vídeo ou áudio")
    parser.add_argument(
        "--lang", default="pt", choices=list(LANG_MAP.keys()),
        help="Idioma do áudio (padrão: pt)"
    )
    parser.add_argument(
        "--api-key", default=os.environ.get("ASSEMBLYAI_API_KEY", ""),
        help="API key AssemblyAI (ou defina ASSEMBLYAI_API_KEY no ambiente)"
    )
    parser.add_argument(
        "--saida", default=None,
        help="Caminho do arquivo Markdown de saída (padrão: mesmo nome do vídeo)"
    )
    args = parser.parse_args()

    # ── Validações ──────────────────────────────────────────────────────────
    if not args.api_key:
        print(
            "[ERRO] API key não encontrada.\n"
            "  Opção 1: python transcrever_video.py video.mp4 --api-key SUA_KEY\n"
            "  Opção 2: export ASSEMBLYAI_API_KEY=SUA_KEY\n"
            "  Cadastre-se grátis em: https://www.assemblyai.com/"
        )
        sys.exit(1)

    input_path = Path(args.arquivo).expanduser().resolve()
    if not input_path.exists():
        print(f"[ERRO] Arquivo não encontrado: {input_path}")
        sys.exit(1)

    ext = input_path.suffix.lower()
    is_video = ext in SUPPORTED_VIDEO_EXTS
    is_audio = ext in SUPPORTED_AUDIO_EXTS

    if not is_video and not is_audio:
        print(f"[ERRO] Formato não suportado: '{ext}'")
        sys.exit(1)

    # ── Extração de áudio (só se for vídeo) ───────────────────────────────
    with tempfile.TemporaryDirectory() as tmpdir:
        if is_video:
            if not check_ffmpeg():
                print(
                    "[ERRO] ffmpeg não encontrado.\n"
                    "  Ubuntu/Debian: sudo apt install ffmpeg\n"
                    "  macOS: brew install ffmpeg"
                )
                sys.exit(1)
            audio_path = Path(tmpdir) / (input_path.stem + ".wav")
            extract_audio(input_path, audio_path)
        else:
            # Áudio direto — sem extração
            audio_path = input_path
            print(f"[1/4] Arquivo de áudio detectado: '{input_path.name}' (sem extração)")

        # ── Transcrição ───────────────────────────────────────────────────
        language = LANG_MAP[args.lang]
        transcript = transcribe(audio_path, language, args.api_key)

        # ── Resumo por IA ─────────────────────────────────────────────────
        summary = generate_summary_lemur(transcript)

        # ── Markdown final ────────────────────────────────────────────────
        md_content = build_markdown(transcript, summary, input_path, args.lang)

        output_path = Path(args.saida) if args.saida else input_path.with_suffix(".md")
        output_path.write_text(md_content, encoding="utf-8")

        print(f"\n✅ Concluído! Relatório salvo em: {output_path}")
        print(f"   Falantes detectados : {_count_speakers(transcript)}")
        print(f"   Duração processada  : {format_timestamp(transcript.audio_duration or 0)}")
        print(f"   Palavras transcritas: {len((transcript.text or '').split()):,}")


if __name__ == "__main__":
    main()
