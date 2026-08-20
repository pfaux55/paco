from __future__ import annotations

from dataclasses import dataclass
import re


MODEL_PROFILES = ("auto", "fast", "balanced", "coding", "reasoning", "manual")
PROFILE_LABELS = {
    "auto": "Auto",
    "fast": "Fast",
    "balanced": "Balanced",
    "coding": "Coding",
    "reasoning": "Reasoning",
    "manual": "Manual",
    "vision": "Vision",
}


@dataclass(frozen=True, slots=True)
class ModelSelection:
    model: str
    profile: str
    reason: str
    automatic: bool
    context_window: int = 8192
    max_output_tokens: int = 1536

    @property
    def profile_label(self) -> str:
        return PROFILE_LABELS.get(self.profile, self.profile.title())

    @property
    def input_token_budget(self) -> int:
        return max(512, self.context_window - self.max_output_tokens - 256)


_CODE_PATTERNS = (
    re.compile(r"```|`[^`\n]+`"),
    re.compile(r"\b(?:debug|refactor|implement|compile|stack\s*trace|traceback|exception|unit\s+test)\b", re.I),
    re.compile(r"\b(?:code|coding|bug|function|method|repository|codebase|pull\s+request|frontend|backend|endpoint|algorithm|script)\b", re.I),
    re.compile(r"\b(?:python|javascript|typescript|java|c\+\+|c#|rust|golang|sql|html|css|react|api)\b", re.I),
    re.compile(r"\b[\w.-]+\.(?:py|js|ts|tsx|jsx|java|cpp|cs|rs|go|sql|html|css|json|yaml|yml)\b", re.I),
)
_REASONING_PATTERNS = (
    re.compile(r"\b(?:analy[sz]e|reason|prove|derive|evaluate|trade-?offs?|architecture|root cause)\b", re.I),
    re.compile(r"\b(?:compare|strategy|plan|design|investigate|diagnose)\b.*\b(?:options?|approaches?|system|solution)\b", re.I),
    re.compile(r"\bstep[- ]by[- ]step\b|\bthink through\b", re.I),
    re.compile(r"\bpros?\s+and\s+cons?\b", re.I),
)
_PROFILE_PRIORITIES = {
    "fast": ("llama3.2:3b", "qwen3.5:4b", "qwen3:4b", "gemma3:1b"),
    "balanced": ("qwen3.5:4b", "qwen3:4b", "llama3.1:8b", "llama3.2:3b", "qwen2.5-coder:7b"),
    "coding": ("qwen2.5-coder:7b", "qwen2.5-coder", "qwen3.5:4b", "qwen3:4b", "llama3.1:8b"),
    "reasoning": ("qwen3.5:4b", "qwen3:4b", "llama3.1:8b", "qwen2.5-coder:7b", "llama3.2:3b"),
}
_VISION_PRIORITIES = (
    "qwen3.5:4b",
    "qwen3-vl:4b",
    "qwen3.5:2b",
    "qwen3-vl:2b",
    "gemma3:4b",
    "granite3.2-vision",
)
_PROFILE_BUDGETS = {
    "fast": (8192, 768),
    "balanced": (8192, 1536),
    "coding": (8192, 2048),
    "reasoning": (8192, 2048),
    "manual": (8192, 1536),
    "vision": (8192, 1536),
}


class ModelRouter:
    """Choose a safe installed Ollama model for an 8 GB-class local GPU."""

    def select(
        self,
        prompt: str,
        requested_profile: str,
        available_models: list[str],
        manual_model: str,
        *,
        requires_vision: bool = False,
    ) -> ModelSelection:
        profile = requested_profile if requested_profile in MODEL_PROFILES else "auto"
        available = self._deduplicate(available_models)
        if requires_vision:
            candidates = [
                model
                for model in self._safe_candidates(available)
                if self.is_vision_model(model)
            ]
            manual = manual_model.strip()
            if manual in candidates:
                model = manual
            else:
                model = self._preferred_named_model(_VISION_PRIORITIES, candidates)
            if not model and candidates:
                model = candidates[0]
            context_window, max_output_tokens = _PROFILE_BUDGETS["vision"]
            return ModelSelection(
                model=model,
                profile="vision",
                reason=(
                    "Image attachment routed to a local vision model"
                    if model
                    else "No installed local vision model can process the image attachment"
                ),
                automatic=True,
                context_window=context_window,
                max_output_tokens=max_output_tokens,
            )
        if profile == "manual":
            model = manual_model.strip() or (available[0] if available else "")
            context_window, max_output_tokens = _PROFILE_BUDGETS["manual"]
            return ModelSelection(
                model,
                "manual",
                "Manual model selected",
                automatic=False,
                context_window=context_window,
                max_output_tokens=max_output_tokens,
            )

        reason = f"{PROFILE_LABELS.get(profile, profile.title())} profile selected"
        effective_profile = profile
        if profile == "auto":
            effective_profile, reason = self.classify(prompt)

        candidates = self._safe_candidates(available)
        model = self._preferred_model(effective_profile, candidates)
        if not model and manual_model.strip() in available:
            model = manual_model.strip()
        if not model and candidates:
            model = candidates[0]
        if not model and available:
            model = available[0]
        if not model:
            model = manual_model.strip()

        context_window, max_output_tokens = _PROFILE_BUDGETS.get(
            effective_profile,
            _PROFILE_BUDGETS["balanced"],
        )
        return ModelSelection(
            model=model,
            profile=effective_profile,
            reason=reason,
            automatic=profile == "auto",
            context_window=context_window,
            max_output_tokens=max_output_tokens,
        )

    @staticmethod
    def system_prompt(profile: str) -> str:
        base = (
            "You are Paco, a private local desktop assistant. Be accurate, direct, and useful. "
            "State uncertainty plainly and never claim an action succeeded unless the provided context confirms it."
        )
        guidance = {
            "fast": " Answer briefly and prioritize the direct result.",
            "balanced": " Give a clear answer with enough explanation to support the recommendation.",
            "coding": (
                " For coding tasks, produce maintainable, production-ready solutions, preserve stated constraints, "
                "and use fenced code blocks with the correct language label."
            ),
            "reasoning": (
                " Analyze the problem carefully, then provide conclusions, key factors, and trade-offs without "
                "revealing private chain-of-thought."
            ),
            "manual": " Adapt depth and format to the request.",
            "vision": (
                " Analyze attached images carefully, distinguish visible evidence from inference, and say when "
                "details are unreadable or uncertain."
            ),
        }.get(profile, " Adapt depth and format to the request.")
        return base + guidance

    @staticmethod
    def classify(prompt: str) -> tuple[str, str]:
        text = prompt.strip()
        if any(pattern.search(text) for pattern in _CODE_PATTERNS):
            return "coding", "Code-related request"
        if any(pattern.search(text) for pattern in _REASONING_PATTERNS):
            return "reasoning", "Reasoning-intensive request"

        word_count = len(re.findall(r"\b\w+\b", text))
        question_count = text.count("?")
        if word_count <= 24 and question_count <= 1 and text.count("\n") <= 1:
            return "fast", "Short general request"
        if word_count >= 90 or question_count >= 3:
            return "reasoning", "Long or multi-part request"
        return "balanced", "General request"

    @staticmethod
    def _deduplicate(models: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for model in models:
            cleaned = model.strip()
            key = cleaned.lower()
            if cleaned and key not in seen:
                seen.add(key)
                result.append(cleaned)
        return result

    @staticmethod
    def _safe_candidates(models: list[str]) -> list[str]:
        safe = [model for model in models if not ModelRouter._is_oversized(model)]
        return safe or models

    @staticmethod
    def _is_oversized(model: str) -> bool:
        normalized = model.lower()
        if "mistral-nemo" in normalized:
            return True
        sizes = [float(value) for value in re.findall(r"(?<![a-z0-9])(\d+(?:\.\d+)?)b\b", normalized)]
        return any(size > 8.1 for size in sizes)

    @staticmethod
    def _preferred_model(profile: str, models: list[str]) -> str:
        priorities = _PROFILE_PRIORITIES.get(profile, _PROFILE_PRIORITIES["balanced"])
        return ModelRouter._preferred_named_model(priorities, models)

    @staticmethod
    def _preferred_named_model(priorities: tuple[str, ...], models: list[str]) -> str:
        normalized = [(model, model.lower()) for model in models]
        for preferred in priorities:
            for model, lowered in normalized:
                if lowered == preferred or lowered.startswith(f"{preferred}:") or preferred in lowered:
                    return model
        return ""

    @staticmethod
    def is_vision_model(model: str) -> bool:
        normalized = model.strip().lower()
        if any(
            marker in normalized
            for marker in ("qwen3.5", "qwen3-vl", "granite3.2-vision", "llama3.2-vision", "ministral-3")
        ):
            return True
        if normalized in {"gemma3", "gemma3:latest"}:
            return True
        return bool(re.search(r"\bgemma3:(?:4b|12b|27b)(?:\b|-)", normalized))
