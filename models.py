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
class WhisperConfig:
    model: str = ""
    device: str = "auto"  # auto | cpu | cuda
    compute_type: str = ""  # vazio = int8 na cpu, float16 na gpu
    diarization: bool = True
    hf_token: str = ""
    num_speakers: int = 0  # 0 = deixar o pyannote decidir
    min_speakers: int = 0
    max_speakers: int = 0


@dataclass
class PipelineRequest:
    file_paths: list[Path]
    lang_code: str
    output_dir: Path
    title: str
    context_prompt: str
    gemini: GeminiConfig
    provider: str = "gemini"
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    summary_enabled: bool = True
