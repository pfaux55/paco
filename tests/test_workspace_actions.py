from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.services.desktop_actions import DesktopActionError, DesktopActionService
from local_matrix_assistant.services.workspace_actions import WorkspaceActionService


class WorkspaceActionServiceTests(unittest.TestCase):
    def build_service(self, root: Path, default: Path | None = None) -> WorkspaceActionService:
        desktop = DesktopActionService(
            default or root,
            working_folders=[str(root)],
            active_working_folder=str(root),
        )
        return WorkspaceActionService(desktop)

    def test_list_files_is_recursive_bounded_and_ignores_dependency_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('ok')", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git" / "secret").write_text("ignored", encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "package.js").write_text("ignored", encoding="utf-8")
            service = self.build_service(root)

            result = service.execute(service.parse("list workspace files"))  # type: ignore[arg-type]

            self.assertIn("src/app.py", result.message)
            self.assertNotIn("secret", result.message)
            self.assertNotIn("package.js", result.message)

    def test_read_file_adds_line_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("first\nsecond\n", encoding="utf-8")
            service = self.build_service(root)

            result = service.execute(service.parse("read file app.py"))  # type: ignore[arg-type]

            self.assertIn("1 | first", result.message)
            self.assertIn("2 | second", result.message)

    def test_search_files_is_case_insensitive_and_reports_locations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("# TODO: finish\nprint('ok')", encoding="utf-8")
            service = self.build_service(root)

            result = service.execute(service.parse('search files for "todo" in "src"'))  # type: ignore[arg-type]

            self.assertIn("src/app.py:1", result.message)
            self.assertIn("TODO: finish", result.message)

    def test_read_only_search_checks_cancellation_between_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one.py").write_text("needle = True\n", encoding="utf-8")
            service = self.build_service(root)
            checks: list[bool] = []

            result = service.execute(
                service.parse('search files for "needle"'),  # type: ignore[arg-type]
                lambda: checks.append(True) or True,
            )

            self.assertTrue(checks)
            self.assertIn("search limits reached", result.message)

    def test_read_rejects_path_escape_binary_and_large_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "project"
            root.mkdir()
            (parent / "outside.txt").write_text("private", encoding="utf-8")
            (root / "binary.bin").write_bytes(b"abc\x00def")
            (root / "large.txt").write_bytes(b"x" * (WorkspaceActionService.max_file_bytes + 1))
            service = self.build_service(root)

            with self.assertRaisesRegex(DesktopActionError, "active Agent folder"):
                service.execute(service.parse("read file ../outside.txt"))  # type: ignore[arg-type]
            with self.assertRaisesRegex(DesktopActionError, "Binary"):
                service.execute(service.parse("read file binary.bin"))  # type: ignore[arg-type]
            with self.assertRaisesRegex(DesktopActionError, "safety limit"):
                service.execute(service.parse("read file large.txt"))  # type: ignore[arg-type]

    def test_exact_replace_is_atomic_and_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "project"
            backup_root = parent / "jarvis"
            root.mkdir()
            path = root / "app.py"
            path.write_text("status = 'old'\n", encoding="utf-8")
            service = self.build_service(root, backup_root)

            action = service.parse('replace in file app.py text "old" with "ready"')
            result = service.execute(action)  # type: ignore[arg-type]

            self.assertEqual("status = 'ready'\n", path.read_text(encoding="utf-8"))
            backup_text = result.message.split("Backup: ", 1)[1]
            self.assertEqual("status = 'old'\n", Path(backup_text).read_text(encoding="utf-8"))
            self.assertEqual([], list(root.glob("*.tmp")))

    def test_multiple_matches_require_explicit_replace_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "values.txt"
            path.write_text("old old", encoding="utf-8")
            service = self.build_service(root)

            with self.assertRaisesRegex(DesktopActionError, "occurs 2 times"):
                service.execute(service.parse('replace in file values.txt "old" with "new"'))  # type: ignore[arg-type]
            self.assertEqual("old old", path.read_text(encoding="utf-8"))

            result = service.execute(service.parse('replace all in file values.txt "old" with "new"'))  # type: ignore[arg-type]
            self.assertIn("2 exact replacements", result.message)
            self.assertEqual("new new", path.read_text(encoding="utf-8"))

    def test_replace_preserves_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "config.txt"
            path.write_bytes(b"\xef\xbb\xbfmode=old\r\n")
            service = self.build_service(root)

            service.execute(service.parse('replace in file config.txt "old" with "new"'))  # type: ignore[arg-type]

            updated = path.read_bytes()
            self.assertTrue(updated.startswith(b"\xef\xbb\xbf"))
            self.assertEqual(b"\xef\xbb\xbfmode=new\r\n", updated)

    def test_non_action_discussion_is_not_parsed(self) -> None:
        service = self.build_service(Path.cwd())

        self.assertIsNone(service.parse("Explain how file replacement works"))

    def test_generated_file_command_is_distinct_from_literal_file_creation(self) -> None:
        service = self.build_service(Path.cwd())

        action = service.parse("create a Python file src/health.py that exposes a health check")

        self.assertEqual("draft_create", action.kind)  # type: ignore[union-attr]
        self.assertEqual("src/health.py", action.target)  # type: ignore[union-attr]
        self.assertEqual("exposes a health check", action.query)  # type: ignore[union-attr]
        create_and_run = service.parse("create a Python file demo.py that prints ready then run it")
        self.assertEqual("draft_create_and_run", create_and_run.kind)  # type: ignore[union-attr]
        self.assertEqual("prints ready", create_and_run.query)  # type: ignore[union-attr]
        self.assertIsNone(service.parse("create file notes.txt that contains remember this"))

    def test_workspace_analysis_commands_capture_a_bounded_question(self) -> None:
        service = self.build_service(Path.cwd())

        analysis = service.parse("analyze the workspace for startup errors")
        explanation = service.parse("explain the project architecture")
        investigation = service.parse("investigate why login tests fail")

        self.assertEqual("analyze_workspace", analysis.kind)  # type: ignore[union-attr]
        self.assertEqual("startup errors", analysis.query)  # type: ignore[union-attr]
        self.assertEqual("architecture", explanation.query)  # type: ignore[union-attr]
        self.assertEqual("login tests fail", investigation.query)  # type: ignore[union-attr]

    def test_workspace_fix_commands_require_an_explicit_issue(self) -> None:
        service = self.build_service(Path.cwd())

        workspace_fix = service.parse("fix workspace issue: login crashes when the token is empty")
        bug_fix = service.parse("fix the bug where subtraction adds values")

        self.assertEqual("draft_workspace_fix", workspace_fix.kind)  # type: ignore[union-attr]
        self.assertEqual("login crashes when the token is empty", workspace_fix.query)  # type: ignore[union-attr]
        self.assertEqual("subtraction adds values", bug_fix.query)  # type: ignore[union-attr]
        self.assertIsNone(service.parse("fix file app.py"))
        with self.assertRaisesRegex(DesktopActionError, "Describe the workspace issue"):
            service.parse("fix workspace issue")

    def test_generated_file_requires_review_and_exclusive_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = self.build_service(root)

            preview = service.prepare_create(
                "src/health.py",
                "def healthy() -> bool:\n    return True",
                model="qwen2.5-coder:7b",
            )

            path = root / "src" / "health.py"
            self.assertFalse(path.exists())
            self.assertEqual("src/health.py", preview.relative_path)
            self.assertIn("--- /dev/null", preview.diff)
            self.assertIn("+def healthy() -> bool:", preview.diff)
            result = service.apply_create(preview)
            self.assertEqual("def healthy() -> bool:\n    return True\n", path.read_text(encoding="utf-8"))
            self.assertIn("Created reviewed file", result.message)

    def test_generated_file_rejects_invalid_content_escape_and_concurrent_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "project"
            root.mkdir()
            service = self.build_service(root)

            with self.assertRaisesRegex(DesktopActionError, "invalid"):
                service.prepare_create("config.json", "{broken")
            with self.assertRaisesRegex(DesktopActionError, "stay inside"):
                service.prepare_create("../outside.py", "value = 1")

            preview = service.prepare_create("safe.py", "value = 1")
            (root / "safe.py").write_text("user content\n", encoding="utf-8")
            with self.assertRaisesRegex(DesktopActionError, "not overwritten"):
                service.apply_create(preview)
            self.assertEqual("user content\n", (root / "safe.py").read_text(encoding="utf-8"))

    def test_model_edit_builds_reviewable_diff_before_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "project"
            root.mkdir()
            path = root / "maths.py"
            path.write_text("def total(a, b):\n    return a + b\n", encoding="utf-8")
            service = self.build_service(root, parent / "backups")
            action = service.parse("edit file maths.py to add type hints")

            self.assertEqual("draft_edit", action.kind)  # type: ignore[union-attr]
            snapshot = service.load_edit_target(action.target)  # type: ignore[union-attr]
            preview = service.prepare_edit(
                snapshot,
                "def total(a: int, b: int) -> int:\n    return a + b",
                model="qwen2.5-coder:7b",
            )

            self.assertEqual("def total(a, b):\n    return a + b\n", path.read_text(encoding="utf-8"))
            self.assertIn("-def total(a, b):", preview.diff)
            self.assertIn("+def total(a: int, b: int) -> int:", preview.diff)
            result = service.apply_edit(preview)
            self.assertIn("Applied reviewed edit", result.message)
            self.assertEqual(
                "def total(a: int, b: int) -> int:\n    return a + b\n",
                path.read_text(encoding="utf-8"),
            )

    def test_invalid_python_proposal_is_rejected_before_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "app.py"
            path.write_text("value = 1\n", encoding="utf-8")
            service = self.build_service(root)
            snapshot = service.load_edit_target("app.py")

            with self.assertRaisesRegex(DesktopActionError, "invalid"):
                service.prepare_edit(snapshot, "def broken(:\n")
            self.assertEqual("value = 1\n", path.read_text(encoding="utf-8"))

    def test_apply_refuses_file_changed_after_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "app.py"
            path.write_text("value = 1\n", encoding="utf-8")
            service = self.build_service(root)
            preview = service.prepare_edit(service.load_edit_target("app.py"), "value = 2")
            path.write_text("value = 3\n", encoding="utf-8")

            with self.assertRaisesRegex(DesktopActionError, "changed after the preview"):
                service.apply_edit(preview)
            self.assertEqual("value = 3\n", path.read_text(encoding="utf-8"))

    def test_multi_file_command_parses_quoted_comma_separated_paths(self) -> None:
        service = self.build_service(Path.cwd())

        action = service.parse('edit files src/a.py, "src/b file.py" to add shared validation')

        self.assertEqual("draft_batch_edit", action.kind)  # type: ignore[union-attr]
        self.assertEqual(("src/a.py", "src/b file.py"), action.targets)  # type: ignore[union-attr]
        self.assertEqual("add shared validation", action.query)  # type: ignore[union-attr]

    def test_batch_edit_applies_all_reviewed_files_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "project"
            root.mkdir()
            first = root / "first.py"
            second = root / "second.py"
            first.write_text("VALUE = 1\n", encoding="utf-8")
            second.write_text("from first import VALUE\nRESULT = VALUE\n", encoding="utf-8")
            service = self.build_service(root, parent / "backups")
            first_edit = service.prepare_edit(service.load_edit_target("first.py"), "VALUE = 2", model="coder")
            second_edit = service.prepare_edit(
                service.load_edit_target("second.py"),
                "from first import VALUE\nRESULT = VALUE * 2",
                model="coder",
            )
            batch = service.prepare_batch_edit(
                [first_edit, second_edit],
                model="coder",
                plan="Update the constant and its consumer.",
            )

            result = service.apply_batch_edit(batch)

            self.assertEqual("VALUE = 2\n", first.read_text(encoding="utf-8"))
            self.assertIn("RESULT = VALUE * 2", second.read_text(encoding="utf-8"))
            self.assertIn("Applied reviewed batch edit to 2 files", result.message)
            self.assertIn("first.py", batch.diff)
            self.assertIn("second.py", batch.diff)

    def test_changed_batch_file_aborts_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.py"
            second = root / "second.py"
            first.write_text("VALUE = 1\n", encoding="utf-8")
            second.write_text("VALUE = 2\n", encoding="utf-8")
            service = self.build_service(root)
            batch = service.prepare_batch_edit(
                [
                    service.prepare_edit(service.load_edit_target("first.py"), "VALUE = 10"),
                    service.prepare_edit(service.load_edit_target("second.py"), "VALUE = 20"),
                ]
            )
            second.write_text("VALUE = 3\n", encoding="utf-8")

            with self.assertRaisesRegex(DesktopActionError, "no batch files were modified"):
                service.apply_batch_edit(batch)

            self.assertEqual("VALUE = 1\n", first.read_text(encoding="utf-8"))
            self.assertEqual("VALUE = 3\n", second.read_text(encoding="utf-8"))

    def test_batch_write_failure_rolls_back_files_already_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.py"
            second = root / "second.py"
            first.write_text("VALUE = 1\n", encoding="utf-8")
            second.write_text("VALUE = 2\n", encoding="utf-8")
            service = self.build_service(root)
            batch = service.prepare_batch_edit(
                [
                    service.prepare_edit(service.load_edit_target("first.py"), "VALUE = 10"),
                    service.prepare_edit(service.load_edit_target("second.py"), "VALUE = 20"),
                ]
            )
            original_atomic_replace = service._atomic_replace
            call_count = 0

            def fail_second_write(path: Path, data: bytes) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise DesktopActionError("simulated disk failure")
                original_atomic_replace(path, data)

            with patch.object(service, "_atomic_replace", side_effect=fail_second_write):
                with self.assertRaisesRegex(DesktopActionError, "all modified files were restored"):
                    service.apply_batch_edit(batch)

            self.assertEqual("VALUE = 1\n", first.read_text(encoding="utf-8"))
            self.assertEqual("VALUE = 2\n", second.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
