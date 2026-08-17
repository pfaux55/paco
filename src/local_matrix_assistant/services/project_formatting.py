from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil
import tempfile
from typing import Callable

from local_matrix_assistant.services.desktop_actions import DesktopActionError, DesktopActionResult
from local_matrix_assistant.services.project_tasks import ProjectTaskPlan, ProjectTaskService
from local_matrix_assistant.services.workspace_actions import (
    WorkspaceActionService,
    WorkspaceBatchEditPreview,
    WorkspaceEditPreview,
)


@dataclass(frozen=True, slots=True)
class ProjectFormatEvent:
    kind: str
    text: str


class ProjectFormattingService:
    """Run a known formatter on a temporary copy and return a reviewed workspace diff."""

    max_staged_files = 2500
    max_staged_bytes = 80 * 1024 * 1024
    max_changed_files = 40

    def __init__(
        self,
        project_tasks: ProjectTaskService,
        workspace_actions: WorkspaceActionService,
    ) -> None:
        self.project_tasks = project_tasks
        self.workspace_actions = workspace_actions

    def preview(
        self,
        plan: ProjectTaskPlan,
        on_event: Callable[[ProjectFormatEvent], None],
        should_cancel: Callable[[], bool],
    ) -> WorkspaceBatchEditPreview | DesktopActionResult:
        if plan.kind != "format":
            raise DesktopActionError("Only a formatting plan can create a formatter preview.")
        root = self.workspace_actions.workspace_root().resolve()
        if plan.cwd.resolve() != root:
            raise DesktopActionError("The active Agent folder changed before formatting started.")

        on_event(ProjectFormatEvent("phase", "Copying eligible project files into isolated staging..."))
        with tempfile.TemporaryDirectory(prefix="paco-format-") as temporary:
            stage = Path(temporary).resolve() / "workspace"
            stage.mkdir(parents=True)
            copied = self._copy_workspace(root, stage, should_cancel)
            if should_cancel():
                return self._canceled_result()
            if not copied:
                raise DesktopActionError("No eligible workspace files were available to format.")

            on_event(ProjectFormatEvent("phase", f"Running {plan.label} in isolated staging..."))
            result = self.project_tasks.run_staged(
                plan,
                stage,
                lambda text: on_event(ProjectFormatEvent("output", text)),
                should_cancel,
            )
            if result.canceled or should_cancel():
                return self._canceled_result()
            if not result.success:
                raise DesktopActionError(
                    f"{result.summary} The selected workspace was not changed."
                )

            on_event(ProjectFormatEvent("phase", "Comparing staged formatting with the workspace..."))
            edits = self._collect_edits(stage, copied, should_cancel)
            if should_cancel():
                return self._canceled_result()
            if not edits:
                return DesktopActionResult(
                    "format_no_changes",
                    f"{plan.label} found no formatting changes. The workspace is already clean.",
                    str(root),
                )
            return self.workspace_actions.prepare_batch_edit(
                edits,
                plan=(
                    f"{plan.label} ran on an isolated local copy. Review every changed file before applying."
                ),
                operation="format",
                max_files=self.max_changed_files,
                require_complete_diff=True,
            )

    def _copy_workspace(
        self,
        root: Path,
        stage: Path,
        should_cancel: Callable[[], bool],
    ) -> dict[str, bytes]:
        copied: dict[str, bytes] = {}
        total_bytes = 0
        for path in self.workspace_actions.iter_workspace_files():
            if should_cancel():
                break
            if len(copied) >= self.max_staged_files:
                raise DesktopActionError(
                    f"Formatting staging is limited to {self.max_staged_files:,} files. Choose a smaller Agent folder."
                )
            try:
                relative = path.relative_to(root).as_posix()
                size = path.stat().st_size
                if size > self.workspace_actions.max_file_bytes:
                    continue
                if total_bytes + size > self.max_staged_bytes:
                    raise DesktopActionError(
                        "Formatting staging exceeded the 80 MB safety limit. Choose a smaller Agent folder."
                    )
                data = path.read_bytes()
                destination = stage / Path(relative)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
            except DesktopActionError:
                raise
            except OSError as exc:
                raise DesktopActionError(f"Could not stage {path.name} for formatting: {exc}") from exc
            copied[relative] = data
            total_bytes += len(data)
        return copied

    def _collect_edits(
        self,
        stage: Path,
        copied: dict[str, bytes],
        should_cancel: Callable[[], bool],
    ) -> list[WorkspaceEditPreview]:
        edits: list[WorkspaceEditPreview] = []
        for relative, original_bytes in copied.items():
            if should_cancel():
                break
            staged_path = stage / Path(relative)
            if not staged_path.is_file():
                raise DesktopActionError(
                    f"The formatter removed {relative} in staging; no preview was created."
                )
            try:
                staged_bytes = staged_path.read_bytes()
            except OSError as exc:
                raise DesktopActionError(f"Could not read staged formatter output for {relative}: {exc}") from exc
            if staged_bytes == original_bytes:
                continue
            if len(edits) >= self.max_changed_files:
                raise DesktopActionError(
                    f"The formatter changed more than {self.max_changed_files} files. "
                    "Choose a smaller Agent folder before formatting."
                )
            if len(staged_bytes) > self.workspace_actions.max_file_bytes:
                raise DesktopActionError(f"Formatted output is too large to review safely: {relative}")
            if b"\x00" in staged_bytes[:8192]:
                raise DesktopActionError(f"The formatter changed a binary file; no preview was created: {relative}")
            had_bom = staged_bytes.startswith(b"\xef\xbb\xbf")
            try:
                proposed = staged_bytes.decode("utf-8-sig" if had_bom else "utf-8")
            except UnicodeDecodeError as exc:
                raise DesktopActionError(
                    f"The formatter produced non-UTF-8 output; no preview was created: {relative}"
                ) from exc

            snapshot = self.workspace_actions.load_edit_target(relative)
            if hashlib.sha256(original_bytes).hexdigest() != snapshot.digest:
                raise DesktopActionError(
                    f"{relative} changed while formatting was staged. Review a new formatting preview."
                )
            try:
                edits.append(
                    self.workspace_actions.prepare_edit(
                        snapshot,
                        proposed,
                        require_complete_diff=True,
                    )
                )
            except DesktopActionError as exc:
                if "contains no file changes" in str(exc):
                    continue
                raise
        return edits

    @staticmethod
    def _canceled_result() -> DesktopActionResult:
        return DesktopActionResult(
            "agent_canceled",
            "Formatting canceled; the selected workspace was not changed.",
            "",
        )
