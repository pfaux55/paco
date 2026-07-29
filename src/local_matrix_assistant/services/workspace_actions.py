from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import ast
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tomllib
from typing import Callable
import uuid

from local_matrix_assistant.services.desktop_actions import (
    DesktopActionError,
    DesktopActionResult,
    DesktopActionService,
)


_POLITE_PREFIX = r"(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
_QUOTED = r'(?:"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')'
_LIST_FILES_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:list|show)(?:\s+the)?(?:\s+(?:project|workspace))?\s+files"
    r"(?:\s+(?:in|under)\s+(?P<target>.+?))?\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_READ_FILE_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:read|show|view)(?:\s+the)?\s+file\s+(?P<target>.+?)\s*[!?]*\s*$",
    re.IGNORECASE,
)
_SEARCH_FILES_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}search(?:\s+the)?(?:\s+(?:project|workspace))?\s+files\s+for\s+"
    rf"(?P<query>{_QUOTED})(?:\s+in\s+(?P<target>{_QUOTED}|\S+))?\s*[!?]*\s*$",
    re.IGNORECASE | re.DOTALL,
)
_REPLACE_FILE_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}replace(?P<all>\s+all)?\s+in(?:\s+the)?(?:\s+file)?\s+"
    rf"(?P<target>{_QUOTED}|\S+)\s+(?:text\s+)?(?P<match>{_QUOTED})\s+with\s+"
    rf"(?P<replacement>{_QUOTED})\s*[!?]*\s*$",
    re.IGNORECASE | re.DOTALL,
)
_EDIT_FILE_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:edit|update|modify)(?:\s+the)?\s+file\s+"
    rf"(?P<target>{_QUOTED}|\S+)(?:\s+(?:to|so\s+that|with\s+instructions?)\s+|\s*:\s*)"
    r"(?P<instruction>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_CREATE_GENERATED_FILE_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:create|make)(?:\s+me)?\s+(?:a\s+)?(?:new\s+)?"
    r"(?:(?:python|javascript|typescript|json|toml|markdown|html|css|sql|text)\s+)?file"
    rf"(?:\s+(?:called|named))?\s+(?P<target>{_QUOTED}|\S+)"
    r"\s+(?P<bridge>to|that|which)\s+(?P<instruction>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_RUN_AFTER_CREATE = re.compile(
    r"\s+(?:(?:and\s+)?then|and)\s+(?:run|execute)(?:\s+it|\s+the\s+file)?\s*[.!?]*$",
    re.IGNORECASE,
)
_ANALYZE_WORKSPACE_COMMANDS = (
    re.compile(
        rf"^\s*{_POLITE_PREFIX}(?:analyze|review|inspect|audit)(?:\s+the)?\s+"
        r"(?:project|workspace|codebase)(?:\s+(?:for|about))?(?:\s+(?P<query>.+?))?\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"^\s*{_POLITE_PREFIX}explain(?:\s+the)?\s+(?:project|workspace|codebase)"
        r"(?:\s+(?P<query>.+?))?\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"^\s*{_POLITE_PREFIX}explain\s+(?P<query>.+?)\s+in(?:\s+the)?\s+"
        r"(?:project|workspace|codebase)\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"^\s*{_POLITE_PREFIX}investigate(?:\s+(?:the\s+)?(?:project|workspace|codebase))?"
        r"(?:\s+(?:for|why|how|whether))?\s+(?P<query>.+?)\s*$",
        re.IGNORECASE | re.DOTALL,
    ),
)
_FIX_WORKSPACE_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:fix|repair|resolve)"
    r"(?:\s+(?:the\s+)?(?:project|workspace|codebase)(?:\s+(?:bug|issue|problem))?"
    r"|\s+(?:the\s+)?(?:bug|issue|problem))"
    r"(?:\s*:\s*|\s+(?:where|that|with|causing)\s+|\s+)(?P<query>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_INCOMPLETE_FIX_WORKSPACE_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:fix|repair|resolve)(?:\s+(?:the\s+)?(?:project|workspace|codebase))?"
    r"(?:\s+(?:bug|issue|problem))?\s*:?[\s.!?]*$",
    re.IGNORECASE,
)
_EDIT_FILES_PREFIX = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:edit|update|modify)(?:\s+the)?\s+files\s+(?P<details>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_INSTRUCTION_SEPARATOR = re.compile(
    r"(?:\s+(?:to|so\s+that|with\s+instructions?)\s+|(?:\s+:\s*|\s*:\s+))",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class WorkspaceAction:
    kind: str
    target: str = "."
    query: str = ""
    replacement: str = ""
    replace_all: bool = False
    targets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceFileSnapshot:
    path: Path
    relative_path: str
    content: str
    original_bytes: bytes
    had_bom: bool
    digest: str


@dataclass(frozen=True, slots=True)
class WorkspaceEditPreview:
    path: Path
    relative_path: str
    original_content: str
    proposed_content: str
    original_digest: str
    original_bytes: bytes
    had_bom: bool
    diff: str
    model: str = ""


@dataclass(frozen=True, slots=True)
class WorkspaceCreatePreview:
    path: Path
    workspace_root: Path
    relative_path: str
    proposed_content: str
    diff: str
    model: str = ""
    run_after_create: bool = False


@dataclass(frozen=True, slots=True)
class WorkspaceBatchEditPreview:
    edits: tuple[WorkspaceEditPreview, ...]
    diff: str
    model: str = ""
    plan: str = ""
    operation: str = "edit"

    @property
    def relative_path(self) -> str:
        return f"{len(self.edits)} files"


class WorkspaceActionService:
    """Bounded read/search/edit operations inside the active Agent folder."""

    max_file_bytes = 2 * 1024 * 1024
    max_read_characters = 50_000
    max_read_lines = 400
    max_listed_files = 240
    max_scanned_files = 600
    max_search_matches = 100
    max_walk_depth = 6
    max_model_edit_tokens = 2800
    max_batch_file_tokens = 2200
    max_batch_input_tokens = 4000
    max_batch_files = 4
    max_diff_characters = 60_000
    max_generated_file_characters = 100_000
    ignored_directories = {
        ".git",
        ".hg",
        ".svn",
        ".jarvis-backups",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".venv-win",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "target",
        "venv",
    }

    def __init__(self, desktop_actions: DesktopActionService) -> None:
        self.desktop_actions = desktop_actions

    def parse(self, text: str) -> WorkspaceAction | None:
        fix_match = _FIX_WORKSPACE_COMMAND.match(text)
        if fix_match:
            query = fix_match.group("query").strip().rstrip(".!?")
            if not query or query.casefold() in {"bug", "issue", "problem"}:
                raise DesktopActionError("Describe the workspace issue to fix.")
            if len(query) > 1_000:
                raise DesktopActionError("The workspace fix request is too long; keep it under 1,000 characters.")
            return WorkspaceAction("draft_workspace_fix", query=query)
        if _INCOMPLETE_FIX_WORKSPACE_COMMAND.match(text):
            raise DesktopActionError("Describe the workspace issue to fix.")

        for pattern in _ANALYZE_WORKSPACE_COMMANDS:
            analysis_match = pattern.match(text)
            if analysis_match:
                query = (analysis_match.groupdict().get("query") or "").strip().rstrip(".!?")
                query = query or "Explain the architecture, main components, and execution flow"
                if len(query) > 1_000:
                    raise DesktopActionError(
                        "The workspace analysis question is too long; keep it under 1,000 characters."
                    )
                return WorkspaceAction("analyze_workspace", query=query)

        create_match = _CREATE_GENERATED_FILE_COMMAND.match(text)
        if create_match:
            if create_match.group("bridge").lower() in {"that", "which"} and re.match(
                r"contains?\b",
                create_match.group("instruction"),
                flags=re.IGNORECASE,
            ):
                return None
            target = self._unquote(create_match.group("target"))
            instruction = create_match.group("instruction").strip()
            run_after_create = bool(_RUN_AFTER_CREATE.search(instruction))
            if run_after_create:
                instruction = _RUN_AFTER_CREATE.sub("", instruction).strip()
            instruction = instruction.rstrip(".!?")
            if not target:
                raise DesktopActionError("No file name was provided.")
            if not instruction:
                raise DesktopActionError("Describe the file to create.")
            if len(instruction) > 2_000:
                raise DesktopActionError("The new-file request is too long; keep it under 2,000 characters.")
            return WorkspaceAction(
                "draft_create_and_run" if run_after_create else "draft_create",
                target=target,
                query=instruction,
            )

        replace_match = _REPLACE_FILE_COMMAND.match(text)
        if replace_match:
            target = self._unquote(replace_match.group("target"))
            match_text = self._unquote(replace_match.group("match"))
            replacement = self._unquote(replace_match.group("replacement"))
            if not match_text:
                raise DesktopActionError("Replacement text cannot be empty.")
            return WorkspaceAction(
                "replace_file",
                target=target,
                query=match_text,
                replacement=replacement,
                replace_all=bool(replace_match.group("all")),
            )

        files_match = _EDIT_FILES_PREFIX.match(text)
        if files_match:
            details = files_match.group("details").strip()
            separator = self._find_unquoted_separator(details, _INSTRUCTION_SEPARATOR)
            if separator is None:
                raise DesktopActionError("Use 'edit files file1, file2 to <change>' or a colon before the change.")
            raw_targets = details[: separator.start()].strip()
            instruction = details[separator.end() :].strip()
            targets = self._split_quoted_targets(raw_targets)
            if len(targets) < 2:
                raise DesktopActionError("Multi-file edits require at least two comma-separated file paths.")
            if len(targets) > self.max_batch_files:
                raise DesktopActionError(f"Review at most {self.max_batch_files} files in one edit.")
            normalized = [os.path.normcase(target) for target in targets]
            if len(set(normalized)) != len(normalized):
                raise DesktopActionError("Each file may appear only once in a multi-file edit.")
            if not instruction:
                raise DesktopActionError("Describe the coordinated change to make.")
            return WorkspaceAction("draft_batch_edit", query=instruction, targets=tuple(targets))

        edit_match = _EDIT_FILE_COMMAND.match(text)
        if edit_match:
            target = self._unquote(edit_match.group("target"))
            instruction = edit_match.group("instruction").strip()
            if not instruction:
                raise DesktopActionError("Describe the change to make.")
            return WorkspaceAction("draft_edit", target=target, query=instruction)

        search_match = _SEARCH_FILES_COMMAND.match(text)
        if search_match:
            query = self._unquote(search_match.group("query"))
            target = self._unquote(search_match.group("target") or ".")
            if not query:
                raise DesktopActionError("Search text cannot be empty.")
            if len(query) > 500:
                raise DesktopActionError("Search text is too long.")
            return WorkspaceAction("search_files", target=target, query=query)

        read_match = _READ_FILE_COMMAND.match(text)
        if read_match:
            target = self._unquote(read_match.group("target"))
            return WorkspaceAction("read_file", target=target)

        list_match = _LIST_FILES_COMMAND.match(text)
        if list_match:
            target = self._unquote(list_match.group("target") or ".")
            return WorkspaceAction("list_files", target=target)
        return None

    def execute(
        self,
        action: WorkspaceAction,
        should_cancel: Callable[[], bool] | None = None,
    ) -> DesktopActionResult:
        if action.kind == "list_files":
            return self._list_files(action, should_cancel)
        if action.kind == "read_file":
            return self._read_file(action)
        if action.kind == "search_files":
            return self._search_files(action, should_cancel)
        if action.kind == "replace_file":
            return self._replace_file(action)
        raise DesktopActionError(f"Unsupported workspace action: {action.kind}")

    def load_edit_target(self, target: str) -> WorkspaceFileSnapshot:
        path = self._resolve_existing(target, expect="file")
        original = self._read_bytes(path)
        content, had_bom = self._decode_text(path, original)
        return WorkspaceFileSnapshot(
            path=path,
            relative_path=self._relative(path),
            content=content,
            original_bytes=original,
            had_bom=had_bom,
            digest=hashlib.sha256(original).hexdigest(),
        )

    def workspace_root(self) -> Path:
        return self._workspace_root()

    def iter_workspace_files(self):
        yield from self._walk_files(self._workspace_root())

    def prepare_edit(
        self,
        snapshot: WorkspaceFileSnapshot,
        proposed_content: str,
        *,
        model: str = "",
        require_complete_diff: bool = False,
    ) -> WorkspaceEditPreview:
        proposed = self._preserve_newlines(snapshot.content, proposed_content)
        if proposed == snapshot.content:
            raise DesktopActionError("The proposed edit contains no file changes.")
        self._validate_content(snapshot.path, proposed)
        diff_lines = difflib.unified_diff(
            snapshot.content.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            fromfile=f"a/{snapshot.relative_path}",
            tofile=f"b/{snapshot.relative_path}",
        )
        diff = "".join(diff_lines)
        if len(diff) > self.max_diff_characters:
            if require_complete_diff:
                raise DesktopActionError(
                    "The proposed diff exceeds the complete-review safety limit. Choose a smaller change scope."
                )
            diff = diff[: self.max_diff_characters].rstrip() + "\n... diff preview truncated."
        return WorkspaceEditPreview(
            path=snapshot.path,
            relative_path=snapshot.relative_path,
            original_content=snapshot.content,
            proposed_content=proposed,
            original_digest=snapshot.digest,
            original_bytes=snapshot.original_bytes,
            had_bom=snapshot.had_bom,
            diff=diff,
            model=model,
        )

    def prepare_create(
        self,
        target: str,
        proposed_content: str,
        *,
        model: str = "",
        run_after_create: bool = False,
    ) -> WorkspaceCreatePreview:
        workspace_root = self._workspace_root()
        path = self.resolve_create_target(target)
        if run_after_create and path.suffix.casefold() != ".py":
            raise DesktopActionError("Create-and-run currently supports Python source files only.")
        proposed = proposed_content.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not proposed:
            raise DesktopActionError("The local model returned an empty file proposal.")
        if len(proposed) > self.max_generated_file_characters:
            raise DesktopActionError(
                f"The generated file exceeds the {self.max_generated_file_characters:,}-character safety limit."
            )
        proposed += "\n"
        self._validate_content(path, proposed)
        relative_path = path.relative_to(workspace_root).as_posix()
        diff_lines = difflib.unified_diff(
            [],
            proposed.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile=f"b/{relative_path}",
        )
        diff = "".join(diff_lines)
        if len(diff) > self.max_diff_characters:
            diff = diff[: self.max_diff_characters].rstrip() + "\n... diff preview truncated."
        return WorkspaceCreatePreview(
            path=path,
            workspace_root=workspace_root,
            relative_path=relative_path,
            proposed_content=proposed,
            diff=diff,
            model=model,
            run_after_create=run_after_create,
        )

    def resolve_create_target(self, target: str) -> Path:
        workspace_root = self._workspace_root()
        path = self.desktop_actions.resolve_output_path(target, default_suffix=".txt").resolve()
        if not self._is_inside(path, workspace_root):
            raise DesktopActionError("New workspace files must stay inside the active Agent folder.")
        if os.path.lexists(path):
            raise DesktopActionError(f"The file already exists and was not overwritten: {path}")
        return path

    def prepare_batch_edit(
        self,
        edits: list[WorkspaceEditPreview],
        *,
        model: str = "",
        plan: str = "",
        operation: str = "edit",
        max_files: int | None = None,
        require_complete_diff: bool = False,
    ) -> WorkspaceBatchEditPreview:
        if not edits:
            raise DesktopActionError("The proposed batch contains no file changes.")
        file_limit = self.max_batch_files if max_files is None else min(50, max(1, max_files))
        if len(edits) > file_limit:
            raise DesktopActionError(f"Review at most {file_limit} files in one edit.")
        paths = [str(edit.path.resolve()) for edit in edits]
        if len({os.path.normcase(path) for path in paths}) != len(paths):
            raise DesktopActionError("A batch edit cannot include the same file twice.")
        combined = "\n\n".join(edit.diff.rstrip() for edit in edits if edit.diff.strip())
        if len(combined) > self.max_diff_characters:
            if require_complete_diff:
                raise DesktopActionError(
                    "The combined diff exceeds the complete-review safety limit. Choose a smaller change scope."
                )
            combined = combined[: self.max_diff_characters].rstrip() + "\n... combined diff preview truncated."
        return WorkspaceBatchEditPreview(
            tuple(edits),
            combined,
            model=model,
            plan=plan,
            operation=operation,
        )

    def apply_edit(self, preview: WorkspaceEditPreview) -> DesktopActionResult:
        path = self._resolve_existing(str(preview.path), expect="file")
        if path != preview.path.resolve():
            raise DesktopActionError("The reviewed file target changed; the edit was not applied.")
        current = self._read_bytes(path)
        if hashlib.sha256(current).hexdigest() != preview.original_digest:
            raise DesktopActionError("The file changed after the preview was generated; review a new edit instead.")
        self._validate_content(path, preview.proposed_content)
        backup = self._write_backup(path, current)
        encoded = preview.proposed_content.encode("utf-8")
        if preview.had_bom:
            encoded = b"\xef\xbb\xbf" + encoded
        self._atomic_replace(path, encoded)
        message = f"Applied reviewed edit to {preview.relative_path}.\nBackup: {backup}"
        return DesktopActionResult("apply_edit", message, str(path))

    def apply_create(self, preview: WorkspaceCreatePreview) -> DesktopActionResult:
        workspace_root = self._workspace_root()
        if workspace_root != preview.workspace_root.resolve():
            raise DesktopActionError("The active Agent folder changed; review the new file again.")
        current_path = preview.path.resolve()
        if current_path != preview.path or not self._is_inside(current_path, workspace_root):
            raise DesktopActionError("The reviewed new-file target changed; the file was not created.")
        if os.path.lexists(preview.path):
            raise DesktopActionError(
                f"The file was created after the preview and was not overwritten: {preview.relative_path}"
            )
        self._validate_content(preview.path, preview.proposed_content)
        try:
            preview.path.parent.mkdir(parents=True, exist_ok=True)
            resolved_after_parent_create = preview.path.resolve()
            if resolved_after_parent_create != preview.path or not self._is_inside(
                resolved_after_parent_create,
                workspace_root,
            ):
                raise DesktopActionError("The reviewed new-file target changed; the file was not created.")
            with preview.path.open("xb") as handle:
                handle.write(preview.proposed_content.encode("utf-8"))
        except FileExistsError as exc:
            raise DesktopActionError(
                f"The file was created after the preview and was not overwritten: {preview.relative_path}"
            ) from exc
        except DesktopActionError:
            raise
        except OSError as exc:
            raise DesktopActionError(f"Could not create the reviewed file: {exc}") from exc
        return DesktopActionResult(
            "apply_create",
            f"Created reviewed file: {preview.relative_path}",
            str(preview.path),
        )

    def apply_batch_edit(self, preview: WorkspaceBatchEditPreview) -> DesktopActionResult:
        if not preview.edits:
            raise DesktopActionError("The batch edit is empty.")
        resolved: list[tuple[WorkspaceEditPreview, Path, bytes]] = []
        for edit in preview.edits:
            path = self._resolve_existing(str(edit.path), expect="file")
            current = self._read_bytes(path)
            if hashlib.sha256(current).hexdigest() != edit.original_digest:
                raise DesktopActionError(
                    f"{edit.relative_path} changed after preview; no batch files were modified."
                )
            self._validate_content(path, edit.proposed_content)
            resolved.append((edit, path, current))

        backups: list[Path] = []
        for _edit, path, current in resolved:
            backups.append(self._write_backup(path, current))

        applied: list[tuple[WorkspaceEditPreview, Path]] = []
        try:
            for edit, path, _current in resolved:
                latest = self._read_bytes(path)
                if hashlib.sha256(latest).hexdigest() != edit.original_digest:
                    raise DesktopActionError(f"{edit.relative_path} changed while applying the batch.")
                encoded = edit.proposed_content.encode("utf-8")
                if edit.had_bom:
                    encoded = b"\xef\xbb\xbf" + encoded
                self._atomic_replace(path, encoded)
                applied.append((edit, path))
        except Exception as exc:
            rollback_failures: list[str] = []
            for edit, path in reversed(applied):
                try:
                    self._atomic_replace(path, edit.original_bytes)
                except DesktopActionError:
                    rollback_failures.append(edit.relative_path)
            if rollback_failures:
                raise DesktopActionError(
                    "Batch apply failed and automatic rollback was incomplete for: "
                    + ", ".join(rollback_failures)
                    + ". Restore those files from the reported backups."
                ) from exc
            raise DesktopActionError(f"Batch apply failed; all modified files were restored. {exc}") from exc

        names = ", ".join(edit.relative_path for edit, _path, _current in resolved)
        backup_lines = "\n".join(f"- {backup}" for backup in backups)
        return DesktopActionResult(
            "apply_batch_edit",
            f"Applied reviewed batch edit to {len(resolved)} files: {names}.\nBackups:\n{backup_lines}",
            str(self._workspace_root()),
        )

    def _list_files(
        self,
        action: WorkspaceAction,
        should_cancel: Callable[[], bool] | None = None,
    ) -> DesktopActionResult:
        directory = self._resolve_existing(action.target, expect="directory")
        entries: list[str] = []
        truncated = False
        for path in self._walk_files(directory):
            if should_cancel is not None and should_cancel():
                truncated = True
                break
            if len(entries) >= self.max_listed_files:
                truncated = True
                break
            try:
                size = path.stat().st_size
            except OSError:
                continue
            entries.append(f"{self._relative(path)}  ({self._format_size(size)})")
        heading = f"Files under {self._relative(directory)}:"
        if not entries:
            body = f"{heading}\nNo files found."
        else:
            body = heading + "\n" + "\n".join(entries)
            if truncated:
                body += f"\n... showing the first {self.max_listed_files} files."
        return DesktopActionResult("list_files", body, str(directory))

    def _read_file(self, action: WorkspaceAction) -> DesktopActionResult:
        path = self._resolve_existing(action.target, expect="file")
        text, _bom = self._read_text(path)
        lines = text.splitlines()
        visible_lines = lines[: self.max_read_lines]
        rendered = "\n".join(f"{index:>4} | {line}" for index, line in enumerate(visible_lines, start=1))
        truncated = len(lines) > self.max_read_lines or len(rendered) > self.max_read_characters
        if len(rendered) > self.max_read_characters:
            rendered = rendered[: self.max_read_characters].rstrip()
        if truncated:
            rendered += "\n... file output truncated."
        if not rendered:
            rendered = "(empty file)"
        body = f"{self._relative(path)}\n{rendered}"
        return DesktopActionResult("read_file", body, str(path))

    def _search_files(
        self,
        action: WorkspaceAction,
        should_cancel: Callable[[], bool] | None = None,
    ) -> DesktopActionResult:
        directory = self._resolve_existing(action.target, expect="directory")
        needle = action.query.casefold()
        matches: list[str] = []
        scanned = 0
        truncated = False
        for path in self._walk_files(directory):
            if should_cancel is not None and should_cancel():
                truncated = True
                break
            if scanned >= self.max_scanned_files:
                truncated = True
                break
            scanned += 1
            try:
                text, _bom = self._read_text(path)
            except DesktopActionError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if needle not in line.casefold():
                    continue
                compact = re.sub(r"\s+", " ", line).strip()
                matches.append(f"{self._relative(path)}:{line_number}: {compact[:240]}")
                if len(matches) >= self.max_search_matches:
                    truncated = True
                    break
            if len(matches) >= self.max_search_matches:
                break
        heading = f'Search results for "{action.query}" under {self._relative(directory)}:'
        body = heading + "\n" + ("\n".join(matches) if matches else "No matches found.")
        if truncated:
            body += "\n... search limits reached; narrow the folder or query."
        return DesktopActionResult("search_files", body, str(directory))

    def _replace_file(self, action: WorkspaceAction) -> DesktopActionResult:
        path = self._resolve_existing(action.target, expect="file")
        original = self._read_bytes(path)
        text, had_bom = self._decode_text(path, original)
        occurrences = text.count(action.query)
        if occurrences == 0:
            raise DesktopActionError(f"The exact text was not found in {self._relative(path)}.")
        if occurrences > 1 and not action.replace_all:
            raise DesktopActionError(
                f"The exact text occurs {occurrences} times. Use 'replace all in file' to change every match."
            )
        updated = text.replace(action.query, action.replacement, -1 if action.replace_all else 1)
        replacement_count = occurrences if action.replace_all else 1
        backup = self._write_backup(path, original)
        encoded = updated.encode("utf-8")
        if had_bom:
            encoded = b"\xef\xbb\xbf" + encoded
        try:
            if path.read_bytes() != original:
                raise DesktopActionError("The file changed while Agent was preparing the edit; it was not overwritten.")
        except OSError as exc:
            raise DesktopActionError(f"Could not verify the file before editing: {exc}") from exc
        self._atomic_replace(path, encoded)
        body = (
            f"Updated {self._relative(path)} ({replacement_count} exact replacement"
            f"{'s' if replacement_count != 1 else ''}).\nBackup: {backup}"
        )
        return DesktopActionResult("replace_file", body, str(path))

    def _resolve_existing(self, target: str, *, expect: str) -> Path:
        if not target.strip() or "\x00" in target:
            raise DesktopActionError("The workspace path is invalid.")
        root = self._workspace_root()
        requested = Path(target).expanduser()
        path = requested.resolve() if requested.is_absolute() else (root / requested).resolve()
        if not self._is_inside(path, root):
            raise DesktopActionError("Workspace commands must stay inside the active Agent folder.")
        if not path.exists():
            raise DesktopActionError(f"Workspace path not found: {path}")
        if expect == "file" and not path.is_file():
            raise DesktopActionError(f"Expected a file: {path}")
        if expect == "directory" and not path.is_dir():
            raise DesktopActionError(f"Expected a folder: {path}")
        return path

    def _workspace_root(self) -> Path:
        root = self.desktop_actions.active_working_folder or self.desktop_actions.default_files_dir
        resolved = root.resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise DesktopActionError("Choose an existing folder in the Agent tab first.")
        return resolved

    def _walk_files(self, directory: Path):
        root = self._workspace_root()
        for current, subdirectories, filenames in os.walk(directory, followlinks=False):
            current_path = Path(current)
            try:
                depth = len(current_path.relative_to(directory).parts)
            except ValueError:
                continue
            subdirectories[:] = sorted(
                name
                for name in subdirectories
                if name not in self.ignored_directories and depth < self.max_walk_depth
            )
            for filename in sorted(filenames):
                path = (current_path / filename).resolve()
                if self._is_inside(path, root) and path.is_file():
                    yield path

    def _read_text(self, path: Path) -> tuple[str, bool]:
        data = self._read_bytes(path)
        return self._decode_text(path, data)

    def _read_bytes(self, path: Path) -> bytes:
        try:
            size = path.stat().st_size
            if size > self.max_file_bytes:
                raise DesktopActionError(
                    f"File is larger than the {self._format_size(self.max_file_bytes)} safety limit: {self._relative(path)}"
                )
            data = path.read_bytes()
        except DesktopActionError:
            raise
        except OSError as exc:
            raise DesktopActionError(f"Could not read {self._relative(path)}: {exc}") from exc
        return data

    def _decode_text(self, path: Path, data: bytes) -> tuple[str, bool]:
        if b"\x00" in data[:8192]:
            raise DesktopActionError(f"Binary files cannot be read or edited: {self._relative(path)}")
        had_bom = data.startswith(b"\xef\xbb\xbf")
        try:
            text = data.decode("utf-8-sig" if had_bom else "utf-8")
        except UnicodeDecodeError as exc:
            raise DesktopActionError(f"File is not UTF-8 text: {self._relative(path)}") from exc
        return text, had_bom

    def _write_backup(self, path: Path, data: bytes) -> Path:
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
        backup_root = self.desktop_actions.default_files_dir.resolve() / ".jarvis-backups" / digest
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = backup_root / f"{path.name}.{timestamp}.bak"
        try:
            backup_root.mkdir(parents=True, exist_ok=True)
            with backup.open("xb") as handle:
                handle.write(data)
        except OSError as exc:
            raise DesktopActionError(f"Could not create a safety backup; the file was not changed: {exc}") from exc
        return backup

    @staticmethod
    def _atomic_replace(path: Path, data: bytes) -> None:
        temporary = path.with_name(f".{path.name}.jarvis-{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(data)
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            os.replace(temporary, path)
        except OSError as exc:
            raise DesktopActionError(f"Could not update the file atomically: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _preserve_newlines(original: str, proposed: str) -> str:
        normalized = proposed.replace("\r\n", "\n").replace("\r", "\n")
        if original.endswith(("\r\n", "\n", "\r")):
            normalized = normalized.rstrip("\n") + "\n"
        else:
            normalized = normalized.rstrip("\n")
        if "\r\n" in original:
            return normalized.replace("\n", "\r\n")
        if "\r" in original and "\n" not in original:
            return normalized.replace("\n", "\r")
        return normalized

    @staticmethod
    def _validate_content(path: Path, content: str) -> None:
        suffix = path.suffix.lower()
        try:
            if suffix == ".py":
                ast.parse(content, filename=str(path))
            elif suffix == ".json":
                json.loads(content)
            elif suffix == ".toml":
                tomllib.loads(content)
        except (SyntaxError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
            label = suffix.lstrip(".").upper() or "file"
            raise DesktopActionError(f"Proposed {label} content is invalid: {exc}") from exc

    def _relative(self, path: Path) -> str:
        root = self._workspace_root()
        try:
            relative = path.relative_to(root)
        except ValueError:
            return str(path)
        return "." if not relative.parts else relative.as_posix()

    @staticmethod
    def _find_unquoted_separator(text: str, pattern: re.Pattern[str]) -> re.Match[str] | None:
        quote = ""
        escaped = False
        for match in pattern.finditer(text):
            quote = ""
            escaped = False
            for character in text[: match.start()]:
                if escaped:
                    escaped = False
                    continue
                if character == "\\":
                    escaped = True
                    continue
                if character in {'"', "'"}:
                    if not quote:
                        quote = character
                    elif quote == character:
                        quote = ""
            if not quote:
                return match
        return None

    @classmethod
    def _split_quoted_targets(cls, value: str) -> list[str]:
        targets: list[str] = []
        start = 0
        quote = ""
        escaped = False
        for index, character in enumerate(value):
            if escaped:
                escaped = False
                continue
            if character == "\\":
                escaped = True
                continue
            if character in {'"', "'"}:
                if not quote:
                    quote = character
                elif quote == character:
                    quote = ""
            elif character == "," and not quote:
                target = cls._unquote(value[start:index].strip())
                if target:
                    targets.append(target)
                start = index + 1
        if quote:
            raise DesktopActionError("A multi-file path has an unmatched quote.")
        target = cls._unquote(value[start:].strip())
        if target:
            targets.append(target)
        return targets

    @staticmethod
    def _is_inside(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _unquote(value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
            quote = cleaned[0]
            cleaned = cleaned[1:-1]
            cleaned = cleaned.replace("\\\\", "\\").replace(f"\\{quote}", quote)
        if "\x00" in cleaned:
            raise DesktopActionError("The workspace value is invalid.")
        return cleaned

    @staticmethod
    def _format_size(size: int) -> str:
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"
