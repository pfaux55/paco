from __future__ import annotations

from dataclasses import dataclass
import json
import re

from local_matrix_assistant.services.desktop_actions import DesktopActionError
from local_matrix_assistant.services.workspace_actions import WorkspaceBatchEditPreview, WorkspaceEditPreview


@dataclass(frozen=True, slots=True)
class WorkspaceFixFile:
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class WorkspaceFixPlan:
    summary: str
    files: tuple[WorkspaceFixFile, ...]

    def display(self) -> str:
        lines = [self.summary]
        lines.extend(f"- {item.path}: {item.reason}" for item in self.files)
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class WorkspaceFixPreview:
    edit: WorkspaceEditPreview | WorkspaceBatchEditPreview
    investigation: str
    plan: WorkspaceFixPlan
    issue: str
    operation: str = "fix"

    @property
    def relative_path(self) -> str:
        return self.edit.relative_path

    @property
    def diff(self) -> str:
        return self.edit.diff

    @property
    def model(self) -> str:
        return self.edit.model


class WorkspaceFixService:
    max_files = 3
    max_summary_characters = 800
    max_reason_characters = 400

    @classmethod
    def parse_plan(cls, response: str, allowed_files: tuple[str, ...]) -> WorkspaceFixPlan:
        payload = cls._json_payload(response)
        summary = cls._clean_text(payload.get("summary"), "The reasoning model did not provide a fix summary.")
        if len(summary) > cls.max_summary_characters:
            summary = summary[: cls.max_summary_characters].rstrip() + "..."
        raw_files = payload.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise DesktopActionError("The reasoning model could not identify a supported file to change.")
        if len(raw_files) > cls.max_files:
            raise DesktopActionError(f"The proposed fix exceeds the {cls.max_files}-file review limit.")

        allowed = {path.casefold(): path for path in allowed_files}
        files: list[WorkspaceFixFile] = []
        seen: set[str] = set()
        for item in raw_files:
            if not isinstance(item, dict):
                raise DesktopActionError("The reasoning model returned an invalid fix file entry.")
            requested = cls._clean_text(item.get("path"), "A proposed fix file path is missing.")
            canonical = allowed.get(requested.replace("\\", "/").casefold())
            if canonical is None:
                raise DesktopActionError(
                    f"The reasoning model selected a file outside the reviewed evidence: {requested}"
                )
            key = canonical.casefold()
            if key in seen:
                raise DesktopActionError(f"The reasoning model selected {canonical} more than once.")
            seen.add(key)
            reason = cls._clean_text(item.get("reason"), "No reason supplied.")
            if len(reason) > cls.max_reason_characters:
                reason = reason[: cls.max_reason_characters].rstrip() + "..."
            files.append(WorkspaceFixFile(canonical, reason))
        return WorkspaceFixPlan(summary=summary, files=tuple(files))

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
            raise DesktopActionError("The reasoning model returned an invalid fix plan.")
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise DesktopActionError(f"The reasoning model returned invalid fix-plan JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise DesktopActionError("The reasoning model returned an invalid fix plan.")
        return payload

    @staticmethod
    def _clean_text(value: object, error: str) -> str:
        if not isinstance(value, str):
            raise DesktopActionError(error)
        cleaned = re.sub(r"\s+", " ", value).strip()
        if not cleaned:
            raise DesktopActionError(error)
        return cleaned
