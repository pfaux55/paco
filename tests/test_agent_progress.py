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

from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from local_matrix_assistant.ui.agent_progress import AgentProgressCard


class AgentProgressCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.card = AgentProgressCard()

    def tearDown(self) -> None:
        self.card.close()
        self.card.deleteLater()
        self.app.processEvents()

    def test_start_shows_running_task_and_tracks_elapsed_time(self) -> None:
        self.card.start("Drafting document", "Planning sections...", "Stop Agent")

        self.assertFalse(self.card.isHidden())
        self.assertFalse(self.card.activity_indicator.isHidden())
        self.assertTrue(self.card.activity_indicator.is_valid)
        self.assertTrue(self.card.activity_indicator.is_animated)
        self.assertEqual("running", self.card.task_state)
        self.assertEqual("Drafting document", self.card.title_label.text())
        self.assertEqual("Planning sections...", self.card.phase_label.text())
        self.assertEqual("Stop Agent", self.card.cancel_button.text())
        self.assertTrue(self.card.cancel_button.isEnabled())
        self.assertEqual((0, 0), (self.card.progress_bar.minimum(), self.card.progress_bar.maximum()))
        self.assertTrue(self.card._tick_timer.isActive())

        QTest.qWait(1250)
        self.card._update_elapsed()

        self.assertRegex(self.card.elapsed_label.text(), r"^0:0[1-9] elapsed$")

    def test_cancel_is_single_shot_and_updates_the_phase(self) -> None:
        spy = QSignalSpy(self.card.cancel_requested)
        self.card.start("Running tests", "Collecting tests...", "Stop Tests")

        self.card.cancel_button.click()
        self.card.cancel_button.click()

        self.assertEqual(1, spy.count())
        self.assertTrue(self.card.cancel_pending)
        self.assertFalse(self.card.cancel_button.isEnabled())
        self.assertIn("Stopping safely", self.card.phase_label.text())

    def test_finish_exposes_each_terminal_state_then_hides(self) -> None:
        for state, label in (
            ("success", "COMPLETED"),
            ("error", "FAILED"),
            ("canceled", "STOPPED"),
        ):
            with self.subTest(state=state):
                self.card.start("Task", "Working...", "Stop")
                self.card.finish(state, f"{state} result")

                self.assertEqual(state, self.card.task_state)
                self.assertEqual(label, self.card.state_label.text())
                self.assertEqual(f"{state} result", self.card.phase_label.text())
                self.assertTrue(self.card.cancel_button.isHidden())
                self.assertTrue(self.card.activity_indicator.isHidden())
                self.assertFalse(self.card._tick_timer.isActive())
                self.assertTrue(self.card._hide_timer.isActive())

        self.card._hide_timer.setInterval(10)
        self.card.finish("success", "Done")
        QTest.qWait(60)

        self.assertTrue(self.card.isHidden())
        self.assertEqual("idle", self.card.task_state)

    def test_repeated_start_resets_pending_cancel_and_terminal_hide(self) -> None:
        self.card.start("First", "Working...", "Stop")
        self.card.mark_cancel_requested()
        self.card.finish("canceled", "Stopped")

        self.card.start("Second", "Starting again...", "Stop Agent")

        self.assertEqual("running", self.card.task_state)
        self.assertFalse(self.card.cancel_pending)
        self.assertFalse(self.card._hide_timer.isActive())
        self.assertTrue(self.card.cancel_button.isEnabled())
        self.assertEqual("Second", self.card.title_label.text())

    def test_updates_and_finishes_before_start_are_ignored(self) -> None:
        self.card.update_phase("Unexpected")
        self.card.finish("error", "Unexpected")

        self.assertTrue(self.card.isHidden())
        self.assertEqual("idle", self.card.task_state)


if __name__ == "__main__":
    unittest.main()
