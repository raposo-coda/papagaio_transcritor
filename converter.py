"""
converter.py - Normaliza qualquer audio/video suportado com ffmpeg antes do
processamento.

Dois destinos, conforme o modo:
  - nuvem: .mp4, que e o que o Gemini aceita (convert_to_mp4)
  - local: WAV 16 kHz mono, que e o que o Whisper e a separacao de falantes
    consomem internamente (convert_to_wav16k)
"""

from __future__ import annotations

import itertools
import re
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


def probe_duration(file_path: Path) -> float:
    """Duracao em segundos pelo ffprobe; 0 quando nao der para medir."""
    ffprobe_command = shutil.which("ffprobe")
    if not ffprobe_command:
        return 0.0
    result = subprocess.run(
        [
            ffprobe_command, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path),
        ],
        capture_output=True, text=True, check=False,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


# Do mais rigoroso ao mais tolerante. Gravacao de reuniao costuma ter ruido de
# fundo (ar-condicionado, sala) acima de -35 dB: numa de 75 min so -25 dB achou
# pausas perto de todos os cortes.
_SILENCE_THRESHOLDS_DB = (-35, -30, -25, -20)


def _silences(file_path: Path, noise_db: int) -> list[tuple[float, float]]:
    """Trechos de silencio (inicio, fim) achados pelo silencedetect do ffmpeg."""
    ffmpeg_command = get_ffmpeg_command()
    result = subprocess.run(
        [
            ffmpeg_command, "-hide_banner", "-nostats", "-i", str(file_path),
            "-vn", "-af", f"silencedetect=noise={noise_db}dB:d=0.4", "-f", "null", "-",
        ],
        capture_output=True, text=True, check=False,
    )
    silencios = []
    inicio = None
    for linha in result.stderr.splitlines():
        achou = re.search(r"silence_start: (-?[\d.]+)", linha)
        if achou:
            inicio = float(achou.group(1))
            continue
        achou = re.search(r"silence_end: ([\d.]+)", linha)
        if achou and inicio is not None:
            silencios.append((max(inicio, 0.0), float(achou.group(1))))
            inicio = None
    return silencios


def plan_slices(file_path: Path, duration: float, target: float, search: float) -> list[tuple[float, float]]:
    """
    Divide o arquivo em fatias de ~target segundos, cortando no meio de silencios.

    Cortar no silencio evita partir uma palavra entre duas fatias. Para cada
    corte vale o limiar mais rigoroso que ache uma pausa na janela; sem pausa
    nenhuma perto do alvo (musica, falas sobrepostas), o corte cai no proprio
    alvo. O resultado depende so do conteudo, entao repetir o job gera as mesmas
    fatias e o cache de cada uma volta a valer.
    """
    pausas_por_limiar = {}

    def pausas(noise_db: int) -> list[float]:
        if noise_db not in pausas_por_limiar:
            pausas_por_limiar[noise_db] = [(ini + fim) / 2 for ini, fim in _silences(file_path, noise_db)]
        return pausas_por_limiar[noise_db]

    cortes = []
    anterior = 0.0
    # A ultima fatia fica com no maximo ~1,1x o alvo: quanto mais longa, mais o
    # Gemini estica os tempos (uma de 12,6 min voltou com 20,6 min). Sobra curta
    # no fim e inofensiva.
    while duration - anterior > target * 1.1:
        alvo = anterior + target
        corte = alvo
        for noise_db in _SILENCE_THRESHOLDS_DB:
            perto = [p for p in pausas(noise_db) if abs(p - alvo) <= search]
            if perto:
                corte = min(perto, key=lambda p: abs(p - alvo))
                break
        cortes.append(round(corte, 2))
        anterior = corte
    return list(itertools.pairwise([0.0, *cortes, duration]))


def extract_slice(source_path: Path, start: float, end: float, out_path: Path) -> Path:
    """Copia o audio entre start e end para um .m4a, sem reencodar."""
    ffmpeg_command = get_ffmpeg_command()
    command = [
        ffmpeg_command, "-y", "-ss", f"{start:.3f}", "-i", str(source_path),
        "-t", f"{end - start:.3f}", "-vn", "-c:a", "copy",
        str(out_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        log.error(f"  [ffmpeg] stderr:\n{result.stderr}")
        raise RuntimeError(
            f"ffmpeg nao conseguiu separar a fatia {start:.0f}s-{end:.0f}s (codigo {result.returncode}). "
            "O detalhe tecnico esta no log desta sessao."
        )
    return out_path


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


def convert_to_opus(source_path: Path, out_path: Path) -> Path:
    """
    Copia compacta do audio para o editor ouvir junto com o texto.

    Opus mono a 32 kbps fica em ~15 MB por hora e soa bem para fala. O container
    WebM toca direto no navegador (Chrome, Firefox, Safari 15+) e aceita pular para
    qualquer ponto - o que o editor faz o tempo todo.
    """
    ffmpeg_command = get_ffmpeg_command()
    if not ffmpeg_command:
        raise RuntimeError("ffmpeg nao encontrado. Necessario para guardar o audio para o editor.")

    # Escreve num temporario ao lado e so troca no fim: um .webm pela metade nunca
    # fica parecendo pronto se o ffmpeg cair no meio.
    parcial = out_path.with_name(out_path.stem + ".parcial.webm")
    log.info(f"  [ffmpeg] Guardando audio para o editor: {out_path.name}")
    command = [
        ffmpeg_command, "-y", "-i", str(source_path),
        "-vn", "-ac", "1", "-c:a", "libopus", "-b:a", "32k",
        str(parcial),
    ]

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        parcial.unlink(missing_ok=True)
        log.error(f"  [ffmpeg] stderr:\n{result.stderr}")
        raise RuntimeError(
            f"ffmpeg nao conseguiu gerar o audio do editor para {source_path.name} "
            f"(codigo {result.returncode}). O detalhe tecnico esta no log desta sessao."
        )

    parcial.replace(out_path)
    mb_size = out_path.stat().st_size / 1_048_576
    log.ok(f"  [ffmpeg] Audio do editor salvo: {out_path.name} ({mb_size:.1f} MB)")
    return out_path
