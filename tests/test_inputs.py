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

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit

from local_matrix_assistant.ui.chat_panel import MessageInput
from local_matrix_assistant.ui.inputs import ClipboardShortcutFilter


class RecordingClipboardShortcutFilter(ClipboardShortcutFilter):
    def __init__(self) -> None:
        super().__init__()
        self.event_types: list[QEvent.Type] = []

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            self.event_types.append(event.type())
        return super().eventFilter(watched, event)


class ClipboardShortcutFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.shortcut_filter = RecordingClipboardShortcutFilter()
        self.app.installEventFilter(self.shortcut_filter)
        QApplication.clipboard().clear()

    def tearDown(self) -> None:
        self.app.removeEventFilter(self.shortcut_filter)

    def test_ctrl_v_traverses_shortcut_override_and_key_press(self) -> None:
        editor = QLineEdit()
        editor.show()
        editor.setFocus()
        QApplication.clipboard().setText("native event route")

        QTest.keyClick(editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

        self.assertEqual("native event route", editor.text())
        self.assertIn(QEvent.Type.ShortcutOverride, self.shortcut_filter.event_types)
        self.assertIn(QEvent.Type.KeyPress, self.shortcut_filter.event_types)
        editor.close()

    def test_ctrl_v_works_for_every_supported_qt_text_editor(self) -> None:
        editors = (QLineEdit(), QPlainTextEdit(), QTextEdit())
        QApplication.clipboard().setText("all editors")

        for editor in editors:
            with self.subTest(editor=type(editor).__name__):
                editor.show()
                editor.setFocus()
                QTest.keyClick(editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
                text = editor.text() if isinstance(editor, QLineEdit) else editor.toPlainText()
                self.assertEqual("all editors", text)
                editor.close()

    def test_ctrl_v_replaces_selection_once_in_message_input(self) -> None:
        editor = MessageInput()
        editor.setPlainText("replace this")
        editor.selectAll()
        editor.show()
        editor.setFocus()
        QApplication.clipboard().setText("one paste")

        QTest.keyClick(editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

        self.assertEqual("one paste", editor.toPlainText())
        editor.close()

    def test_ctrl_v_preserves_clipboard_image_routing(self) -> None:
        editor = MessageInput()
        editor.accept_clipboard_images = True
        pasted: list[QImage] = []
        editor.clipboard_image_pasted.connect(pasted.append)
        image = QImage(20, 12, QImage.Format.Format_ARGB32)
        image.fill(QColor("#24e081"))
        QApplication.clipboard().setImage(image)
        editor.show()
        editor.setFocus()

        QTest.keyClick(editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

        self.assertEqual(1, len(pasted))
        self.assertEqual((20, 12), (pasted[0].width(), pasted[0].height()))
        self.assertEqual("", editor.toPlainText())
        editor.close()

    def test_filter_does_not_mutate_read_only_or_disabled_fields(self) -> None:
        read_only = QLineEdit("read only")
        read_only.setReadOnly(True)
        disabled = QPlainTextEdit("disabled")
        disabled.setEnabled(False)
        QApplication.clipboard().setText("must not paste")

        QTest.keyClick(read_only, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(disabled, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)

        self.assertEqual("read only", read_only.text())
        self.assertEqual("disabled", disabled.toPlainText())

    def test_ctrl_alt_v_is_not_claimed_as_plain_paste(self) -> None:
        editor = QLineEdit()
        QApplication.clipboard().setText("must not paste")

        QTest.keyClick(
            editor,
            Qt.Key.Key_V,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
        )

        self.assertNotEqual("must not paste", editor.text())


if __name__ == "__main__":
    unittest.main()
