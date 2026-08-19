from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import QCoreApplication, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from local_matrix_assistant.ui.agent_panel import AgentPanel
from local_matrix_assistant.services.agent_permissions import (
    CREATE_ONLY_ACCESS,
    READ_ONLY_ACCESS,
)
from local_matrix_assistant.services.attachments import LocalAttachment
from local_matrix_assistant.services.agent_history import (
    AgentHistoryEvent,
    AgentHistoryRecord,
    AgentTaskDetail,
)


class AgentPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.panel = AgentPanel(r"D:\projects")
        self.panel.show()

    def tearDown(self) -> None:
        self.panel.close()

    def test_agent_header_uses_paco_mark(self) -> None:
        self.assertFalse(self.panel.paco_mark.pixmap().isNull())
        self.assertEqual("Paco agent", self.panel.paco_mark.accessibleName())

    def test_run_button_tracks_command_input(self) -> None:
        self.assertFalse(self.panel.run_button.isEnabled())
        self.panel.command_input.setPlainText("create file notes.txt")
        QTest.qWait(10)
        self.assertTrue(self.panel.run_button.isEnabled())
        self.panel.set_busy(True)
        self.assertFalse(self.panel.run_button.isEnabled())

    def test_agent_file_enables_send_and_can_be_removed(self) -> None:
        attachment = LocalAttachment(
            path=r"D:\outside\requirements.txt",
            name="requirements.txt",
            size_bytes=18,
            content="PySide6==6.8.0",
        )
        removed: list[str] = []
        self.panel.attachment_remove_requested.connect(removed.append)

        self.panel.set_pending_attachments([attachment])

        self.assertEqual(1, self.panel.attachment_count())
        self.assertFalse(self.panel.attachment_tray.isHidden())
        self.assertTrue(self.panel.run_button.isEnabled())
        self.panel._attachment_rows[0][3].click()
        self.assertEqual([attachment.path], removed)

        self.panel.set_pending_attachments([])
        self.assertTrue(self.panel.attachment_tray.isHidden())
        self.assertFalse(self.panel.run_button.isEnabled())

    def test_agent_composer_accepts_file_drops(self) -> None:
        self.assertTrue(self.panel.command_panel.acceptDrops())
        self.assertIn("drop files", self.panel.command_input.placeholderText())

    def test_drop_over_agent_log_reaches_page_file_handler(self) -> None:
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile("C:/work/example.py")])
        dropped: list[list[str]] = []
        self.panel.file_paths_dropped.connect(dropped.append)
        target = self.panel.action_log.viewport()
        drag_event = QDragEnterEvent(
            QPoint(5, 5),
            Qt.DropAction.CopyAction,
            mime_data,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        drop_event = QDropEvent(
            QPointF(5, 5),
            Qt.DropAction.CopyAction,
            mime_data,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        QCoreApplication.sendEvent(target, drag_event)
        QCoreApplication.sendEvent(target, drop_event)

        self.assertTrue(drag_event.isAccepted())
        self.assertTrue(drop_event.isAccepted())
        self.assertEqual([["C:/work/example.py"]], dropped)

    def test_workspace_access_control_is_clear_persistent_ready_and_busy_safe(self) -> None:
        changes: list[str] = []
        self.panel.permission_mode_changed.connect(changes.append)

        self.assertEqual("Create-only", self.panel.permission_mode_combo.itemText(0))
        self.assertEqual(CREATE_ONLY_ACCESS, self.panel.permission_mode_combo.itemData(0))
        self.assertEqual(CREATE_ONLY_ACCESS, self.panel.permission_mode_combo.currentData())
        read_only_index = self.panel.permission_mode_combo.findData(READ_ONLY_ACCESS)
        self.panel.permission_mode_combo.setCurrentIndex(read_only_index)

        self.assertEqual([READ_ONLY_ACCESS], changes)
        self.assertEqual(READ_ONLY_ACCESS, self.panel.permission_mode_combo.property("accessMode"))
        self.assertIn("writes", self.panel.permission_mode_combo.toolTip())
        self.panel.set_busy(True)
        self.assertFalse(self.panel.permission_mode_combo.isEnabled())
        self.panel.set_busy(False)
        self.assertTrue(self.panel.permission_mode_combo.isEnabled())

    def test_workspace_access_control_fits_compact_agent_layout(self) -> None:
        self.panel.resize(796, 640)
        self.panel.set_active_folder(r"D:\projects\a-very-long-workspace-folder-name\source")
        self.panel.set_permission_mode(READ_ONLY_ACCESS)
        QTest.qWait(20)

        self.assertEqual(640, self.panel.height())
        self.assertEqual(0, self.panel.panel_scroll.horizontalScrollBar().maximum())
        self.assertFalse(self.panel.permission_mode_combo.isHidden())
        self.assertGreater(self.panel.active_folder_label.width(), 200)

    def test_enter_requests_action_and_take_command_clears_input(self) -> None:
        runs: list[str] = []
        self.panel.command_input.submit_requested.connect(lambda: runs.append("run"))
        self.panel.command_input.setFocus()
        QTest.keyClicks(self.panel.command_input, "open Notepad")
        QTest.keyClick(self.panel.command_input, Qt.Key.Key_Return)

        self.assertEqual(["run"], runs)
        self.assertEqual("open Notepad", self.panel.take_command())
        self.assertEqual("", self.panel.command_input.toPlainText())

    def test_active_folder_and_action_log_are_separate(self) -> None:
        self.panel.set_active_folder(r"D:\work")
        self.panel.append_log("Agent", "Created file: D:\\work\\notes.txt")

        self.assertEqual(r"D:\work", self.panel.active_folder_label.text())
        self.assertEqual("Choose Folder", self.panel.choose_folder_button.text())
        self.assertIn("Created file", self.panel.action_log.toPlainText())
        self.assertEqual(1, self.panel.task_timeline.entry_count)
        self.assertIn("Created file", self.panel.task_timeline.cards[0].body_label.text())

    def test_new_history_events_capture_the_workspace_active_at_that_time(self) -> None:
        self.panel.append_log("Command", "run tests")
        self.panel.set_active_folder(r"D:\second-project")
        self.panel.append_log("Command", "run lint")

        record = self.panel.history_record(r"D:\second-project")

        self.assertEqual(r"D:\projects", record.events[0].workspace_path)
        self.assertEqual(r"D:\second-project", record.events[1].workspace_path)
        self.assertEqual("Workspace: projects", self.panel.task_timeline.cards[0].scope_label.text())
        self.assertEqual(
            "Workspace: second-project",
            self.panel.task_timeline.cards[1].scope_label.text(),
        )

    def test_task_timeline_and_execution_details_toggle_without_losing_output(self) -> None:
        self.panel.append_log("Command", "run tests")
        self.panel.append_task_output("test_ready ... ok\n")

        self.assertIs(self.panel.task_timeline, self.panel.log_stack.currentWidget())
        self.panel.log_view_button.click()
        self.assertIs(self.panel.execution_details_page, self.panel.log_stack.currentWidget())
        self.assertEqual("EXECUTION · ALL", self.panel.log_view_label.text())
        self.assertIn("test_ready ... ok", self.panel.action_log.toPlainText())
        self.panel.log_view_button.click()
        self.assertIs(self.panel.task_timeline, self.panel.log_stack.currentWidget())
        self.assertEqual("TASKS · ALL", self.panel.log_view_label.text())

    def test_execution_details_can_isolate_each_command_and_restore_all_output(self) -> None:
        self.panel.append_log("Command", "run tests")
        self.panel.append_task_output("test_example ... ok\n")
        self.panel.append_log("Agent", "Tests passed")
        self.panel.finish_task_detail()
        self.panel.append_log("Command", "run lint")
        self.panel.append_task_output("lint warning\n")
        self.panel.append_log("Error", "Lint failed")
        self.panel.finish_task_detail()

        self.assertEqual(3, self.panel.task_detail_combo.count())
        self.assertEqual(2, len(self.panel.history_record().task_details))
        first_task = self.panel.history_record().task_details[0]
        first_card = self.panel.task_timeline.cards[0]
        self.assertEqual(first_task.task_id, first_card.task_id)
        first_card.show_details_button.click()

        isolated = self.panel.action_log.toPlainText()
        self.assertIn("run tests", isolated)
        self.assertIn("test_example ... ok", isolated)
        self.assertIn("Tests passed", isolated)
        self.assertNotIn("run lint", isolated)
        self.assertNotIn("lint warning", isolated)
        self.panel.copy_task_output_button.click()
        self.assertEqual(isolated, QApplication.clipboard().text())
        self.assertEqual("Copied", self.panel.copy_task_output_button.text())
        self.panel._copy_output_timer.timeout.emit()
        self.assertEqual("Copy", self.panel.copy_task_output_button.text())
        self.assertEqual("EXECUTION · TASK", self.panel.log_view_label.text())

        self.panel.task_detail_combo.setCurrentIndex(0)
        combined = self.panel.action_log.toPlainText()
        self.assertIn("run tests", combined)
        self.assertIn("run lint", combined)
        self.assertEqual("EXECUTION · ALL", self.panel.log_view_label.text())

        restored = AgentPanel(r"D:\projects")
        restored.show()
        restored.load_history(self.panel.history_record())
        self.assertEqual(3, restored.task_detail_combo.count())
        restored.task_detail_combo.setCurrentIndex(
            restored.task_detail_combo.findData(first_task.task_id)
        )
        self.assertEqual(isolated, restored.action_log.toPlainText())
        self.assertFalse(restored.task_timeline.cards[0].show_details_button.isHidden())
        restored.close()

    def test_task_state_and_duration_update_live_and_finish_persistently(self) -> None:
        clock = [100.0]
        with patch("local_matrix_assistant.ui.agent_panel.time.monotonic", side_effect=lambda: clock[0]):
            self.panel.append_log("Command", "create report")
            task_id = self.panel.history_record().task_details[0].task_id
            card = self.panel.task_timeline.cards[0]
            self.assertEqual("running", self.panel.history_record().task_details[0].status)
            self.assertEqual("RUNNING · <1s", card.task_state_label.text())

            self.panel.set_task_detail_status("waiting_review")
            self.panel.task_detail_combo.setCurrentIndex(
                self.panel.task_detail_combo.findData(task_id)
            )
            clock[0] = 105.4
            self.panel._refresh_active_task_timing()

            self.assertEqual(task_id, self.panel.task_detail_combo.currentData())
            self.assertEqual("REVIEW · 5s", card.task_state_label.text())
            self.panel.finish_task_detail("success")

        detail = self.panel.history_record().task_details[0]
        self.assertEqual("success", detail.status)
        self.assertEqual(5.4, detail.duration_seconds)
        self.assertTrue(detail.completed_at)
        self.assertEqual("SUCCESS · 5s", card.task_state_label.text())
        self.assertFalse(self.panel._task_duration_timer.isActive())

    def test_interrupted_task_state_is_visible_after_history_restore(self) -> None:
        record = AgentHistoryRecord(
            events=[
                AgentHistoryEvent(
                    "Command",
                    "run tests",
                    workspace_path=r"D:\projects",
                    task_id="task_1",
                )
            ],
            task_details=[
                AgentTaskDetail(
                    "task_1",
                    "run tests",
                    r"D:\projects",
                    status="interrupted",
                    duration_seconds=12.4,
                )
            ],
        )

        self.panel.load_history(record)

        self.assertEqual(
            "INTERRUPTED · 12s",
            self.panel.task_timeline.cards[0].task_state_label.text(),
        )
        self.assertFalse(self.panel._task_duration_timer.isActive())

    def test_save_output_emits_exact_selected_task_and_respects_access_mode(self) -> None:
        requests: list[tuple[str, str]] = []
        self.panel.save_task_output_requested.connect(
            lambda name, content: requests.append((name, content))
        )
        self.panel.append_log("Command", "Run Tests: API / Unit")
        self.panel.append_task_output("test_api ... ok\n")
        self.panel.finish_task_detail()
        task = self.panel.history_record().task_details[0]
        self.panel.show_task_details(task.task_id)

        selected_output = self.panel.action_log.toPlainText()
        self.panel.save_task_output_button.click()

        self.assertEqual(1, len(requests))
        self.assertEqual("agent-run-tests-api-unit.txt", requests[0][0])
        self.assertEqual(selected_output, requests[0][1])

        self.panel.set_busy(True)
        self.assertFalse(self.panel.save_task_output_button.isEnabled())
        self.assertTrue(self.panel.copy_task_output_button.isEnabled())
        self.panel.set_busy(False)

        self.panel.set_permission_mode(READ_ONLY_ACCESS)
        self.assertFalse(self.panel.save_task_output_button.isEnabled())
        self.assertTrue(self.panel.copy_task_output_button.isEnabled())
        self.panel.request_save_selected_task_output()
        self.assertEqual(1, len(requests))

    def test_task_output_selector_fits_compact_agent_layout(self) -> None:
        self.panel.resize(796, 640)
        self.panel.append_log(
            "Command",
            "run project script a-very-long-project-script-name:with:qualifiers",
        )
        self.panel.append_task_output("streamed output\n")
        self.panel.finish_task_detail()
        self.panel.show_execution_details()
        QTest.qWait(20)

        self.assertEqual(640, self.panel.height())
        self.assertEqual(0, self.panel.panel_scroll.horizontalScrollBar().maximum())
        self.assertFalse(self.panel.task_detail_combo.isHidden())
        self.assertGreater(self.panel.task_detail_combo.width(), 250)

    def test_timeline_filter_tracks_current_workspace_without_changing_saved_history(self) -> None:
        self.panel.append_log("Command", "run tests")
        self.panel.set_active_folder(r"D:\other")
        self.panel.append_log("Command", "run lint")
        current_index = self.panel.timeline_filter_combo.findData("current")
        self.panel.timeline_filter_combo.setCurrentIndex(current_index)

        self.assertEqual(1, self.panel.task_timeline.visible_entry_count)
        self.assertEqual("run lint", self.panel.task_timeline.cards[1].full_text)
        self.assertEqual("TASKS · CURRENT", self.panel.log_view_label.text())
        self.assertEqual(2, len(self.panel.history_record().events))
        self.assertEqual("current", self.panel.history_record().timeline_filter)

        self.panel.set_active_folder(r"D:\projects")
        self.assertEqual(1, self.panel.task_timeline.visible_entry_count)
        self.assertFalse(self.panel.task_timeline.cards[0].isHidden())
        self.assertTrue(self.panel.task_timeline.cards[1].isHidden())

        self.panel.show_execution_details()
        self.assertEqual("EXECUTION · ALL", self.panel.log_view_label.text())
        self.assertIn("run tests", self.panel.action_log.toPlainText())
        self.assertIn("run lint", self.panel.action_log.toPlainText())

        restored = AgentPanel(r"D:\projects")
        restored.show()
        restored.load_history(self.panel.history_record())
        self.assertEqual("current", restored.timeline_filter_combo.currentData())
        self.assertEqual(1, restored.task_timeline.visible_entry_count)
        restored.close()

    def test_persisted_command_can_be_restored_for_review_without_running(self) -> None:
        recalled: list[str] = []
        submitted: list[str] = []
        self.panel.command_recalled.connect(recalled.append)
        self.panel.command_input.submit_requested.connect(lambda: submitted.append("run"))
        self.panel.load_history(
            AgentHistoryRecord(
                events=[
                    AgentHistoryEvent(
                        "Command",
                        "run lint",
                        "2026-07-28 12:00:00",
                        workspace_path=r"D:\projects",
                    )
                ]
            )
        )

        self.panel.task_timeline.cards[0].reuse_command_button.click()

        self.assertEqual("run lint", self.panel.command_input.toPlainText())
        self.assertEqual(["run lint"], recalled)
        self.assertEqual([], submitted)
        self.assertTrue(self.panel.run_button.isEnabled())

    def test_command_from_another_workspace_is_not_restored(self) -> None:
        blocked: list[tuple[str, str, str]] = []
        self.panel.command_recall_blocked.connect(
            lambda command, origin, current: blocked.append((command, origin, current))
        )
        self.panel.load_history(
            AgentHistoryRecord(
                events=[
                    AgentHistoryEvent(
                        "Command",
                        "format project",
                        workspace_path=r"D:\other-project",
                    )
                ]
            )
        )

        self.panel.task_timeline.cards[0].reuse_command_button.click()

        self.assertEqual("", self.panel.command_input.toPlainText())
        self.assertEqual(
            [("format project", r"D:\other-project", r"D:\projects")],
            blocked,
        )

    def test_command_recall_is_disabled_while_busy_or_awaiting_script_approval(self) -> None:
        self.panel.append_log("Command", "run tests")
        button = self.panel.task_timeline.cards[0].reuse_command_button

        self.panel.set_busy(True)
        self.assertFalse(button.isEnabled())
        button.click()
        self.assertEqual("", self.panel.command_input.toPlainText())

        self.panel.set_busy(False)
        self.panel.show_script_approval(
            name="check",
            command="python check.py",
            folder=r"D:\projects",
            warning="Review this script.",
            high_risk=False,
        )
        self.assertFalse(button.isEnabled())
        button.click()
        self.assertEqual("", self.panel.command_input.toPlainText())

        self.panel.clear_script_approval()
        self.assertTrue(button.isEnabled())

    def test_edit_preview_has_explicit_apply_and_discard_states(self) -> None:
        self.panel.show_edit_preview("src/app.py", "--- a/src/app.py\n+++ b/src/app.py\n-old\n+new")

        self.assertTrue(self.panel.edit_preview_panel.isVisible())
        self.assertEqual("src/app.py", self.panel.edit_target_label.text())
        self.assertIn("+new", self.panel.edit_diff_view.toPlainText())
        self.assertTrue(self.panel.apply_edit_button.isEnabled())

        self.panel.set_busy(True)
        self.assertFalse(self.panel.apply_edit_button.isEnabled())
        self.panel.set_busy(False)
        self.panel.clear_edit_preview()
        self.assertFalse(self.panel.edit_preview_panel.isVisible())

    def test_new_file_preview_uses_explicit_create_state_and_resets(self) -> None:
        self.panel.show_edit_preview(
            "src/health.py",
            "--- /dev/null\n+++ b/src/health.py\n+def healthy():\n+    return True\n",
            operation="create",
        )

        self.assertEqual("PROPOSED NEW FILE", self.panel.preview_title.text())
        self.assertEqual("Create File", self.panel.apply_edit_button.text())
        self.assertTrue(self.panel.edit_preview_panel.isVisible())

        self.panel.clear_edit_preview()

        self.assertEqual("PROPOSED EDIT", self.panel.preview_title.text())
        self.assertEqual("Apply Edit", self.panel.apply_edit_button.text())

    def test_fix_preview_offers_apply_and_test(self) -> None:
        requests: list[str] = []
        self.panel.apply_and_test_requested.connect(lambda: requests.append("test"))
        self.panel.show_edit_preview(
            "2 files",
            "--- a/app.py\n+++ b/app.py\n-old\n+new\n",
            operation="fix",
        )

        self.assertEqual("PROPOSED FIX", self.panel.preview_title.text())
        self.assertEqual("Apply Fix", self.panel.apply_edit_button.text())
        self.assertTrue(self.panel.apply_and_test_button.isVisible())
        self.panel.apply_and_test_button.click()
        self.assertEqual(["test"], requests)

        self.panel.clear_edit_preview()
        self.assertFalse(self.panel.apply_and_test_button.isVisible())

    def test_natural_change_preview_uses_explicit_review_state(self) -> None:
        self.panel.show_edit_preview(
            "src/login.py",
            "--- a/src/login.py\n+++ b/src/login.py\n-old\n+new\n",
            operation="change",
        )

        self.assertEqual("PROPOSED CHANGE", self.panel.preview_title.text())
        self.assertEqual("Apply Change", self.panel.apply_edit_button.text())
        self.assertTrue(self.panel.apply_and_test_button.isVisible())

    def test_format_preview_uses_explicit_review_and_test_state(self) -> None:
        self.panel.show_edit_preview(
            "2 files",
            "--- a/app.py\n+++ b/app.py\n-value=1\n+value = 1\n",
            operation="format",
        )

        self.assertEqual("PROPOSED FORMATTING", self.panel.preview_title.text())
        self.assertEqual("Apply Formatting", self.panel.apply_edit_button.text())
        self.assertTrue(self.panel.apply_and_test_button.isVisible())

    def test_format_preview_scrolls_without_forcing_compact_window_taller(self) -> None:
        self.panel.resize(796, 640)
        self.panel.show_edit_preview(
            "2 files",
            "--- a/app.py\n+++ b/app.py\n-value=1\n+value = 1\n",
            operation="format",
        )
        QTest.qWait(10)

        self.assertEqual(640, self.panel.height())
        self.assertEqual(0, self.panel.panel_scroll.horizontalScrollBar().maximum())
        self.assertGreater(self.panel.panel_scroll.verticalScrollBar().maximum(), 0)
        self.assertFalse(self.panel.apply_edit_button.isHidden())

    def test_project_script_approval_exposes_exact_command_and_actions(self) -> None:
        actions: list[str] = []
        self.panel.approve_script_requested.connect(lambda: actions.append("approve"))
        self.panel.reject_script_requested.connect(lambda: actions.append("reject"))

        self.panel.show_script_approval(
            name="typecheck",
            command="tsc --noEmit",
            folder=r"D:\projects\app",
            warning="Project scripts may modify files.",
            high_risk=False,
        )

        self.assertFalse(self.panel.script_approval_panel.isHidden())
        self.assertEqual("npm script: typecheck", self.panel.script_name_label.text())
        self.assertEqual("tsc --noEmit", self.panel.script_command_view.toPlainText())
        self.assertFalse(self.panel.command_input.isEnabled())
        self.panel.run_script_button.click()
        self.panel.reject_script_button.click()
        self.assertEqual(["approve", "reject"], actions)

        self.panel.clear_script_approval()
        self.assertTrue(self.panel.script_approval_panel.isHidden())
        self.assertTrue(self.panel.command_input.isEnabled())

    def test_high_risk_script_approval_has_distinct_state_at_compact_size(self) -> None:
        self.panel.resize(796, 640)
        self.panel.show_script_approval(
            name="deploy:prod",
            command="npm publish",
            folder=r"D:\projects\app",
            warning="High-risk script.",
            high_risk=True,
        )
        QTest.qWait(10)

        self.assertEqual("high", self.panel.script_approval_panel.property("riskLevel"))
        self.assertEqual("HIGH RISK", self.panel.script_risk_label.text())
        self.assertEqual(640, self.panel.height())
        self.assertEqual(0, self.panel.panel_scroll.horizontalScrollBar().maximum())

    def test_multi_file_diff_can_be_reviewed_per_file_and_empty_diff_fails_closed(self) -> None:
        diff = (
            "--- a/config.py\n+++ b/config.py\n@@ -1 +1 @@\n-old = 1\n+new = 1\n\n"
            "--- a/consumer.py\n+++ b/consumer.py\n@@ -1 +1 @@\n-old()\n+new()\n"
        )
        self.panel.show_edit_preview("2 files", diff, operation="change")

        self.assertEqual(3, self.panel.diff_review.file_selector.count())
        self.assertIn("2 files", self.panel.diff_review.summary_label.text())
        self.panel.diff_review.file_selector.setCurrentIndex(2)
        self.assertIn("consumer.py", self.panel.edit_diff_view.toPlainText())
        self.assertNotIn("config.py", self.panel.edit_diff_view.toPlainText())
        self.assertTrue(self.panel.apply_edit_button.isEnabled())

        self.panel.show_edit_preview("config.py", "", operation="edit")

        self.assertIs(
            self.panel.diff_review.empty_label,
            self.panel.diff_review.content_stack.currentWidget(),
        )
        self.assertFalse(self.panel.apply_edit_button.isEnabled())

    def test_failed_fix_banner_offers_reviewed_follow_up_or_dismissal(self) -> None:
        requests: list[str] = []
        self.panel.follow_up_fix_requested.connect(lambda: requests.append("draft"))
        self.panel.dismiss_follow_up_requested.connect(lambda: requests.append("dismiss"))

        self.panel.show_follow_up_fix()

        self.assertTrue(self.panel.follow_up_fix_panel.isVisible())
        self.assertIn("No file changes", self.panel.follow_up_fix_detail.text())
        self.panel.draft_follow_up_button.click()
        self.panel.dismiss_follow_up_button.click()
        self.assertEqual(["draft", "dismiss"], requests)

        self.panel.clear_follow_up_fix()
        self.assertFalse(self.panel.follow_up_fix_panel.isVisible())

    def test_test_task_exposes_cancel_and_appends_raw_output(self) -> None:
        self.panel.append_log("Agent", "Running Python unittest.\n\n")
        self.panel.set_task_running(True)
        self.panel.append_task_output("test_ready ... ok\n")

        self.assertTrue(self.panel.cancel_task_button.isVisible())
        self.assertIs(self.panel.execution_details_page, self.panel.log_stack.currentWidget())
        self.assertIn("test_ready ... ok", self.panel.action_log.toPlainText())

        self.panel.set_task_running(False)
        self.assertFalse(self.panel.cancel_task_button.isVisible())

    def test_history_snapshot_restore_and_clear_preserve_full_execution_details(self) -> None:
        changes: list[bool] = []
        self.panel.history_changed.connect(lambda: changes.append(True))
        record = AgentHistoryRecord(
            events=[
                AgentHistoryEvent(
                    "Command",
                    "run tests",
                    "2026-07-28 10:20:30",
                    r"D:\work\report.docx",
                    "file",
                )
            ],
            execution_details="COMMAND\nrun tests\n\nfull streamed output\n",
            active_folder=r"D:\work",
        )

        self.panel.load_history(record)

        self.assertEqual(1, self.panel.task_timeline.entry_count)
        self.assertEqual("2026-07-28 10:20:30", self.panel.task_timeline.cards[0].timestamp)
        self.assertEqual(record.execution_details, self.panel.action_log.toPlainText())
        self.assertTrue(self.panel.clear_history_button.isEnabled())
        self.assertEqual(record.events, self.panel.history_record().events)
        self.assertFalse(self.panel.task_timeline.cards[0].open_file_button.isHidden())
        self.assertEqual([], changes)

        self.panel.clear_history_button.click()

        self.assertEqual(1, self.panel.task_timeline.entry_count)
        self.assertEqual("Confirm Clear All", self.panel.clear_history_button.text())
        self.panel.clear_history_button.click()

        self.assertEqual(0, self.panel.task_timeline.entry_count)
        self.assertEqual("", self.panel.action_log.toPlainText())
        self.assertFalse(self.panel.clear_history_button.isEnabled())
        self.assertEqual([True], changes)

    def test_clear_all_confirmation_expires_without_deleting_history(self) -> None:
        states: list[bool] = []
        self.panel.history_clear_confirmation_changed.connect(states.append)
        self.panel.append_log("Command", "run tests")

        self.panel.clear_history_button.click()
        self.assertEqual("Confirm Clear All", self.panel.clear_history_button.text())
        self.assertTrue(self.panel._clear_history_confirmation_timer.isActive())
        self.panel._clear_history_confirmation_timer.timeout.emit()

        self.assertEqual(1, self.panel.task_timeline.entry_count)
        self.assertIn("run tests", self.panel.action_log.toPlainText())
        self.assertEqual("Clear All", self.panel.clear_history_button.text())
        self.assertEqual([True, False], states)

    def test_clear_all_removes_hidden_workspaces_not_only_the_visible_filter(self) -> None:
        self.panel.append_log("Command", "run tests")
        self.panel.set_active_folder(r"D:\other")
        self.panel.append_log("Command", "run lint")
        self.panel.timeline_filter_combo.setCurrentIndex(
            self.panel.timeline_filter_combo.findData("current")
        )
        self.assertEqual(1, self.panel.task_timeline.visible_entry_count)

        self.panel.clear_history_button.click()
        self.panel.clear_history_button.click()

        self.assertEqual(0, self.panel.task_timeline.entry_count)
        self.assertEqual("", self.panel.action_log.toPlainText())

    def test_running_agent_action_uses_specific_stop_label(self) -> None:
        self.panel.set_task_running(True, "Stop Agent")

        self.assertTrue(self.panel.cancel_task_button.isVisible())
        self.assertEqual("Stop Agent", self.panel.cancel_task_button.text())
        self.assertFalse(self.panel.progress_card.isHidden())
        self.assertEqual("running", self.panel.progress_card.task_state)

    def test_progress_phase_cancel_and_outcome_are_exposed_in_the_agent_panel(self) -> None:
        canceled: list[bool] = []
        self.panel.cancel_task_requested.connect(lambda: canceled.append(True))
        self.panel.set_task_running(
            True,
            "Stop Agent",
            title="Drafting document",
            phase="Planning sections...",
        )

        self.panel.update_task_phase("Writing the document...")
        self.assertEqual("Writing the document...", self.panel.progress_card.phase_label.text())
        self.panel.cancel_task_button.click()

        self.assertEqual([True], canceled)
        self.assertTrue(self.panel.progress_card.cancel_pending)

        self.panel.set_task_running(False)
        self.panel.finish_task_progress("canceled", "Stopped safely.")

        self.assertEqual("canceled", self.panel.progress_card.task_state)
        self.assertEqual("Stopped safely.", self.panel.progress_card.phase_label.text())


if __name__ == "__main__":
    unittest.main()
