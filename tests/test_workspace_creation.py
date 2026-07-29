from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.services.desktop_actions import DesktopActionError
from local_matrix_assistant.services.workspace_creation import WorkspaceCreationService


class WorkspaceCreationServiceTests(unittest.TestCase):
    def test_recognizes_free_form_build_requests_without_capturing_chat(self) -> None:
        self.assertIsNotNone(WorkspaceCreationService.parse("build snake game"))
        self.assertIsNotNone(WorkspaceCreationService.parse("write code file for a timer app"))
        self.assertIsNone(WorkspaceCreationService.parse("tell me a joke"))
        self.assertIsNone(WorkspaceCreationService.parse("make me a sandwich"))
        create_and_run = WorkspaceCreationService.parse("build a Python file then run it")
        self.assertTrue(create_and_run.run_after_create)  # type: ignore[union-attr]

    def test_parses_safe_single_file_plan(self) -> None:
        plan = WorkspaceCreationService.parse_plan(
            '{"path":"index.html","instructions":"Build a playable snake game."}'
        )

        self.assertEqual("index.html", plan.path)
        self.assertIn("snake", plan.instructions)

    def test_rejects_unsafe_or_executable_plan_paths(self) -> None:
        for response in (
            '{"path":"../escape.py","instructions":"x"}',
            '{"path":".hidden.py","instructions":"x"}',
            '{"path":"payload.exe","instructions":"x"}',
        ):
            with self.subTest(response=response), self.assertRaises(DesktopActionError):
                WorkspaceCreationService.parse_plan(response)


if __name__ == "__main__":
    unittest.main()
