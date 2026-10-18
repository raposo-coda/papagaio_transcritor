"""
vozes.py - Banco de vozes salvas e reconhecimento de quem esta falando.

O usuario ouve uma amostra no editor, da nome a um falante e pede "salvar esta
voz". Daqui em diante, toda transcricao local com separacao de falantes compara
cada voz encontrada com as salvas e, se parecer a mesma pessoa, ja usa o nome.

O que fica guardado e um embedding de voz (um vetor de numeros tirado pelo mesmo
modelo CAM++ da diarizacao), nunca o audio. Ainda assim e dado biometrico: so e
criado por acao explicita, mora em APP_DATA_DIR/vozes.json, nunca sai da maquina
e pode ser apagado pela interface.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

try:
    from . import diarizer
    from .config import APP_DATA_DIR
    from .logger import log
    from .utils import write_json_atomic
except ImportError:
    import diarizer  # type: ignore
    from config import APP_DATA_DIR  # type: ignore
    from logger import log  # type: ignore
    from utils import write_json_atomic  # type: ignore

BANCO_FILE = APP_DATA_DIR / "vozes.json"

# Semelhanca de cosseno minima para dar o nome. Abaixo disso a voz fica como
# "Falante N": um nome errado engana mais do que nenhum nome. O editor mostra o
# score ao lado, entao o usuario consegue julgar os casos limitrofes.
#
# Calibrado nos WAVs de testes/ (trechos de ~5s por voz): a mesma pessoa deu
# 0.65-0.79 e vozes sem relacao chegaram a 0.67. Com 0.5 houve falso positivo;
# 0.7 separou. Com mais fala por pessoa os vetores ficam mais estaveis e a
# mesma voz tende a pontuar mais alto.
_LIMIAR = 0.7

# Quanto audio de cada falante entra no embedding. Mais que isso quase nao muda o
# vetor e so gasta tempo; os trechos mais longos vem primeiro por serem mais limpos.
_MAX_SEGUNDOS = 30.0

# Trechos muito curtos sao quase so respiracao e ruido.
_MIN_TRECHO_SEGUNDOS = 0.8

MAX_NOME = 60
MAX_AMOSTRAS_POR_VOZ = 20

_lock = threading.Lock()
_extrator = None


# ---------------------------------------------------------------------------
# Banco em disco
# ---------------------------------------------------------------------------


def _ler_banco() -> dict[str, list[list[float]]]:
    if not BANCO_FILE.is_file():
        return {}
    try:
        data = json.loads(BANCO_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning(f"  [vozes] Banco de vozes ilegivel ({exc}); seguindo sem ele.")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(nome): lista for nome, lista in data.items() if isinstance(lista, list) and lista}


def tem_vozes() -> bool:
    with _lock:
        return bool(_ler_banco())


def listar() -> list[dict]:
    """Nomes e quantas amostras cada um tem - nunca os vetores."""
    with _lock:
        banco = _ler_banco()
    return [{"nome": nome, "amostras": len(lista)} for nome, lista in sorted(banco.items())]


def normalizar_nome(nome: str) -> str:
    limpo = " ".join("".join(c for c in (nome or "") if c.isprintable()).split())
    if not limpo:
        raise ValueError("Informe um nome para a voz.")
    if len(limpo) > MAX_NOME:
        raise ValueError(f"Nome muito longo (maximo {MAX_NOME} caracteres).")
    return limpo


def adicionar(nome: str, embedding: list[float]) -> int:
    """Acrescenta uma amostra a voz. Devolve quantas amostras ela passa a ter."""
    nome = normalizar_nome(nome)
    with _lock:
        banco = _ler_banco()
        lista = banco.setdefault(nome, [])
        lista.append([round(float(x), 6) for x in embedding])
        # Guarda so as mais recentes: o vetor medio estabiliza rapido, e o arquivo
        # nao cresce para sempre.
        del lista[:-MAX_AMOSTRAS_POR_VOZ]
        BANCO_FILE.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(BANCO_FILE, banco)
        return len(lista)


def apagar(nome: str) -> bool:
    with _lock:
        banco = _ler_banco()
        if nome not in banco:
            return False
        del banco[nome]
        write_json_atomic(BANCO_FILE, banco)
        return True


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


def _carregar_extrator():
    global _extrator
    if _extrator is not None:
        return _extrator

    import sherpa_onnx

    diarizer.ensure_models()
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(diarizer._embedding_path()))
    if not config.validate():
        raise RuntimeError(
            "Modelo de voz invalido - apague a pasta 'diarization' dos modelos e tente de novo."
        )
    _extrator = sherpa_onnx.SpeakerEmbeddingExtractor(config)
    return _extrator


def _embedding_das_amostras(amostras, intervalos: list[tuple[float, float]]) -> list[float] | None:
    """Junta os trechos (mais longos primeiro) ate _MAX_SEGUNDOS e tira um vetor so."""
    import numpy as np

    taxa = diarizer.SAMPLE_RATE
    validos = sorted(
        ((ini, fim) for ini, fim in intervalos if fim - ini >= _MIN_TRECHO_SEGUNDOS),
        key=lambda item: item[1] - item[0],
        reverse=True,
    )
    pedacos = []
    acumulado = 0.0
    for ini, fim in validos:
        if acumulado >= _MAX_SEGUNDOS:
            break
        fim = min(fim, ini + (_MAX_SEGUNDOS - acumulado))
        pedaco = amostras[int(ini * taxa): int(fim * taxa)]
        if len(pedaco):
            pedacos.append(pedaco)
            acumulado += len(pedaco) / taxa
    if acumulado < _MIN_TRECHO_SEGUNDOS:
        return None

    extrator = _carregar_extrator()
    stream = extrator.create_stream()
    stream.accept_waveform(sample_rate=taxa, waveform=np.concatenate(pedacos))
    stream.input_finished()
    if not extrator.is_ready(stream):
        return None
    return list(extrator.compute(stream))


def embedding_de_intervalos(wav_path: Path, intervalos: list[tuple[float, float]]) -> list[float] | None:
    """Vetor de voz de um falante a partir dos intervalos (em segundos) em que ele fala."""
    amostras, _ = diarizer.read_wav_mono16k(wav_path)
    return _embedding_das_amostras(amostras, intervalos)


# ---------------------------------------------------------------------------
# Reconhecimento
# ---------------------------------------------------------------------------


def _unitario(vetor):
    import numpy as np

    arr = np.asarray(vetor, dtype=np.float32)
    norma = float(np.linalg.norm(arr))
    return arr / norma if norma else arr


def reconhecer(wav_path: Path, turnos: list) -> dict[int, tuple[str, float]]:
    """
    Compara cada falante da diarizacao com as vozes salvas.

    Devolve {indice_do_falante: (nome, semelhanca)} so para quem passou do
    limiar. A atribuicao e gulosa pelo maior score, e um nome nunca vai para
    dois falantes do mesmo audio.
    """
    with _lock:
        banco = _ler_banco()
    if not banco or not turnos:
        return {}

    # Centroide de cada voz salva: a media das amostras normalizadas.
    centroides = {nome: _unitario(sum(_unitario(v) for v in lista)) for nome, lista in banco.items()}

    por_falante: dict[int, list[tuple[float, float]]] = {}
    for turno in turnos:
        por_falante.setdefault(turno.speaker, []).append((turno.start, turno.end))

    amostras, _ = diarizer.read_wav_mono16k(wav_path)
    candidatos: list[tuple[float, int, str]] = []
    for falante, intervalos in por_falante.items():
        vetor = _embedding_das_amostras(amostras, intervalos)
        if vetor is None:
            continue
        unit = _unitario(vetor)
        for nome, centro in centroides.items():
            score = float(unit @ centro)
            if score >= _LIMIAR:
                candidatos.append((score, falante, nome))
    del amostras

    resultado: dict[int, tuple[str, float]] = {}
    nomes_usados: set[str] = set()
    for score, falante, nome in sorted(candidatos, reverse=True):
        if falante in resultado or nome in nomes_usados:
            continue
        resultado[falante] = (nome, score)
        nomes_usados.add(nome)

    if resultado:
        for nome, score in resultado.values():
            log.ok(f"  [falantes] Voz reconhecida: {nome} ({score:.0%} de semelhanca)")
    else:
        log.info("  [falantes] Nenhuma voz salva reconhecida neste audio.")
    return resultado
