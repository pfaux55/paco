from __future__ import annotations

from dataclasses import dataclass
import re

from local_matrix_assistant.services.command_router import POLITE_PREFIX as _POLITE_PREFIX
from local_matrix_assistant.services.desktop_actions import DesktopActionError


_CHANGE_START = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?P<verb>add|allow|change|delete|ensure|fix|handle|implement|improve|introduce|"
    r"make|prevent|refactor|remove|rename|repair|resolve|support|update)\b",
    re.IGNORECASE,
)
_TECHNICAL_SIGNAL = re.compile(
    r"\b(?:api|app|application|architecture|auth|authentication|authorization|button|cache|class|"
    r"code|component|config|configuration|controller|database|documentation|endpoint|error|export|"
    r"file|flow|form|function|handler|import|input|layout|login|menu|model|module|navigation|parser|"
    r"project|request|response|route|screen|service|setting|sidebar|startup|test|tests|token|ui|"
    r"validation|view|voice|widget|window|worker|workspace)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_DESTRUCTIVE = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:(?:download|execute|install|launch|open|run|start)\b|"
    r"(?:delete|move|remove|rename)\s+(?:the\s+)?(?:file|folder|directory)\b|"
    r"(?:move|rename)\s+(?:the\s+)?\S+\.[a-zA-Z0-9]+\s+to\b)",
    re.IGNORECASE,
)
_EXPLICIT_NEW_FILE = re.compile(
    r"\b(?:new\s+file|file\s+(?:called|named)|create\s+(?:a\s+)?file)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class NaturalWorkspaceChange:
    kind: str
    request: str


class WorkspaceChangeService:
    """Recognize broad coding changes that can safely enter the reviewed edit pipeline."""

    max_request_characters = 1_000

    @classmethod
    def parse(cls, text: str) -> NaturalWorkspaceChange | None:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if len(cleaned) < 10 or len(cleaned) > cls.max_request_characters:
            return None
        if _UNSUPPORTED_DESTRUCTIVE.match(cleaned) or _EXPLICIT_NEW_FILE.search(cleaned):
            return None
        match = _CHANGE_START.match(cleaned)
        if match is None or _TECHNICAL_SIGNAL.search(cleaned) is None:
            return None
        verb = match.group("verb").casefold()
        kind = "fix" if verb in {"fix", "repair", "resolve"} else "change"
        return NaturalWorkspaceChange(kind=kind, request=cleaned.rstrip(".!?"))

    @classmethod
    def require_request(cls, text: str) -> NaturalWorkspaceChange:
        request = cls.parse(text)
        if request is None:
            raise DesktopActionError("Describe a supported change to existing workspace code or documentation.")
        return request
