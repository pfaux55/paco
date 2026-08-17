from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from local_matrix_assistant.ui.agent_timeline import AgentEventCard, AgentTimeline


class AgentTimelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_cards_are_structured_selectable_and_kind_specific(self) -> None:
        timeline = AgentTimeline()
        timeline.show()

        command = timeline.append_entry("Command", "Add input validation")
        error = timeline.append_entry("Error", "Model unavailable", "2026-07-28 10:20:30")

        self.assertEqual(2, timeline.entry_count)
        self.assertEqual("command", command.event_kind)
        self.assertEqual("error", error.event_kind)
        self.assertEqual("Add input validation", command.body_label.text())
        self.assertTrue(
            command.body_label.textInteractionFlags()
            & Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.assertIn("Command", command.accessibleName())
        self.assertEqual("2026-07-28 10:20:30", error.timestamp)
        self.assertFalse(command.reuse_command_button.isHidden())
        self.assertTrue(error.reuse_command_button.isHidden())
        self.assertFalse(timeline.empty_label.isVisible())
        timeline.close()

    def test_command_card_restores_exact_text_without_executing(self) -> None:
        timeline = AgentTimeline()
        timeline.show()
        recalled: list[tuple[str, str]] = []
        timeline.reuse_command_requested.connect(
            lambda command, workspace: recalled.append((command, workspace))
        )
        card = timeline.append_entry(
            "Command",
            "run tests in tests/test_agent.py",
            workspace_path=r"D:\projects\paco",
        )

        card.reuse_command_button.click()

        self.assertEqual(
            [("run tests in tests/test_agent.py", r"D:\projects\paco")],
            recalled,
        )
        self.assertEqual("Workspace: paco", card.scope_label.text())
        self.assertEqual(r"D:\projects\paco", card.scope_label.toolTip())
        timeline.close()

    def test_command_card_opens_its_linked_task_details(self) -> None:
        timeline = AgentTimeline()
        timeline.show()
        requested: list[str] = []
        timeline.show_task_details_requested.connect(requested.append)
        card = timeline.append_entry(
            "Command",
            "run tests",
            workspace_path=r"D:\projects\paco",
            task_id="task_123",
        )

        card.show_details_button.click()

        self.assertEqual(["task_123"], requested)
        self.assertFalse(card.show_details_button.isHidden())
        timeline.close()

    def test_command_action_fits_a_compact_timeline(self) -> None:
        timeline = AgentTimeline()
        timeline.resize(320, 180)
        timeline.show()
        card = timeline.append_entry(
            "Command",
            "run project script typecheck",
            workspace_path=r"D:\projects\a-very-long-workspace-folder-name\source",
            task_id="task_1",
        )
        QTest.qWait(20)

        self.assertFalse(card.reuse_command_button.isHidden())
        self.assertFalse(card.scope_label.isHidden())
        self.assertFalse(card.show_details_button.isHidden())
        self.assertLessEqual(card.geometry().right(), timeline.viewport().width())
        self.assertEqual(0, timeline.horizontalScrollBar().maximum())
        timeline.close()

    def test_timeline_bounds_old_cards_and_shortens_only_the_visual_preview(self) -> None:
        timeline = AgentTimeline()
        timeline.max_events = 3
        long_text = "start\n" + ("x" * 4_000) + "\nend"

        for index in range(5):
            timeline.append_entry("Agent", long_text if index == 4 else f"event {index}")

        self.assertEqual(3, timeline.entry_count)
        self.assertEqual(long_text, timeline.cards[-1].full_text)
        self.assertIn("timeline preview shortened", timeline.cards[-1].body_label.text())
        self.assertIn("Full output", timeline.cards[-1].body_label.text())
        timeline.close()

    def test_workspace_filter_hides_other_and_legacy_events_without_deleting_them(self) -> None:
        timeline = AgentTimeline()
        timeline.resize(500, 180)
        timeline.show()
        first = timeline.append_entry(
            "Command",
            "run tests",
            workspace_path=r"D:\projects\first",
        )
        first_result = timeline.append_entry(
            "Agent",
            "Tests passed",
            workspace_path=r"D:\projects\first",
        )
        second = timeline.append_entry(
            "Command",
            "run lint",
            workspace_path=r"D:\projects\second",
        )
        legacy = timeline.append_entry("Command", "legacy command")

        self.assertEqual(4, timeline.visible_entry_count)
        timeline.set_workspace_filter(r"D:\projects\first")
        QTest.qWait(20)

        self.assertEqual(4, timeline.entry_count)
        self.assertEqual(2, timeline.visible_entry_count)
        self.assertFalse(first.isHidden())
        self.assertFalse(first_result.isHidden())
        self.assertTrue(second.isHidden())
        self.assertTrue(legacy.isHidden())
        self.assertIn("All workspaces", timeline.empty_label.text())

        timeline.set_workspace_filter(r"D:\projects\missing")
        QTest.qWait(20)
        self.assertEqual(0, timeline.visible_entry_count)
        self.assertFalse(timeline.empty_label.isHidden())
        self.assertLessEqual(timeline.widget().height(), timeline.viewport().height())

        timeline.set_workspace_filter()
        QTest.qWait(20)
        self.assertEqual(4, timeline.visible_entry_count)
        self.assertEqual(4, timeline.entry_count)
        timeline.close()

    def test_timeline_fits_cards_without_a_blank_scroll_region(self) -> None:
        timeline = AgentTimeline()
        timeline.resize(500, 160)
        timeline.show()

        timeline.append_entry("Command", "Run the workspace tests")
        timeline.append_entry("Agent", "Collected the available test suites.")
        timeline.append_entry("Error", "One validation failed. Review execution details.")
        QTest.qWait(30)

        last_card = timeline.cards[-1]
        unused_height = timeline.widget().height() - last_card.geometry().bottom()
        self.assertLessEqual(unused_height, 12)
        bar = timeline.verticalScrollBar()
        self.assertEqual(bar.maximum(), bar.value())
        timeline.close()

    def test_artifact_card_emits_file_and_folder_actions(self) -> None:
        timeline = AgentTimeline()
        timeline.show()
        opened_files: list[str] = []
        opened_folders: list[str] = []
        timeline.open_file_requested.connect(opened_files.append)
        timeline.open_folder_requested.connect(opened_folders.append)
        path = r"D:\work\reports\open-source-models.docx"

        card = timeline.append_entry(
            "Agent",
            "Created Word document.",
            artifact_path=path,
            artifact_kind="file",
        )
        QTest.qWait(20)

        self.assertFalse(card.artifact_actions.isHidden())
        self.assertFalse(card.open_file_button.isHidden())
        self.assertEqual(path, card.artifact_label.toolTip())
        card.open_file_button.click()
        card.open_folder_button.click()
        self.assertEqual([path], opened_files)
        self.assertEqual([path], opened_folders)
        timeline.close()

    def test_queued_relayout_is_safe_during_widget_destruction(self) -> None:
        timeline = AgentTimeline()
        timeline.show()
        timeline.append_entry("Agent", "A queued card layout update")

        timeline.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

        self.assertTrue(timeline._disposed)


if __name__ == "__main__":
    unittest.main()
