from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.services.desktop_actions import DesktopActionError, DesktopActionService
from local_matrix_assistant.services.project_tasks import ProjectTaskPlan, ProjectTaskService


class ProjectTaskServiceTests(unittest.TestCase):
    def build_service(self, root: Path) -> ProjectTaskService:
        desktop = DesktopActionService(
            root / "paco-files",
            working_folders=[str(root)],
            active_working_folder=str(root),
        )
        return ProjectTaskService(desktop)

    @staticmethod
    def write_test(root: Path, body: str) -> None:
        tests = root / "tests"
        tests.mkdir()
        (tests / "test_sample.py").write_text(body, encoding="utf-8")

    def test_python_unittest_plan_is_allowlisted_and_uses_no_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_test(root, "import unittest\n")
            service = self.build_service(root)

            plan = service.plan(service.parse("run project tests"))  # type: ignore[arg-type]

            self.assertEqual("Python unittest", plan.label)
            self.assertEqual(("-m", "unittest", "discover", "-s", "tests", "-v"), plan.argv[1:])
            self.assertEqual(root.resolve(), plan.cwd)

    def test_parses_explicit_build_lint_and_format_check_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service = self.build_service(Path(tmp))
            commands = {
                "build the project": "build",
                "run workspace build": "build",
                "run lint": "lint",
                "lint project": "lint",
                "check formatting": "format_check",
                "format check": "format_check",
                "format project": "format",
            }

            for command, expected_kind in commands.items():
                with self.subTest(command=command):
                    request = service.parse(command)
                    self.assertIsNotNone(request)
                    self.assertEqual(expected_kind, request.kind)

            self.assertIsNone(service.parse("build a login page"))

    def test_python_build_lint_and_format_plans_are_non_shell_allowlists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[build-system]\nrequires = []\n[tool.ruff]\nline-length = 100\n",
                encoding="utf-8",
            )
            service = self.build_service(root)

            with patch("local_matrix_assistant.services.project_tasks.shutil.which", return_value=None):
                build = service.plan(service.parse("build project"))  # type: ignore[arg-type]
                lint = service.plan(service.parse("run lint"))  # type: ignore[arg-type]
                formatting = service.plan(service.parse("check formatting"))  # type: ignore[arg-type]
                format_write = service.plan(service.parse("format project"))  # type: ignore[arg-type]

            self.assertEqual(("-m", "build", "--no-isolation"), build.argv[1:])
            self.assertEqual(("-m", "ruff", "check", "."), lint.argv[1:])
            self.assertEqual(("-m", "ruff", "format", "--check", "."), formatting.argv[1:])
            self.assertEqual(("-m", "ruff", "format", "."), format_write.argv[1:])
            self.assertEqual("Stop Build", build.stop_label)
            self.assertNotIn("--fix", lint.argv)
            self.assertEqual("Stop Format", format_write.stop_label)

            with self.assertRaisesRegex(DesktopActionError, "isolated staging copy"):
                service.run(format_write, lambda _chunk: None, lambda: False)

    def test_node_workflows_require_named_package_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                '{"scripts":{"test":"vitest","build":"vite build","lint":"eslint .",'
                '"format:check":"prettier --check ."}}',
                encoding="utf-8",
            )
            service = self.build_service(root)
            formatter_bin = root / "node_modules" / ".bin"
            formatter_bin.mkdir(parents=True)
            for name in ("prettier", "prettier.cmd"):
                (formatter_bin / name).write_text("", encoding="utf-8")

            with patch(
                "local_matrix_assistant.services.project_tasks.shutil.which",
                return_value="C:/tools/npm.cmd",
            ):
                build = service.plan(service.parse("build project"))  # type: ignore[arg-type]
                lint = service.plan(service.parse("lint project"))  # type: ignore[arg-type]
                formatting = service.plan(service.parse("check formatting"))  # type: ignore[arg-type]
                format_write = service.plan(service.parse("format project"))  # type: ignore[arg-type]

            self.assertEqual(("C:/tools/npm.cmd", "run", "build"), build.argv)
            self.assertEqual(("C:/tools/npm.cmd", "run", "lint"), lint.argv)
            self.assertEqual(("C:/tools/npm.cmd", "run", "format:check"), formatting.argv)
            self.assertEqual(("--write", "."), format_write.argv[-2:])
            self.assertIn("prettier", Path(format_write.argv[0]).name)

    def test_node_tests_folder_is_not_misclassified_as_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "app.test.js").write_text("test('ready', () => {});", encoding="utf-8")
            (root / "package.json").write_text(
                '{"scripts":{"test":"vitest","lint":"eslint ."}}',
                encoding="utf-8",
            )
            service = self.build_service(root)

            with patch(
                "local_matrix_assistant.services.project_tasks.shutil.which",
                return_value="C:/tools/npm.cmd",
            ):
                plan = service.plan(service.parse("run lint"))  # type: ignore[arg-type]

            self.assertEqual("Node npm lint", plan.label)

    def test_rust_workflows_use_cargo_without_a_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text("[package]\nname='sample'\n", encoding="utf-8")
            service = self.build_service(root)

            with patch(
                "local_matrix_assistant.services.project_tasks.shutil.which",
                return_value="C:/tools/cargo.exe",
            ):
                build = service.plan(service.parse("build project"))  # type: ignore[arg-type]
                lint = service.plan(service.parse("run lint"))  # type: ignore[arg-type]
                formatting = service.plan(service.parse("check formatting"))  # type: ignore[arg-type]
                format_write = service.plan(service.parse("format project"))  # type: ignore[arg-type]

            self.assertEqual("build", build.argv[1])
            self.assertEqual("clippy", lint.argv[1])
            self.assertEqual(("fmt", "--all", "--", "--check"), formatting.argv[1:])
            self.assertEqual(("fmt", "--all"), format_write.argv[1:])

    def test_test_target_cannot_escape_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            root = parent / "project"
            root.mkdir()
            self.write_test(root, "import unittest\n")
            outside = parent / "outside.py"
            outside.write_text("print('outside')", encoding="utf-8")
            service = self.build_service(root)

            with self.assertRaisesRegex(DesktopActionError, "inside the active Agent folder"):
                service.plan(service.parse("run tests in ../outside.py"))  # type: ignore[arg-type]

    def test_run_streams_output_and_reports_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_test(
                root,
                "import unittest\n\nclass SampleTests(unittest.TestCase):\n"
                "    def test_ready(self):\n        self.assertEqual(2, 1 + 1)\n\n"
                "if __name__ == '__main__':\n    unittest.main()\n",
            )
            service = self.build_service(root)
            plan = service.plan(service.parse("run tests"))  # type: ignore[arg-type]
            chunks: list[str] = []

            result = service.run(plan, chunks.append, lambda: False)

            self.assertTrue(result.success)
            self.assertEqual(0, result.exit_code)
            self.assertIn("test_ready", "".join(chunks))
            self.assertIn("test_ready", result.output)
            self.assertIn("Tests passed", result.summary)

    def test_direct_python_file_run_uses_workspace_interpreter_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "hello.py").write_text("print('hello from python')\n", encoding="utf-8")
            service = self.build_service(root)

            request = service.parse("run hello.py")
            plan = service.plan(request)  # type: ignore[arg-type]
            result = service.run(plan, lambda _chunk: None, lambda: False)

            self.assertEqual("run_python", plan.kind)
            self.assertEqual("hello.py", plan.argv[-1])
            self.assertEqual(root.resolve(), plan.cwd)
            self.assertTrue(result.success)
            self.assertIn("hello from python", result.output)
            self.assertIn("Python script completed", result.summary)

    def test_failed_tests_return_failure_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_test(
                root,
                "import unittest\n\nclass SampleTests(unittest.TestCase):\n"
                "    def test_failure(self):\n        self.fail('expected failure')\n",
            )
            service = self.build_service(root)
            plan = service.plan(service.parse("run tests"))  # type: ignore[arg-type]

            result = service.run(plan, lambda _chunk: None, lambda: False)

            self.assertFalse(result.success)
            self.assertNotEqual(0, result.exit_code)
            self.assertIn("expected failure", result.output)
            self.assertIn("Tests failed", result.summary)

    def test_long_process_can_be_canceled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = self.build_service(root)
            plan = ProjectTaskPlan(
                "run_tests",
                "Cancellation test",
                root,
                (sys.executable, "-c", "import time; print('started', flush=True); time.sleep(10)"),
                timeout_seconds=20,
            )
            started = time.monotonic()

            result = service.run(plan, lambda _chunk: None, lambda: time.monotonic() - started > 0.2)

            self.assertTrue(result.canceled)
            self.assertFalse(result.success)
            self.assertLess(result.duration_seconds, 5)

    def test_output_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = self.build_service(root)
            service.max_output_characters = 120
            plan = ProjectTaskPlan(
                "run_tests",
                "Output test",
                root,
                (sys.executable, "-c", "print('x' * 1000)"),
                timeout_seconds=10,
            )
            chunks: list[str] = []

            result = service.run(plan, chunks.append, lambda: False)

            self.assertTrue(result.success)
            self.assertTrue(result.output_truncated)
            self.assertIn("output limit reached", "".join(chunks))
            self.assertIn("output limit reached", result.output)

    def test_non_test_task_uses_specific_success_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = self.build_service(root)
            plan = ProjectTaskPlan(
                "lint",
                "Lint summary test",
                root,
                (sys.executable, "-c", "print('clean')"),
                timeout_seconds=10,
            )

            result = service.run(plan, lambda _chunk: None, lambda: False)

            self.assertTrue(result.success)
            self.assertIn("Lint passed", result.summary)


if __name__ == "__main__":
    unittest.main()
