from __future__ import annotations

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.services.desktop_actions import DesktopActionError
from local_matrix_assistant.services.workspace_fix import WorkspaceFixService


class WorkspaceFixServiceTests(unittest.TestCase):
    def test_parses_fenced_plan_and_canonicalizes_reviewed_paths(self) -> None:
        response = (
            "```json\n"
            '{"summary":"Fix the subtraction branch.","files":['
            '{"path":"src\\\\math.py","reason":"The failing branch is here."}]}'
            "\n```"
        )

        plan = WorkspaceFixService.parse_plan(response, ("src/math.py", "tests/test_math.py"))

        self.assertEqual("Fix the subtraction branch.", plan.summary)
        self.assertEqual(("src/math.py",), tuple(item.path for item in plan.files))
        self.assertIn("src/math.py", plan.display())

    def test_rejects_unreviewed_duplicate_oversized_and_invalid_plans(self) -> None:
        allowed = ("src/app.py", "src/api.py", "tests/test_app.py", "tests/test_api.py")
        with self.assertRaisesRegex(DesktopActionError, "outside the reviewed evidence"):
            WorkspaceFixService.parse_plan(
                '{"summary":"bad","files":[{"path":"../outside.py","reason":"change it"}]}',
                allowed,
            )
        with self.assertRaisesRegex(DesktopActionError, "more than once"):
            WorkspaceFixService.parse_plan(
                '{"summary":"bad","files":['
                '{"path":"src/app.py","reason":"one"},'
                '{"path":"src/app.py","reason":"two"}]}',
                allowed,
            )
        with self.assertRaisesRegex(DesktopActionError, "3-file"):
            WorkspaceFixService.parse_plan(
                '{"summary":"too broad","files":['
                + ",".join(
                    f'{{"path":"{path}","reason":"change"}}'
                    for path in allowed
                )
                + "]}",
                allowed,
            )
        with self.assertRaisesRegex(DesktopActionError, "invalid fix plan"):
            WorkspaceFixService.parse_plan("not json", allowed)


if __name__ == "__main__":
    unittest.main()
