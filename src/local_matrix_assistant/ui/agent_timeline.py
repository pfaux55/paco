from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QDateTime, QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid


class AgentEventCard(QFrame):
    max_preview_characters = 3_200
    open_file_requested = Signal(str)
    open_folder_requested = Signal(str)
    reuse_command_requested = Signal(str, str)
    show_task_details_requested = Signal(str)

    def __init__(
        self,
        role: str,
        text: str,
        timestamp: str = "",
        artifact_path: str = "",
        artifact_kind: str = "",
        workspace_path: str = "",
        task_id: str = "",
    ) -> None:
        super().__init__()
        self.role = role.strip() or "Agent"
        self.full_text = text
        self.timestamp = timestamp.strip() or QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self.artifact_path = artifact_path.strip()
        self.artifact_kind = artifact_kind if artifact_kind in {"file", "folder"} else ""
        self.workspace_path = workspace_path.strip()
        self.task_id = task_id.strip()
        self.event_kind = self._event_kind(self.role)
        self.setObjectName("agentEventCard")
        self.setProperty("eventKind", self.event_kind)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 12)
        layout.setSpacing(7)
        header = QHBoxLayout()
        header.setSpacing(7)
        dot = QLabel("●")
        dot.setObjectName("agentEventDot")
        dot.setProperty("eventKind", self.event_kind)
        header.addWidget(dot)
        role_label = QLabel(self.role.upper())
        role_label.setObjectName("agentEventRole")
        header.addWidget(role_label)
        header.addStretch(1)
        self.reuse_command_button = QPushButton("Use Again")
        self.reuse_command_button.setObjectName("agentRecallButton")
        self.reuse_command_button.setAccessibleName("Restore this command to the Agent composer")
        self.reuse_command_button.setToolTip("Restore this command for review without running it")
        self.reuse_command_button.clicked.connect(
            lambda _checked=False: self.reuse_command_requested.emit(
                self.full_text,
                self.workspace_path,
            )
        )
        self.reuse_command_button.setVisible(self.event_kind == "command" and bool(text.strip()))
        header.addWidget(self.reuse_command_button)
        self.timestamp_label = QLabel(self._display_time(self.timestamp))
        self.timestamp_label.setObjectName("agentEventTime")
        self.timestamp_label.setToolTip(self.timestamp)
        header.addWidget(self.timestamp_label)
        layout.addLayout(header)

        self.body_label = QLabel(self._preview_text(text))
        self.body_label.setObjectName("agentEventBody")
        self.body_label.setTextFormat(Qt.TextFormat.PlainText)
        self.body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body_label.setWordWrap(True)
        self.body_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout.addWidget(self.body_label)

        scope_name = Path(self.workspace_path).name or self.workspace_path
        self._scope_text = f"Workspace: {scope_name}" if scope_name else ""
        self.scope_actions = QWidget()
        self.scope_actions.setObjectName("agentScopeActions")
        scope_layout = QHBoxLayout(self.scope_actions)
        scope_layout.setContentsMargins(0, 0, 0, 0)
        scope_layout.setSpacing(7)
        self.scope_label = QLabel(self._scope_text)
        self.scope_label.setObjectName("agentEventScope")
        self.scope_label.setToolTip(self.workspace_path)
        self.scope_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.scope_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.scope_label.setVisible(bool(self.workspace_path))
        scope_layout.addWidget(self.scope_label, stretch=1)
        self.task_state_label = QLabel("")
        self.task_state_label.setObjectName("agentTaskState")
        self.task_state_label.setVisible(False)
        scope_layout.addWidget(self.task_state_label)
        self.show_details_button = QPushButton("Details")
        self.show_details_button.setObjectName("agentDetailsButton")
        self.show_details_button.setToolTip("Open the saved execution output for this command")
        self.show_details_button.setAccessibleName("Show execution details for this command")
        self.show_details_button.clicked.connect(
            lambda _checked=False: self.show_task_details_requested.emit(self.task_id)
        )
        self.show_details_button.setVisible(bool(self.task_id))
        scope_layout.addWidget(self.show_details_button)
        self.scope_actions.setVisible(
            self.event_kind == "command" and bool(self.workspace_path or self.task_id)
        )
        layout.addWidget(self.scope_actions)

        self.artifact_actions = QWidget()
        self.artifact_actions.setObjectName("agentArtifactActions")
        artifact_layout = QHBoxLayout(self.artifact_actions)
        artifact_layout.setContentsMargins(0, 2, 0, 0)
        artifact_layout.setSpacing(7)
        self._artifact_name = Path(self.artifact_path).name or self.artifact_path
        self.artifact_label = QLabel(self._artifact_name)
        self.artifact_label.setObjectName("agentArtifactName")
        self.artifact_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.artifact_label.setToolTip(self.artifact_path)
        artifact_layout.addWidget(self.artifact_label, stretch=1)
        self.open_file_button = QPushButton("Open File")
        self.open_file_button.setObjectName("agentArtifactButton")
        self.open_file_button.setAccessibleName(f"Open file {self._artifact_name}")
        self.open_file_button.clicked.connect(
            lambda _checked=False: self.open_file_requested.emit(self.artifact_path)
        )
        self.open_file_button.setVisible(self.artifact_kind == "file")
        artifact_layout.addWidget(self.open_file_button)
        self.open_folder_button = QPushButton("Open Folder")
        self.open_folder_button.setObjectName("agentArtifactButton")
        self.open_folder_button.setAccessibleName(f"Open folder containing {self._artifact_name}")
        self.open_folder_button.clicked.connect(
            lambda _checked=False: self.open_folder_requested.emit(self.artifact_path)
        )
        artifact_layout.addWidget(self.open_folder_button)
        self.artifact_actions.setVisible(bool(self.artifact_path and self.artifact_kind))
        layout.addWidget(self.artifact_actions)
        self._content_layout = layout
        self.setAccessibleName(f"{self.role} task event")
        self.setAccessibleDescription(text[:500])

    def set_task_state(self, status: str, duration_seconds: float) -> None:
        labels = {
            "running": "RUNNING",
            "waiting_review": "REVIEW",
            "waiting_approval": "APPROVAL",
            "success": "SUCCESS",
            "error": "ERROR",
            "canceled": "CANCELED",
            "blocked": "BLOCKED",
            "discarded": "DISCARDED",
            "interrupted": "INTERRUPTED",
            "completed": "COMPLETED",
        }
        normalized = status if status in labels else "completed"
        duration = self._display_duration(duration_seconds)
        self.task_state_label.setText(f"{labels[normalized]} · {duration}")
        self.task_state_label.setProperty("taskState", normalized)
        self.task_state_label.setVisible(bool(self.task_id))
        self.task_state_label.style().unpolish(self.task_state_label)
        self.task_state_label.style().polish(self.task_state_label)

    @staticmethod
    def _display_duration(duration_seconds: float) -> str:
        seconds = max(0.0, float(duration_seconds))
        if seconds < 1:
            return "<1s"
        if seconds < 60:
            return f"{int(seconds)}s"
        minutes, remainder = divmod(int(seconds), 60)
        return f"{minutes}m {remainder}s"

    def fit_to_width(self, width: int) -> None:
        if not isValid(self) or not isValid(self._content_layout):
            return
        self.timestamp_label.setVisible(width >= 350)
        margins = self._content_layout.contentsMargins()
        body_width = max(120, width - margins.left() - margins.right())
        body_height = max(self.body_label.fontMetrics().height(), self.body_label.heightForWidth(body_width))
        self.body_label.setFixedHeight(body_height)
        header_height = max(18, self._content_layout.itemAt(0).sizeHint().height())
        total = margins.top() + header_height + self._content_layout.spacing() + body_height + margins.bottom()
        if self.scope_actions.isVisible():
            details_width = self.show_details_button.sizeHint().width() + 9 if self.show_details_button.isVisible() else 0
            if self.task_state_label.isVisible():
                details_width += self.task_state_label.sizeHint().width() + 7
            self.scope_label.setText(
                self.scope_label.fontMetrics().elidedText(
                    self._scope_text,
                    Qt.TextElideMode.ElideMiddle,
                    max(50, body_width - details_width),
                )
            )
            total += self._content_layout.spacing() + self.scope_actions.sizeHint().height()
        if self.artifact_actions.isVisible():
            buttons_width = self.open_folder_button.sizeHint().width()
            if self.open_file_button.isVisible():
                buttons_width += self.open_file_button.sizeHint().width() + 7
            label_width = max(70, body_width - buttons_width - 14)
            self.artifact_label.setText(
                self.artifact_label.fontMetrics().elidedText(
                    self._artifact_name,
                    Qt.TextElideMode.ElideMiddle,
                    label_width,
                )
            )
            total += self._content_layout.spacing() + self.artifact_actions.sizeHint().height()
        self.setFixedHeight(total)

    @classmethod
    def _preview_text(cls, text: str) -> str:
        if len(text) <= cls.max_preview_characters:
            return text
        head = text[:2_500].rstrip()
        tail = text[-450:].lstrip()
        return f"{head}\n\n… timeline preview shortened …\n\n{tail}\n\nFull output is available in Execution Details."

    @staticmethod
    def _event_kind(role: str) -> str:
        folded = role.casefold()
        if "error" in folded:
            return "error"
        if "command" in folded or "follow-up" in folded:
            return "command"
        return "agent"

    @staticmethod
    def _display_time(timestamp: str) -> str:
        if len(timestamp) >= 16 and timestamp[10:11] in {" ", "T"}:
            return timestamp[11:16]
        return timestamp[:16]


class AgentTimeline(QScrollArea):
    max_events = 80
    open_file_requested = Signal(str)
    open_folder_requested = Signal(str)
    reuse_command_requested = Signal(str, str)
    show_task_details_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("agentTimeline")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        canvas = QWidget()
        canvas.setObjectName("agentTimelineCanvas")
        self._layout = QVBoxLayout(canvas)
        self._layout.setContentsMargins(2, 2, 6, 2)
        self._layout.setSpacing(9)
        self.empty_label = QLabel(
            "Your Agent tasks will appear here as a clear timeline of commands, results, reviews, and errors."
        )
        self.empty_label.setObjectName("agentTimelineEmpty")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self._layout.addWidget(self.empty_label, stretch=1)
        self._layout.addStretch(0)
        self.setWidget(canvas)
        self._canvas = canvas
        self.cards: list[AgentEventCard] = []
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._refresh_card_layout)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._scroll_to_bottom)
        self._disposed = False
        self._workspace_filter = ""

    def append_entry(
        self,
        role: str,
        text: str,
        timestamp: str = "",
        artifact_path: str = "",
        artifact_kind: str = "",
        workspace_path: str = "",
        task_id: str = "",
    ) -> AgentEventCard:
        card = AgentEventCard(
            role,
            text,
            timestamp,
            artifact_path,
            artifact_kind,
            workspace_path,
            task_id,
        )
        card.open_file_requested.connect(self.open_file_requested.emit)
        card.open_folder_requested.connect(self.open_folder_requested.emit)
        card.reuse_command_requested.connect(self.reuse_command_requested.emit)
        card.show_task_details_requested.connect(self.show_task_details_requested.emit)
        self.cards.append(card)
        self._layout.insertWidget(self._layout.count() - 1, card)
        while len(self.cards) > self.max_events:
            expired = self.cards.pop(0)
            self._layout.removeWidget(expired)
            expired.deleteLater()
        self._apply_workspace_filter()
        self._layout_timer.start(0)
        return card

    def clear_entries(self) -> None:
        self._layout_timer.stop()
        self._scroll_timer.stop()
        for card in self.cards:
            self._layout.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.cards.clear()
        self.empty_label.setVisible(True)
        self._layout.setStretchFactor(self.empty_label, 1)
        self._canvas.setFixedHeight(max(1, self.viewport().height()))
        self.verticalScrollBar().setValue(0)

    def set_workspace_filter(self, workspace_path: str = "") -> None:
        self._workspace_filter = self._normalize_workspace(workspace_path)
        self._apply_workspace_filter()
        self._layout_timer.start(0)

    @property
    def entry_count(self) -> int:
        return len(self.cards)

    @property
    def visible_entry_count(self) -> int:
        return sum(not card.isHidden() for card in self.cards if isValid(card))

    @property
    def workspace_filter_active(self) -> bool:
        return bool(self._workspace_filter)

    def _apply_workspace_filter(self) -> None:
        visible_count = 0
        for card in self.cards:
            if not isValid(card):
                continue
            visible = not self._workspace_filter or (
                bool(card.workspace_path)
                and self._normalize_workspace(card.workspace_path) == self._workspace_filter
            )
            card.setVisible(visible)
            visible_count += int(visible)
        self.empty_label.setText(
            "No Agent tasks are recorded for this workspace. Switch to All workspaces to review other tasks."
            if self._workspace_filter
            else "Your Agent tasks will appear here as a clear timeline of commands, results, reviews, and errors."
        )
        self.empty_label.setVisible(visible_count == 0)
        self._layout.setStretchFactor(self.empty_label, 1 if visible_count == 0 else 0)

    @staticmethod
    def _normalize_workspace(workspace_path: str) -> str:
        raw = workspace_path.strip()
        if not raw or "\x00" in raw:
            return ""
        return os.path.normcase(os.path.abspath(os.path.expanduser(raw)))

    def _scroll_to_bottom(self) -> None:
        if self._disposed or not isValid(self):
            return
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _refresh_card_layout(self) -> None:
        if (
            self._disposed
            or not isValid(self)
            or not isValid(self._canvas)
            or not isValid(self._layout)
        ):
            return
        viewport_width = max(160, self.viewport().width())
        card_width = max(140, viewport_width - 8)
        visible_cards = [card for card in self.cards if isValid(card) and not card.isHidden()]
        for card in visible_cards:
            if isValid(card):
                card.fit_to_width(card_width)
        margins = self._layout.contentsMargins()
        visible_heights = sum(card.height() for card in visible_cards)
        spacing = self._layout.spacing() * max(0, len(visible_cards) - 1)
        required_height = margins.top() + margins.bottom() + visible_heights + spacing
        self._canvas.setFixedHeight(max(self.viewport().height(), required_height))
        self._canvas.updateGeometry()
        self._scroll_timer.start(0)

    def _mark_disposed(self) -> None:
        self._disposed = True
        self.cards.clear()
        self._layout_timer.stop()
        self._scroll_timer.stop()

    def event(self, event: QEvent) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.DeferredDelete:
            self._mark_disposed()
        return super().event(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._layout_timer.start(0)
