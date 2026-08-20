from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence, QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPlainTextEdit, QSlider, QTextEdit


class ClipboardShortcutFilter(QObject):
    """Keep native paste available when a window-level shortcut claims Ctrl+V."""

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return False
        if not isinstance(watched, (QLineEdit, QPlainTextEdit, QTextEdit)):
            return False
        if watched.isReadOnly() or not watched.isEnabled():
            return False

        modifiers = event.modifiers()
        is_ctrl_v = (
            event.key() == Qt.Key.Key_V
            and bool(modifiers & Qt.KeyboardModifier.ControlModifier)
            and not bool(
                modifiers
                & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
            )
        )
        if not event.matches(QKeySequence.StandardKey.Paste) and not is_ctrl_v:
            return False

        event.accept()
        if event.type() == QEvent.Type.KeyPress:
            watched.paste()
        return True


def install_clipboard_shortcut_filter(app: QApplication) -> ClipboardShortcutFilter:
    shortcut_filter = ClipboardShortcutFilter()
    app.installEventFilter(shortcut_filter)
    return shortcut_filter


class ClipboardLineEdit(QLineEdit):
    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste()
            event.accept()
            return
        super().keyPressEvent(event)


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        event.ignore()


class NoWheelSlider(QSlider):
    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        event.ignore()
