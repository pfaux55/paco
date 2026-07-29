from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

from local_matrix_assistant.services.desktop_actions import DesktopActionError


_POLITE_PREFIX = r"(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
_CREATION_START = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:build|create|develop|generate|implement|make|scaffold|write)\b",
    re.IGNORECASE,
)
_CREATION_SIGNAL = re.compile(
    r"\b(?:api|app|application|code|component|file|game|page|program|project|script|service|site|tool|"
    r"webapp|website)\b",
    re.IGNORECASE,
)
_SAFE_SOURCE_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_RUN_AFTER_CREATION = re.compile(
    r"\b(?:(?:and\s+)?then|and)\s+(?:run|execute)(?:\s+it|\s+the\s+(?:file|script))?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class NaturalWorkspaceCreation:
    request: str
    run_after_create: bool = False


@dataclass(frozen=True, slots=True)
class WorkspaceCreationPlan:
    path: str
    instructions: str


class WorkspaceCreationService:
    """Recognize broad creation requests and validate a single-file model plan."""

    max_request_characters = 1_000
    max_instruction_characters = 2_000
    max_path_characters = 240

    @classmethod
    def parse(cls, text: str) -> NaturalWorkspaceCreation | None:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if len(cleaned) < 10 or len(cleaned) > cls.max_request_characters:
            return None
        if _CREATION_START.match(cleaned) is None or _CREATION_SIGNAL.search(cleaned) is None:
            return None
        return NaturalWorkspaceCreation(
            cleaned.rstrip(".!?"),
            run_after_create=bool(_RUN_AFTER_CREATION.search(cleaned)),
        )

    @classmethod
    def parse_plan(cls, response: str) -> WorkspaceCreationPlan:
        payload = cls._json_payload(response)
        path = cls._clean_text(payload.get("path"), "The coding model did not choose a file path.")
        instructions = cls._clean_text(
            payload.get("instructions"),
            "The coding model did not describe the file to create.",
        )
        if len(path) > cls.max_path_characters:
            raise DesktopActionError("The proposed file path is too long.")
        if len(instructions) > cls.max_instruction_characters:
            instructions = instructions[: cls.max_instruction_characters].rstrip() + "..."

        requested = Path(path)
        if requested.is_absolute() or ".." in requested.parts or not requested.name:
            raise DesktopActionError("The coding model proposed an unsafe file path.")
        if any(part.startswith(".") for part in requested.parts):
            raise DesktopActionError("The coding model proposed a hidden file path.")
        if requested.suffix.casefold() not in _SAFE_SOURCE_SUFFIXES:
            raise DesktopActionError("The coding model proposed an unsupported file type.")
        return WorkspaceCreationPlan(path=path.replace("\\", "/"), instructions=instructions)

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
            raise DesktopActionError("The coding model returned an invalid new-file plan.")
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise DesktopActionError(f"The coding model returned invalid new-file JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise DesktopActionError("The coding model returned an invalid new-file plan.")
        return payload

    @staticmethod
    def _clean_text(value: object, error: str) -> str:
        if not isinstance(value, str):
            raise DesktopActionError(error)
        cleaned = re.sub(r"\s+", " ", value).strip()
        if not cleaned:
            raise DesktopActionError(error)
        return cleaned
