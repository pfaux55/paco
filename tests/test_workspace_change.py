from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.services.workspace_change import WorkspaceChangeService


class WorkspaceChangeServiceTests(unittest.TestCase):
    def test_recognizes_natural_existing_workspace_changes(self) -> None:
        change = WorkspaceChangeService.parse("Add validation so login rejects empty tokens")
        polite = WorkspaceChangeService.parse("Could you make the sidebar responsive on small windows?")
        fix = WorkspaceChangeService.parse("Fix authentication when the token is empty")
        removal = WorkspaceChangeService.parse("Remove the unused import from the authentication module")

        self.assertEqual("change", change.kind)  # type: ignore[union-attr]
        self.assertEqual("change", polite.kind)  # type: ignore[union-attr]
        self.assertEqual("fix", fix.kind)  # type: ignore[union-attr]
        self.assertEqual("change", removal.kind)  # type: ignore[union-attr]

    def test_rejects_destructive_new_file_and_nontechnical_requests(self) -> None:
        self.assertIsNone(WorkspaceChangeService.parse("delete file app.py"))
        self.assertIsNone(WorkspaceChangeService.parse("rename module.py to core.py"))
        self.assertIsNone(WorkspaceChangeService.parse("add a new file called health.py"))
        self.assertIsNone(WorkspaceChangeService.parse("make me a sandwich please"))
        self.assertIsNone(WorkspaceChangeService.parse("tell me a joke"))


if __name__ == "__main__":
    unittest.main()
