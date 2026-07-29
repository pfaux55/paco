from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton


class SystemNoticeBar(QFrame):
    action_requested = Signal(str)
    dismiss_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.notice_key = ""
        self.action_id = ""
        self.setObjectName("systemNoticeWarning")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(10)

        self.message_label = QLabel("")
        self.message_label.setObjectName("systemNoticeMessage")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label, stretch=1)

        self.action_button = QPushButton("")
        self.action_button.setObjectName("systemNoticeAction")
        self.action_button.clicked.connect(self._emit_action)
        layout.addWidget(self.action_button)

        self.dismiss_button = QPushButton("X")
        self.dismiss_button.setObjectName("systemNoticeDismiss")
        self.dismiss_button.setToolTip("Dismiss this notice")
        self.dismiss_button.setAccessibleName("Dismiss system notice")
        self.dismiss_button.clicked.connect(self.dismiss_requested.emit)
        layout.addWidget(self.dismiss_button)
        self.setVisible(False)

    def show_notice(
        self,
        *,
        key: str,
        message: str,
        severity: str = "warning",
        action_id: str = "",
        action_label: str = "",
        dismissible: bool = True,
    ) -> None:
        self.notice_key = key
        self.action_id = action_id
        self.message_label.setText(message)
        self.action_button.setText(action_label)
        self.action_button.setVisible(bool(action_id and action_label))
        self.action_button.setAccessibleName(action_label or "System notice action")
        self.dismiss_button.setVisible(dismissible)
        object_name = {
            "error": "systemNoticeError",
            "info": "systemNoticeInfo",
        }.get(severity, "systemNoticeWarning")
        if self.objectName() != object_name:
            self.setObjectName(object_name)
            self.style().unpolish(self)
            self.style().polish(self)
        self.setAccessibleName(f"System notice: {message}")
        self.setVisible(True)

    def clear_notice(self) -> None:
        self.notice_key = ""
        self.action_id = ""
        self.message_label.clear()
        self.setVisible(False)

    def _emit_action(self) -> None:
        if self.action_id:
            self.action_requested.emit(self.action_id)
