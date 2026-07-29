from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


SHORTCUTS = (
    ("Ctrl+N", "New chat"),
    ("Ctrl+K", "Search chats"),
    ("Ctrl+B", "Toggle navigation and chat history"),
    ("Ctrl+L", "Focus chat composer"),
    ("Ctrl+O", "Attach local files"),
    ("Ctrl+Shift+R", "Regenerate latest response"),
    ("Ctrl+Shift+Space", "Start, send, or interrupt voice capture"),
    ("Ctrl+Shift+M", "Mute or unmute microphone"),
    ("Ctrl+Shift+X", "Stop spoken output"),
    ("F2", "Rename selected chat"),
    ("Alt+1", "Open Chat"),
    ("Alt+2", "Open Agent"),
    ("Alt+3", "Open Voice"),
    ("Alt+4", "Open Settings"),
    ("Ctrl+,", "Open Settings"),
    ("Ctrl+/", "Show shortcuts"),
    ("Escape", "Cancel edit or close the active overlay"),
    ("Enter", "Send or run"),
    ("Shift+Enter", "Insert a new line"),
)


class ShortcutHelpDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Jarvis Keyboard Shortcuts")
        self.setModal(False)
        self.setMinimumWidth(430)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 20)
        layout.setSpacing(14)
        title = QLabel("KEYBOARD SHORTCUTS")
        title.setObjectName("messageRole")
        layout.addWidget(title)
        subtitle = QLabel("Navigate and work without leaving the keyboard.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)

        card = QFrame()
        card.setObjectName("shortcutCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)
        self.rows: list[tuple[QLabel, QLabel]] = []
        for keys, action in SHORTCUTS:
            row = QHBoxLayout()
            action_label = QLabel(action)
            key_label = QLabel(keys)
            key_label.setObjectName("shortcutKey")
            key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(action_label, stretch=1)
            row.addWidget(key_label)
            card_layout.addLayout(row)
            self.rows.append((action_label, key_label))
        layout.addWidget(card)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        layout.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignRight)
