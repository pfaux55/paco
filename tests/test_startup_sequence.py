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

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from local_matrix_assistant.ui.startup_sequence import StartupSequence


class FakeAudioPlayer:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1

    def play_wav(self, data: bytes) -> None:
        del data

    def set_output_device_name(self, output_name: str) -> None:
        del output_name


class StartupSequenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_watchdog_recovers_if_startup_animation_never_finishes(self) -> None:
        root = QWidget()
        root.resize(800, 600)
        content = QWidget(root)
        player = FakeAudioPlayer()

        with patch.object(StartupSequence, "startup_timeout_ms", 20):
            sequence = StartupSequence(
                app_name="Jarvis",
                root=root,
                content_root=content,
                startup_player=player,
            )
            sequence.begin()
            QTest.qWait(120)

        self.assertFalse(sequence.overlay.isVisible())
        self.assertTrue(sequence.overlay._finished)
        self.assertFalse(sequence.overlay.isEnabled())
        self.assertFalse(sequence._startup_timeout.isActive())
        self.assertEqual(1.0, sequence.content_opacity.opacity())
        self.assertGreaterEqual(player.stop_calls, 1)
        root.close()


if __name__ == "__main__":
    unittest.main()
