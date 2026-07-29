from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecommendedModel:
    name: str
    label: str
    purpose: str
    approximate_size: str

    @property
    def display_name(self) -> str:
        return f"{self.label} - {self.name} ({self.approximate_size})"


RECOMMENDED_MODELS = (
    RecommendedModel(
        "llama3.2:3b",
        "Fast",
        "Low-latency general chat and short requests.",
        "~2 GB",
    ),
    RecommendedModel(
        "qwen3.5:4b",
        "Balanced + Vision",
        "Strong general reasoning and local image understanding.",
        "~3.5 GB",
    ),
    RecommendedModel(
        "qwen2.5-coder:7b",
        "Coding",
        "Deeper code generation, debugging, and repository work.",
        "~5 GB",
    ),
)

RECOMMENDED_MODEL_NAMES = frozenset(model.name for model in RECOMMENDED_MODELS)
