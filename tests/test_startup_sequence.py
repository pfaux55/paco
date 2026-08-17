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

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from local_matrix_assistant.ui.startup_sequence import StartupSequence


class StartupSequenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_watchdog_recovers_if_startup_animation_never_finishes(self) -> None:
        root = QWidget()
        root.resize(800, 600)
        content = QWidget(root)
        with patch.object(StartupSequence, "startup_timeout_ms", 20):
            sequence = StartupSequence(
                app_name="Paco",
                root=root,
            )
            sequence.begin()
            QTest.qWait(120)

        self.assertFalse(sequence.overlay.isVisible())
        self.assertTrue(sequence.overlay._finished)
        self.assertFalse(sequence.overlay.isEnabled())
        self.assertFalse(sequence._startup_timeout.isActive())
        self.assertIsNone(content.graphicsEffect())
        root.close()

    def test_begin_starts_and_advances_the_startup_animation(self) -> None:
        root = QWidget()
        root.resize(800, 600)
        sequence = StartupSequence(
            app_name="Paco",
            root=root,
        )
        root.show()

        sequence.begin()

        self.assertTrue(sequence.overlay._started)
        self.assertTrue(sequence.overlay.isVisible())
        self.assertTrue(sequence.overlay._animation_group.state())
        QTest.qWait(200)
        self.assertGreater(sequence.overlay._intro_progress, 0.0)
        root.close()

    def test_first_painted_intro_frame_reports_launcher_handoff_ready(self) -> None:
        root = QWidget()
        root.resize(800, 600)
        sequence = StartupSequence(app_name="Paco", root=root)
        ready = []
        sequence.first_frame_ready.connect(lambda: ready.append(True))
        root.show()
        sequence.begin()
        QTest.qWait(30)

        self.assertEqual([True], ready)
        root.close()

    def test_launcher_ring_stays_on_exact_screen_center_through_handoff(self) -> None:
        root = QWidget()
        root.resize(800, 600)
        root.move(200, 100)
        launcher_center = QPoint(600, 400)
        environment = {
            "PACO_STARTUP_EVENT": "test-event",
            "PACO_STARTUP_RING_CENTER_X": str(launcher_center.x()),
            "PACO_STARTUP_RING_CENTER_Y": str(launcher_center.y()),
        }
        with patch.dict(os.environ, environment):
            sequence = StartupSequence(app_name="Paco", root=root)
        root.show()
        sequence.begin()
        QTest.qWait(500)

        ring = sequence.overlay._pulse_indicator
        self.assertEqual(launcher_center, ring.pos() + QPoint(ring.width() // 2, ring.height() // 2))
        self.assertEqual(
            sequence.overlay.mapFromGlobal(launcher_center),
            sequence.overlay._intro_center(),
        )
        root.close()


if __name__ == "__main__":
    unittest.main()
