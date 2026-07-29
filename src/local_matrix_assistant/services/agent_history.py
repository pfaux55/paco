from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import json
from json import JSONDecodeError
import math
import os
from pathlib import Path
import re


@dataclass(frozen=True, slots=True)
class AgentHistoryEvent:
    role: str
    text: str
    timestamp: str = ""
    artifact_path: str = ""
    artifact_kind: str = ""
    workspace_path: str = ""
    task_id: str = ""


@dataclass(frozen=True, slots=True)
class AgentTaskDetail:
    task_id: str
    command: str
    workspace_path: str = ""
    started_at: str = ""
    content: str = ""
    status: str = "completed"
    duration_seconds: float = 0.0
    completed_at: str = ""


@dataclass(slots=True)
class AgentHistoryRecord:
    events: list[AgentHistoryEvent] = field(default_factory=list)
    execution_details: str = ""
    active_folder: str = ""
    updated_at: str = ""
    timeline_filter: str = "all"
    task_details: list[AgentTaskDetail] = field(default_factory=list)


class AgentHistoryStore:
    schema_version = 1
    max_events = 80
    max_role_characters = 40
    max_event_characters = 20_000
    max_execution_characters = 200_000
    max_folder_characters = 1_000
    max_artifact_path_characters = 1_000
    max_task_details = 40
    max_task_id_characters = 64
    max_task_command_characters = 2_000
    max_task_detail_characters = 60_000
    max_task_detail_total_characters = 200_000
    task_statuses = frozenset(
        {
            "running",
            "waiting_review",
            "waiting_approval",
            "success",
            "error",
            "canceled",
            "blocked",
            "discarded",
            "interrupted",
            "completed",
        }
    )
    transient_task_statuses = frozenset({"running", "waiting_review", "waiting_approval"})

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> AgentHistoryRecord:
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError):
            return AgentHistoryRecord()
        if not raw:
            return AgentHistoryRecord()
        try:
            payload = json.loads(raw)
        except JSONDecodeError:
            return AgentHistoryRecord()
        if not isinstance(payload, dict) or payload.get("schema_version") != self.schema_version:
            return AgentHistoryRecord()

        raw_events = payload.get("events", [])
        events: list[AgentHistoryEvent] = []
        if isinstance(raw_events, list):
            for item in raw_events[-self.max_events :]:
                if not isinstance(item, dict):
                    continue
                role = self._bounded_text(item.get("role"), self.max_role_characters).strip()
                text = self._bounded_text(item.get("text"), self.max_event_characters)
                timestamp = self._bounded_text(item.get("timestamp"), 32).strip()
                artifact_path = self._bounded_text(
                    item.get("artifact_path"),
                    self.max_artifact_path_characters,
                ).strip()
                artifact_kind = self._artifact_kind(item.get("artifact_kind"), artifact_path)
                workspace_path = self._workspace_path(item.get("workspace_path"))
                task_id = self._task_id(item.get("task_id"))
                if role and text.strip():
                    events.append(
                        AgentHistoryEvent(
                            role,
                            text,
                            timestamp,
                            artifact_path,
                            artifact_kind,
                            workspace_path,
                            task_id,
                        )
                    )

        task_details = [
            replace(detail, status="interrupted")
            if detail.status in self.transient_task_statuses
            else detail
            for detail in self._normalize_task_details(payload.get("task_details", []))
        ]

        return AgentHistoryRecord(
            events=events,
            execution_details=self._bounded_tail(
                payload.get("execution_details"),
                self.max_execution_characters,
            ),
            active_folder=self._bounded_text(
                payload.get("active_folder"),
                self.max_folder_characters,
            ).strip(),
            updated_at=self._bounded_text(payload.get("updated_at"), 32).strip(),
            timeline_filter=self._timeline_filter(payload.get("timeline_filter")),
            task_details=task_details,
        )

    def save(self, record: AgentHistoryRecord) -> None:
        normalized = self._normalize(record)
        if not normalized.events and not normalized.execution_details and not normalized.task_details:
            self.clear()
            return
        payload = {
            "schema_version": self.schema_version,
            "updated_at": self.now_stamp(),
            "active_folder": normalized.active_folder,
            "timeline_filter": normalized.timeline_filter,
            "events": [
                {
                    "role": event.role,
                    "text": event.text,
                    "timestamp": event.timestamp,
                    "artifact_path": event.artifact_path,
                    "artifact_kind": event.artifact_kind,
                    "workspace_path": event.workspace_path,
                    "task_id": event.task_id,
                }
                for event in normalized.events
            ],
            "task_details": [
                {
                    "task_id": detail.task_id,
                    "command": detail.command,
                    "workspace_path": detail.workspace_path,
                    "started_at": detail.started_at,
                    "content": detail.content,
                    "status": detail.status,
                    "duration_seconds": detail.duration_seconds,
                    "completed_at": detail.completed_at,
                }
                for detail in normalized.task_details
            ],
            "execution_details": normalized.execution_details,
        }
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with temporary_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        except OSError:
            temporary_path.unlink(missing_ok=True)
            raise

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
        self.path.with_suffix(self.path.suffix + ".tmp").unlink(missing_ok=True)

    def _normalize(self, record: AgentHistoryRecord) -> AgentHistoryRecord:
        events: list[AgentHistoryEvent] = []
        for event in record.events[-self.max_events :]:
            role = self._bounded_text(event.role, self.max_role_characters).strip()
            text = self._bounded_text(event.text, self.max_event_characters)
            timestamp = self._bounded_text(event.timestamp, 32).strip()
            artifact_path = self._bounded_text(
                event.artifact_path,
                self.max_artifact_path_characters,
            ).strip()
            artifact_kind = self._artifact_kind(event.artifact_kind, artifact_path)
            workspace_path = self._workspace_path(event.workspace_path)
            task_id = self._task_id(event.task_id)
            if role and text.strip():
                events.append(
                    AgentHistoryEvent(
                        role,
                        text,
                        timestamp,
                        artifact_path,
                        artifact_kind,
                        workspace_path,
                        task_id,
                    )
                )
        return AgentHistoryRecord(
            events=events,
            execution_details=self._bounded_tail(
                record.execution_details,
                self.max_execution_characters,
            ),
            active_folder=self._bounded_text(
                record.active_folder,
                self.max_folder_characters,
            ).strip(),
            updated_at=self.now_stamp(),
            timeline_filter=self._timeline_filter(record.timeline_filter),
            task_details=self._normalize_task_details(record.task_details),
        )

    @staticmethod
    def _bounded_text(value: object, limit: int) -> str:
        return value[:limit] if isinstance(value, str) else ""

    @staticmethod
    def _bounded_tail(value: object, limit: int) -> str:
        return value[-limit:] if isinstance(value, str) else ""

    @staticmethod
    def _artifact_kind(value: object, artifact_path: str) -> str:
        kind = value.strip().casefold() if isinstance(value, str) else ""
        return kind if artifact_path and kind in {"file", "folder"} else ""

    def _workspace_path(self, value: object) -> str:
        path = self._bounded_text(value, self.max_folder_characters).strip()
        if not path or "\x00" in path or not Path(path).is_absolute():
            return ""
        return path

    @staticmethod
    def _timeline_filter(value: object) -> str:
        return value if value in {"all", "current"} else "all"

    def _task_id(self, value: object) -> str:
        task_id = self._bounded_text(value, self.max_task_id_characters).strip()
        return task_id if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", task_id) else ""

    def _normalize_task_details(self, value: object) -> list[AgentTaskDetail]:
        if not isinstance(value, list):
            return []
        normalized_reversed: list[AgentTaskDetail] = []
        remaining = self.max_task_detail_total_characters
        for raw_detail in reversed(value[-self.max_task_details :]):
            if isinstance(raw_detail, AgentTaskDetail):
                task_id_value = raw_detail.task_id
                command_value = raw_detail.command
                workspace_value = raw_detail.workspace_path
                started_value = raw_detail.started_at
                content_value = raw_detail.content
                status_value = raw_detail.status
                duration_value = raw_detail.duration_seconds
                completed_value = raw_detail.completed_at
            elif isinstance(raw_detail, dict):
                task_id_value = raw_detail.get("task_id")
                command_value = raw_detail.get("command")
                workspace_value = raw_detail.get("workspace_path")
                started_value = raw_detail.get("started_at")
                content_value = raw_detail.get("content")
                status_value = raw_detail.get("status")
                duration_value = raw_detail.get("duration_seconds")
                completed_value = raw_detail.get("completed_at")
            else:
                continue
            task_id = self._task_id(task_id_value)
            command = self._bounded_text(command_value, self.max_task_command_characters).strip()
            if not task_id or not command:
                continue
            workspace_path = self._workspace_path(workspace_value)
            started_at = self._bounded_text(started_value, 32).strip()
            content = self._bounded_tail(content_value, self.max_task_detail_characters)
            status = (
                status_value
                if isinstance(status_value, str) and status_value in self.task_statuses
                else "completed"
            )
            duration_seconds = self._duration_seconds(duration_value)
            completed_at = self._bounded_text(completed_value, 32).strip()
            if remaining <= 0:
                continue
            if len(content) > remaining:
                content = content[-remaining:]
            remaining -= len(content)
            normalized_reversed.append(
                AgentTaskDetail(
                    task_id,
                    command,
                    workspace_path,
                    started_at,
                    content,
                    status,
                    duration_seconds,
                    completed_at,
                )
            )
        normalized_reversed.reverse()
        return normalized_reversed

    @staticmethod
    def _duration_seconds(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0.0
        number = float(value)
        if not math.isfinite(number):
            return 0.0
        return round(min(86_400.0, max(0.0, number)), 3)

    @staticmethod
    def now_stamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
