from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.services.desktop_actions import DesktopActionError, DesktopActionService
from local_matrix_assistant.services.project_formatting import (
    ProjectFormatEvent,
    ProjectFormattingService,
)
from local_matrix_assistant.services.project_tasks import ProjectTaskPlan, ProjectTaskService
from local_matrix_assistant.services.workspace_actions import (
    WorkspaceActionService,
    WorkspaceBatchEditPreview,
)


class ProjectFormattingServiceTests(unittest.TestCase):
    @staticmethod
    def build_services(root: Path):
        desktop = DesktopActionService(
            root.parent / "paco-files",
            working_folders=[str(root)],
            active_working_folder=str(root),
        )
        workspace = WorkspaceActionService(desktop)
        tasks = ProjectTaskService(desktop)
        return workspace, tasks, ProjectFormattingService(tasks, workspace)

    @staticmethod
    def formatting_plan(root: Path, script: str) -> ProjectTaskPlan:
        return ProjectTaskPlan(
            "format",
            "Test formatter",
            root.resolve(),
            (sys.executable, "-c", script),
            timeout_seconds=10,
        )

    def test_formatter_runs_on_copy_then_applies_reviewed_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            source = root / "app.py"
            original = b"def ready():\n return True\n"
            formatted = "def ready():\n    return True\n"
            source.write_bytes(original)
            workspace, _tasks, service = self.build_services(root)
            plan = self.formatting_plan(
                root,
                "from pathlib import Path; Path('app.py').write_text(" + repr(formatted) + ", encoding='utf-8')",
            )
            events: list[ProjectFormatEvent] = []

            preview = service.preview(plan, events.append, lambda: False)

            self.assertIsInstance(preview, WorkspaceBatchEditPreview)
            assert isinstance(preview, WorkspaceBatchEditPreview)
            self.assertEqual("format", preview.operation)
            self.assertEqual(1, len(preview.edits))
            self.assertEqual(original, source.read_bytes())
            self.assertIn("+    return True", preview.diff)
            self.assertEqual(["phase", "phase", "phase"], [event.kind for event in events])

            result = workspace.apply_batch_edit(preview)

            self.assertEqual(formatted, source.read_text(encoding="utf-8"))
            self.assertEqual("apply_batch_edit", result.kind)
            self.assertIn("Backups", result.message)

    def test_formatter_failure_never_changes_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            source = root / "app.py"
            original = b"value=1\n"
            source.write_bytes(original)
            _workspace, _tasks, service = self.build_services(root)
            plan = self.formatting_plan(
                root,
                "from pathlib import Path; Path('app.py').write_text('value = 1\\n'); raise SystemExit(2)",
            )

            with self.assertRaisesRegex(DesktopActionError, "workspace was not changed"):
                service.preview(plan, lambda _event: None, lambda: False)

            self.assertEqual(original, source.read_bytes())

    def test_formatter_cancel_never_changes_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            source = root / "app.py"
            source.write_bytes(b"value=1\n")
            _workspace, _tasks, service = self.build_services(root)
            plan = self.formatting_plan(root, "raise AssertionError('must not run')")

            result = service.preview(plan, lambda _event: None, lambda: True)

            self.assertEqual("agent_canceled", result.kind)
            self.assertEqual(b"value=1\n", source.read_bytes())

    def test_external_change_after_preview_blocks_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            source = root / "app.py"
            source.write_bytes(b"value=1\n")
            workspace, _tasks, service = self.build_services(root)
            plan = self.formatting_plan(
                root,
                "from pathlib import Path; Path('app.py').write_text('value = 1\\n')",
            )
            preview = service.preview(plan, lambda _event: None, lambda: False)
            assert isinstance(preview, WorkspaceBatchEditPreview)
            source.write_bytes(b"value = 2\n")

            with self.assertRaisesRegex(DesktopActionError, "changed after preview"):
                workspace.apply_batch_edit(preview)

            self.assertEqual(b"value = 2\n", source.read_bytes())

    def test_clean_formatter_result_returns_explicit_no_change_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "app.py").write_bytes(b"value = 1\n")
            _workspace, _tasks, service = self.build_services(root)
            plan = self.formatting_plan(root, "print('already clean')")

            result = service.preview(plan, lambda _event: None, lambda: False)

            self.assertEqual("format_no_changes", result.kind)
            self.assertIn("already clean", result.message)

    def test_formatter_created_files_remain_only_in_temporary_staging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            (root / "app.py").write_bytes(b"value = 1\n")
            _workspace, _tasks, service = self.build_services(root)
            plan = self.formatting_plan(
                root,
                "from pathlib import Path; Path('formatter-cache.txt').write_text('temporary')",
            )

            result = service.preview(plan, lambda _event: None, lambda: False)

            self.assertEqual("format_no_changes", result.kind)
            self.assertFalse((root / "formatter-cache.txt").exists())

    def test_formatter_preview_fails_closed_when_complete_diff_cannot_fit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            source = root / "app.py"
            original = b"value=1\n"
            source.write_bytes(original)
            workspace, _tasks, service = self.build_services(root)
            workspace.max_diff_characters = 24
            plan = self.formatting_plan(
                root,
                "from pathlib import Path; Path('app.py').write_text('value = 1000000\\n')",
            )

            with self.assertRaisesRegex(DesktopActionError, "complete-review safety limit"):
                service.preview(plan, lambda _event: None, lambda: False)

            self.assertEqual(original, source.read_bytes())


if __name__ == "__main__":
    unittest.main()
