from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.services.agent_history import (
    AgentHistoryEvent,
    AgentHistoryRecord,
    AgentHistoryStore,
    AgentTaskDetail,
)


class AgentHistoryStoreTests(unittest.TestCase):
    def test_round_trip_preserves_bounded_timeline_and_execution_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_history.json"
            store = AgentHistoryStore(path)
            record = AgentHistoryRecord(
                events=[
                    AgentHistoryEvent(
                        "Command",
                        "run tests",
                        "2026-07-28 10:20:30",
                        workspace_path=r"D:\projects\paco",
                        task_id="task_1",
                    ),
                    AgentHistoryEvent(
                        "Agent",
                        "Tests passed. ✓",
                        "2026-07-28 10:20:32",
                        r"D:\projects\paco\report.docx",
                        "file",
                    ),
                ],
                execution_details="COMMAND\nrun tests\n\ntest_example ... ok\n",
                active_folder=r"D:\projects\paco",
                timeline_filter="current",
                task_details=[
                    AgentTaskDetail(
                        "task_1",
                        "run tests",
                        r"D:\projects\paco",
                        "2026-07-28 10:20:30",
                        "COMMAND\nrun tests\n\ntest_example ... ok\n",
                        "success",
                        2.125,
                        "2026-07-28 10:20:32",
                    )
                ],
            )

            store.save(record)
            loaded = store.load()

            self.assertEqual(record.events, loaded.events)
            self.assertEqual(record.execution_details, loaded.execution_details)
            self.assertEqual(record.active_folder, loaded.active_folder)
            self.assertEqual("current", loaded.timeline_filter)
            self.assertEqual(record.task_details, loaded.task_details)
            self.assertTrue(loaded.updated_at)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_load_rejects_corruption_and_bounds_untrusted_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_history.json"
            store = AgentHistoryStore(path)
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual([], store.load().events)

            events = [
                {
                    "role": "Agent",
                    "text": ("x" * (store.max_event_characters + 20)) if index == 99 else f"event {index}",
                    "timestamp": "2026-07-28 10:20:30",
                    "artifact_path": "p" * (store.max_artifact_path_characters + 20),
                    "artifact_kind": "FILE",
                    "workspace_path": "w" * (store.max_folder_characters + 20),
                }
                for index in range(100)
            ]
            path.write_text(
                json.dumps(
                    {
                        "schema_version": store.schema_version,
                        "events": events,
                        "execution_details": "a" + ("z" * store.max_execution_characters),
                        "active_folder": "f" * (store.max_folder_characters + 5),
                    }
                ),
                encoding="utf-8",
            )

            loaded = store.load()

            self.assertEqual(store.max_events, len(loaded.events))
            self.assertEqual(store.max_event_characters, len(loaded.events[-1].text))
            self.assertEqual(store.max_execution_characters, len(loaded.execution_details))
            self.assertTrue(loaded.execution_details.startswith("z"))
            self.assertEqual(store.max_folder_characters, len(loaded.active_folder))
            self.assertEqual(store.max_artifact_path_characters, len(loaded.events[-1].artifact_path))
            self.assertEqual("file", loaded.events[-1].artifact_kind)
            self.assertEqual("", loaded.events[-1].workspace_path)

    def test_legacy_event_without_workspace_scope_remains_recallable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_history.json"
            store = AgentHistoryStore(path)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": store.schema_version,
                        "events": [{"role": "Command", "text": "run tests"}],
                    }
                ),
                encoding="utf-8",
            )

            loaded = store.load()

            self.assertEqual(1, len(loaded.events))
            self.assertEqual("", loaded.events[0].workspace_path)
            self.assertEqual("all", loaded.timeline_filter)

    def test_task_details_are_validated_and_bounded_across_the_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_history.json"
            store = AgentHistoryStore(path)
            details = [
                {
                    "task_id": f"task_{index}",
                    "command": f"command {index}",
                    "workspace_path": r"D:\projects\paco",
                    "started_at": "2026-07-28 10:20:30",
                    "content": str(index) * 10_000,
                }
                for index in range(45)
            ]
            details.append({"task_id": "bad id", "command": "ignored", "content": "unsafe"})
            path.write_text(
                json.dumps(
                    {
                        "schema_version": store.schema_version,
                        "task_details": details,
                        "execution_details": "all output",
                    }
                ),
                encoding="utf-8",
            )

            loaded = store.load()

            self.assertLessEqual(len(loaded.task_details), store.max_task_details)
            self.assertLessEqual(
                sum(len(detail.content) for detail in loaded.task_details),
                store.max_task_detail_total_characters,
            )
            self.assertTrue(all(" " not in detail.task_id for detail in loaded.task_details))
            self.assertEqual("task_44", loaded.task_details[-1].task_id)

    def test_task_outcomes_are_validated_and_unfinished_tasks_recover_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_history.json"
            store = AgentHistoryStore(path)
            path.write_text(
                json.dumps(
                    {
                        "schema_version": store.schema_version,
                        "execution_details": "output",
                        "task_details": [
                            {
                                "task_id": "running_task",
                                "command": "run tests",
                                "status": "running",
                                "duration_seconds": 4.5678,
                            },
                            {
                                "task_id": "invalid_task",
                                "command": "inspect",
                                "status": ["unsafe"],
                                "duration_seconds": float("inf"),
                            },
                            {
                                "task_id": "bounded_task",
                                "command": "build",
                                "status": "error",
                                "duration_seconds": 100_000,
                                "completed_at": "2026-07-28 10:20:32",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            loaded = store.load()

            self.assertEqual("interrupted", loaded.task_details[0].status)
            self.assertEqual(4.568, loaded.task_details[0].duration_seconds)
            self.assertEqual("completed", loaded.task_details[1].status)
            self.assertEqual(0.0, loaded.task_details[1].duration_seconds)
            self.assertEqual("error", loaded.task_details[2].status)
            self.assertEqual(86_400.0, loaded.task_details[2].duration_seconds)
            self.assertEqual("2026-07-28 10:20:32", loaded.task_details[2].completed_at)

    def test_clear_removes_record_and_abandoned_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_history.json"
            store = AgentHistoryStore(path)
            store.save(AgentHistoryRecord(events=[AgentHistoryEvent("Agent", "Ready")]))
            path.with_suffix(".json.tmp").write_text("partial", encoding="utf-8")

            store.clear()

            self.assertFalse(path.exists())
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
