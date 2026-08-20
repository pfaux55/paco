from __future__ import annotations

from dataclasses import dataclass
import re

from local_matrix_assistant.services.desktop_actions import DesktopActionError
from local_matrix_assistant.services.model_response import clean_model_text, extract_json_object, truncate_text


_BLOCKED_DESTRUCTIVE = re.compile(
    r"^\s*(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    r"(?:delete|download|execute|install|move|remove|rename)\b",
    re.IGNORECASE,
)
_INTENT_KINDS = {
    "answer",
    "clarify",
    "workspace_change",
    "workspace_create",
    "workspace_create_and_run",
    "workspace_question",
    "workspace_run",
}


@dataclass(frozen=True, slots=True)
class AgentIntent:
    kind: str
    request: str


class AgentIntentService:
    """Validate model routing for conversational Agent requests."""

    max_request_characters = 2_000

    @classmethod
    def can_interpret(cls, text: str) -> bool:
        cleaned = re.sub(r"\s+", " ", text).strip()
        return bool(
            cleaned
            and len(cleaned) <= cls.max_request_characters
            and _BLOCKED_DESTRUCTIVE.match(cleaned) is None
        )

    @classmethod
    def parse_response(cls, response: str) -> AgentIntent:
        payload = cls._json_payload(response)
        kind = clean_model_text(
            payload.get("kind"),
            "The model did not identify the request type.",
        ).casefold()
        request = clean_model_text(payload.get("request"), "The model did not interpret the request.")
        if kind not in _INTENT_KINDS:
            raise DesktopActionError(f"The model returned an unsupported request type: {kind}")
        request = truncate_text(request, cls.max_request_characters)
        return AgentIntent(kind=kind, request=request)

    @staticmethod
    def _json_payload(response: str) -> dict:
        return extract_json_object(
            response,
            invalid_response_message="The model returned an invalid Agent routing response.",
            invalid_json_prefix="The model returned invalid Agent routing JSON",
        )
