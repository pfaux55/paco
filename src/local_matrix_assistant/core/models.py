from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChatStreamResult:
    content: str
    thinking: str = ""
    canceled: bool = False
    total_duration_ns: int = 0
    load_duration_ns: int = 0
    prompt_eval_count: int = 0
    prompt_eval_duration_ns: int = 0
    eval_count: int = 0
    eval_duration_ns: int = 0

    @property
    def generation_tokens_per_second(self) -> float:
        if self.eval_count <= 0 or self.eval_duration_ns <= 0:
            return 0.0
        bounded_count = min(self.eval_count, 10_000_000)
        bounded_duration_ns = min(self.eval_duration_ns, 86_400_000_000_000)
        duration_seconds = max(bounded_duration_ns / 1_000_000_000, 0.000_001)
        return min(100_000.0, bounded_count / duration_seconds)


@dataclass(slots=True)
class ModelPullProgress:
    model: str
    status: str
    completed_bytes: int = 0
    total_bytes: int = 0

    @property
    def percent(self) -> int | None:
        if self.total_bytes <= 0:
            return None
        return min(100, max(0, round((self.completed_bytes / self.total_bytes) * 100)))


@dataclass(slots=True)
class ModelPullResult:
    model: str
    canceled: bool = False


@dataclass(slots=True)
class ConversationSummary:
    conversation_id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int
    preview: str = ""


@dataclass(slots=True)
class ConversationRecord:
    summary: "ConversationSummary"
    messages: list["ChatMessage"]
    memory: "ConversationMemory" = field(default_factory=lambda: ConversationMemory())


@dataclass(slots=True)
class ConversationMemory:
    content: str = ""
    covered_messages: int = 0
    updated_at: str = ""
    source: str = ""


@dataclass(slots=True)
class VoiceOption:
    voice_id: str
    label: str
    gender: str
    engine: str
    model_path: Path
    config_path: Path
    sample_text: str


@dataclass(slots=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str
    domain: str = ""
    published_at: str = ""
    source_type: str = "web"
    extracted_text: str = ""
    content_type: str = ""
    word_count: int = 0
    search_rank: int = 0
    provider: str = ""


@dataclass(slots=True)
class WebSearchResponse:
    provider: str
    query: str
    results: list[WebSearchResult]
    time_sensitive: bool = False
    canceled: bool = False


@dataclass(slots=True)
class StatusSnapshot:
    ollama_connected: bool
    ollama_message: str
    available_models: list[str]
    model_ready: bool
    model_name: str
    model_message: str
    mic_available: bool
    mic_message: str
    output_available: bool
    output_message: str
    stt_ready: bool
    stt_message: str
    tts_ready: bool
    tts_message: str
    guidance_message: str
