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

from local_matrix_assistant.ui.system_notice import SystemNoticeBar


class SystemNoticeBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_notice_exposes_severity_action_accessibility_and_dismissal(self) -> None:
        notice = SystemNoticeBar()
        actions: list[str] = []
        dismissed: list[bool] = []
        notice.action_requested.connect(actions.append)
        notice.dismiss_requested.connect(lambda: dismissed.append(True))

        notice.show_notice(
            key="settings_unsaved",
            message="Settings could not be saved.",
            severity="error",
            action_id="retry_settings",
            action_label="Retry Save",
            dismissible=False,
        )

        self.assertFalse(notice.isHidden())
        self.assertEqual("systemNoticeError", notice.objectName())
        self.assertIn("Settings could not be saved", notice.accessibleName())
        self.assertTrue(notice.dismiss_button.isHidden())
        notice.action_button.click()
        self.assertEqual(["retry_settings"], actions)

        notice.show_notice(
            key="ollama_offline",
            message="Ollama is offline.",
            action_id="retry_status",
            action_label="Retry",
        )
        notice.dismiss_button.click()
        self.assertEqual([True], dismissed)
        notice.resize(796, 60)
        notice.show()
        self.app.processEvents()
        self.assertLessEqual(notice.minimumSizeHint().width(), 796)
        self.assertLessEqual(notice.dismiss_button.geometry().right(), notice.width())
        notice.clear_notice()
        self.assertTrue(notice.isHidden())
        notice.close()


if __name__ == "__main__":
    unittest.main()
