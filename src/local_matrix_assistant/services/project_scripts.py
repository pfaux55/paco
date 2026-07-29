from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Callable

from local_matrix_assistant.services.desktop_actions import (
    DesktopActionError,
    DesktopActionResult,
    DesktopActionService,
)
from local_matrix_assistant.services.project_tasks import (
    ProjectTaskPlan,
    ProjectTaskResult,
    ProjectTaskService,
)


_POLITE_PREFIX = r"(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
_RUN_SCRIPT_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:run|execute)\s+(?:(?:the\s+)?(?:project|npm)\s+)?script\s+"
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9:._-]{0,63})\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_LIST_SCRIPTS_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:list|show)(?:\s+the)?\s+(?:(?:project|npm)\s+)?scripts?\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_HIGH_RISK_NAME = re.compile(
    r"(?:deploy|publish|release|migrat|seed|install|uninstall|remove|delete|clean|reset|upload|production|prod)",
    re.IGNORECASE,
)
_HIGH_RISK_COMMAND = re.compile(
    r"(?:\brm\s+-|\bdel\s+|remove-item|npm\s+publish|docker\s+push|\b(?:curl|wget|scp|ssh)\b|"
    r"invoke-webrequest|\b(?:aws|az|gcloud)\s+)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ProjectScriptRequest:
    kind: str
    name: str = ""


@dataclass(frozen=True, slots=True)
class ProjectScriptPlan:
    name: str
    configured_command: str
    cwd: Path
    package_json: Path
    package_digest: str
    npm_executable: str
    risk_level: str
    warning: str
    timeout_seconds: int = 180

    @property
    def task_plan(self) -> ProjectTaskPlan:
        return ProjectTaskPlan(
            "script",
            f"npm script: {self.name}",
            self.cwd,
            (self.npm_executable, "--ignore-scripts", "run", self.name),
            timeout_seconds=self.timeout_seconds,
        )


class ProjectScriptService:
    """Discover configured npm scripts and run one only after UI approval."""

    max_package_bytes = 500_000
    max_scripts = 100
    max_command_characters = 4000

    def __init__(
        self,
        desktop_actions: DesktopActionService,
        project_tasks: ProjectTaskService,
    ) -> None:
        self.desktop_actions = desktop_actions
        self.project_tasks = project_tasks

    @staticmethod
    def parse(text: str) -> ProjectScriptRequest | None:
        if match := _RUN_SCRIPT_COMMAND.match(text):
            return ProjectScriptRequest("run", match.group("name").rstrip(".!?"))
        if _LIST_SCRIPTS_COMMAND.match(text):
            return ProjectScriptRequest("list")
        return None

    def list_scripts(self) -> DesktopActionResult:
        root, package, _raw, scripts = self._read_scripts()
        if not scripts:
            return DesktopActionResult(
                "list_project_scripts",
                "package.json does not define any project scripts.",
                str(package),
            )
        lines = ["Configured package.json scripts:"]
        for name, command in sorted(scripts.items(), key=lambda item: item[0].casefold()):
            visible = command if len(command) <= 240 else command[:237] + "..."
            lines.append(f"- {name}: {visible}")
        lines.append("Use `run project script <name>` to open an approval card before execution.")
        return DesktopActionResult("list_project_scripts", "\n".join(lines), str(root))

    def plan(self, request: ProjectScriptRequest) -> ProjectScriptPlan:
        if request.kind != "run":
            raise DesktopActionError(f"Unsupported project-script request: {request.kind}")
        root, package, raw, scripts = self._read_scripts()
        command = scripts.get(request.name)
        if command is None:
            matching_name = next(
                (name for name in scripts if name.casefold() == request.name.casefold()),
                "",
            )
            if matching_name:
                request = ProjectScriptRequest(request.kind, matching_name)
                command = scripts[matching_name]
        if command is None:
            available = ", ".join(sorted(scripts)[:12]) or "none"
            raise DesktopActionError(
                f"Project script '{request.name}' was not found. Available scripts: {available}."
            )
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not npm:
            raise DesktopActionError("npm is not installed or not on PATH.")
        high_risk = bool(
            _HIGH_RISK_NAME.search(request.name)
            or _HIGH_RISK_COMMAND.search(command)
        )
        warning = (
            "High-risk script: it may deploy, delete, publish, install, or access external systems. "
            "Review the exact package.json command before running."
            if high_risk
            else "Project scripts run with your Windows account and may modify files or access the network."
        )
        return ProjectScriptPlan(
            name=request.name,
            configured_command=command,
            cwd=root,
            package_json=package,
            package_digest=hashlib.sha256(raw).hexdigest(),
            npm_executable=npm,
            risk_level="high" if high_risk else "standard",
            warning=warning,
        )

    def run(
        self,
        plan: ProjectScriptPlan,
        on_output: Callable[[str], None],
        should_cancel: Callable[[], bool],
    ) -> ProjectTaskResult:
        self.validate(plan)
        return self.project_tasks.run(plan.task_plan, on_output, should_cancel)

    def validate(self, plan: ProjectScriptPlan) -> None:
        root, package, raw, scripts = self._read_scripts()
        if root != plan.cwd.resolve() or package != plan.package_json.resolve():
            raise DesktopActionError("The active Agent folder changed; review the project script again.")
        if hashlib.sha256(raw).hexdigest() != plan.package_digest:
            raise DesktopActionError("package.json changed after approval was requested; review the script again.")
        if scripts.get(plan.name) != plan.configured_command:
            raise DesktopActionError("The configured project script changed; review it again before running.")

    def _read_scripts(self) -> tuple[Path, Path, bytes, dict[str, str]]:
        root = self._workspace_root()
        package = (root / "package.json").resolve()
        if package.parent != root or not package.is_file():
            raise DesktopActionError("The active Agent folder does not contain a package.json file.")
        try:
            if package.stat().st_size > self.max_package_bytes:
                raise DesktopActionError("package.json exceeds the 500 KB safety limit.")
            raw = package.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except DesktopActionError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DesktopActionError(f"Could not read package.json: {exc}") from exc
        values = payload.get("scripts", {}) if isinstance(payload, dict) else {}
        if not isinstance(values, dict):
            raise DesktopActionError("package.json scripts must be an object.")
        if len(values) > self.max_scripts:
            raise DesktopActionError(f"package.json defines more than {self.max_scripts} scripts.")
        scripts: dict[str, str] = {}
        for name, command in values.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,63}", name):
                continue
            if not isinstance(command, str):
                continue
            cleaned = command.strip()
            if (
                not cleaned
                or len(cleaned) > self.max_command_characters
                or any(character in cleaned for character in ("\x00", "\r", "\n"))
            ):
                continue
            scripts[name] = cleaned
        return root, package, raw, scripts

    def _workspace_root(self) -> Path:
        root = self.desktop_actions.active_working_folder or self.desktop_actions.default_files_dir
        resolved = root.resolve()
        if not resolved.is_dir():
            raise DesktopActionError("Choose an existing folder in the Agent tab first.")
        return resolved
