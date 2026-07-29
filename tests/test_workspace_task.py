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
from local_matrix_assistant.services.workspace_actions import WorkspaceActionService
from local_matrix_assistant.services.workspace_task import WorkspaceTaskService


class WorkspaceTaskServiceTests(unittest.TestCase):
    def test_recognizes_read_only_workspace_questions_without_capturing_chat_or_mutations(self) -> None:
        self.assertTrue(
            WorkspaceTaskService.can_plan(
                "Find where authentication tokens are validated and explain the missing-token behavior"
            )
        )
        self.assertTrue(WorkspaceTaskService.can_plan("How does this project load configuration?"))
        self.assertTrue(WorkspaceTaskService.can_plan("Could you explain how authentication works?"))
        self.assertFalse(WorkspaceTaskService.can_plan("Tell me a joke"))
        self.assertFalse(WorkspaceTaskService.can_plan("delete file app.py"))
        self.assertFalse(WorkspaceTaskService.can_plan("fix the login bug"))

    def test_parses_only_bounded_reads_and_literal_searches(self) -> None:
        response = """```json
        {
          "summary": "Trace token validation.",
          "steps": [
            {"tool": "read_file", "path": "src/auth.py", "reason": "Inspect validation."},
            {"tool": "search_files", "query": "validate_token", "reason": "Find callers."}
          ]
        }
        ```"""

        plan = WorkspaceTaskService.parse_plan(response, ("src/auth.py", "src/app.py"))

        self.assertEqual("src/auth.py", plan.steps[0].path)
        self.assertEqual("validate_token", plan.steps[1].query)
        self.assertIn("read src/auth.py", plan.display())

    def test_rejects_writes_unreviewed_paths_duplicates_and_oversized_plans(self) -> None:
        allowed = ("src/auth.py",)
        invalid_responses = (
            '{"summary":"x","steps":[{"tool":"write_file","path":"src/auth.py","reason":"x"}]}',
            '{"summary":"x","steps":[{"tool":"read_file","path":".env","reason":"x"}]}',
            '{"summary":"x","steps":['
            '{"tool":"read_file","path":"src/auth.py","reason":"x"},'
            '{"tool":"read_file","path":"src/auth.py","reason":"again"}]}',
            '{"summary":"x","steps":['
            '{"tool":"search_files","query":"one","reason":"x"},'
            '{"tool":"search_files","query":"two","reason":"x"},'
            '{"tool":"search_files","query":"three","reason":"x"}]}',
            '{"summary":"x","steps":['
            '{"tool":"read_file","path":"src/auth.py","reason":"1"},'
            '{"tool":"search_files","query":"one","reason":"2"},'
            '{"tool":"search_files","query":"two","reason":"3"},'
            '{"tool":"read_file","path":"src/auth.py","reason":"4"},'
            '{"tool":"read_file","path":"src/auth.py","reason":"5"}]}',
        )
        for response in invalid_responses:
            with self.subTest(response=response), self.assertRaises(DesktopActionError):
                WorkspaceTaskService.parse_plan(response, allowed)

    def test_planned_search_reads_only_explicitly_eligible_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "auth.py").write_text("TOKEN = 'public-test-token'\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=private-secret\n", encoding="utf-8")
            desktop = DesktopActionService(
                root,
                working_folders=[str(root)],
                active_working_folder=str(root),
            )
            actions = WorkspaceActionService(desktop)

            output = WorkspaceTaskService.search_allowed_files(
                actions,
                "TOKEN",
                ("auth.py",),
                lambda: False,
            )

            self.assertIn("public-test-token", output)
            self.assertNotIn("private-secret", output)

    def test_planned_search_honors_cancellation_between_eligible_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one.py").write_text("needle = 'first'\n", encoding="utf-8")
            (root / "two.py").write_text("needle = 'second'\n", encoding="utf-8")
            desktop = DesktopActionService(
                root,
                working_folders=[str(root)],
                active_working_folder=str(root),
            )
            actions = WorkspaceActionService(desktop)
            checks: list[bool] = []

            output = WorkspaceTaskService.search_allowed_files(
                actions,
                "needle",
                ("one.py", "two.py"),
                lambda: checks.append(True) or len(checks) > 1,
            )

            self.assertIn("first", output)
            self.assertNotIn("second", output)


if __name__ == "__main__":
    unittest.main()
