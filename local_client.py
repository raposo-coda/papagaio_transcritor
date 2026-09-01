"""
local_client.py - Transcricao 100% offline com Whisper (via faster-whisper).

Nada aqui envia audio, video ou texto para lugar nenhum. A unica conexao de rede
que pode acontecer e o download do modelo Whisper na primeira execucao; depois
disso o arquivo fica salvo na maquina e o modo local funciona sem internet.
"""

from __future__ import annotations

import importlib.util
import re
from collections import Counter
from pathlib import Path

try:
    from . import diarizer
    from .config import get_models_dir
    from .hardware import WHISPER_MODELS
    from .logger import log
    from .models import LocalConfig, NormalizedTranscriptResult, TranscriptUtterance
    from .utils import fmt_time
except ImportError:
    import diarizer  # type: ignore
    from config import get_models_dir  # type: ignore
    from hardware import WHISPER_MODELS  # type: ignore
    from logger import log  # type: ignore
    from models import LocalConfig, NormalizedTranscriptResult, TranscriptUtterance  # type: ignore
    from utils import fmt_time  # type: ignore

PROVIDER_ID = "whisper-local"
PROVIDER_LABEL = "Whisper local (offline)"

# Repositorios usados pelo faster-whisper para cada tamanho de modelo.
_REPO_POR_MODELO = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
}

_modelo_carregado: tuple[str, str, str, object] | None = None


def _package_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def validate_environment(config: LocalConfig) -> list[str]:
    errors = []
    if not _package_available():
        errors.append(
            "Pacote Python ausente para o modo local: pip install faster-whisper "
            "(se voce usa Docker, refaca a imagem com 'docker compose up -d --build')."
        )
    if config.model_size not in WHISPER_MODELS:
        errors.append(f"Modelo local desconhecido: {config.model_size}")
    return errors


def model_cache_dir(model_size: str) -> Path:
    repo = _REPO_POR_MODELO.get(model_size, "")
    pasta = "models--" + repo.replace("/", "--")
    return get_models_dir() / pasta


def is_model_downloaded(model_size: str) -> bool:
    pasta = model_cache_dir(model_size)
    if not pasta.exists():
        return False
    return any(pasta.rglob("model.bin"))


def _load_model(config: LocalConfig):
    """Carrega (e mantem em memoria) o modelo Whisper pedido."""
    global _modelo_carregado

    chave = (config.model_size, config.device, config.compute_type)
    if _modelo_carregado and _modelo_carregado[:3] == chave:
        return _modelo_carregado[3]

    from faster_whisper import WhisperModel

    tamanho_mb = WHISPER_MODELS.get(config.model_size, {}).get("download_mb", 0)
    if not is_model_downloaded(config.model_size):
        log.warning(
            f"  [local] Primeira execucao com o modelo '{config.model_size}': baixando "
            f"~{tamanho_mb} MB. Isso acontece uma unica vez e precisa de internet."
        )
    else:
        log.info(f"  [local] Modelo '{config.model_size}' ja esta na maquina. Carregando.")

    modelo = WhisperModel(
        config.model_size,
        device=config.device,
        compute_type=config.compute_type,
        download_root=str(get_models_dir()),
    )
    log.ok(f"  [local] Modelo carregado em {config.device.upper()} ({config.compute_type}).")

    _modelo_carregado = (config.model_size, config.device, config.compute_type, modelo)
    return modelo


def _executar_diarizacao(file_path: Path, config: LocalConfig) -> list:
    """
    Roda a separacao de falantes, mas nunca derruba a transcricao por causa dela.

    Ficar sem os rotulos de falante e um resultado pior; ficar sem a transcricao
    e um resultado inutil. Qualquer falha aqui vira aviso e segue o jogo.
    """
    if not config.diarize:
        return []

    erros = diarizer.validate_environment()
    if erros:
        for erro in erros:
            log.warning(f"  [falantes] {erro}")
        log.warning("  [falantes] Seguindo sem separar os falantes.")
        return []

    try:
        return diarizer.diarize(file_path, config.num_speakers)
    except Exception as exc:
        log.exception("  [falantes] Nao foi possivel separar os falantes", exc=exc)
        log.warning("  [falantes] A transcricao continua normalmente, sem rotulos de falante.")
        return []


def _montar_unidades(segmentos, turnos: list, duracao_total: float) -> list[list]:
    """
    Consome os segmentos do Whisper e devolve blocos [falante, inicio, fim, [textos]],
    quebrando o bloco toda vez que a voz muda.

    Com timestamps por palavra, uma frase que troca de voz no meio e partida no
    ponto certo, em vez de ir inteira para o falante errado.
    """
    unidades: list[list] = []
    proximo_aviso = 10.0

    def acrescentar(falante, inicio: float, fim: float, texto: str, juntar: bool = True):
        if juntar and unidades and unidades[-1][0] == falante:
            unidades[-1][2] = fim
            unidades[-1][3].append(texto)
        else:
            unidades.append([falante, inicio, fim, [texto]])

    for segmento in segmentos:
        palavras = getattr(segmento, "words", None) if turnos else None

        if palavras:
            for palavra in palavras:
                texto = palavra.word or ""
                if not texto.strip():
                    continue
                falante = diarizer.speaker_for_interval(turnos, palavra.start, palavra.end)
                acrescentar(falante, palavra.start, palavra.end, texto)
        else:
            texto = (segmento.text or "").strip()
            if texto:
                falante = (
                    diarizer.speaker_for_interval(turnos, segmento.start, segmento.end)
                    if turnos
                    else None
                )
                # Sem falantes para agrupar, cada segmento do Whisper vira uma linha
                # propria - juntar tudo num paragrafo unico so atrapalha a leitura.
                acrescentar(falante, segmento.start, segmento.end, " " + texto, juntar=bool(turnos))

        if duracao_total > 0:
            progresso = (segmento.end / duracao_total) * 100
            if progresso >= proximo_aviso:
                log.info(
                    f"  [local] {min(progresso, 100):.0f}% ({fmt_time(segmento.end)} de {fmt_time(duracao_total)})"
                )
                proximo_aviso = progresso + 10.0

    return unidades


def _rotular(unidades: list[list]) -> list[TranscriptUtterance]:
    """Converte indices de agrupamento em 'Falante 1', 'Falante 2'... por ordem de entrada."""
    rotulos: dict[int, str] = {}
    utterances: list[TranscriptUtterance] = []

    for falante, inicio, fim, partes in unidades:
        texto = " ".join("".join(partes).split())
        if not texto:
            continue
        if falante is None:
            nome = "Falante"
        else:
            if falante not in rotulos:
                rotulos[falante] = f"Falante {len(rotulos) + 1}"
            nome = rotulos[falante]
        utterances.append(
            TranscriptUtterance(
                speaker=nome,
                start_ms=int(inicio * 1000),
                end_ms=int(fim * 1000),
                text=texto,
            )
        )
    return utterances


def transcribe_path(file_path: Path, lang_code: str, config: LocalConfig) -> NormalizedTranscriptResult:
    # A diarizacao vem primeiro: e rapida perto do Whisper, ja informa quantas
    # vozes existem no comeco do log, e libera a memoria do audio antes da parte
    # pesada comecar.
    turnos = _executar_diarizacao(file_path, config)

    modelo = _load_model(config)

    log.info(f"  [local] Transcrevendo offline: {file_path.name}")
    segmentos, info = modelo.transcribe(
        str(file_path),
        language=lang_code or None,
        vad_filter=True,
        beam_size=5,
        # So paga o custo dos timestamps por palavra quando ha turnos para casar.
        word_timestamps=bool(turnos),
    )

    duracao_total = float(getattr(info, "duration", 0) or 0)
    unidades = _montar_unidades(segmentos, turnos, duracao_total)
    utterances = _rotular(unidades)

    texto_completo = "\n".join(item.text for item in utterances if item.text)
    if not duracao_total:
        duracao_total = max((item.end_ms for item in utterances), default=0) / 1000

    falantes = len({item.speaker for item in utterances})
    if turnos:
        log.ok(
            f"  [local] Transcricao concluida: {len(utterances)} trecho(s), {falantes} falante(s). "
            "Nada saiu do computador."
        )
    else:
        log.ok(f"  [local] Transcricao concluida: {len(utterances)} trecho(s). Nada saiu do computador.")

    return NormalizedTranscriptResult(
        source_path=file_path,
        provider_id=PROVIDER_ID,
        provider_label=PROVIDER_LABEL,
        model=f"whisper-{config.model_size}",
        text=texto_completo,
        audio_duration_seconds=duracao_total,
        language_code=getattr(info, "language", "") or lang_code,
        utterances=utterances,
        raw_metadata={
            "diarization": bool(turnos),
            "diarization_backend": diarizer.BACKEND_ID if turnos else "",
            "speakers": falantes if turnos else 0,
            "device": config.device,
        },
    )


# ---------------------------------------------------------------------------
# Resumo local: estatistico, sem IA e sem rede.
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "a", "o", "e", "de", "da", "do", "das", "dos", "que", "em", "um", "uma", "uns", "umas",
    "para", "por", "com", "sem", "no", "na", "nos", "nas", "ao", "aos", "as", "os", "se",
    "eu", "voce", "ele", "ela", "eles", "elas", "meu", "minha", "seu", "sua", "isso",
    "isto", "aquilo", "mas", "mais", "menos", "muito", "pouco", "ja", "nao", "sim", "tambem",
    "so", "ate", "quando", "onde", "como", "porque", "entao", "assim", "aqui", "ali", "la",
    "ser", "estar", "ter", "fazer", "ir", "vai", "foi", "era", "sao", "esta", "tem", "the",
    "and", "of", "to", "in", "is", "it", "that", "this", "for", "on", "with", "you", "i",
    "tipo", "cara", "ne", "ta", "pra", "pro", "vamos", "gente", "coisa", "bem",
}


def _termos_frequentes(texto: str, quantidade: int = 12) -> list[tuple[str, int]]:
    palavras = re.findall(r"[a-zA-ZÀ-ÿ]{4,}", texto.lower())
    contagem = Counter(p for p in palavras if p not in _STOPWORDS)
    return contagem.most_common(quantidade)


def _secao_falantes(transcripts: list[NormalizedTranscriptResult]) -> list[str]:
    """
    Quanto cada falante ocupou da conversa. So aparece quando a separacao de
    falantes rodou de fato - com rotulo unico nao ha o que comparar.
    """
    tempos: dict[str, float] = {}
    palavras: dict[str, int] = {}
    for transcript in transcripts:
        for item in transcript.utterances:
            segundos = max(0, item.end_ms - item.start_ms) / 1000
            tempos[item.speaker] = tempos.get(item.speaker, 0.0) + segundos
            palavras[item.speaker] = palavras.get(item.speaker, 0) + len(item.text.split())

    if len(tempos) < 2:
        return []

    total = sum(tempos.values()) or 1.0
    linhas = [
        "### Quem falou quanto",
        "",
        "| Falante | Tempo de fala | Participacao | Palavras |",
        "|---|---|---|---|",
    ]
    for nome, segundos in sorted(tempos.items(), key=lambda par: par[1], reverse=True):
        linhas.append(
            f"| {nome} | {fmt_time(segundos)} | {segundos / total * 100:.0f}% | {palavras.get(nome, 0):,} |"
        )
    linhas.extend(
        [
            "",
            "_Os falantes foram separados pela voz, aqui na sua maquina. O aplicativo distingue_",
            "_vozes diferentes, mas nao sabe os nomes das pessoas - renomeie no arquivo se quiser._",
            "",
        ]
    )
    return linhas


def generate_summary(transcripts: list[NormalizedTranscriptResult], context_prompt: str) -> str:
    """
    Monta um panorama do conteudo sem usar nenhuma IA: numeros, termos mais
    recorrentes e trechos do proprio texto. Honesto sobre o que e e o que nao e.
    """
    if not transcripts:
        return "_Nenhuma transcricao para resumir._"

    duracao_total = sum(item.audio_duration_seconds or 0 for item in transcripts)
    palavras_total = sum(len((item.text or "").split()) for item in transcripts)

    linhas = [
        "> **Panorama gerado localmente, sem inteligencia artificial.**",
        "> No modo local nada e enviado para a internet - e por isso nao ha um resumo",
        "> interpretativo como o do modo nuvem. O que segue foi calculado a partir do",
        "> proprio texto transcrito: numeros, termos recorrentes e trechos reais.",
        "",
    ]

    if context_prompt.strip():
        linhas.extend(["**Contexto que voce informou:**", "", f"> {context_prompt.strip()}", ""])

    linhas.extend(
        [
            "### Numeros da sessao",
            "",
            f"- Arquivos transcritos: **{len(transcripts)}**",
            f"- Duracao total de audio: **{fmt_time(duracao_total)}**",
            f"- Palavras transcritas: **{palavras_total:,}**",
            "",
        ]
    )

    linhas.extend(_secao_falantes(transcripts))

    texto_geral = "\n".join(item.text or "" for item in transcripts)
    termos = _termos_frequentes(texto_geral)
    if termos:
        linhas.extend(["### Termos mais recorrentes", ""])
        linhas.append(" | ".join(f"`{palavra}` ({vezes}x)" for palavra, vezes in termos))
        linhas.append("")

    for indice, transcript in enumerate(transcripts, start=1):
        linhas.extend([f"### {indice}. {transcript.source_path.name}", ""])
        linhas.append(
            f"- Duracao: {fmt_time(transcript.audio_duration_seconds)} | "
            f"Palavras: {len((transcript.text or '').split()):,} | "
            f"Trechos: {len(transcript.utterances)}"
        )
        palavras = (transcript.text or "").split()
        trecho = " ".join(palavras[:180])
        if len(palavras) > 180:
            trecho += "..."
        linhas.extend(["", "**Inicio da transcricao:**", "", f"> {trecho or '_Sem conteudo falado._'}", ""])

    linhas.extend(
        [
            "---",
            "",
            "_Quer um resumo interpretativo (pontos-chave, decisoes, acoes)? Troque para o_",
            "_modo nuvem no aplicativo - lembrando que ele envia o conteudo ao Google Gemini._",
        ]
    )
    return "\n".join(linhas)
