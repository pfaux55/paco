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
from local_matrix_assistant.services.workspace_analysis import WorkspaceAnalysisService


class WorkspaceAnalysisServiceTests(unittest.TestCase):
    @staticmethod
    def build_service(root: Path) -> WorkspaceAnalysisService:
        desktop = DesktopActionService(
            root,
            working_folders=[str(root)],
            active_working_folder=str(root),
        )
        return WorkspaceAnalysisService(WorkspaceActionService(desktop))

    def test_selects_relevant_line_numbered_sources_and_excludes_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "auth.py").write_text(
                "def validate_token(token: str) -> bool:\n"
                "    if not token:\n"
                "        return False\n"
                "    return token.startswith('local-')\n",
                encoding="utf-8",
            )
            (root / "src" / "formatting.py").write_text(
                "def title(value: str) -> str:\n    return value.title()\n",
                encoding="utf-8",
            )
            (root / "README.md").write_text("# App\nAuthentication uses local tokens.\n", encoding="utf-8")
            (root / ".env").write_text("API_TOKEN=do-not-share\n", encoding="utf-8")
            (root / "credentials.json").write_text('{"password": "secret"}', encoding="utf-8")
            service = self.build_service(root)

            context = service.build("Why does authentication token validation fail?")

            self.assertEqual("src/auth.py", context.selected_files[0])
            self.assertIn("FILE: src/auth.py", context.sources)
            self.assertRegex(context.sources, r"\s+1 \| def validate_token")
            self.assertNotIn("do-not-share", context.sources)
            self.assertNotIn("credentials.json", context.manifest)
            self.assertIn("src/auth.py", context.eligible_files)
            self.assertNotIn(".env", context.eligible_files)
            self.assertNotIn("credentials.json", context.eligible_files)
            self.assertLessEqual(len(context.sources), service.max_source_characters + 100)
            self.assertIn("Reviewed", context.scan_summary())

    def test_generic_architecture_question_prioritizes_foundational_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("# Sample\nThe app starts in src/main.py.\n", encoding="utf-8")
            (root / "pyproject.toml").write_text('[project]\nname = "sample"\n', encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("def main():\n    return 0\n", encoding="utf-8")
            service = self.build_service(root)

            context = service.build("Explain the architecture and main execution flow")

            self.assertIn("README.md", context.selected_files[:3])
            self.assertIn("pyproject.toml", context.selected_files[:3])
            self.assertIn("src/main.py", context.selected_files)

    def test_scan_limits_are_reported_and_empty_workspace_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(8):
                (root / f"module_{index}.py").write_text(f"VALUE = {index}\n", encoding="utf-8")
            service = self.build_service(root)
            service.max_discovered_files = 3

            context = service.build("Explain modules")

            self.assertEqual(3, context.discovered_files)
            self.assertTrue(context.truncated)
            self.assertIn("bounded scan", context.scan_summary())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "image.bin").write_bytes(b"abc\x00def")
            service = self.build_service(root)
            with self.assertRaisesRegex(DesktopActionError, "No readable source"):
                service.build("Explain the project")


if __name__ == "__main__":
    unittest.main()
