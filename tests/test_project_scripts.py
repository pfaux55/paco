from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.services.desktop_actions import DesktopActionError, DesktopActionService
from local_matrix_assistant.services.project_scripts import ProjectScriptService
from local_matrix_assistant.services.project_tasks import ProjectTaskService


class ProjectScriptServiceTests(unittest.TestCase):
    @staticmethod
    def build_service(root: Path) -> ProjectScriptService:
        desktop = DesktopActionService(
            root.parent / "jarvis-files",
            working_folders=[str(root)],
            active_working_folder=str(root),
        )
        tasks = ProjectTaskService(desktop)
        return ProjectScriptService(desktop, tasks)

    @staticmethod
    def write_package(root: Path, scripts: dict[str, str]) -> None:
        (root / "package.json").write_text(
            json.dumps({"name": "script-test", "private": True, "scripts": scripts}),
            encoding="utf-8",
        )

    def test_parse_list_and_exact_script_commands(self) -> None:
        self.assertEqual("list", ProjectScriptService.parse("list project scripts").kind)
        request = ProjectScriptService.parse("run project script typecheck")
        self.assertIsNotNone(request)
        self.assertEqual("run", request.kind)
        self.assertEqual("typecheck", request.name)
        self.assertEqual("build", ProjectScriptService.parse("run npm script build.").name)
        self.assertIsNone(ProjectScriptService.parse("run script build && deploy"))

    def test_plan_captures_exact_command_digest_and_suppresses_lifecycle_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_package(root, {"typecheck": "tsc --noEmit"})
            service = self.build_service(root)

            with patch(
                "local_matrix_assistant.services.project_scripts.shutil.which",
                return_value="C:/tools/npm.cmd",
            ):
                plan = service.plan(service.parse("run project script typecheck"))  # type: ignore[arg-type]

            self.assertEqual("tsc --noEmit", plan.configured_command)
            self.assertEqual("standard", plan.risk_level)
            self.assertEqual(
                ("C:/tools/npm.cmd", "--ignore-scripts", "run", "typecheck"),
                plan.task_plan.argv,
            )
            self.assertEqual("Stop Script", plan.task_plan.stop_label)

    def test_high_risk_script_receives_strong_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_package(root, {"deploy:prod": "npm publish"})
            service = self.build_service(root)

            with patch(
                "local_matrix_assistant.services.project_scripts.shutil.which",
                return_value="C:/tools/npm.cmd",
            ):
                plan = service.plan(service.parse("run project script deploy:prod"))  # type: ignore[arg-type]

            self.assertEqual("high", plan.risk_level)
            self.assertIn("High-risk", plan.warning)

    def test_listing_is_bounded_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_package(root, {"dev": "vite", "test": "vitest"})
            package_before = (root / "package.json").read_bytes()
            service = self.build_service(root)

            result = service.list_scripts()

            self.assertIn("dev: vite", result.message)
            self.assertIn("test: vitest", result.message)
            self.assertIn("approval card", result.message)
            self.assertEqual(package_before, (root / "package.json").read_bytes())

    def test_changed_package_is_rejected_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_package(root, {"check": "node check.js"})
            service = self.build_service(root)
            with patch(
                "local_matrix_assistant.services.project_scripts.shutil.which",
                return_value="C:/tools/npm.cmd",
            ):
                plan = service.plan(service.parse("run project script check"))  # type: ignore[arg-type]
            self.write_package(root, {"check": "node changed.js"})

            with (
                patch.object(service.project_tasks, "run") as run,
                self.assertRaisesRegex(DesktopActionError, "package.json changed"),
            ):
                service.run(plan, lambda _chunk: None, lambda: False)

            run.assert_not_called()

    def test_npm_runs_approved_script_without_pre_or_post_hooks(self) -> None:
        npm = shutil.which("npm.cmd" if sys.platform == "win32" else "npm")
        if not npm:
            self.skipTest("npm is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_package(
                root,
                {
                    "preapproved": "node -e \"require('fs').writeFileSync('pre.txt','ran')\"",
                    "approved": "node -e \"require('fs').writeFileSync('approved.txt','ran')\"",
                    "postapproved": "node -e \"require('fs').writeFileSync('post.txt','ran')\"",
                },
            )
            service = self.build_service(root)
            plan = service.plan(service.parse("run project script approved"))  # type: ignore[arg-type]

            result = service.run(plan, lambda _chunk: None, lambda: False)

            self.assertTrue(result.success, result.output)
            self.assertTrue((root / "approved.txt").is_file())
            self.assertFalse((root / "pre.txt").exists())
            self.assertFalse((root / "post.txt").exists())


if __name__ == "__main__":
    unittest.main()
