from __future__ import annotations

from dataclasses import dataclass
import json
import re

from local_matrix_assistant.services.desktop_actions import DesktopActionError


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
        kind = cls._clean_text(payload.get("kind"), "The model did not identify the request type.").casefold()
        request = cls._clean_text(payload.get("request"), "The model did not interpret the request.")
        if kind not in _INTENT_KINDS:
            raise DesktopActionError(f"The model returned an unsupported request type: {kind}")
        if len(request) > cls.max_request_characters:
            request = request[: cls.max_request_characters].rstrip() + "..."
        return AgentIntent(kind=kind, request=request)

    @staticmethod
    def _json_payload(response: str) -> dict:
        cleaned = response.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                cleaned = "\n".join(lines[1:-1]).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise DesktopActionError("The model returned an invalid Agent routing response.")
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise DesktopActionError(f"The model returned invalid Agent routing JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise DesktopActionError("The model returned an invalid Agent routing response.")
        return payload

    @staticmethod
    def _clean_text(value: object, error: str) -> str:
        if not isinstance(value, str):
            raise DesktopActionError(error)
        cleaned = re.sub(r"\s+", " ", value).strip()
        if not cleaned:
            raise DesktopActionError(error)
        return cleaned
