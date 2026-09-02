"""
converter.py - Normaliza qualquer audio/video suportado com ffmpeg antes do
processamento.

Dois destinos, conforme o modo:
  - nuvem: .mp4, que e o que o Gemini aceita (convert_to_mp4)
  - local: WAV 16 kHz mono, que e o que o Whisper e a separacao de falantes
    consomem internamente (convert_to_wav16k)
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

try:
    from .config import VIDEO_EXTS
    from .logger import log
    from .utils import ascii_safe_stem
except ImportError:
    from config import VIDEO_EXTS  # type: ignore
    from logger import log  # type: ignore
    from utils import ascii_safe_stem  # type: ignore


def get_ffmpeg_command() -> str | None:
    return shutil.which("ffmpeg")


def check_ffmpeg() -> bool:
    ffmpeg_command = get_ffmpeg_command()
    if not ffmpeg_command:
        return False
    try:
        subprocess.run(
            [ffmpeg_command, "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def convert_to_mp4(source_path: Path, out_dir: Path) -> tuple[Path, str]:
    """Converte para .mp4 e retorna (caminho, mime_type). O mime type reflete o
    conteudo real (audio/mp4 quando a origem e so audio) para nao confundir o
    processamento de arquivos do Gemini, que valida o mime declarado contra o
    conteudo."""
    ffmpeg_command = get_ffmpeg_command()
    if not ffmpeg_command:
        raise RuntimeError("ffmpeg nao encontrado. Necessario para normalizar o arquivo antes do envio ao Gemini.")

    # Nome ASCII: o Gemini envia o nome do arquivo num header HTTP, que nao
    # aceita acentos. O nome original volta em transcript.source_path.
    out_path = out_dir / f"{ascii_safe_stem(source_path.stem)}.mp4"
    is_video = source_path.suffix.lower() in VIDEO_EXTS

    log.info(f"  [ffmpeg] Convertendo para mp4: {source_path.name}")
    if is_video:
        command = [
            ffmpeg_command, "-y", "-i", str(source_path),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(out_path),
        ]
        mime_type = "video/mp4"
    else:
        command = [
            ffmpeg_command, "-y", "-i", str(source_path),
            "-vn", "-c:a", "aac", "-b:a", "192k",
            str(out_path),
        ]
        mime_type = "audio/mp4"

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # O stderr do ffmpeg traz caminhos absolutos e metadados do arquivo. Fica
        # so no log local; a mensagem que sobe pela API diz o necessario e nada mais.
        log.error(f"  [ffmpeg] stderr:\n{result.stderr}")
        raise RuntimeError(
            f"ffmpeg nao conseguiu converter {source_path.name} (codigo {result.returncode}). "
            "O detalhe tecnico esta no log desta sessao."
        )

    mb_size = out_path.stat().st_size / 1_048_576
    log.ok(f"  [ffmpeg] Convertido: {out_path.name} ({mb_size:.1f} MB)")
    return out_path, mime_type


def convert_to_wav16k(source_path: Path, out_dir: Path) -> Path:
    """
    Extrai o audio para WAV PCM 16 bits, mono, 16 kHz.

    E o formato que o Whisper e a separacao de falantes usam internamente, entao
    o modo local converte uma vez so e alimenta os dois. Para arquivos de video
    isso tambem evita o reencode de imagem do convert_to_mp4, que seria jogado
    fora em seguida.
    """
    ffmpeg_command = get_ffmpeg_command()
    if not ffmpeg_command:
        raise RuntimeError("ffmpeg nao encontrado. Necessario para preparar o audio antes da transcricao.")

    out_path = out_dir / f"{source_path.stem}.wav"

    log.info(f"  [ffmpeg] Extraindo audio 16 kHz mono: {source_path.name}")
    command = [
        ffmpeg_command, "-y", "-i", str(source_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(out_path),
    ]

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # O stderr do ffmpeg traz caminhos absolutos e metadados do arquivo. Fica
        # so no log local; a mensagem que sobe pela API diz o necessario e nada mais.
        log.error(f"  [ffmpeg] stderr:\n{result.stderr}")
        raise RuntimeError(
            f"ffmpeg nao conseguiu extrair o audio de {source_path.name} (codigo {result.returncode}). "
            "O detalhe tecnico esta no log desta sessao."
        )

    mb_size = out_path.stat().st_size / 1_048_576
    log.ok(f"  [ffmpeg] Audio pronto: {out_path.name} ({mb_size:.1f} MB)")
    return out_path
