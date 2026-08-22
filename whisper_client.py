"""
whisper_client.py - Transcricao local via faster-whisper + diarizacao via pyannote.audio.

O audio e decodificado uma unica vez com PyAV (embutido no faster-whisper) para um
array 16 kHz mono, que alimenta tanto o Whisper quanto o pyannote. Isso dispensa o
binario do ffmpeg e evita gravar wav temporario.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

try:
    from .config import (
        DIARIZATION_MODEL,
        WHISPER_DEFAULT_COMPUTE_TYPE,
        WHISPER_DEFAULT_MODEL,
        WHISPER_SAMPLE_RATE,
    )
    from .logger import log
    from .models import NormalizedTranscriptResult, TranscriptUtterance, WhisperConfig
except ImportError:
    from config import (  # type: ignore
        DIARIZATION_MODEL,
        WHISPER_DEFAULT_COMPUTE_TYPE,
        WHISPER_DEFAULT_MODEL,
        WHISPER_SAMPLE_RATE,
    )
    from logger import log  # type: ignore
    from models import NormalizedTranscriptResult, TranscriptUtterance, WhisperConfig  # type: ignore

_MODEL_CACHE: dict[tuple[str, str, str], object] = {}
_DIARIZER_CACHE: dict[str, object] = {}


def _package_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def validate_environment(config: WhisperConfig) -> list[str]:
    errors = []
    if not _package_available("faster_whisper"):
        errors.append("Pacote Python ausente: pip install -r requirements-whisper.txt (faster-whisper)")
    if config.diarization:
        if not _package_available("pyannote.audio"):
            errors.append("Diarizacao ligada mas pyannote.audio nao esta instalado: pip install -r requirements-whisper.txt")
        if not _package_available("torch"):
            errors.append("Diarizacao ligada mas torch nao esta instalado: pip install -r requirements-whisper.txt")
        if not config.hf_token.strip():
            errors.append(
                "Diarizacao ligada mas falta o token do HuggingFace. Gere em "
                "huggingface.co/settings/tokens e aceite os termos de "
                f"huggingface.co/{DIARIZATION_MODEL}."
            )
    return errors


def resolve_device(requested: str) -> str:
    """Resolve 'auto' para cuda quando houver GPU utilizavel, senao cpu."""
    requested = (requested or "auto").strip().lower()
    if requested in ("cpu", "cuda"):
        return requested
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _resolve_compute_type(config: WhisperConfig, device: str) -> str:
    if config.compute_type.strip():
        return config.compute_type.strip()
    return "float16" if device == "cuda" else WHISPER_DEFAULT_COMPUTE_TYPE


def _load_model(model_size: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    key = (model_size, device, compute_type)
    if key not in _MODEL_CACHE:
        log.info(f"  [whisper] Carregando modelo '{model_size}' ({device}/{compute_type}). O primeiro uso baixa os pesos.")
        _MODEL_CACHE[key] = WhisperModel(model_size, device=device, compute_type=compute_type)
        log.ok(f"  [whisper] Modelo '{model_size}' carregado")
    return _MODEL_CACHE[key]


def _load_diarizer(hf_token: str, device: str):
    from pyannote.audio import Pipeline

    if DIARIZATION_MODEL not in _DIARIZER_CACHE:
        log.info(f"  [pyannote] Carregando '{DIARIZATION_MODEL}'. O primeiro uso baixa os pesos.")
        # O nome do parametro mudou no pyannote.audio 4.x (use_auth_token -> token).
        params = inspect.signature(Pipeline.from_pretrained).parameters
        token_kwarg = "token" if "token" in params else "use_auth_token"
        pipeline = Pipeline.from_pretrained(DIARIZATION_MODEL, **{token_kwarg: hf_token})
        if pipeline is None:
            raise RuntimeError(
                f"pyannote nao conseguiu carregar '{DIARIZATION_MODEL}'. Verifique se o token e valido "
                f"e se voce aceitou os termos em huggingface.co/{DIARIZATION_MODEL}."
            )
        if device == "cuda":
            import torch

            pipeline.to(torch.device("cuda"))
        _DIARIZER_CACHE[DIARIZATION_MODEL] = pipeline
        log.ok("  [pyannote] Modelo de diarizacao carregado")
    return _DIARIZER_CACHE[DIARIZATION_MODEL]


def _decode_audio(file_path: Path):
    from faster_whisper.audio import decode_audio

    log.info(f"  [whisper] Decodificando audio: {file_path.name}")
    return decode_audio(str(file_path), sampling_rate=WHISPER_SAMPLE_RATE)


def _run_diarization(audio, config: WhisperConfig, device: str) -> list[tuple[float, float, str]]:
    """Retorna [(start_s, end_s, label)] ordenado. Lista vazia se nao houver turnos."""
    import torch

    pipeline = _load_diarizer(config.hf_token.strip(), device)

    kwargs = {}
    if config.num_speakers > 0:
        kwargs["num_speakers"] = config.num_speakers
    else:
        if config.min_speakers > 0:
            kwargs["min_speakers"] = config.min_speakers
        if config.max_speakers > 0:
            kwargs["max_speakers"] = config.max_speakers

    waveform = torch.from_numpy(audio).unsqueeze(0)
    log.info(f"  [pyannote] Identificando falantes{' ' + str(kwargs) if kwargs else ''}...")
    annotation = pipeline({"waveform": waveform, "sample_rate": WHISPER_SAMPLE_RATE}, **kwargs)

    turns = [
        (float(segment.start), float(segment.end), str(label))
        for segment, _, label in annotation.itertracks(yield_label=True)
    ]
    turns.sort(key=lambda item: item[0])
    log.ok(f"  [pyannote] {len({turn[2] for turn in turns})} falante(s) em {len(turns)} turno(s)")
    return turns


def _speaker_names(turns: list[tuple[float, float, str]]) -> dict[str, str]:
    """Mapeia rotulos do pyannote (SPEAKER_00...) para 'Falante N' na ordem de entrada."""
    names: dict[str, str] = {}
    for _, _, label in turns:
        if label not in names:
            names[label] = f"Falante {len(names) + 1}"
    return names


def _speaker_for(start: float, end: float, turns: list[tuple[float, float, str]]) -> str | None:
    """Rotulo do turno com maior sobreposicao com [start, end]."""
    best_label = None
    best_overlap = 0.0
    for turn_start, turn_end, label in turns:
        if turn_start >= end:
            break
        overlap = min(end, turn_end) - max(start, turn_start)
        if overlap > best_overlap:
            best_overlap = overlap
            best_label = label
    return best_label


def transcribe_path(
    file_path: Path,
    lang_code: str,
    config: WhisperConfig,
    mime_type: str | None = None,
) -> NormalizedTranscriptResult:
    """Mesma assinatura de gemini_client.transcribe_path. mime_type e ignorado
    (o audio e decodificado localmente pelo PyAV)."""
    model_size = config.model.strip() or WHISPER_DEFAULT_MODEL
    device = resolve_device(config.device)
    compute_type = _resolve_compute_type(config, device)

    audio = _decode_audio(file_path)
    audio_seconds = len(audio) / WHISPER_SAMPLE_RATE

    model = _load_model(model_size, device, compute_type)
    log.info(f"  [whisper] Transcrevendo {file_path.name} ({audio_seconds / 60:.1f} min) com '{model_size}'")

    segments_iter, info = model.transcribe(
        audio,
        language=lang_code or None,
        vad_filter=True,
        beam_size=5,
    )

    total_seconds = float(getattr(info, "duration", 0) or audio_seconds)
    raw_segments: list[tuple[float, float, str]] = []
    next_log_at = 10.0
    for segment in segments_iter:
        text = (segment.text or "").strip()
        if text:
            raw_segments.append((float(segment.start), float(segment.end), text))
        if total_seconds > 0:
            progress = segment.end / total_seconds * 100
            if progress >= next_log_at:
                log.info(f"  [whisper] {min(progress, 100):.0f}% ({segment.end / 60:.1f}/{total_seconds / 60:.1f} min)")
                next_log_at = (int(progress) // 10 + 1) * 10.0

    log.ok(f"  [whisper] {len(raw_segments)} segmento(s) transcritos")

    turns: list[tuple[float, float, str]] = []
    diarization_used = False
    if config.diarization and raw_segments:
        try:
            turns = _run_diarization(audio, config, device)
            diarization_used = bool(turns)
        except Exception as exc:
            log.exception("  [pyannote] Diarizacao falhou; seguindo com um unico falante", exc=exc)

    names = _speaker_names(turns)
    utterances = [
        TranscriptUtterance(
            speaker=names.get(_speaker_for(start, end, turns) or "", "Falante 1"),
            start_ms=int(start * 1000),
            end_ms=int(end * 1000),
            text=text,
        )
        for start, end, text in raw_segments
    ]

    full_text = "\n".join(item.text for item in utterances if item.text)
    duration = total_seconds or (max((item.end_ms for item in utterances), default=0) / 1000)
    label = "Whisper local + pyannote" if diarization_used else "Whisper local"

    return NormalizedTranscriptResult(
        source_path=file_path,
        provider_id="whisper",
        provider_label=label,
        model=model_size,
        text=full_text,
        audio_duration_seconds=duration,
        language_code=getattr(info, "language", "") or lang_code,
        utterances=utterances,
        raw_metadata={
            "device": device,
            "compute_type": compute_type,
            "diarization": diarization_used,
            "speakers": len(names),
        },
    )
