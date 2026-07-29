from __future__ import annotations

from dataclasses import dataclass
import math
import re

from local_matrix_assistant.core.models import ChatMessage


_TRUNCATION_MARKER = "\n\n[... middle omitted to fit local model context ...]\n\n"
_ESTIMATED_TOKENS_PER_IMAGE = 1024


@dataclass(frozen=True, slots=True)
class ContextStats:
    total_messages: int
    retained_messages: int
    trimmed_messages: int
    estimated_tokens: int
    token_budget: int
    latest_message_truncated: bool = False
    memory_messages: int = 0
    memory_tokens: int = 0

    @property
    def usage_ratio(self) -> float:
        return self.estimated_tokens / self.token_budget if self.token_budget else 0.0

    @property
    def unsummarized_messages(self) -> int:
        return max(0, self.trimmed_messages - self.memory_messages)

    def note(self) -> str:
        parts = [f"Context: ~{self.estimated_tokens:,} / {self.token_budget:,} input tokens"]
        if self.retained_messages:
            parts.append(f"{self.retained_messages} recent messages")
        if self.memory_messages:
            parts.append(f"{self.memory_messages} older messages summarized")
        if self.unsummarized_messages:
            parts.append(f"{self.unsummarized_messages} older messages omitted")
        if self.latest_message_truncated:
            parts.append("newest message shortened from the middle")
        return " | ".join(parts) + "."


@dataclass(frozen=True, slots=True)
class ContextSelection:
    messages: list[ChatMessage]
    stats: ContextStats


class ContextManager:
    """Build conservative local-model context while preserving recent turns."""

    @staticmethod
    def estimate_text_tokens(text: str) -> int:
        if not text:
            return 0
        ascii_chars = sum(1 for character in text if ord(character) < 128)
        non_ascii_chars = len(text) - ascii_chars
        code_like = bool(re.search(r"```|[{};]|\b(?:def|class|function|const|SELECT|FROM)\b", text))
        divisor = 3.1 if code_like else 4.0
        character_estimate = math.ceil(ascii_chars / divisor) + math.ceil(non_ascii_chars * 0.75)
        word_estimate = math.ceil(len(re.findall(r"\S+", text)) * 0.72)
        return max(1, character_estimate, word_estimate)

    @classmethod
    def estimate_message_tokens(cls, message: ChatMessage) -> int:
        return cls.estimate_text_tokens(message.content) + 8 + cls._estimate_image_tokens(message)

    @classmethod
    def estimate_messages_tokens(cls, messages: list[ChatMessage]) -> int:
        return sum(cls.estimate_message_tokens(message) for message in messages)

    @classmethod
    def select_recent_turns(cls, messages: list[ChatMessage], token_budget: int) -> ContextSelection:
        safe_budget = max(128, int(token_budget))
        if not messages:
            return ContextSelection([], ContextStats(0, 0, 0, 0, safe_budget))

        turns = cls._group_turns(messages)
        selected_reversed: list[list[ChatMessage]] = []
        remaining = safe_budget
        latest_truncated = False

        for reverse_index, turn in enumerate(reversed(turns)):
            turn_tokens = cls.estimate_messages_tokens(turn)
            if turn_tokens <= remaining:
                selected_reversed.append(turn)
                remaining -= turn_tokens
                continue
            if reverse_index == 0:
                fitted = cls._fit_latest_turn(turn, remaining)
                if fitted:
                    selected_reversed.append(fitted)
                    latest_truncated = True
            break

        selected_turns = list(reversed(selected_reversed))
        selected = [message for turn in selected_turns for message in turn]
        retained_count = len(selected)
        stats = ContextStats(
            total_messages=len(messages),
            retained_messages=retained_count,
            trimmed_messages=max(0, len(messages) - retained_count),
            estimated_tokens=cls.estimate_messages_tokens(selected),
            token_budget=safe_budget,
            latest_message_truncated=latest_truncated,
        )
        return ContextSelection(selected, stats)

    @classmethod
    def truncate_text(cls, text: str, max_tokens: int) -> tuple[str, bool]:
        limit = max(16, int(max_tokens))
        if cls.estimate_text_tokens(text) <= limit:
            return text, False

        marker_tokens = cls.estimate_text_tokens(_TRUNCATION_MARKER)
        if limit <= marker_tokens + 16:
            character_limit = max(16, limit * 3)
            return text[:character_limit].rstrip() + "\n\n[... truncated ...]", True

        available_characters = max(32, (limit - marker_tokens) * 3)
        while available_characters > 24:
            head_length = int(available_characters * 0.62)
            tail_length = available_characters - head_length
            candidate = text[:head_length].rstrip() + _TRUNCATION_MARKER + text[-tail_length:].lstrip()
            if cls.estimate_text_tokens(candidate) <= limit:
                return candidate, True
            available_characters = int(available_characters * 0.88)
        return text[: max(16, limit * 2)].rstrip() + "\n\n[... truncated ...]", True

    @staticmethod
    def _group_turns(messages: list[ChatMessage]) -> list[list[ChatMessage]]:
        turns: list[list[ChatMessage]] = []
        current: list[ChatMessage] = []
        for message in messages:
            if message.role == "user" and current:
                turns.append(current)
                current = [message]
            else:
                current.append(message)
        if current:
            turns.append(current)
        return turns

    @classmethod
    def _fit_latest_turn(cls, turn: list[ChatMessage], token_budget: int) -> list[ChatMessage]:
        if token_budget <= 12 or not turn:
            return []
        latest_user = next((message for message in reversed(turn) if message.role == "user"), turn[-1])
        text_budget = token_budget - 8 - cls._estimate_image_tokens(latest_user)
        if text_budget <= 12:
            return []
        content, _truncated = cls.truncate_text(latest_user.content, text_budget)
        if not content:
            return []
        return [
            ChatMessage(
                role=latest_user.role,
                content=content,
                timestamp=latest_user.timestamp,
                metadata={**latest_user.metadata, "context_truncated": True},
            )
        ]

    @staticmethod
    def _estimate_image_tokens(message: ChatMessage) -> int:
        attachments = message.metadata.get("attachments", []) if isinstance(message.metadata, dict) else []
        if not isinstance(attachments, list):
            return 0
        count = sum(
            1
            for attachment in attachments
            if isinstance(attachment, dict) and bool(attachment.get("image_data"))
        )
        return min(3, count) * _ESTIMATED_TOKENS_PER_IMAGE
