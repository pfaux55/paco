from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from local_matrix_assistant.services.desktop_actions import DesktopActionError
from local_matrix_assistant.services.model_response import clean_model_text, extract_json_object
from local_matrix_assistant.services.workspace_actions import WorkspaceActionService


_INVESTIGATIVE_START = re.compile(
    r"^\s*(?:please\s+)?(?:(?:can|could|would)\s+you\s+)?"
    r"(?:explain|find|locate|identify|trace|determine|check|review|understand|map|summarize|"
    r"where|why|how|what|which|show\s+me|tell\s+me)\b",
    re.IGNORECASE,
)
_WORKSPACE_SIGNAL = re.compile(
    r"\b(?:api|app(?:lication)?|architecture|auth(?:entication|orization)?|bug|cache|caching|class|"
    r"code|codebase|component|config|configuration|configured|controller|database|error|file|flow|"
    r"function|handler|implementation|login|model|module|project|repo(?:sitory)?|request|response|"
    r"route|service|setting|source|startup|test|tests|token|validation|worker|workspace)\b",
    re.IGNORECASE,
)
_MUTATING_START = re.compile(
    r"^\s*(?:create|delete|download|edit|execute|fix|install|launch|make|modify|move|open|remove|"
    r"rename|repair|replace|resolve|run|start|update|write)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class WorkspaceTaskStep:
    tool: str
    reason: str
    path: str = ""
    query: str = ""

    @property
    def target(self) -> str:
        return self.path if self.tool == "read_file" else self.query


@dataclass(frozen=True, slots=True)
class WorkspaceTaskPlan:
    summary: str
    steps: tuple[WorkspaceTaskStep, ...]

    def display(self) -> str:
        lines = [self.summary]
        for step in self.steps:
            label = f"read {step.path}" if step.tool == "read_file" else f'search for "{step.query}"'
            lines.append(f"- {label}: {step.reason}")
        return "\n".join(lines)


class WorkspaceTaskService:
    """Validate a small model-authored plan containing read-only workspace tools."""

    max_request_characters = 1_000
    max_steps = 4
    max_search_steps = 2
    max_summary_characters = 600
    max_reason_characters = 300
    max_search_characters = 100
    max_search_files = 240
    max_search_bytes = 3 * 1024 * 1024
    max_search_matches = 60

    @classmethod
    def can_plan(cls, text: str) -> bool:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if len(cleaned) < 10 or len(cleaned) > cls.max_request_characters:
            return False
        if _MUTATING_START.match(cleaned):
            return False
        return bool(_INVESTIGATIVE_START.match(cleaned) and _WORKSPACE_SIGNAL.search(cleaned))

    @classmethod
    def clean_request(cls, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cls.can_plan(cleaned):
            raise DesktopActionError("Describe a read-only question about the selected workspace.")
        return cleaned.rstrip(".!?")

    @classmethod
    def parse_plan(cls, response: str, allowed_files: tuple[str, ...]) -> WorkspaceTaskPlan:
        payload = cls._json_payload(response)
        summary = clean_model_text(
            payload.get("summary"),
            "The reasoning model did not provide a task summary.",
        )
        if len(summary) > cls.max_summary_characters:
            summary = summary[: cls.max_summary_characters].rstrip() + "..."
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise DesktopActionError("The reasoning model did not provide a read-only investigation step.")
        if len(raw_steps) > cls.max_steps:
            raise DesktopActionError(f"The investigation exceeds the {cls.max_steps}-step safety limit.")

        allowed = {path.replace("\\", "/").casefold(): path for path in allowed_files}
        steps: list[WorkspaceTaskStep] = []
        seen: set[tuple[str, str]] = set()
        search_steps = 0
        for raw_step in raw_steps:
            if not isinstance(raw_step, dict):
                raise DesktopActionError("The reasoning model returned an invalid investigation step.")
            tool = clean_model_text(
                raw_step.get("tool"),
                "An investigation tool is missing.",
            ).casefold()
            reason = clean_model_text(
                raw_step.get("reason"),
                "An investigation step reason is missing.",
            )
            if len(reason) > cls.max_reason_characters:
                reason = reason[: cls.max_reason_characters].rstrip() + "..."
            if tool == "read_file":
                requested = clean_model_text(raw_step.get("path"), "A read step path is missing.")
                canonical = allowed.get(requested.replace("\\", "/").casefold())
                if canonical is None:
                    raise DesktopActionError(
                        f"The reasoning model tried to read a file outside the reviewed evidence: {requested}"
                    )
                key = (tool, canonical.casefold())
                step = WorkspaceTaskStep(tool=tool, path=canonical, reason=reason)
            elif tool == "search_files":
                query = clean_model_text(raw_step.get("query"), "A search step query is missing.")
                if len(query) < 2:
                    raise DesktopActionError("A planned search query must contain at least two characters.")
                if len(query) > cls.max_search_characters:
                    raise DesktopActionError(
                        f"A planned search exceeds the {cls.max_search_characters}-character limit."
                    )
                search_steps += 1
                if search_steps > cls.max_search_steps:
                    raise DesktopActionError(
                        f"The investigation exceeds the {cls.max_search_steps}-search safety limit."
                    )
                key = (tool, query.casefold())
                step = WorkspaceTaskStep(tool=tool, query=query, reason=reason)
            else:
                raise DesktopActionError(f"Unsupported planned workspace tool: {tool}")
            if key in seen:
                raise DesktopActionError(f"The investigation repeats the same {tool} step.")
            seen.add(key)
            steps.append(step)
        return WorkspaceTaskPlan(summary=summary, steps=tuple(steps))

    @classmethod
    def search_allowed_files(
        cls,
        workspace_actions: WorkspaceActionService,
        query: str,
        allowed_files: tuple[str, ...],
        should_cancel: Callable[[], bool],
    ) -> str:
        needle = query.casefold()
        matches: list[str] = []
        scanned_files = 0
        scanned_bytes = 0
        truncated = False
        for relative_path in allowed_files[: cls.max_search_files]:
            if should_cancel():
                break
            try:
                snapshot = workspace_actions.load_edit_target(relative_path)
            except DesktopActionError:
                continue
            size = len(snapshot.original_bytes)
            if scanned_bytes + size > cls.max_search_bytes:
                truncated = True
                break
            scanned_files += 1
            scanned_bytes += size
            for line_number, line in enumerate(snapshot.content.splitlines(), start=1):
                if needle not in line.casefold():
                    continue
                compact = re.sub(r"\s+", " ", line).strip()
                matches.append(f"{snapshot.relative_path}:{line_number}: {compact[:240]}")
                if len(matches) >= cls.max_search_matches:
                    truncated = True
                    break
            if len(matches) >= cls.max_search_matches:
                break
        body = (
            f'Literal search results for "{query}" across {scanned_files} eligible workspace files:\n'
            + ("\n".join(matches) if matches else "No matches found.")
        )
        if truncated:
            body += "\n... bounded search limits reached."
        return body

    @staticmethod
    def _json_payload(response: str) -> dict:
        return extract_json_object(
            response,
            invalid_response_message="The reasoning model returned an invalid investigation plan.",
            invalid_json_prefix="The reasoning model returned invalid task-plan JSON",
        )
