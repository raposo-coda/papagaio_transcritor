"""
models.py - Tipos normalizados para pipeline e Gemini.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TranscriptUtterance:
    speaker: str
    start_ms: int
    end_ms: int
    text: str


@dataclass
class TranscriptChapter:
    headline: str
    summary: str
    start_ms: int
    end_ms: int


@dataclass
class TranscriptEntity:
    entity_type: str
    text: str


@dataclass
class NormalizedTranscriptResult:
    source_path: Path
    provider_id: str
    provider_label: str
    model: str
    text: str
    audio_duration_seconds: float = 0.0
    language_code: str = ""
    transcript_id: str = ""
    utterances: list[TranscriptUtterance] = field(default_factory=list)
    chapters: list[TranscriptChapter] = field(default_factory=list)
    entities: list[TranscriptEntity] = field(default_factory=list)
    raw_metadata: dict = field(default_factory=dict)


@dataclass
class GeminiConfig:
    api_key: str = ""
    transcription_model: str = ""
    summary_model: str = ""


@dataclass
class LocalConfig:
    """Configuracao do modo local (Whisper offline)."""

    model_size: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"
    diarize: bool = True  # separar os falantes (offline, via sherpa-onnx)
    num_speakers: int = 0  # 0 = descobrir automaticamente


@dataclass
class PipelineRequest:
    file_paths: list[Path]
    lang_code: str
    output_dir: Path
    title: str
    context_prompt: str
    gemini: GeminiConfig
    mode: str = "cloud"  # "cloud" (Gemini) ou "local" (Whisper offline)
    local: LocalConfig = field(default_factory=LocalConfig)
    summary_enabled: bool = True  # desligado: o consolidado sai so como indice
    job_id: str = ""  # identifica a execucao; desempata pastas de sessao sem titulo
