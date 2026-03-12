#!/usr/bin/env python3
"""
transcrever_video.py  — GUI Edition
=====================================
Interface gráfica para transcrição de vídeo/áudio com IA na nuvem.

Dependências:
    pip install assemblyai

Sistema:
    ffmpeg  →  sudo apt install ffmpeg   (Ubuntu/Debian)
               brew install ffmpeg       (macOS)

API key gratuita (5h/mês):
    https://www.assemblyai.com/
"""

# ── Stdlib ────────────────────────────────────────────────────────────────────
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

# ── Terceiros ─────────────────────────────────────────────────────────────────
try:
    import assemblyai as aai
except ImportError:
    print("[ERRO] Execute: pip install assemblyai")
    sys.exit(1)

# ══════════════════════════════════════════════════════════════════════════════
# Constantes
# ══════════════════════════════════════════════════════════════════════════════

APP_NAME    = "TranscritorIA"
APP_VERSION = "2.0"
CONFIG_FILE = Path.home() / ".transcritor_config.json"

SUPPORTED_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v",
    ".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac",
}
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".m4v"}

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

# Provedores de IA para geração de resumo
# needs_key: se exige API key própria | needs_url: se exige base URL (Ollama)
PROVIDERS = {
    "AssemblyAI LeMUR": {
        "id": "lemur",
        "needs_key": False,
        "needs_url": False,
        "default_model": "(automático)",
        "hint": "Usa a mesma key da AssemblyAI. Grátis no plano atual.",
    },
    "Anthropic (Claude)": {
        "id": "anthropic",
        "needs_key": True,
        "needs_url": False,
        "default_model": "claude-haiku-4-5",
        "hint": "Requer pip install anthropic  |  console.anthropic.com",
    },
    "OpenAI (ChatGPT)": {
        "id": "openai",
        "needs_key": True,
        "needs_url": False,
        "default_model": "gpt-4o-mini",
        "hint": "Requer pip install openai  |  platform.openai.com",
    },
    "Ollama (local)": {
        "id": "ollama",
        "needs_key": False,
        "needs_url": True,
        "default_model": "llama3",
        "hint": "Requer ollama rodando localmente  |  ollama.com",
    },
    "OpenAI-compatible": {
        "id": "openai_compat",
        "needs_key": True,
        "needs_url": True,
        "default_model": "mistral",
        "hint": "Qualquer API compatível com OpenAI (Groq, Together, LM Studio...)",
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# Utilitários
# ══════════════════════════════════════════════════════════════════════════════

def fmt_time(seconds: float) -> str:
    t = int(seconds)
    return f"{t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}"


def check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}


def save_config(data: dict):
    try:
        CONFIG_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# Gestao de sessao e cache local
# ══════════════════════════════════════════════════════════════════════════════

def make_safe_name(title: str, fallback: str = "sessao") -> str:
    """Converte um titulo livre em nome de pasta seguro."""
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in title).strip()
    safe = safe[:60].strip().replace(" ", "_")
    return safe if safe else fallback


def get_session_dir(output_dir: Path, title: str) -> Path:
    """
    Retorna (e cria se necessario) a pasta da sessao.
    Se ja existir, reutiliza silenciosamente.
    """
    name = make_safe_name(title, fallback="sessao")
    session = output_dir / name
    session.mkdir(parents=True, exist_ok=True)
    return session


CACHE_FILE = "_cache.json"


def load_cache(session_dir: Path) -> dict:
    """Carrega o cache de transcript_ids {stem: id} da sessao."""
    cf = session_dir / CACHE_FILE
    if cf.exists():
        try:
            return json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_cache(session_dir: Path, cache: dict):
    """Persiste o cache de transcript_ids no disco."""
    cf = session_dir / CACHE_FILE
    cf.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def fetch_cached_transcript(transcript_id: str, api_key: str, log) -> "aai.Transcript | None":
    """Recupera uma transcricao ja existente na AssemblyAI pelo ID."""
    try:
        aai.settings.api_key = api_key
        t = aai.Transcript.get_by_id(transcript_id)
        if t and t.status == aai.TranscriptStatus.completed:
            words = len((t.text or "").split())
            log(f"  [cache] Reutilizado transcript_id={transcript_id} ({words:,} palavras)")
            return t
        log(f"  [warn] Transcript {transcript_id} existe mas status={getattr(t,'status','?')}")
    except Exception as e:
        log(f"  [warn] Nao foi possivel recuperar transcript {transcript_id}: {e}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Lógica de transcrição (backend — roda em thread separada)
# ══════════════════════════════════════════════════════════════════════════════

def extract_audio(video_path: Path, out_path: Path, log) -> Path:
    log(f"  [ffmpeg] Extraindo áudio: {video_path.name}")
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou:\n{r.stderr[-800:]}")
    mb = out_path.stat().st_size / 1_048_576
    log(f"  [ok] Audio extraido ({mb:.1f} MB)")
    return out_path


def transcribe_file(audio_path: Path, lang: str, api_key: str, log) -> aai.Transcript:
    aai.settings.api_key = api_key
    log(f"  [upload] Enviando para AssemblyAI: {audio_path.name}")

    # speech_models (plural, lista) é obrigatorio na API atual.
    # Tenta universal-3-pro primeiro (pt/en/es/fr/de/it); cai para
    # universal-2 automaticamente para outros idiomas (99 idiomas).
    config = aai.TranscriptionConfig(
        speech_models=["universal-3-pro", "universal-2"],
        language_detection=True,
        speaker_labels=True,
        speakers_expected=None,
        punctuate=True,
        format_text=True,
        auto_chapters=True,
        entity_detection=True,
    )

    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(str(audio_path), config=config)
    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"Transcricao falhou: {transcript.error}")
    words = len((transcript.text or "").split())
    log(f"  [ok] Transcrito: {words:,} palavras, {fmt_time(transcript.audio_duration or 0)}")
    return transcript


# ══════════════════════════════════════════════════════════════════════════════
# Motor de resumo — providers plugáveis
# ══════════════════════════════════════════════════════════════════════════════

def _build_instruction(context_prompt: str) -> str:
    """
    Prompt CURTO de instrucao — usado pelo LeMUR, que ja tem acesso
    ao conteudo via transcript_ids. Nao embute texto.
    """
    ctx_block = ""
    if context_prompt.strip():
        ctx_block = f"**Contexto fornecido pelo usuario:**\n{context_prompt.strip()}\n\n"

    return (
        f"{ctx_block}"
        "Analise a(s) transcricao(oes) e produza um relatorio estruturado em Markdown.\n\n"
        "Se o contexto indicar um formato especifico (ex: por ambiente, por topico), "
        "siga-o fielmente.\nCaso contrario, use:\n\n"
        "1. **Resumo Executivo** - paragrafo com o tema central\n"
        "2. **Pontos-chave** - principais assuntos discutidos\n"
        "3. **Decisoes e Acoes** - compromissos e proximos passos\n"
        "4. **Participantes** - perfil de cada falante (se houver)\n"
        "5. **Conclusao** - encerramento sintetico\n\n"
        "Use Markdown limpo. Seja direto e objetivo."
    )


def _build_prompt_with_text(transcripts: list, context_prompt: str) -> str:
    """
    Prompt COMPLETO com texto embutido — usado pelos providers externos
    (Anthropic, OpenAI, Ollama) que nao tem acesso aos transcript_ids.
    """
    n = len(transcripts)
    file_note = (
        f"A seguir estao as transcricoes de {n} arquivos de um mesmo contexto. "
        "Trate-os como partes de uma mesma sessao.\n\n"
        if n > 1 else ""
    )

    texts = []
    for i, t in enumerate(transcripts, 1):
        text = (t.text or "").strip()
        if text:
            header = f"--- Arquivo {i} ---" if n > 1 else "--- Transcricao ---"
            texts.append(f"{header}\n{text}")

    body = "\n\n".join(texts) if texts else "_Sem texto disponivel._"
    instruction = _build_instruction(context_prompt)
    return f"{file_note}{body}\n\n{instruction}"


def _call_lemur(transcripts: list, context_prompt: str, log) -> "str | None":
    """
    LeMUR: envia apenas a instrucao curta + transcript_ids.
    O conteudo eh recuperado pela AssemblyAI internamente via IDs.
    Nao embute o texto no prompt para evitar excesso de tokens.
    """
    transcript_ids = [t.id for t in transcripts if t and t.id]
    if not transcript_ids:
        log("  [lemur] Sem transcript_ids validos, pulando LeMUR.")
        return None

    log(f"  [lemur] {len(transcript_ids)} transcript_id(s) enviados")
    instruction = _build_instruction(context_prompt)

    try:
        result = aai.Lemur().task(
            prompt=instruction,
            transcript_ids=transcript_ids,
            max_output_size=4000,
        )
        response = (result.response or "").strip()
        if response:
            return response
        log("  [lemur] Retornou resposta vazia.")
    except Exception as e:
        log(f"  [lemur] Falhou: {type(e).__name__}: {e}")
    return None


def _call_anthropic(prompt: str, model: str, api_key: str, log) -> "str | None":
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key or None)
        msg = client.messages.create(
            model=model or "claude-haiku-4-5",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip() if msg.content else None
    except ImportError:
        log("  [warn] Instale: pip install anthropic")
    except Exception as e:
        log(f"  [warn] Anthropic falhou: {type(e).__name__}: {e}")
    return None


def _call_openai(prompt: str, model: str, api_key: str, base_url: str, log) -> "str | None":
    try:
        import openai
        kwargs = {"api_key": api_key or None}
        if base_url:
            kwargs["base_url"] = base_url.rstrip("/")
        client = openai.OpenAI(**kwargs)
        resp = client.chat.completions.create(
            model=model or "gpt-4o-mini",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content.strip() if resp.choices else None
    except ImportError:
        log("  [warn] Instale: pip install openai")
    except Exception as e:
        log(f"  [warn] OpenAI falhou: {type(e).__name__}: {e}")
    return None


def _call_ollama(prompt: str, model: str, base_url: str, log) -> "str | None":
    """Chama Ollama via endpoint /api/generate (sem dependência extra, usa urllib)."""
    import json as _json
    import urllib.request
    url  = (base_url.rstrip("/") if base_url else "http://localhost:11434") + "/api/generate"
    body = _json.dumps({"model": model or "llama3", "prompt": prompt, "stream": False}).encode()
    try:
        req  = urllib.request.Request(url, data=body,
                                      headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            data = _json.loads(r.read())
        return (data.get("response") or "").strip() or None
    except Exception as e:
        log(f"  [warn] Ollama falhou: {type(e).__name__}: {e}")
    return None


def generate_summary(
    transcripts: list,
    context_prompt: str,
    log,
    provider_id: str = "lemur",
    ai_model: str = "",
    ai_key: str = "",
    ai_url: str = "",
) -> str:
    """
    Gera o resumo usando o provider escolhido.
    - LeMUR: instrucao curta + transcript_ids (sem texto embutido)
    - Demais: texto completo embutido no prompt
    Fallback para resumo local se o provider falhar.
    """
    log(f"  [ia] Provider: {provider_id} | Modelo: {ai_model or 'default'}")
    result = None

    try:
        if provider_id == "lemur":
            # Instrucao curta — conteudo via transcript_ids
            result = _call_lemur(transcripts, context_prompt, log)

        else:
            # Texto embutido — necessario para providers externos
            prompt = _build_prompt_with_text(transcripts, context_prompt)
            log(f"  [ia] Prompt: ~{len(prompt.split())} palavras")

            if provider_id == "anthropic":
                result = _call_anthropic(prompt, ai_model, ai_key, log)

            elif provider_id == "openai":
                result = _call_openai(prompt, ai_model, ai_key, "", log)

            elif provider_id == "openai_compat":
                result = _call_openai(prompt, ai_model, ai_key, ai_url, log)

            elif provider_id == "ollama":
                result = _call_ollama(prompt, ai_model, ai_url, log)

    except Exception as e:
        log(f"  [warn] Provider '{provider_id}' lancou excecao: {type(e).__name__}: {e}")

    if result:
        log(f"  [ok] Resumo gerado via {provider_id}")
        return result

    log("  [fallback] Gerando resumo local a partir dos capitulos...")
    return _fallback_summary_all(transcripts)


def _fallback_summary(transcript) -> str:
    if not transcript:
        return "_Resumo indisponivel._"
    lines = ["## Resumo (fallback local)\n"]
    for ch in (transcript.chapters or []):
        ts = fmt_time(ch.start / 1000)
        lines.append(f"- `{ts}` - **{ch.headline}**: {ch.summary}")
    return "\n".join(lines)


def _fallback_summary_all(transcripts: list) -> str:
    """Fallback que consolida capitulos e texto de TODOS os arquivos."""
    if not transcripts:
        return "_Resumo indisponivel: nenhuma transcricao encontrada._"

    lines = [
        "## Resumo Consolidado (gerado localmente)",
        "",
        "> **Nota:** O servico LeMUR nao estava disponivel. "
        "Este resumo foi gerado localmente a partir dos capitulos detectados.",
        "",
    ]

    for i, t in enumerate(transcripts, 1):
        chapters = t.chapters or []
        if len(transcripts) > 1:
            lines.append(f"### Arquivo {i}")
            lines.append("")

        if chapters:
            lines.append("**Topicos identificados:**")
            lines.append("")
            for ch in chapters:
                ts = fmt_time(ch.start / 1000)
                lines.append(f"- `{ts}` **{ch.headline}** — {ch.summary}")
            lines.append("")
        else:
            # Sem capitulos: primeiras 300 palavras do texto
            text = (t.text or "").strip()
            if text:
                snippet = " ".join(text.split()[:300])
                if len(text.split()) > 300:
                    snippet += "..."
                lines.append("**Trecho inicial da transcricao:**")
                lines.append("")
                lines.append(f"> {snippet}")
                lines.append("")
            else:
                lines.append("_Sem conteudo disponivel para este arquivo._")
                lines.append("")

    return "\n".join(lines)


def build_file_markdown(transcript: aai.Transcript, file_path: Path, lang: str) -> str:
    dur   = fmt_time(transcript.audio_duration or 0)
    words = len((transcript.text or "").split())
    speakers = len({u.speaker for u in (transcript.utterances or [])})

    lines = [
        f"# Transcricao: {file_path.name}", "",
        "---", "",
        "## Metadados", "",
        "| Campo | Valor |",
        "|---|---|",
        f"| Arquivo | `{file_path.name}` |",
        f"| Idioma | `{lang}` |",
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
            lines += [f"### `{s}` -> `{e}` - {ch.headline}", "", f"> {ch.summary}", ""]
        lines += ["---", ""]

    lines += ["## Transcricao Completa", ""]
    if transcript.utterances:
        cur = None
        for utt in transcript.utterances:
            spk = f"Falante {utt.speaker}"
            ts  = fmt_time(utt.start / 1000)
            if spk != cur:
                cur = spk
                lines.append(f"\n**{spk}** - `{ts}`\n")
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


def build_consolidated_markdown(
    transcripts: list,
    file_paths: list,
    summary: str,
    context_prompt: str,
    lang: str,
    output_dir: Path,
    title: str = "",
) -> str:
    now   = datetime.now().strftime("%d/%m/%Y %H:%M")
    n     = len(transcripts)
    total_dur   = sum(t.audio_duration or 0 for t in transcripts)
    total_words = sum(len((t.text or "").split()) for t in transcripts)

    heading = title.strip() if title.strip() else f"Relatorio Consolidado - {n} arquivo(s)"
    lines = [
        f"# {heading}",
        f"_Gerado em {now}_", "",
        "---", "",
    ]

    if context_prompt.strip():
        lines += [
            "## Contexto Fornecido", "",
            f"> {context_prompt.strip()}", "",
            "---", "",
        ]

    lines += [
        "## Metadados Gerais", "",
        "| Campo | Valor |",
        "|---|---|",
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
        lines.append(f"{i}. **{p.name}** - {dur}, {words:,} palavras")

    lines += ["", "---", "",
              "## Analise e Resumo Unificado (IA)", "",
              summary.strip(), "",
              "---", ""]

    if n > 1:
        lines += ["## Relatorios Individuais", ""]
        for p in file_paths:
            lines.append(f"- [{p.name}]({p.stem}.md)")
        lines += ["", "---", ""]

    lines.append("_Gerado automaticamente por TranscritorIA + AssemblyAI_")
    return "\n".join(lines)


def run_pipeline(file_paths, lang_code, api_key, context_prompt, title,
                 output_dir, log, on_done, on_error,
                 provider_id="lemur", ai_model="", ai_key="", ai_url=""):
    try:
        if not check_ffmpeg():
            raise RuntimeError(
                "ffmpeg nao encontrado.\n"
                "Ubuntu/Debian: sudo apt install ffmpeg\n"
                "macOS: brew install ffmpeg"
            )

        # ── Pasta da sessao (reutiliza se ja existir) ─────────────────────────
        session_dir = get_session_dir(output_dir, title or "sessao")
        cache       = load_cache(session_dir)

        existing = [f for f in cache if f in [fp.stem for fp in file_paths]]
        if existing:
            log(f"  [cache] Pasta de sessao: {session_dir.name}")
            log(f"  [cache] Arquivos ja transcritos: {', '.join(existing)}")
        else:
            log(f"  [sessao] Nova pasta: {session_dir}")

        transcripts = []
        n = len(file_paths)

        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, fp in enumerate(file_paths, 1):
                log(f"\n{'='*48}")
                log(f"Arquivo [{idx}/{n}]: {fp.name}")
                log(f"{'='*48}")

                md_path = session_dir / (fp.stem + ".md")

                # ── Verificar cache ───────────────────────────────────────────
                cached_id = cache.get(fp.stem)
                if cached_id and md_path.exists():
                    transcript = fetch_cached_transcript(cached_id, api_key, log)
                    if transcript:
                        log(f"  [cache] MD ja existe, pulando upload: {md_path.name}")
                        transcripts.append(transcript)
                        continue
                    else:
                        log(f"  [cache] Cache invalido para {fp.stem}, retranscrevendo...")

                # ── Extração + transcrição ────────────────────────────────────
                if fp.suffix.lower() in VIDEO_EXTS:
                    audio_path = Path(tmpdir) / (fp.stem + f"_{idx}.wav")
                    extract_audio(fp, audio_path, log)
                else:
                    audio_path = fp
                    log(f"  [audio] Arquivo de audio direto: {fp.name}")

                transcript = transcribe_file(audio_path, lang_code, api_key, log)
                transcripts.append(transcript)

                # ── Salvar MD individual + atualizar cache ────────────────────
                md_content = build_file_markdown(transcript, fp, lang_code)
                md_path.write_text(md_content, encoding="utf-8")
                log(f"  [salvo] {md_path.name}")

                cache[fp.stem] = transcript.id
                save_cache(session_dir, cache)
                log(f"  [cache] transcript_id salvo ({transcript.id})")

        # ── Resumo consolidado ────────────────────────────────────────────────
        log(f"\n{'='*48}")
        log("Gerando resumo consolidado...")
        summary = generate_summary(transcripts, context_prompt, log,
                                     provider_id=provider_id, ai_model=ai_model,
                                     ai_key=ai_key, ai_url=ai_url)

        consolidated = build_consolidated_markdown(
            transcripts, file_paths, summary, context_prompt, lang_code, session_dir, title
        )
        out = session_dir / "_consolidado.md"
        out.write_text(consolidated, encoding="utf-8")
        log(f"  [salvo] {out.name}")

        on_done(session_dir, out)

    except Exception as exc:
        on_error(str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# Interface Gráfica
# ══════════════════════════════════════════════════════════════════════════════

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.resizable(True, True)
        self.minsize(780, 640)
        self.configure(bg=COLORS["bg"])

        self._cfg   = load_config()
        self._files: list = []
        self._queue: queue.Queue = queue.Queue()
        self._running = False

        self._build_ui()
        self._restore_config()
        self._poll_queue()

        self.update_idletasks()
        w, h = 860, 740
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ── Construção da UI ──────────────────────────────────────────────────────

    def _build_ui(self):
        C = COLORS

        # Cabeçalho
        header = tk.Frame(self, bg=C["surface"], pady=14)
        header.pack(fill="x")
        tk.Label(header, text=f"  {APP_NAME}",
                 font=("Segoe UI", 17, "bold"),
                 fg=C["accent"], bg=C["surface"]).pack(side="left", padx=18)
        tk.Label(header, text="Transcricao + Diarizacao + Resumo IA",
                 font=("Segoe UI", 9), fg=C["subtext"], bg=C["surface"]).pack(side="left")
        tk.Label(header, text=f"v{APP_VERSION}",
                 font=("Segoe UI", 8), fg=C["muted"], bg=C["surface"]).pack(side="right", padx=18)

        # Corpo
        body = tk.Frame(self, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=16, pady=10)

        # ── Painel esquerdo ───────────────────────────────────────────────────
        left = tk.Frame(body, bg=C["bg"])
        left.pack(side="left", fill="both", expand=True, padx=(0, 10))

        # -- Configuração
        self._section(left, "  Configuracao")

        # API Key
        tk.Label(left, text="AssemblyAI API Key",
                 font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).pack(anchor="w")
        api_row = tk.Frame(left, bg=C["bg"])
        api_row.pack(fill="x", pady=(2, 8))
        self._api_key = tk.StringVar()
        self._api_entry = tk.Entry(
            api_row, textvariable=self._api_key, show="*",
            font=("Consolas", 10),
            bg=C["surface"], fg=C["text"],
            insertbackground=C["text"], relief="flat", bd=0,
        )
        self._api_entry.pack(side="left", fill="x", expand=True, ipady=7, ipadx=10)
        self._key_visible = False
        self._show_key_btn = tk.Button(
            api_row, text=" Ver ", font=("Segoe UI", 8),
            bg=C["border"], fg=C["text"],
            activebackground=C["border"], activeforeground=C["text"],
            relief="flat", bd=0, cursor="hand2",
            command=self._toggle_key,
        )
        self._show_key_btn.pack(side="left", padx=(4, 0), ipady=7, ipadx=4)

        # Idioma + Saída
        row2 = tk.Frame(left, bg=C["bg"])
        row2.pack(fill="x", pady=(0, 8))

        lc = tk.Frame(row2, bg=C["bg"])
        lc.pack(side="left", expand=True, fill="x", padx=(0, 6))
        tk.Label(lc, text="Idioma", font=("Segoe UI", 9),
                 fg=C["subtext"], bg=C["bg"]).pack(anchor="w")
        self._lang_var = tk.StringVar(value="Portugues (pt)")
        lang_cb = ttk.Combobox(lc, textvariable=self._lang_var,
                               values=list(LANGS.keys()), state="readonly",
                               font=("Segoe UI", 9))
        lang_cb.pack(fill="x", ipady=5)

        oc = tk.Frame(row2, bg=C["bg"])
        oc.pack(side="left", expand=True, fill="x")
        tk.Label(oc, text="Pasta de saida", font=("Segoe UI", 9),
                 fg=C["subtext"], bg=C["bg"]).pack(anchor="w")
        or2 = tk.Frame(oc, bg=C["bg"])
        or2.pack(fill="x")
        self._out_dir = tk.StringVar(value=str(Path.home() / "Transcricoes"))
        tk.Entry(or2, textvariable=self._out_dir, font=("Segoe UI", 9),
                 bg=C["surface"], fg=C["text"],
                 insertbackground=C["text"], relief="flat", bd=0,
                 ).pack(side="left", fill="x", expand=True, ipady=7, ipadx=8)
        self._mkbtn(or2, " ... ", self._pick_output, small=True
                    ).pack(side="left", padx=(4, 0), ipady=7)

        # -- Provider de IA para resumo
        self._section(left, "  IA para Resumo")

        prov_row = tk.Frame(left, bg=C["bg"])
        prov_row.pack(fill="x", pady=(0, 4))

        pc = tk.Frame(prov_row, bg=C["bg"])
        pc.pack(side="left", expand=True, fill="x", padx=(0, 6))
        tk.Label(pc, text="Provider", font=("Segoe UI", 9),
                 fg=C["subtext"], bg=C["bg"]).pack(anchor="w")
        self._provider_var = tk.StringVar(value="AssemblyAI LeMUR")
        self._provider_cb = ttk.Combobox(pc, textvariable=self._provider_var,
                                         values=list(PROVIDERS.keys()),
                                         state="readonly", font=("Segoe UI", 9))
        self._provider_cb.pack(fill="x", ipady=5)
        self._provider_cb.bind("<<ComboboxSelected>>", self._on_provider_change)

        mc = tk.Frame(prov_row, bg=C["bg"])
        mc.pack(side="left", expand=True, fill="x")
        tk.Label(mc, text="Modelo", font=("Segoe UI", 9),
                 fg=C["subtext"], bg=C["bg"]).pack(anchor="w")
        self._ai_model_var = tk.StringVar(value="(automático)")
        tk.Entry(mc, textvariable=self._ai_model_var, font=("Segoe UI", 9),
                 bg=C["surface"], fg=C["text"],
                 insertbackground=C["text"], relief="flat", bd=0,
                 ).pack(fill="x", ipady=7, ipadx=8)

        # Campos opcionais — sempre visiveis, habilitados/desabilitados por provider.
        # Nao usar pack_forget/grid_remove: qualquer mudanca de layout no tkinter
        # forca redraw do frame pai e faz ttk.Combobox readonly perder o valor exibido.

        # API Key do provider
        tk.Label(left, text="API Key do Provider",
                 font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).pack(anchor="w")
        ai_key_row = tk.Frame(left, bg=C["bg"])
        ai_key_row.pack(fill="x", pady=(0, 4))
        self._ai_key_var = tk.StringVar()
        self._ai_key_entry = tk.Entry(
            ai_key_row, textvariable=self._ai_key_var, show="*",
            font=("Consolas", 9),
            bg=C["surface"], fg=C["text"],
            insertbackground=C["text"], relief="flat", bd=0,
        )
        self._ai_key_entry.pack(side="left", fill="x", expand=True, ipady=7, ipadx=8)
        self._ai_key_visible = False
        self._ai_key_toggle = tk.Button(
            ai_key_row, text=" Ver ", font=("Segoe UI", 8),
            bg=C["border"], fg=C["text"],
            activebackground=C["border"], activeforeground=C["text"],
            relief="flat", bd=0, cursor="hand2",
            command=self._toggle_ai_key,
        )
        self._ai_key_toggle.pack(side="left", padx=(4, 0), ipady=7, ipadx=4)

        # Base URL
        tk.Label(left, text="Base URL",
                 font=("Segoe UI", 9), fg=C["subtext"], bg=C["bg"]).pack(anchor="w")
        self._ai_url_var = tk.StringVar(value="http://localhost:11434")
        self._ai_url_entry = tk.Entry(left, textvariable=self._ai_url_var, font=("Segoe UI", 9),
                 bg=C["surface"], fg=C["text"],
                 insertbackground=C["text"], relief="flat", bd=0)
        self._ai_url_entry.pack(fill="x", ipady=7, ipadx=8, pady=(0, 4))

        # Hint
        self._provider_hint = tk.Label(
            left, text=PROVIDERS["AssemblyAI LeMUR"]["hint"],
            font=("Segoe UI", 7, "italic"), fg=C["muted"], bg=C["bg"],
            wraplength=340, justify="left",
        )
        self._provider_hint.pack(anchor="w", pady=(0, 6))

        # Aplicar estado inicial (enable/disable sem mudar layout)
        self._on_provider_change()

        # -- Titulo da sessao
        self._section(left, "  Titulo da Sessao")
        tk.Label(left,
                 text="Nome do relatorio consolidado (opcional — ex: Reuniao Sprint 12)",
                 font=("Segoe UI", 8), fg=C["muted"], bg=C["bg"]
                 ).pack(anchor="w", pady=(0, 4))
        self._title_var = tk.StringVar()
        tk.Entry(
            left, textvariable=self._title_var,
            font=("Segoe UI", 10),
            bg=C["surface"], fg=C["text"],
            insertbackground=C["text"], relief="flat", bd=0,
        ).pack(fill="x", ipady=7, ipadx=10, pady=(0, 4))

        # -- Contexto
        self._section(left, "  Prompt de Contexto")
        tk.Label(left,
                 text="Descreva o contexto, participantes ou foco da analise (opcional)",
                 font=("Segoe UI", 8), fg=C["muted"], bg=C["bg"]
                 ).pack(anchor="w", pady=(0, 4))
        self._ctx_box = scrolledtext.ScrolledText(
            left, height=5, wrap="word",
            font=("Segoe UI", 9),
            bg=C["surface"], fg=C["text"],
            insertbackground=C["text"],
            relief="flat", bd=0, padx=8, pady=6,
        )
        self._ctx_box.pack(fill="both", expand=True)

        # -- Arquivos
        self._section(left, "  Arquivos")
        fc = tk.Frame(left, bg=C["bg"])
        fc.pack(fill="x", pady=(0, 4))
        self._mkbtn(fc, "+ Adicionar", self._add_files).pack(side="left")
        self._mkbtn(fc, "x Remover",  self._remove_file, danger=True).pack(side="left", padx=6)
        self._mkbtn(fc, "^ Subir",    self._move_up,  small=True).pack(side="left")
        self._mkbtn(fc, "v Descer",   self._move_down, small=True).pack(side="left", padx=(4,0))

        lf = tk.Frame(left, bg=C["border"], padx=1, pady=1)
        lf.pack(fill="both", expand=True)
        self._file_lb = tk.Listbox(
            lf, bg=C["surface"], fg=C["text"],
            selectbackground=C["accent"], selectforeground="#fff",
            font=("Segoe UI", 9), relief="flat", bd=0, activestyle="none",
        )
        self._file_lb.pack(fill="both", expand=True, padx=2, pady=2)

        # -- Botão principal
        bf = tk.Frame(left, bg=C["bg"], pady=10)
        bf.pack(fill="x")
        self._run_btn = tk.Button(
            bf, text="  Iniciar Transcricao",
            font=("Segoe UI", 11, "bold"),
            bg=C["accent"], fg="#fff",
            activebackground=C["accent2"], activeforeground="#fff",
            relief="flat", bd=0, cursor="hand2", pady=11,
            command=self._start,
        )
        self._run_btn.pack(fill="x")
        self._progress = ttk.Progressbar(bf, mode="indeterminate")
        self._progress.pack(fill="x", pady=(6, 0))

        # ── Painel direito: log ───────────────────────────────────────────────
        right = tk.Frame(body, bg=C["bg"], width=290)
        right.pack(side="right", fill="both")
        right.pack_propagate(False)

        self._section(right, "  Log de Execucao")
        self._log_box = scrolledtext.ScrolledText(
            right, wrap="word",
            font=("Consolas", 8),
            bg=C["surface"], fg=C["text"],
            insertbackground=C["text"],
            relief="flat", bd=0, padx=8, pady=6,
            state="disabled",
        )
        self._log_box.pack(fill="both", expand=True)
        self._log_box.tag_config("ok",   foreground=C["success"])
        self._log_box.tag_config("warn", foreground=C["warning"])
        self._log_box.tag_config("err",  foreground=C["error"])
        self._log_box.tag_config("head", foreground=C["accent"])
        self._log_box.tag_config("dim",  foreground=C["muted"])

        self._mkbtn(right, "Limpar log", self._clear_log, small=True
                    ).pack(anchor="e", pady=(4, 0))

        # Status bar
        sb = tk.Frame(self, bg=C["surface"], pady=4)
        sb.pack(fill="x", side="bottom")
        self._status = tk.StringVar(value="Pronto.")
        tk.Label(sb, textvariable=self._status,
                 font=("Segoe UI", 8), fg=C["subtext"], bg=C["surface"]
                 ).pack(side="left", padx=12)

    def _section(self, parent, title):
        f = tk.Frame(parent, bg=COLORS["bg"])
        f.pack(fill="x", pady=(10, 4))
        tk.Label(f, text=title, font=("Segoe UI", 9, "bold"),
                 fg=COLORS["accent"], bg=COLORS["bg"]).pack(side="left")
        tk.Frame(f, bg=COLORS["border"], height=1).pack(
            side="left", fill="x", expand=True, padx=(8, 0), pady=6)

    def _mkbtn(self, parent, text, cmd, small=False, danger=False):
        C = COLORS
        bg = C["error"] if danger else (C["border"] if small else C["accent"])
        return tk.Button(
            parent, text=text, command=cmd,
            font=("Segoe UI", 8 if small else 9),
            bg=bg, fg="#fff",
            activebackground=C["error"] if danger else C["accent2"],
            activeforeground="#fff",
            relief="flat", bd=0, cursor="hand2",
            padx=8, pady=4,
        )

    # ── Handlers ─────────────────────────────────────────────────────────────

    def _on_provider_change(self, event=None):
        """
        Habilita/desabilita campos conforme o provider.
        NAO altera o layout (pack/grid_remove) para evitar reflow
        que faz ttk.Combobox readonly perder o valor exibido.
        """
        name = self._provider_var.get()
        cfg  = PROVIDERS.get(name, {})

        # Modelo default
        self._ai_model_var.set(cfg.get("default_model", ""))

        # API Key
        key_state = "normal" if cfg.get("needs_key") else "disabled"
        key_bg    = COLORS["surface"] if cfg.get("needs_key") else COLORS["border"]
        self._ai_key_entry.config(state=key_state, bg=key_bg)
        self._ai_key_toggle.config(state=key_state)

        # Base URL
        url_state = "normal" if cfg.get("needs_url") else "disabled"
        url_bg    = COLORS["surface"] if cfg.get("needs_url") else COLORS["border"]
        self._ai_url_entry.config(state=url_state, bg=url_bg)
        if cfg.get("needs_url"):
            cur = self._ai_url_var.get()
            if not cur:
                default = "http://localhost:11434" if cfg.get("id") == "ollama" else ""
                self._ai_url_var.set(default)

        # Hint
        self._provider_hint.config(text=cfg.get("hint", ""))

    def _toggle_ai_key(self):
        self._ai_key_visible = not self._ai_key_visible
        self._ai_key_entry.config(show="" if self._ai_key_visible else "*")
        self._ai_key_toggle.config(text=" Ocultar " if self._ai_key_visible else " Ver ")

    def _toggle_key(self):
        self._key_visible = not self._key_visible
        self._api_entry.config(show="" if self._key_visible else "*")
        self._show_key_btn.config(text=" Ocultar " if self._key_visible else " Ver ")

    def _pick_output(self):
        d = filedialog.askdirectory(title="Selecionar pasta de saida")
        if d:
            self._out_dir.set(d)

    def _add_files(self):
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_EXTS))
        paths = filedialog.askopenfilenames(
            title="Selecionar arquivos",
            filetypes=[("Video / Audio", exts), ("Todos", "*.*")],
        )
        added = 0
        for p in paths:
            fp = Path(p)
            if fp not in self._files:
                self._files.append(fp)
                self._file_lb.insert("end", fp.name)
                added += 1
        if added:
            self._status.set(f"{added} arquivo(s) adicionado(s). Total: {len(self._files)}")

    def _remove_file(self):
        sel = self._file_lb.curselection()
        if not sel:
            return
        for i in reversed(sel):
            self._files.pop(i)
            self._file_lb.delete(i)
        self._status.set(f"Removido. Total: {len(self._files)} arquivo(s).")

    def _move_up(self):
        sel = self._file_lb.curselection()
        if not sel or sel[0] == 0:
            return
        i = sel[0]
        self._files[i], self._files[i-1] = self._files[i-1], self._files[i]
        self._refresh_list(i - 1)

    def _move_down(self):
        sel = self._file_lb.curselection()
        if not sel or sel[0] >= len(self._files) - 1:
            return
        i = sel[0]
        self._files[i], self._files[i+1] = self._files[i+1], self._files[i]
        self._refresh_list(i + 1)

    def _refresh_list(self, select=None):
        self._file_lb.delete(0, "end")
        for f in self._files:
            self._file_lb.insert("end", f.name)
        if select is not None:
            self._file_lb.selection_set(select)
            self._file_lb.see(select)

    def _start(self):
        if self._running:
            return

        api_key = self._api_key.get().strip()
        if not api_key:
            messagebox.showerror("API Key ausente",
                                 "Informe a API Key da AssemblyAI.\n"
                                 "Cadastre-se gratuitamente em: https://www.assemblyai.com/")
            return

        if not self._files:
            messagebox.showerror("Sem arquivos", "Adicione pelo menos um arquivo.")
            return

        lang_label = self._lang_var.get()
        lang_code  = LANGS.get(lang_label, "pt")
        out_dir    = Path(self._out_dir.get()).expanduser().resolve()
        ctx        = self._ctx_box.get("1.0", "end").strip()

        prov_name  = self._provider_var.get()
        prov_cfg   = PROVIDERS.get(prov_name, {})
        provider_id = prov_cfg.get("id", "lemur")
        ai_model   = self._ai_model_var.get().strip()
        ai_key     = self._ai_key_var.get().strip()
        ai_url     = self._ai_url_var.get().strip()

        save_config({
            "api_key":   api_key,
            "lang":      lang_label,
            "out_dir":   str(out_dir),
            "provider":  prov_name,
            "ai_model":  ai_model,
            "ai_key":    ai_key,
            "ai_url":    ai_url,
        })

        self._running = True
        self._run_btn.config(state="disabled", text="  Processando...")
        self._progress.start(12)

        self._log(f"Iniciando: {len(self._files)} arquivo(s)", "head")
        self._log(f"Idioma: {lang_label} | Saida: {out_dir}", "dim")
        if ctx:
            self._log(f"Contexto: {ctx[:70]}{'...' if len(ctx)>70 else ''}", "dim")

        title = self._title_var.get().strip()

        self._log(f"Provider IA: {prov_name} | Modelo: {ai_model or 'default'}", "dim")

        threading.Thread(
            target=run_pipeline,
            args=(
                list(self._files), lang_code, api_key, ctx, title, out_dir,
                lambda m: self._queue.put(("log", m)),
                lambda od, fp: self._queue.put(("done", (od, fp))),
                lambda e:  self._queue.put(("error", e)),
            ),
            kwargs=dict(
                provider_id=provider_id,
                ai_model=ai_model,
                ai_key=ai_key,
                ai_url=ai_url,
            ),
            daemon=True,
        ).start()

    # ── Log ───────────────────────────────────────────────────────────────────

    def _log(self, msg: str, tag: str = ""):
        self._log_box.config(state="normal")
        ts   = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        if not tag:
            low = msg.lower()
            if "[ok]" in low or "concluido" in low or "salvo" in low:
                tag = "ok"
            elif "[warn]" in low or "aviso" in low:
                tag = "warn"
            elif "[erro]" in low or "falhou" in low or "erro" in low:
                tag = "err"
            elif "===" in msg or "iniciando" in low:
                tag = "head"
        self._log_box.insert("end", line, tag if tag else "")
        self._log_box.see("end")
        self._log_box.config(state="disabled")

    def _clear_log(self):
        self._log_box.config(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.config(state="disabled")

    # ── Poll da fila ──────────────────────────────────────────────────────────

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                    self._status.set(payload.strip()[:90])
                elif kind == "done":
                    od, fp = payload
                    self._on_done(od, fp)
                elif kind == "error":
                    self._on_error(payload)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _on_done(self, out_dir, final_file):
        self._running = False
        self._progress.stop()
        self._run_btn.config(state="normal", text="  Iniciar Transcricao")
        self._log(f"Concluido! Pasta: {out_dir}", "ok")
        self._status.set("Concluido!")
        if messagebox.askyesno(
            "Concluido!",
            f"Transcricao finalizada com sucesso!\n\n"
            f"Relatorio consolidado:\n  {final_file.name}\n\n"
            f"Abrir pasta de saida?",
        ):
            self._open_folder(out_dir)

    def _on_error(self, msg):
        self._running = False
        self._progress.stop()
        self._run_btn.config(state="normal", text="  Iniciar Transcricao")
        self._log(f"ERRO: {msg}", "err")
        self._status.set("Erro. Veja o log.")
        messagebox.showerror("Erro", msg)

    def _open_folder(self, path):
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass

    # ── Config ────────────────────────────────────────────────────────────────

    def _restore_config(self):
        self._api_key.set(self._cfg.get("api_key", ""))
        lang = self._cfg.get("lang", "Portugues (pt)")
        if lang in LANGS:
            self._lang_var.set(lang)
        out = self._cfg.get("out_dir", "")
        if out:
            self._out_dir.set(out)
        prov = self._cfg.get("provider", "AssemblyAI LeMUR")
        if prov in PROVIDERS:
            self._provider_var.set(prov)
        if self._cfg.get("ai_model"):
            self._ai_model_var.set(self._cfg["ai_model"])
        self._ai_key_var.set(self._cfg.get("ai_key", ""))
        self._ai_url_var.set(self._cfg.get("ai_url", ""))
        self._on_provider_change()


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    root = App()
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TCombobox",
                     fieldbackground=COLORS["surface"],
                     background=COLORS["surface"],
                     foreground=COLORS["text"],
                     arrowcolor=COLORS["text"],
                     bordercolor=COLORS["border"],
                     relief="flat")
    style.configure("TProgressbar",
                     troughcolor=COLORS["surface"],
                     background=COLORS["accent"],
                     bordercolor=COLORS["bg"],
                     lightcolor=COLORS["accent"],
                     darkcolor=COLORS["accent"])
    root.mainloop()
