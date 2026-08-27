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

from PySide6.QtWidgets import QApplication

from local_matrix_assistant.ui.shortcut_help import SHORTCUTS, ShortcutHelpDialog


class ShortcutHelpDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_lists_navigation_history_and_composer_shortcuts(self) -> None:
        dialog = ShortcutHelpDialog()
        labels = {(keys.text(), action.text()) for action, keys in dialog.rows}

        self.assertEqual(len(SHORTCUTS), len(dialog.rows))
        self.assertIn(("Ctrl+K", "Search chats"), labels)
        self.assertIn(("Ctrl+B", "Toggle navigation and chat history"), labels)
        self.assertIn(("Ctrl+O", "Attach local files"), labels)
        self.assertIn(("Ctrl++ / Ctrl+=", "Zoom in chat"), labels)
        self.assertIn(("Ctrl+-", "Zoom out chat"), labels)
        self.assertIn(("Ctrl+Shift+Space", "Start, send, or interrupt voice capture"), labels)
        self.assertIn(("Ctrl+Shift+M", "Mute or unmute microphone"), labels)
        self.assertIn(("Ctrl+Shift+X", "Stop spoken output"), labels)
        self.assertIn(("F2", "Rename selected chat"), labels)
        self.assertIn(("Shift+Enter", "Insert a new line"), labels)
        dialog.close()


if __name__ == "__main__":
    unittest.main()
