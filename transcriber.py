"""
transcriber.py — Extração de áudio e transcrição via AssemblyAI
"""

import json
import subprocess
import tempfile
from pathlib import Path

import assemblyai as aai

from .config import VIDEO_EXTS, CACHE_FILE
from .logger import log


# ── Utilitários ───────────────────────────────────────────────────────────────

def fmt_time(seconds: float) -> str:
    t = int(seconds)
    return f"{t//3600:02d}:{(t%3600)//60:02d}:{t%60:02d}"


def check_ffmpeg() -> bool:
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


# ── Gestão de sessão e cache ──────────────────────────────────────────────────

def make_safe_name(title: str, fallback: str = "sessao") -> str:
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in title).strip()
    safe = safe[:60].strip().replace(" ", "_")
    return safe if safe else fallback


def get_session_dir(output_dir: Path, title: str) -> Path:
    name = make_safe_name(title, fallback="sessao")
    session = output_dir / name
    session.mkdir(parents=True, exist_ok=True)
    log.debug(f"Pasta de sessao: {session}")
    return session


def load_cache(session_dir: Path) -> dict:
    cf = session_dir / CACHE_FILE
    if cf.exists():
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
            log.debug(f"Cache carregado: {len(data)} entrada(s)")
            return data
        except Exception as e:
            log.warning(f"Falha ao ler cache: {e}")
    return {}


def save_cache(session_dir: Path, cache: dict):
    cf = session_dir / CACHE_FILE
    try:
        cf.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
        log.debug(f"Cache salvo: {len(cache)} entrada(s)")
    except Exception as e:
        log.warning(f"Falha ao salvar cache: {e}")


def fetch_cached_transcript(transcript_id: str, api_key: str) -> "aai.Transcript | None":
    log.info(f"  [cache] Recuperando transcript_id={transcript_id}")
    try:
        aai.settings.api_key = api_key
        t = aai.Transcript.get_by_id(transcript_id)
        if t and t.status == aai.TranscriptStatus.completed:
            words = len((t.text or "").split())
            log.ok(f"  [cache] Reutilizado: {words:,} palavras")
            return t
        status = getattr(t, "status", "?")
        log.warning(f"  [cache] Status inesperado: {status}")
    except Exception as e:
        log.exception(f"  [cache] Falha ao recuperar transcript", exc=e)
    return None


# ── Extração de áudio ─────────────────────────────────────────────────────────

def extract_audio(video_path: Path, out_path: Path) -> Path:
    log.info(f"  [ffmpeg] Extraindo audio: {video_path.name}")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(out_path),
    ]
    log.debug(f"  [ffmpeg] cmd: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        # Log ffmpeg stderr completo no arquivo para diagnóstico
        log.error(f"  [ffmpeg] stderr:\n{result.stderr}")
        raise RuntimeError(f"ffmpeg falhou (code {result.returncode}): {result.stderr[-400:]}")

    mb = out_path.stat().st_size / 1_048_576
    log.ok(f"  [ffmpeg] Audio extraido: {out_path.name} ({mb:.1f} MB)")
    return out_path


# ── Transcrição ───────────────────────────────────────────────────────────────

def transcribe_file(audio_path: Path, api_key: str) -> aai.Transcript:
    log.info(f"  [aai] Enviando para AssemblyAI: {audio_path.name}")
    log.debug(f"  [aai] Tamanho: {audio_path.stat().st_size / 1_048_576:.1f} MB")

    aai.settings.api_key = api_key

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

    log.debug("  [aai] Config: speech_models=['universal-3-pro','universal-2'], "
              "language_detection=True, speaker_labels=True")

    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(str(audio_path), config=config)

    log.debug(f"  [aai] Status retornado: {transcript.status}")

    if transcript.status == aai.TranscriptStatus.error:
        log.error(f"  [aai] Erro da API: {transcript.error}")
        raise RuntimeError(f"Transcricao falhou: {transcript.error}")

    words = len((transcript.text or "").split())
    duration = fmt_time(transcript.audio_duration or 0)
    log.ok(f"  [aai] Transcrito: {words:,} palavras, {duration}, id={transcript.id}")
    return transcript


# ── Orquestrador por arquivo ──────────────────────────────────────────────────

def process_file(
    fp: Path,
    idx: int,
    total: int,
    api_key: str,
    session_dir: Path,
    cache: dict,
    tmpdir: str,
) -> aai.Transcript:
    """
    Processa um único arquivo: checa cache → extrai áudio → transcreve.
    Retorna o Transcript (novo ou recuperado do cache).
    """
    log.info(f"\n{'='*50}")
    log.info(f"Arquivo [{idx}/{total}]: {fp.name}")
    log.info(f"{'='*50}")

    md_path = session_dir / (fp.stem + ".md")
    cached_id = cache.get(fp.stem)

    # Tentar reusar transcrição existente
    if cached_id and md_path.exists():
        log.info(f"  [cache] Encontrado: id={cached_id}, md={md_path.name}")
        transcript = fetch_cached_transcript(cached_id, api_key)
        if transcript:
            return transcript
        log.warning(f"  [cache] Cache invalido para '{fp.stem}', retranscrevendo...")
    elif cached_id and not md_path.exists():
        log.warning(f"  [cache] ID existe mas .md nao encontrado — retranscrevendo")
    else:
        log.debug(f"  [cache] Sem cache para '{fp.stem}'")

    # Extração de áudio (só se for vídeo)
    if fp.suffix.lower() in VIDEO_EXTS:
        audio_path = Path(tmpdir) / (fp.stem + f"_{idx}.wav")
        extract_audio(fp, audio_path)
    else:
        audio_path = fp
        log.info(f"  [audio] Arquivo de audio direto: {fp.name}")

    return transcribe_file(audio_path, api_key)
