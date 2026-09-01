"""
diarizer.py - Separacao de falantes (diarizacao) 100% offline, via sherpa-onnx.

Descobre "quem falou quando" a partir de um WAV 16 kHz mono. Roda em ONNX Runtime,
sem PyTorch e sem conta em lugar nenhum: os dois modelos vem de GitHub Releases,
sem token e sem aceite de licenca.

O audio nunca sai da maquina. A unica conexao possivel e o download unico dos
modelos; depois disso funciona offline.

Este modulo so conhece audio e tempo. Quem casa os turnos com o texto do Whisper
e o local_client.
"""

from __future__ import annotations

import importlib.util
import shutil
import tarfile
import tempfile
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path

try:
    from .config import get_models_dir
    from .logger import log
except ImportError:
    from config import get_models_dir  # type: ignore
    from logger import log  # type: ignore

BACKEND_ID = "sherpa-onnx"
BACKEND_LABEL = "sherpa-onnx (segmentacao pyannote 3.0 + embeddings CAM++)"

_BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download"

# Segmentacao: pyannote/segmentation-3.0 convertido para ONNX. Licenca MIT,
# redistribuido sem gate - por isso nao exige conta no Hugging Face.
_SEGMENTATION = {
    "url": f"{_BASE}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
    "archive_dir": "sherpa-onnx-pyannote-segmentation-3-0",
    "filename": "model.onnx",
    "bytes": 6_958_444,
}

# Embedding de voz: CAM++ treinado no VoxCeleb. Escolhido por ser compacto e nao
# ser especifico de um idioma so (os modelos zh-cn do exemplo oficial nao servem
# bem para portugues).
_EMBEDDING = {
    "url": f"{_BASE}/speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx",
    "filename": "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx",
    "bytes": 29_596_978,
}

DOWNLOAD_MB = round((_SEGMENTATION["bytes"] + _EMBEDDING["bytes"]) / 1_048_576)

SAMPLE_RATE = 16000

# Usado so quando o numero de falantes e automatico. Menor => mais falantes.
_CLUSTER_THRESHOLD = 0.5

# Acima disso o array de audio em memoria fica grande (1 h ~ 230 MB em float32).
_AVISO_DURACAO_SEGUNDOS = 2 * 3600

_diarizador_carregado: tuple[int, object] | None = None


@dataclass
class DiarizationTurn:
    """Um trecho continuo em que um mesmo falante esta com a palavra."""

    start: float  # segundos
    end: float
    speaker: int  # indice do agrupamento (0, 1, 2, ...)


# ---------------------------------------------------------------------------
# Disponibilidade e modelos
# ---------------------------------------------------------------------------


def validate_environment() -> list[str]:
    errors = []
    if importlib.util.find_spec("sherpa_onnx") is None:
        errors.append(
            "Pacote Python ausente para a separacao de falantes: pip install sherpa-onnx "
            "(se voce usa Docker, refaca a imagem com 'docker compose up -d --build')."
        )
    if importlib.util.find_spec("numpy") is None:
        errors.append("Pacote Python ausente para a separacao de falantes: pip install numpy")
    return errors


def models_dir() -> Path:
    caminho = get_models_dir() / "diarization"
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


def _segmentation_path() -> Path:
    return models_dir() / _SEGMENTATION["archive_dir"] / _SEGMENTATION["filename"]


def _embedding_path() -> Path:
    return models_dir() / _EMBEDDING["filename"]


def is_model_downloaded() -> bool:
    return _segmentation_path().is_file() and _embedding_path().is_file()


def _extrair_membro(arquivo: Path, membro: str, destino: Path):
    """
    Tira um unico arquivo de dentro do .tar.bz2, pelo nome exato.

    Extrair o pacote inteiro abriria espaco para "tar slip" - um membro com
    caminho absoluto ou com '..' escreveria fora da pasta de destino. Aqui so
    um nome conhecido e aceito, e so se for arquivo comum.
    """
    with tarfile.open(arquivo, "r:bz2") as tar:
        try:
            info = tar.getmember(membro)
        except KeyError:
            raise RuntimeError(
                f"O pacote do modelo veio sem o arquivo esperado ({membro})."
            ) from None
        if not info.isfile():
            raise RuntimeError(f"Entrada inesperada no pacote do modelo: {membro}")

        origem = tar.extractfile(info)
        if origem is None:
            raise RuntimeError(f"Nao foi possivel ler {membro} de dentro do pacote.")
        with origem, destino.open("wb") as saida:
            shutil.copyfileobj(origem, saida)


def _baixar(url: str, destino: Path, rotulo: str):
    """Baixa para um arquivo temporario e so entao move para o destino final."""
    # Os URLs sao constantes deste modulo, mas checar o esquema deixa explicito
    # que nada aqui abre file:// ou algo montado a partir de entrada do usuario.
    if not url.startswith("https://"):
        raise RuntimeError(f"URL de modelo invalida (esperado https): {url}")

    log.info(f"  [falantes] Baixando {rotulo}...")
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_suffix(destino.suffix + ".part")

    try:
        # O esquema e validado como https logo acima e a URL e constante do modulo,
        # entao nao ha como cair em file:// ou esquema exotico vindo do usuario.
        with urllib.request.urlopen(url, timeout=60) as resposta:  # noqa: S310  # nosec B310
            total = int(resposta.headers.get("Content-Length") or 0)
            baixado = 0
            proximo_aviso = 25
            with parcial.open("wb") as saida:
                while True:
                    pedaco = resposta.read(262_144)
                    if not pedaco:
                        break
                    saida.write(pedaco)
                    baixado += len(pedaco)
                    if total:
                        pct = baixado / total * 100
                        if pct >= proximo_aviso:
                            log.info(f"  [falantes] {rotulo}: {pct:.0f}%")
                            proximo_aviso += 25
        parcial.replace(destino)
    except Exception:
        parcial.unlink(missing_ok=True)
        raise


def ensure_models():
    """Garante os dois modelos em disco. Baixa so o que estiver faltando."""
    if is_model_downloaded():
        return

    log.warning(
        f"  [falantes] Primeira execucao com separacao de falantes: baixando ~{DOWNLOAD_MB} MB "
        "de modelos. Acontece uma unica vez e precisa de internet; nenhum dado seu e enviado."
    )

    if not _embedding_path().is_file():
        _baixar(_EMBEDDING["url"], _embedding_path(), "modelo de voz")

    if not _segmentation_path().is_file():
        with tempfile.TemporaryDirectory() as tmp:
            arquivo = Path(tmp) / "segmentation.tar.bz2"
            _baixar(_SEGMENTATION["url"], arquivo, "modelo de segmentacao")
            destino = _segmentation_path()
            destino.parent.mkdir(parents=True, exist_ok=True)
            _extrair_membro(
                arquivo,
                f"{_SEGMENTATION['archive_dir']}/{_SEGMENTATION['filename']}",
                destino,
            )

    log.ok("  [falantes] Modelos prontos. A partir de agora funciona sem internet.")


# ---------------------------------------------------------------------------
# Leitura do audio
# ---------------------------------------------------------------------------


def read_wav_mono16k(wav_path: Path):
    """Le um WAV PCM 16 bits, mono, 16 kHz e devolve (samples float32, duracao)."""
    import numpy as np

    with wave.open(str(wav_path), "rb") as wav:
        canais = wav.getnchannels()
        taxa = wav.getframerate()
        largura = wav.getsampwidth()
        quadros = wav.getnframes()
        if canais != 1 or taxa != SAMPLE_RATE or largura != 2:
            raise RuntimeError(
                f"Audio precisa ser WAV PCM 16 bits, mono, {SAMPLE_RATE} Hz para a separacao "
                f"de falantes (recebido: {canais} canal(is), {taxa} Hz, {largura * 8} bits)."
            )
        bruto = wav.readframes(quadros)

    amostras = np.frombuffer(bruto, dtype=np.int16).astype(np.float32) / 32768.0
    return amostras, quadros / float(SAMPLE_RATE)


# ---------------------------------------------------------------------------
# Diarizacao
# ---------------------------------------------------------------------------


def _load(num_speakers: int):
    """Carrega (e mantem em memoria) o pipeline de diarizacao."""
    global _diarizador_carregado

    if _diarizador_carregado and _diarizador_carregado[0] == num_speakers:
        return _diarizador_carregado[1]

    import sherpa_onnx

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(_segmentation_path()),
            ),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(_embedding_path()),
        ),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=num_speakers if num_speakers and num_speakers > 0 else -1,
            threshold=_CLUSTER_THRESHOLD,
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError(
            "Configuracao da separacao de falantes invalida - os arquivos de modelo podem "
            "estar corrompidos. Apague a pasta 'diarization' dos modelos e tente de novo."
        )

    diarizador = sherpa_onnx.OfflineSpeakerDiarization(config)
    _diarizador_carregado = (num_speakers, diarizador)
    return diarizador


def diarize(wav_path: Path, num_speakers: int = 0) -> list[DiarizationTurn]:
    """
    Descobre os turnos de fala de um WAV 16 kHz mono.

    num_speakers = 0 (ou negativo) deixa o numero de falantes ser descoberto
    automaticamente por agrupamento.
    """
    ensure_models()
    diarizador = _load(num_speakers)

    amostras, duracao = read_wav_mono16k(wav_path)
    if duracao > _AVISO_DURACAO_SEGUNDOS:
        log.warning(
            f"  [falantes] Audio longo ({duracao / 3600:.1f} h): a separacao de falantes "
            "carrega tudo em memoria e pode consumir bastante RAM."
        )

    if getattr(diarizador, "sample_rate", SAMPLE_RATE) != SAMPLE_RATE:
        raise RuntimeError(
            f"O modelo espera {diarizador.sample_rate} Hz, mas o audio foi preparado em {SAMPLE_RATE} Hz."
        )

    alvo = "automatico" if not num_speakers or num_speakers <= 0 else str(num_speakers)
    log.info(f"  [falantes] Analisando vozes (numero de falantes: {alvo})...")

    estado = {"proximo": 25}

    def progresso(processados: int, total: int) -> int:
        if total:
            pct = processados / total * 100
            if pct >= estado["proximo"]:
                log.info(f"  [falantes] {min(pct, 100):.0f}%")
                estado["proximo"] += 25
        return 0

    resultado = diarizador.process(amostras, callback=progresso).sort_by_start_time()
    del amostras

    turnos = [DiarizationTurn(start=r.start, end=r.end, speaker=int(r.speaker)) for r in resultado]

    quantos = len({turno.speaker for turno in turnos})
    log.ok(f"  [falantes] {quantos} falante(s) identificado(s) em {len(turnos)} turno(s).")
    return turnos


# ---------------------------------------------------------------------------
# Casamento com o texto
# ---------------------------------------------------------------------------


def speaker_for_interval(turnos: list[DiarizationTurn], start: float, end: float) -> int | None:
    """
    Qual falante ocupa a maior parte do intervalo [start, end].

    Se o intervalo nao encosta em turno nenhum (silencio, ruido), cai para o
    turno mais proximo no tempo - melhor colar a palavra num vizinho plausivel
    do que descartar o rotulo.
    """
    if not turnos:
        return None

    melhor_falante = None
    melhor_sobreposicao = 0.0
    for turno in turnos:
        sobreposicao = min(end, turno.end) - max(start, turno.start)
        if sobreposicao > melhor_sobreposicao:
            melhor_sobreposicao = sobreposicao
            melhor_falante = turno.speaker

    if melhor_falante is not None:
        return melhor_falante

    meio = (start + end) / 2
    mais_proximo = min(
        turnos,
        key=lambda t: 0.0 if t.start <= meio <= t.end else min(abs(t.start - meio), abs(t.end - meio)),
    )
    return mais_proximo.speaker
