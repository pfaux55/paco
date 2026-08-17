from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable

from local_matrix_assistant.services.desktop_actions import DesktopActionError, DesktopActionService


_POLITE_PREFIX = r"(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
_RUN_TESTS_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:run|execute)(?:\s+the)?(?:\s+(?:project|workspace|python|unit))?\s+tests?"
    r"(?:\s+(?:in|for)\s+(?P<target>.+?))?\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_BUILD_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:(?:run|execute)\s+(?:the\s+)?(?:(?:project|workspace)\s+)?build|"
    r"build\s+(?:the\s+)?(?:project|workspace))\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_LINT_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:(?:(?:run|execute)\s+)?(?:the\s+)?(?:(?:project|workspace)\s+)?"
    r"lint(?:er|ing)?|lint(?:er|ing)?\s+(?:the\s+)?(?:project|workspace))\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_FORMAT_CHECK_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:(?:(?:run|execute)\s+)?(?:check|verify)\s+(?:the\s+)?"
    r"(?:(?:project|workspace)\s+)?format(?:ting)?|format(?:ting)?\s+check)\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_FORMAT_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}format\s+(?:the\s+)?(?:project|workspace)\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_RUN_PYTHON_FILE_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:run|execute)(?:\s+the)?(?:\s+python)?(?:\s+(?:file|script))?\s+"
    r"(?P<target>\"[^\"\r\n]+\.py\"|'[^'\r\n]+\.py'|[^\r\n]+?\.py)\s*[.!?]*\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ProjectTaskRequest:
    kind: str
    target: str = ""


@dataclass(frozen=True, slots=True)
class ProjectTaskPlan:
    kind: str
    label: str
    cwd: Path
    argv: tuple[str, ...]
    timeout_seconds: int = 180

    @property
    def command_display(self) -> str:
        return " ".join(self._quote_display(argument) for argument in self.argv)

    @property
    def stop_label(self) -> str:
        return {
            "run_tests": "Stop Tests",
            "build": "Stop Build",
            "lint": "Stop Lint",
            "format_check": "Stop Check",
            "format": "Stop Format",
            "script": "Stop Script",
            "run_python": "Stop Python",
        }.get(self.kind, "Stop Task")

    @property
    def activity_name(self) -> str:
        return {
            "run_tests": "tests",
            "build": "build",
            "lint": "lint",
            "format_check": "format check",
            "format": "format staging",
            "script": "project script",
            "run_python": "Python script",
        }.get(self.kind, "project task")

    @staticmethod
    def _quote_display(value: str) -> str:
        return f'"{value}"' if any(character.isspace() for character in value) else value


@dataclass(frozen=True, slots=True)
class ProjectTaskResult:
    success: bool
    canceled: bool
    timed_out: bool
    exit_code: int | None
    duration_seconds: float
    summary: str
    output_truncated: bool = False
    output: str = ""


class ProjectTaskService:
    """Detect and run allowlisted project workflows without invoking a shell."""

    max_output_characters = 180_000

    def __init__(self, desktop_actions: DesktopActionService) -> None:
        self.desktop_actions = desktop_actions

    def parse(self, text: str) -> ProjectTaskRequest | None:
        match = _RUN_TESTS_COMMAND.match(text)
        if match:
            target = self._unquote((match.group("target") or "").strip())
            return ProjectTaskRequest("run_tests", target)
        for kind, pattern in (
            ("build", _BUILD_COMMAND),
            ("lint", _LINT_COMMAND),
            ("format_check", _FORMAT_CHECK_COMMAND),
            ("format", _FORMAT_COMMAND),
        ):
            if pattern.match(text):
                return ProjectTaskRequest(kind)
        python_match = _RUN_PYTHON_FILE_COMMAND.match(text)
        if python_match:
            return ProjectTaskRequest("run_python", self._unquote(python_match.group("target").strip()))
        return None

    def plan(self, request: ProjectTaskRequest) -> ProjectTaskPlan:
        if request.kind not in {"run_tests", "build", "lint", "format_check", "format", "run_python"}:
            raise DesktopActionError(f"Unsupported project task: {request.kind}")
        root = self._workspace_root()
        if request.kind == "run_python":
            if not request.target:
                raise DesktopActionError("Choose a Python file to run.")
            target = self._resolve_target(request.target, root)
            if not target.is_file() or target.suffix.casefold() != ".py":
                raise DesktopActionError("Only Python source files can use the direct script runner.")
            return ProjectTaskPlan(
                "run_python",
                f"Python: {target.relative_to(root).as_posix()}",
                root,
                (str(self._project_python(root)), target.relative_to(root).as_posix()),
                timeout_seconds=60,
            )
        if request.target and request.kind != "run_tests":
            raise DesktopActionError("Only test runs support a file or folder target.")
        target = self._resolve_target(request.target, root) if request.target else None

        if self._is_python_project(root):
            return self._python_plan(request.kind, root, target)
        if (root / "package.json").is_file():
            return self._node_plan(request.kind, root, target)
        if (root / "Cargo.toml").is_file():
            return self._rust_plan(request.kind, root, target)
        raise DesktopActionError(
            "No supported project setup was detected. Agent recognizes Python, package.json, or Cargo.toml."
        )

    def run(
        self,
        plan: ProjectTaskPlan,
        on_output: Callable[[str], None],
        should_cancel: Callable[[], bool],
    ) -> ProjectTaskResult:
        if plan.kind == "format":
            raise DesktopActionError(
                "Formatting must run in an isolated staging copy before workspace changes are reviewed."
            )
        return self._run_process(plan, on_output, should_cancel)

    def run_staged(
        self,
        plan: ProjectTaskPlan,
        staged_cwd: Path,
        on_output: Callable[[str], None],
        should_cancel: Callable[[], bool],
    ) -> ProjectTaskResult:
        if plan.kind != "format":
            raise DesktopActionError("Only formatting plans can use the staged project runner.")
        original = plan.cwd.resolve()
        staged = staged_cwd.resolve()
        if staged == original or original in staged.parents or staged in original.parents:
            raise DesktopActionError("The formatter staging folder must be outside the active workspace.")
        if not staged.is_dir():
            raise DesktopActionError("The formatter staging folder is unavailable.")
        return self._run_process(replace(plan, cwd=staged), on_output, should_cancel)

    def _run_process(
        self,
        plan: ProjectTaskPlan,
        on_output: Callable[[str], None],
        should_cancel: Callable[[], bool],
    ) -> ProjectTaskResult:
        environment = os.environ.copy()
        environment.update({"CI": "1", "NO_COLOR": "1", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )
        try:
            process = subprocess.Popen(
                list(plan.argv),
                cwd=str(plan.cwd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
                env=environment,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise DesktopActionError(f"Could not start {plan.activity_name}: {exc}") from exc

        output_queue: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            assert process.stdout is not None
            try:
                for line in iter(process.stdout.readline, ""):
                    output_queue.put(line)
            finally:
                output_queue.put(None)

        reader = threading.Thread(target=read_output, name="paco-project-output", daemon=True)
        reader.start()
        started = time.monotonic()
        output_characters = 0
        output_truncated = False
        captured_output: list[str] = []
        reader_finished = False
        process_finished_at: float | None = None
        canceled = False
        timed_out = False

        while True:
            while True:
                try:
                    item = output_queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    reader_finished = True
                    continue
                remaining = self.max_output_characters - output_characters
                if remaining > 0:
                    visible = item[:remaining]
                    output_characters += len(visible)
                    captured_output.append(visible)
                    on_output(visible)
                if len(item) > max(0, remaining) and not output_truncated:
                    output_truncated = True
                    marker = "\n... output limit reached; remaining task output hidden.\n"
                    captured_output.append(marker)
                    on_output(marker)

            elapsed = time.monotonic() - started
            if process.poll() is None and should_cancel():
                canceled = True
                self._terminate_process_tree(process)
            elif process.poll() is None and elapsed >= plan.timeout_seconds:
                timed_out = True
                self._terminate_process_tree(process)

            if process.poll() is not None:
                if process_finished_at is None:
                    process_finished_at = time.monotonic()
                if output_queue.empty() and (
                    reader_finished or time.monotonic() - process_finished_at >= 1.0
                ):
                    break
            time.sleep(0.03)

        reader.join(timeout=1)
        if process.stdout is not None:
            process.stdout.close()
        duration = time.monotonic() - started
        exit_code = process.returncode
        title = {
            "run_tests": "Test run",
            "build": "Build",
            "lint": "Lint",
            "format_check": "Format check",
            "format": "Formatting",
            "script": "Project script",
            "run_python": "Python script",
        }.get(plan.kind, "Project task")
        success_text = {
            "run_tests": "Tests passed",
            "build": "Build completed",
            "lint": "Lint passed",
            "format_check": "Format check passed",
            "format": "Formatting completed",
            "script": "Project script completed",
            "run_python": "Python script completed",
        }.get(plan.kind, "Project task completed")
        failure_title = "Tests" if plan.kind == "run_tests" else title
        if canceled:
            summary = f"{title} canceled after {duration:.1f}s."
        elif timed_out:
            summary = f"{title} timed out after {duration:.1f}s."
        elif exit_code == 0:
            summary = f"{success_text} in {duration:.1f}s."
        else:
            summary = f"{failure_title} failed with exit code {exit_code} after {duration:.1f}s."
        return ProjectTaskResult(
            success=not canceled and not timed_out and exit_code == 0,
            canceled=canceled,
            timed_out=timed_out,
            exit_code=exit_code,
            duration_seconds=duration,
            summary=summary,
            output_truncated=output_truncated,
            output="".join(captured_output),
        )

    def _python_test_plan(self, root: Path, target: Path | None) -> ProjectTaskPlan:
        python = self._project_python(root)
        if self._uses_pytest(root):
            argv = [str(python), "-m", "pytest", "-q"]
            if target:
                argv.append(self._relative_argument(target, root))
            label = "Python pytest"
        else:
            argv = [str(python), "-m", "unittest"]
            if target:
                if target.is_dir():
                    argv.extend(["discover", "-s", self._relative_argument(target, root), "-v"])
                else:
                    argv.extend([self._relative_argument(target, root), "-v"])
            else:
                test_directory = root / "tests"
                argv.extend(["discover", "-s", "tests" if test_directory.is_dir() else ".", "-v"])
            label = "Python unittest"
        return ProjectTaskPlan("run_tests", label, root, tuple(argv))

    def _python_plan(
        self,
        kind: str,
        root: Path,
        target: Path | None,
    ) -> ProjectTaskPlan:
        if kind == "run_tests":
            return self._python_test_plan(root, target)
        python = self._project_python(root)
        if kind == "build":
            if not any((root / marker).is_file() for marker in ("pyproject.toml", "setup.py", "setup.cfg")):
                raise DesktopActionError("No Python package build configuration was detected.")
            return ProjectTaskPlan(
                "build",
                "Python package build",
                root,
                (str(python), "-m", "build", "--no-isolation"),
            )

        configuration = self._python_configuration(root)
        if kind == "lint":
            tool = self._python_tool_prefix(root, "ruff", configuration)
            if tool:
                return ProjectTaskPlan("lint", "Python Ruff lint", root, (*tool, "check", "."))
            tool = self._python_tool_prefix(root, "flake8", configuration)
            if tool:
                return ProjectTaskPlan("lint", "Python Flake8 lint", root, (*tool, "."))
            tool = self._python_tool_prefix(root, "pylint", configuration)
            if tool:
                return ProjectTaskPlan("lint", "Python Pylint", root, (*tool, "."))
            raise DesktopActionError(
                "No supported Python linter was detected. Configure Ruff, Flake8, or Pylint first."
            )

        tool = self._python_tool_prefix(root, "ruff", configuration)
        if tool:
            if kind == "format":
                return ProjectTaskPlan(
                    "format",
                    "Python Ruff formatter",
                    root,
                    (*tool, "format", "."),
                )
            return ProjectTaskPlan(
                "format_check",
                "Python Ruff format check",
                root,
                (*tool, "format", "--check", "."),
            )
        tool = self._python_tool_prefix(root, "black", configuration)
        if tool:
            if kind == "format":
                return ProjectTaskPlan(
                    "format",
                    "Python Black formatter",
                    root,
                    (*tool, "."),
                )
            return ProjectTaskPlan(
                "format_check",
                "Python Black format check",
                root,
                (*tool, "--check", "."),
            )
        raise DesktopActionError(
            "No supported Python formatter was detected. Configure Ruff or Black first."
        )

    @staticmethod
    def _node_plan(kind: str, root: Path, target: Path | None) -> ProjectTaskPlan:
        if target is not None:
            raise DesktopActionError("Targeted Node tests are not enabled; run the configured project test script.")
        try:
            payload = json.loads((root / "package.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DesktopActionError(f"Could not read package.json: {exc}") from exc
        scripts = payload.get("scripts", {}) if isinstance(payload, dict) else {}
        if not isinstance(scripts, dict):
            scripts = {}
        if kind == "format":
            formatter = ProjectTaskService._node_formatter(root, payload)
            return ProjectTaskPlan(
                "format",
                "Node Prettier formatter",
                root,
                (*formatter, "--write", "."),
            )
        executable = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not executable:
            raise DesktopActionError("npm is not installed or not on PATH.")
        script_names = {
            "run_tests": ("test",),
            "build": ("build",),
            "lint": ("lint",),
            "format_check": ("format:check", "format-check", "check:format"),
        }[kind]
        script = next(
            (name for name in script_names if str(scripts.get(name, "")).strip()),
            "",
        )
        if not script:
            expected = " or ".join(script_names)
            raise DesktopActionError(f"package.json does not define a {expected} script.")
        labels = {
            "run_tests": "Node npm test",
            "build": "Node npm build",
            "lint": "Node npm lint",
            "format_check": "Node npm format check",
        }
        return ProjectTaskPlan(kind, labels[kind], root, (executable, "run", script))

    @staticmethod
    def _rust_plan(kind: str, root: Path, target: Path | None) -> ProjectTaskPlan:
        if target is not None:
            raise DesktopActionError("Targeted Rust tests are not enabled; run the Cargo test suite.")
        executable = shutil.which("cargo")
        if not executable:
            raise DesktopActionError("Cargo is not installed or not on PATH.")
        if kind == "run_tests":
            return ProjectTaskPlan(kind, "Rust cargo test", root, (executable, "test", "--color", "never"))
        if kind == "build":
            return ProjectTaskPlan(kind, "Rust cargo build", root, (executable, "build", "--color", "never"))
        if kind == "lint":
            return ProjectTaskPlan(
                kind,
                "Rust Clippy lint",
                root,
                (executable, "clippy", "--all-targets", "--all-features", "--", "-D", "warnings"),
            )
        if kind == "format":
            return ProjectTaskPlan(
                kind,
                "Rust formatter",
                root,
                (executable, "fmt", "--all"),
            )
        return ProjectTaskPlan(
            kind,
            "Rust format check",
            root,
            (executable, "fmt", "--all", "--", "--check"),
        )

    @staticmethod
    def _node_formatter(root: Path, payload: object) -> tuple[str, ...]:
        names = ("prettier.cmd", "prettier") if os.name == "nt" else ("prettier", "prettier.cmd")
        for name in names:
            candidate = root / "node_modules" / ".bin" / name
            if candidate.is_file():
                return (str(candidate),)
        if executable := shutil.which("prettier"):
            return (executable,)
        dependencies: dict = {}
        if isinstance(payload, dict):
            for key in ("devDependencies", "dependencies"):
                values = payload.get(key, {})
                if isinstance(values, dict):
                    dependencies.update(values)
        if "prettier" in dependencies:
            raise DesktopActionError("Prettier is configured but not installed. Run npm install first.")
        raise DesktopActionError("No supported Node formatter was detected. Configure Prettier first.")

    @classmethod
    def _python_tool_prefix(
        cls,
        root: Path,
        tool: str,
        configuration: str,
    ) -> tuple[str, ...] | None:
        executable_names = (f"{tool}.exe", tool)
        for environment in (root / ".venv-win", root / ".venv", root / "venv"):
            for bin_folder in (environment / "Scripts", environment / "bin"):
                for name in executable_names:
                    candidate = bin_folder / name
                    if candidate.is_file():
                        return (str(candidate),)
        if executable := shutil.which(tool):
            return (executable,)
        if re.search(rf"(?<![A-Za-z0-9_-]){re.escape(tool)}(?![A-Za-z0-9_-])", configuration):
            return (str(cls._project_python(root)), "-m", tool)
        return None

    @staticmethod
    def _python_configuration(root: Path) -> str:
        paths = [
            root / "pyproject.toml",
            root / "setup.cfg",
            root / "tox.ini",
            root / ".flake8",
            *sorted(root.glob("requirements*.txt"))[:10],
        ]
        chunks: list[str] = []
        remaining = 400_000
        for path in paths:
            if remaining <= 0 or not path.is_file():
                continue
            try:
                if path.stat().st_size > 200_000:
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")[:remaining]
            except OSError:
                continue
            chunks.append(content.casefold())
            remaining -= len(content)
        return "\n".join(chunks)

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    def _workspace_root(self) -> Path:
        root = self.desktop_actions.active_working_folder or self.desktop_actions.default_files_dir
        resolved = root.resolve()
        if not resolved.is_dir():
            raise DesktopActionError("Choose an existing folder in the Agent tab first.")
        return resolved

    def _resolve_target(self, target: str, root: Path) -> Path:
        requested = Path(target).expanduser()
        path = requested.resolve() if requested.is_absolute() else (root / requested).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise DesktopActionError("Test targets must stay inside the active Agent folder.") from exc
        if not path.exists():
            raise DesktopActionError(f"Test target not found: {path}")
        if not path.is_file() and not path.is_dir():
            raise DesktopActionError(f"Unsupported test target: {path}")
        return path

    @staticmethod
    def _relative_argument(path: Path, root: Path) -> str:
        return path.relative_to(root).as_posix()

    @staticmethod
    def _is_python_project(root: Path) -> bool:
        markers = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
        if any((root / marker).is_file() for marker in markers) or any(root.glob("test*.py")):
            return True
        tests = root / "tests"
        return tests.is_dir() and next(tests.rglob("test*.py"), None) is not None

    @staticmethod
    def _uses_pytest(root: Path) -> bool:
        if any((root / marker).exists() for marker in ("pytest.ini", ".pytest.ini", "conftest.py")):
            return True
        for name, markers in (
            ("pyproject.toml", ("[tool.pytest", "pytest")),
            ("setup.cfg", ("[tool:pytest",)),
            ("requirements.txt", ("pytest",)),
        ):
            path = root / name
            if not path.is_file() or path.stat().st_size > 200_000:
                continue
            try:
                lowered = path.read_text(encoding="utf-8").lower()
                if any(marker in lowered for marker in markers):
                    return True
            except (OSError, UnicodeDecodeError):
                continue
        return False

    @staticmethod
    def _project_python(root: Path) -> Path:
        candidates = (
            root / ".venv-win" / "Scripts" / "python.exe",
            root / ".venv" / "Scripts" / "python.exe",
            root / "venv" / "Scripts" / "python.exe",
            root / ".venv" / "bin" / "python",
            root / "venv" / "bin" / "python",
        )
        return next((candidate for candidate in candidates if candidate.is_file()), Path(sys.executable))

    @staticmethod
    def _unquote(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            return value[1:-1]
        return value
