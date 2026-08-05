from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import os
import re
import time
import uuid

from PySide6.QtCore import QSignalBlocker, QTimer, Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QApplication,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from local_matrix_assistant.core.constants import MIN_COMPOSER_HEIGHT
from local_matrix_assistant.services.agent_permissions import READ_ONLY_ACCESS, STANDARD_ACCESS
from local_matrix_assistant.services.agent_history import (
    AgentHistoryEvent,
    AgentHistoryRecord,
    AgentTaskDetail,
)
from local_matrix_assistant.ui.chat_panel import MessageInput
from local_matrix_assistant.ui.agent_timeline import AgentEventCard, AgentTimeline
from local_matrix_assistant.ui.agent_progress import AgentProgressCard
from local_matrix_assistant.ui.brand import jarvis_mark
from local_matrix_assistant.ui.diff_review import DiffReviewWidget
from local_matrix_assistant.ui.status_panel import StatusPanel


class AgentPanel(QWidget):
    max_execution_characters = 200_000
    max_task_detail_characters = 60_000
    max_task_detail_total_characters = 200_000
    task_statuses = frozenset(
        {
            "running",
            "waiting_review",
            "waiting_approval",
            "success",
            "error",
            "canceled",
            "blocked",
            "discarded",
            "interrupted",
            "completed",
        }
    )
    apply_edit_requested = Signal()
    apply_and_test_requested = Signal()
    discard_edit_requested = Signal()
    cancel_task_requested = Signal()
    follow_up_fix_requested = Signal()
    dismiss_follow_up_requested = Signal()
    history_changed = Signal()
    open_artifact_file_requested = Signal(str)
    open_artifact_folder_requested = Signal(str)
    approve_script_requested = Signal()
    reject_script_requested = Signal()
    command_recalled = Signal(str)
    permission_mode_changed = Signal(str)
    command_recall_blocked = Signal(str, str, str)
    history_clear_confirmation_changed = Signal(bool)
    history_cleared = Signal()
    save_task_output_requested = Signal(str, str)

    def __init__(self, active_folder: str, workspace_scope: str = "") -> None:
        super().__init__()
        self._busy = False
        self._task_running = False
        self._reviewable_diff = False
        self._script_approval_pending = False
        self._clear_history_confirmation_armed = False
        self._clear_history_confirmation_timer = QTimer(self)
        self._clear_history_confirmation_timer.setSingleShot(True)
        self._clear_history_confirmation_timer.setInterval(5_000)
        self._clear_history_confirmation_timer.timeout.connect(
            lambda: self.cancel_clear_history_confirmation(announce=True)
        )
        self._execution_details_all = ""
        self._task_details: list[AgentTaskDetail] = []
        self._active_task_detail_id = ""
        self._task_detail_open = False
        self._active_task_started_monotonic = 0.0
        self._task_duration_timer = QTimer(self)
        self._task_duration_timer.setInterval(1_000)
        self._task_duration_timer.timeout.connect(self._refresh_active_task_timing)
        self._copy_output_timer = QTimer(self)
        self._copy_output_timer.setSingleShot(True)
        self._copy_output_timer.setInterval(1_500)
        self._copy_output_timer.timeout.connect(self._reset_copy_output_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("agentCanvas")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(22, 22, 22, 22)
        panel_layout.setSpacing(16)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        self.jarvis_mark = jarvis_mark(20, accessible_name="Jarvis agent")
        header_layout.addWidget(self.jarvis_mark)
        header = QLabel("Agent")
        header.setObjectName("messageRole")
        header_layout.addWidget(header)
        header_layout.addStretch(1)
        panel_layout.addLayout(header_layout)

        description = QLabel(
            "Ask naturally, continue the conversation, inspect or change the selected workspace, create files, "
            "run project tasks, or open apps. Writes remain reviewable and project scripts require approval."
        )
        description.setObjectName("subtitle")
        description.setWordWrap(True)
        panel_layout.addWidget(description)

        scope_panel = QFrame()
        scope_panel.setObjectName("composerDock")
        scope_layout = QGridLayout(scope_panel)
        scope_layout.setContentsMargins(16, 12, 16, 12)
        scope_layout.setHorizontalSpacing(10)
        scope_layout.setVerticalSpacing(7)
        scope_layout.setColumnStretch(1, 1)
        scope_layout.addWidget(QLabel("ACTIVE FOLDER"), 0, 0)
        self.active_folder_label = QLabel()
        self.active_folder_label.setObjectName("statusLabel")
        self.active_folder_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        scope_layout.addWidget(self.active_folder_label, 0, 1)
        self.choose_folder_button = QPushButton("Choose Folder")
        scope_layout.addWidget(self.choose_folder_button, 0, 2)
        access_label = QLabel("ACCESS")
        access_label.setObjectName("agentScopeLabel")
        scope_layout.addWidget(access_label, 1, 0)
        self.permission_mode_combo = QComboBox()
        self.permission_mode_combo.setObjectName("agentPermissionMode")
        self.permission_mode_combo.setAccessibleName("Agent workspace access mode")
        self.permission_mode_combo.addItem("Standard access", STANDARD_ACCESS)
        self.permission_mode_combo.addItem("Read-only", READ_ONLY_ACCESS)
        self.permission_mode_combo.setMinimumContentsLength(13)
        self.permission_mode_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.permission_mode_combo.setMaximumWidth(190)
        scope_layout.addWidget(self.permission_mode_combo, 1, 1, Qt.AlignmentFlag.AlignLeft)
        access_scope = QLabel("Saved per workspace")
        access_scope.setObjectName("statusLabel")
        access_scope.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        scope_layout.addWidget(access_scope, 1, 2)
        panel_layout.addWidget(scope_panel)
        self.set_active_folder(active_folder, workspace_scope=workspace_scope or active_folder)

        log_header = QGridLayout()
        log_header.setHorizontalSpacing(9)
        log_header.setVerticalSpacing(6)
        log_header.setColumnStretch(1, 1)
        self.log_view_label = QLabel("TASKS · ALL")
        self.log_view_label.setObjectName("agentLogHeading")
        log_header.addWidget(self.log_view_label, 0, 0, 1, 2)
        self.timeline_filter_combo = QComboBox()
        self.timeline_filter_combo.setObjectName("agentTimelineFilter")
        self.timeline_filter_combo.setAccessibleName("Agent timeline workspace filter")
        self.timeline_filter_combo.addItem("All workspaces", "all")
        self.timeline_filter_combo.addItem("Current workspace", "current")
        self.timeline_filter_combo.setToolTip(
            "Filter task cards by workspace. Execution Details always contains the complete saved log."
        )
        self.timeline_filter_combo.setMaximumWidth(170)
        filter_label = QLabel("SHOW")
        filter_label.setObjectName("agentScopeLabel")
        log_header.addWidget(filter_label, 1, 0)
        log_header.addWidget(
            self.timeline_filter_combo,
            1,
            1,
            Qt.AlignmentFlag.AlignLeft,
        )
        self.log_view_button = QPushButton("Execution Details")
        self.log_view_button.setObjectName("agentLogToggle")
        self.log_view_button.setToolTip("Switch between structured task cards and raw execution output.")
        log_header.addWidget(self.log_view_button, 0, 2)
        self.clear_history_button = QPushButton("Clear All")
        self.clear_history_button.setObjectName("agentLogClear")
        self.clear_history_button.setToolTip(
            "Delete all saved Agent task cards and execution details after confirmation."
        )
        self.clear_history_button.setEnabled(False)
        log_header.addWidget(self.clear_history_button, 0, 3)
        filter_note = QLabel("Execution Details includes all workspaces")
        filter_note.setObjectName("statusLabel")
        filter_note.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        log_header.addWidget(filter_note, 1, 2, 1, 2)
        panel_layout.addLayout(log_header)

        self.progress_card = AgentProgressCard()
        self.cancel_task_button = self.progress_card.cancel_button
        self.progress_card.cancel_requested.connect(self.cancel_task_requested)
        panel_layout.addWidget(self.progress_card)

        self.log_stack = QStackedWidget()
        self.log_stack.setObjectName("agentLogStack")
        self.task_timeline = AgentTimeline()
        self.task_timeline.open_file_requested.connect(self.open_artifact_file_requested.emit)
        self.task_timeline.open_folder_requested.connect(self.open_artifact_folder_requested.emit)
        self.task_timeline.reuse_command_requested.connect(self.recall_command)
        self.task_timeline.show_task_details_requested.connect(self.show_task_details)
        self.log_stack.addWidget(self.task_timeline)
        self.execution_details_page = QWidget()
        self.execution_details_page.setObjectName("agentExecutionDetailsPage")
        execution_layout = QVBoxLayout(self.execution_details_page)
        execution_layout.setContentsMargins(0, 0, 0, 0)
        execution_layout.setSpacing(7)
        detail_toolbar = QHBoxLayout()
        detail_toolbar.setContentsMargins(0, 0, 0, 0)
        detail_toolbar.addWidget(QLabel("TASK OUTPUT"))
        self.task_detail_combo = QComboBox()
        self.task_detail_combo.setObjectName("agentTaskDetailSelector")
        self.task_detail_combo.setAccessibleName("Agent task execution detail")
        self.task_detail_combo.setToolTip("Show all raw output or isolate one Agent command.")
        self.task_detail_combo.setMinimumContentsLength(12)
        self.task_detail_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.task_detail_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Fixed,
        )
        self.task_detail_combo.addItem("All tasks", "__all__")
        detail_toolbar.addWidget(self.task_detail_combo, stretch=1)
        self.copy_task_output_button = QPushButton("Copy")
        self.copy_task_output_button.setObjectName("agentOutputAction")
        self.copy_task_output_button.setToolTip("Copy the selected execution output")
        self.copy_task_output_button.setAccessibleName("Copy selected Agent execution output")
        detail_toolbar.addWidget(self.copy_task_output_button)
        self.save_task_output_button = QPushButton("Save Output")
        self.save_task_output_button.setObjectName("agentOutputAction")
        self.save_task_output_button.setToolTip(
            "Save the selected output inside an allowed Agent folder"
        )
        self.save_task_output_button.setAccessibleName("Save selected Agent execution output")
        detail_toolbar.addWidget(self.save_task_output_button)
        execution_layout.addLayout(detail_toolbar)
        self.action_log = QPlainTextEdit()
        self.action_log.setObjectName("agentActionLog")
        self.action_log.setReadOnly(True)
        self.action_log.document().setMaximumBlockCount(500)
        self.action_log.setPlaceholderText("Raw commands, model output, and project-task details appear here.")
        execution_layout.addWidget(self.action_log, stretch=1)
        self.log_stack.addWidget(self.execution_details_page)
        panel_layout.addWidget(self.log_stack, stretch=1)

        self.edit_preview_panel = QFrame()
        self.edit_preview_panel.setObjectName("editPreviewPanel")
        preview_layout = QVBoxLayout(self.edit_preview_panel)
        preview_layout.setContentsMargins(16, 14, 16, 14)
        preview_layout.setSpacing(10)
        preview_header = QHBoxLayout()
        self.preview_title = QLabel("PROPOSED EDIT")
        self.preview_title.setObjectName("messageRole")
        preview_header.addWidget(self.preview_title)
        self.edit_target_label = QLabel("")
        self.edit_target_label.setObjectName("statusLabel")
        preview_header.addWidget(self.edit_target_label, stretch=1)
        preview_layout.addLayout(preview_header)
        self.diff_review = DiffReviewWidget()
        self.edit_diff_view = self.diff_review.diff_view
        preview_layout.addWidget(self.diff_review)
        preview_actions = QHBoxLayout()
        preview_actions.addStretch(1)
        self.discard_edit_button = QPushButton("Discard")
        self.apply_and_test_button = QPushButton("Apply & Test")
        self.apply_and_test_button.setObjectName("applyAndTestButton")
        self.apply_and_test_button.setVisible(False)
        self.apply_edit_button = QPushButton("Apply Edit")
        self.apply_edit_button.setObjectName("primaryButton")
        preview_actions.addWidget(self.discard_edit_button)
        preview_actions.addWidget(self.apply_and_test_button)
        preview_actions.addWidget(self.apply_edit_button)
        preview_layout.addLayout(preview_actions)
        self.edit_preview_panel.setVisible(False)
        panel_layout.addWidget(self.edit_preview_panel)

        self.follow_up_fix_panel = QFrame()
        self.follow_up_fix_panel.setObjectName("followUpPanel")
        follow_up_layout = QHBoxLayout(self.follow_up_fix_panel)
        follow_up_layout.setContentsMargins(16, 12, 16, 12)
        follow_up_copy = QVBoxLayout()
        follow_up_copy.setSpacing(3)
        follow_up_title = QLabel("TESTS STILL FAIL")
        follow_up_title.setObjectName("followUpTitle")
        follow_up_copy.addWidget(follow_up_title)
        self.follow_up_fix_detail = QLabel(
            "Use the captured failure to draft another reviewed fix. No file changes until approval."
        )
        self.follow_up_fix_detail.setObjectName("statusLabel")
        self.follow_up_fix_detail.setWordWrap(True)
        follow_up_copy.addWidget(self.follow_up_fix_detail)
        follow_up_layout.addLayout(follow_up_copy, stretch=1)
        self.dismiss_follow_up_button = QPushButton("Dismiss")
        self.draft_follow_up_button = QPushButton("Draft Follow-up Fix")
        self.draft_follow_up_button.setObjectName("primaryButton")
        follow_up_layout.addWidget(self.dismiss_follow_up_button)
        follow_up_layout.addWidget(self.draft_follow_up_button)
        self.follow_up_fix_panel.setVisible(False)
        panel_layout.addWidget(self.follow_up_fix_panel)

        self.script_approval_panel = QFrame()
        self.script_approval_panel.setObjectName("scriptApprovalPanel")
        approval_layout = QVBoxLayout(self.script_approval_panel)
        approval_layout.setContentsMargins(16, 14, 16, 14)
        approval_layout.setSpacing(9)
        approval_header = QHBoxLayout()
        approval_title = QLabel("PROJECT SCRIPT APPROVAL")
        approval_title.setObjectName("messageRole")
        approval_header.addWidget(approval_title)
        approval_header.addStretch(1)
        self.script_risk_label = QLabel("REVIEW REQUIRED")
        self.script_risk_label.setObjectName("scriptRiskLabel")
        approval_header.addWidget(self.script_risk_label)
        approval_layout.addLayout(approval_header)

        self.script_name_label = QLabel("")
        self.script_name_label.setObjectName("scriptApprovalName")
        self.script_name_label.setTextInteractionFlags(
            self.script_name_label.textInteractionFlags()
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        approval_layout.addWidget(self.script_name_label)
        self.script_folder_label = QLabel("")
        self.script_folder_label.setObjectName("statusLabel")
        self.script_folder_label.setWordWrap(True)
        self.script_folder_label.setTextInteractionFlags(
            self.script_folder_label.textInteractionFlags()
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        approval_layout.addWidget(self.script_folder_label)

        command_heading = QLabel("CONFIGURED PACKAGE.JSON COMMAND")
        command_heading.setObjectName("agentLogHeading")
        approval_layout.addWidget(command_heading)
        self.script_command_view = QPlainTextEdit()
        self.script_command_view.setObjectName("scriptApprovalCommand")
        self.script_command_view.setReadOnly(True)
        self.script_command_view.setMinimumHeight(68)
        self.script_command_view.setMaximumHeight(110)
        self.script_command_view.setAccessibleName("Configured project script command")
        approval_layout.addWidget(self.script_command_view)

        self.script_warning_label = QLabel("")
        self.script_warning_label.setObjectName("scriptApprovalWarning")
        self.script_warning_label.setWordWrap(True)
        approval_layout.addWidget(self.script_warning_label)
        approval_actions = QHBoxLayout()
        approval_actions.addStretch(1)
        self.reject_script_button = QPushButton("Reject")
        self.run_script_button = QPushButton("Run Script")
        self.run_script_button.setObjectName("scriptRunButton")
        approval_actions.addWidget(self.reject_script_button)
        approval_actions.addWidget(self.run_script_button)
        approval_layout.addLayout(approval_actions)
        self.script_approval_panel.setVisible(False)
        panel_layout.addWidget(self.script_approval_panel)

        command_panel = QFrame()
        command_panel.setObjectName("composerDock")
        command_layout = QVBoxLayout(command_panel)
        command_layout.setContentsMargins(16, 16, 16, 14)
        command_layout.setSpacing(10)

        input_row = QHBoxLayout()
        self.command_input = MessageInput()
        self.command_input.setPlaceholderText("Tell Agent what you need. Enter sends; Shift+Enter adds a new line.")
        self.command_input.setMinimumHeight(MIN_COMPOSER_HEIGHT)
        self.command_input.setMaximumHeight(130)
        self.command_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        input_row.addWidget(self.command_input, stretch=1)
        self.run_button = QPushButton("Send")
        self.run_button.setEnabled(False)
        input_row.addWidget(self.run_button)
        command_layout.addLayout(input_row)

        self.status_panel = StatusPanel()
        command_layout.addWidget(self.status_panel)
        panel_layout.addWidget(command_panel)
        self.panel_scroll = QScrollArea()
        self.panel_scroll.setObjectName("agentPanelScroll")
        self.panel_scroll.setWidgetResizable(True)
        self.panel_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.panel_scroll.setWidget(panel)
        layout.addWidget(self.panel_scroll)

        self.command_input.textChanged.connect(self._sync_run_state)
        self.apply_edit_button.clicked.connect(self.apply_edit_requested)
        self.apply_and_test_button.clicked.connect(self.apply_and_test_requested)
        self.discard_edit_button.clicked.connect(self.discard_edit_requested)
        self.draft_follow_up_button.clicked.connect(self.follow_up_fix_requested)
        self.dismiss_follow_up_button.clicked.connect(self.dismiss_follow_up_requested)
        self.run_script_button.clicked.connect(self.approve_script_requested)
        self.reject_script_button.clicked.connect(self.reject_script_requested)
        self.log_view_button.clicked.connect(self.toggle_log_view)
        self.clear_history_button.clicked.connect(self.request_clear_history)
        self.permission_mode_combo.currentIndexChanged.connect(self._emit_permission_mode)
        self.timeline_filter_combo.currentIndexChanged.connect(self._on_timeline_filter_changed)
        self.task_detail_combo.currentIndexChanged.connect(self._render_execution_selection)
        self.copy_task_output_button.clicked.connect(self.copy_selected_task_output)
        self.save_task_output_button.clicked.connect(self.request_save_selected_task_output)

    def set_active_folder(self, folder: str, *, workspace_scope: str | None = None) -> None:
        visible = folder or "Documents\\Jarvis Files (default)"
        self._workspace_scope = (folder if workspace_scope is None else workspace_scope).strip()
        self.active_folder_label.setText(visible)
        self.active_folder_label.setToolTip(visible)
        if hasattr(self, "task_timeline"):
            self._apply_timeline_filter(show_timeline=False)

    def set_permission_mode(self, mode: str) -> None:
        normalized = READ_ONLY_ACCESS if mode == READ_ONLY_ACCESS else STANDARD_ACCESS
        index = self.permission_mode_combo.findData(normalized)
        with QSignalBlocker(self.permission_mode_combo):
            self.permission_mode_combo.setCurrentIndex(max(0, index))
        read_only = normalized == READ_ONLY_ACCESS
        self.permission_mode_combo.setProperty("accessMode", normalized)
        self.permission_mode_combo.setToolTip(
            "Agent may inspect this workspace, but file writes and executable project tasks are blocked."
            if read_only
            else "Explicit file creation and reviewed edits are allowed; project scripts still require approval."
        )
        self.permission_mode_combo.style().unpolish(self.permission_mode_combo)
        self.permission_mode_combo.style().polish(self.permission_mode_combo)
        if hasattr(self, "save_task_output_button"):
            self._sync_output_actions()

    def append_log(
        self,
        role: str,
        text: str,
        *,
        artifact_path: str = "",
        artifact_kind: str = "",
        workspace_path: str = "",
    ) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        scope = workspace_path.strip() or self._workspace_scope
        folded_role = role.casefold()
        if "command" in folded_role or "follow-up" in folded_role:
            self._start_task_detail(text, scope, timestamp)
        self.task_timeline.append_entry(
            role,
            text,
            timestamp,
            artifact_path,
            artifact_kind,
            scope,
            self._active_task_detail_id if self._task_detail_open else "",
        )
        self._sync_command_recall_state()
        self._sync_task_detail_card_state()
        block = f"{role.upper()}\n{text}"
        self._append_execution_text(
            ("\n\n" if self._execution_details_all else "") + block,
            task_separator=True,
        )
        self._sync_clear_state()
        self.history_changed.emit()

    def append_task_output(self, text: str) -> None:
        if not text:
            return
        self._append_execution_text(text)
        self._sync_clear_state()
        self.history_changed.emit()

    def load_history(self, record: AgentHistoryRecord) -> None:
        self._task_duration_timer.stop()
        self._active_task_started_monotonic = 0.0
        self.task_timeline.clear_entries()
        for event in record.events:
            self.task_timeline.append_entry(
                event.role,
                event.text,
                event.timestamp,
                event.artifact_path,
                event.artifact_kind,
                event.workspace_path,
                event.task_id,
            )
        self._sync_command_recall_state()
        self._execution_details_all = record.execution_details
        self._task_details = list(record.task_details)
        self._active_task_detail_id = ""
        self._task_detail_open = False
        self._refresh_task_detail_selector()
        self._sync_task_detail_card_state()
        filter_index = self.timeline_filter_combo.findData(record.timeline_filter)
        with QSignalBlocker(self.timeline_filter_combo):
            self.timeline_filter_combo.setCurrentIndex(max(0, filter_index))
        self._apply_timeline_filter(show_timeline=False)
        self.show_task_timeline()
        self._sync_clear_state()

    def history_record(self, active_folder: str = "") -> AgentHistoryRecord:
        self._update_active_task_duration(refresh=False)
        return AgentHistoryRecord(
            events=[
                AgentHistoryEvent(
                    card.role,
                    card.full_text,
                    card.timestamp,
                    card.artifact_path,
                    card.artifact_kind,
                    card.workspace_path,
                    card.task_id,
                )
                for card in self.task_timeline.cards
            ],
            execution_details=self._execution_details_all,
            active_folder=active_folder,
            timeline_filter=str(self.timeline_filter_combo.currentData() or "all"),
            task_details=list(self._task_details),
        )

    def clear_history(self) -> None:
        if self._busy:
            return
        self.cancel_clear_history_confirmation(announce=False)
        self.task_timeline.clear_entries()
        self._execution_details_all = ""
        self._task_details.clear()
        self._active_task_detail_id = ""
        self._task_detail_open = False
        self._active_task_started_monotonic = 0.0
        self._task_duration_timer.stop()
        self._refresh_task_detail_selector()
        self.action_log.clear()
        self.show_task_timeline()
        self._sync_clear_state()
        self.history_changed.emit()
        self.history_cleared.emit()

    def request_clear_history(self) -> None:
        if self._busy or not (self.task_timeline.entry_count or self._execution_details_all):
            return
        if self._clear_history_confirmation_armed:
            self.clear_history()
            return
        self._clear_history_confirmation_armed = True
        self.clear_history_button.setText("Confirm Clear All")
        self.clear_history_button.setProperty("confirmClear", True)
        self.clear_history_button.style().unpolish(self.clear_history_button)
        self.clear_history_button.style().polish(self.clear_history_button)
        self._clear_history_confirmation_timer.start()
        self.history_clear_confirmation_changed.emit(True)

    def cancel_clear_history_confirmation(self, *, announce: bool = False) -> bool:
        if not self._clear_history_confirmation_armed:
            return False
        self._clear_history_confirmation_armed = False
        self._clear_history_confirmation_timer.stop()
        self.clear_history_button.setText("Clear All")
        self.clear_history_button.setProperty("confirmClear", False)
        self.clear_history_button.style().unpolish(self.clear_history_button)
        self.clear_history_button.style().polish(self.clear_history_button)
        self._sync_clear_state()
        if announce:
            self.history_clear_confirmation_changed.emit(False)
        return True

    def take_command(self) -> str:
        text = self.command_input.toPlainText().strip()
        if text:
            self.command_input.clear()
        return text

    def recall_command(self, command: str, workspace_path: str = "") -> None:
        text = command.strip()
        if not text or self._busy or self._script_approval_pending:
            return
        origin = workspace_path.strip()
        current = self._workspace_scope.strip()
        if origin and current and self._normalized_workspace(origin) != self._normalized_workspace(current):
            self.command_recall_blocked.emit(text, origin, current)
            return
        self.command_input.setPlainText(text)
        cursor = self.command_input.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.command_input.setTextCursor(cursor)
        self.command_input.setFocus()
        self.panel_scroll.ensureWidgetVisible(self.command_input, 12, 12)
        self.command_recalled.emit(text)

    @staticmethod
    def _normalized_workspace(path: str) -> str:
        return os.path.normcase(os.path.abspath(os.path.expanduser(path)))

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        if busy:
            self.cancel_clear_history_confirmation(announce=False)
        self.command_input.setEnabled(not busy and not self._script_approval_pending)
        review_ready = self.edit_preview_panel.isVisible() and self._reviewable_diff
        self.apply_edit_button.setEnabled(not busy and review_ready)
        self.apply_and_test_button.setEnabled(not busy and review_ready)
        self.discard_edit_button.setEnabled(not busy and self.edit_preview_panel.isVisible())
        self.cancel_task_button.setEnabled(
            self._task_running and not self.progress_card.cancel_pending
        )
        self.draft_follow_up_button.setEnabled(not busy)
        self.dismiss_follow_up_button.setEnabled(not busy)
        self.run_script_button.setEnabled(self._script_approval_pending and not busy)
        self.reject_script_button.setEnabled(self._script_approval_pending and not busy)
        self.permission_mode_combo.setEnabled(not busy)
        self._sync_output_actions()
        self._sync_command_recall_state()
        self._sync_clear_state()
        self._sync_run_state()

    def set_task_running(
        self,
        running: bool,
        label: str = "Stop Tests",
        *,
        title: str = "",
        phase: str = "",
    ) -> None:
        self._task_running = running
        if running:
            testing = label == "Stop Tests"
            self.progress_card.start(
                title or ("Running project tests" if testing else "Working on Agent task"),
                phase or ("Starting the test process..." if testing else "Starting local reasoning..."),
                label,
            )
        else:
            self.progress_card.prepare_finish()
        if running and label == "Stop Tests":
            self.show_execution_details()

    def update_task_phase(self, message: str) -> None:
        self.progress_card.update_phase(message)

    def mark_task_cancel_requested(self) -> None:
        self.progress_card.mark_cancel_requested()

    def finish_task_progress(self, state: str, message: str) -> None:
        self._task_running = False
        self.progress_card.finish(state, message)

    def toggle_log_view(self) -> None:
        if self.log_stack.currentWidget() is self.task_timeline:
            self.show_execution_details()
        else:
            self.show_task_timeline()

    def show_execution_details(self) -> None:
        self.log_stack.setCurrentWidget(self.execution_details_page)
        self._render_execution_selection()
        self.log_view_button.setText("Task Timeline")

    def show_task_details(self, task_id: str) -> None:
        index = self.task_detail_combo.findData(task_id.strip())
        if index <= 0:
            return
        self.task_detail_combo.setCurrentIndex(index)
        self.show_execution_details()

    def show_task_timeline(self) -> None:
        self.log_stack.setCurrentWidget(self.task_timeline)
        current_only = self.timeline_filter_combo.currentData() == "current"
        self.log_view_label.setText("TASKS · CURRENT" if current_only else "TASKS · ALL")
        self.log_view_button.setText("Execution Details")

    def show_edit_preview(self, target: str, diff: str, *, operation: str = "edit") -> None:
        creating = operation in {"create", "create_and_run"}
        creating_and_running = operation == "create_and_run"
        fixing = operation == "fix"
        changing = operation == "change"
        formatting = operation == "format"
        self.preview_title.setText(
            "PROPOSED PYTHON FILE"
            if creating_and_running
            else "PROPOSED NEW FILE"
            if creating
            else "PROPOSED FIX"
            if fixing
            else "PROPOSED CHANGE"
            if changing
            else "PROPOSED FORMATTING"
            if formatting
            else "PROPOSED EDIT"
        )
        self.apply_edit_button.setText(
            "Create & Run"
            if creating_and_running
            else "Create File"
            if creating
            else "Apply Fix"
            if fixing
            else "Apply Change"
            if changing
            else "Apply Formatting"
            if formatting
            else "Apply Edit"
        )
        self.apply_and_test_button.setVisible(fixing or changing or formatting)
        self.edit_target_label.setText(target)
        self.edit_target_label.setToolTip(target)
        self._reviewable_diff = bool(diff.strip())
        self.diff_review.set_diff(target, diff)
        self.edit_preview_panel.setVisible(True)
        self.apply_edit_button.setEnabled(not self._busy and self._reviewable_diff)
        self.apply_and_test_button.setEnabled(not self._busy and self._reviewable_diff)
        self.discard_edit_button.setEnabled(not self._busy)
        QTimer.singleShot(
            0,
            lambda: self.panel_scroll.ensureWidgetVisible(
                self.edit_preview_panel,
                12,
                12,
            ),
        )

    def clear_edit_preview(self) -> None:
        self.edit_preview_panel.setVisible(False)
        self.preview_title.setText("PROPOSED EDIT")
        self.apply_edit_button.setText("Apply Edit")
        self.apply_and_test_button.setVisible(False)
        self.edit_target_label.clear()
        self.edit_target_label.setToolTip("")
        self._reviewable_diff = False
        self.diff_review.clear()

    def show_follow_up_fix(self) -> None:
        self.follow_up_fix_panel.setVisible(True)
        self.draft_follow_up_button.setEnabled(not self._busy)
        self.dismiss_follow_up_button.setEnabled(not self._busy)

    def clear_follow_up_fix(self) -> None:
        self.follow_up_fix_panel.setVisible(False)

    def show_script_approval(
        self,
        *,
        name: str,
        command: str,
        folder: str,
        warning: str,
        high_risk: bool,
    ) -> None:
        self._script_approval_pending = True
        self.script_approval_panel.setProperty("riskLevel", "high" if high_risk else "standard")
        self.script_risk_label.setText("HIGH RISK" if high_risk else "REVIEW REQUIRED")
        self.script_name_label.setText(f"npm script: {name}")
        self.script_folder_label.setText(f"Folder: {folder}")
        self.script_command_view.setPlainText(command)
        self.script_warning_label.setText(warning)
        self.script_approval_panel.setVisible(True)
        self.script_approval_panel.style().unpolish(self.script_approval_panel)
        self.script_approval_panel.style().polish(self.script_approval_panel)
        self.command_input.setEnabled(False)
        self.run_script_button.setEnabled(not self._busy)
        self.reject_script_button.setEnabled(not self._busy)
        self._sync_command_recall_state()
        QTimer.singleShot(
            0,
            lambda: self.panel_scroll.ensureWidgetVisible(
                self.script_approval_panel,
                12,
                12,
            ),
        )

    def clear_script_approval(self) -> None:
        self._script_approval_pending = False
        self.script_approval_panel.setVisible(False)
        self.script_name_label.clear()
        self.script_folder_label.clear()
        self.script_command_view.clear()
        self.script_warning_label.clear()
        self.command_input.setEnabled(not self._busy)
        self._sync_command_recall_state()
        self._sync_run_state()

    def _sync_run_state(self) -> None:
        self.run_button.setEnabled(
            not self._busy
            and not self._script_approval_pending
            and bool(self.command_input.toPlainText().strip())
        )

    def _sync_clear_state(self) -> None:
        has_history = bool(self.task_timeline.entry_count or self._execution_details_all)
        self.clear_history_button.setEnabled(not self._busy and has_history)
        self.command_input.setFixedHeight(self.command_input.preferred_height())

    def _sync_command_recall_state(self) -> None:
        enabled = not self._busy and not self._script_approval_pending
        for card in self.task_timeline.cards:
            card.reuse_command_button.setEnabled(enabled)

    def _apply_timeline_filter(self, _index: int = -1, *, show_timeline: bool = True) -> None:
        current_only = self.timeline_filter_combo.currentData() == "current"
        self.task_timeline.set_workspace_filter(self._workspace_scope if current_only else "")
        if show_timeline:
            self.show_task_timeline()
        elif self.log_stack.currentWidget() is self.task_timeline:
            self.show_task_timeline()

    def _on_timeline_filter_changed(self, index: int) -> None:
        self._apply_timeline_filter(index)
        self.history_changed.emit()

    def finish_task_detail(self, status: str = "completed") -> None:
        if not self._task_detail_open or not self._active_task_detail_id:
            return
        normalized = status if status in self.task_statuses else "completed"
        self._update_active_task_duration(refresh=False)
        for index in range(len(self._task_details) - 1, -1, -1):
            detail = self._task_details[index]
            if detail.task_id == self._active_task_detail_id:
                self._task_details[index] = replace(
                    detail,
                    status=normalized,
                    completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                )
                break
        self._task_detail_open = False
        self._active_task_detail_id = ""
        self._active_task_started_monotonic = 0.0
        self._task_duration_timer.stop()
        self._refresh_task_detail_selector()
        self._sync_task_detail_card_state()
        self.history_changed.emit()

    def set_task_detail_status(self, status: str) -> None:
        if not self._task_detail_open or not self._active_task_detail_id:
            return
        normalized = status if status in self.task_statuses else "running"
        self._update_active_task_duration(refresh=False)
        for index in range(len(self._task_details) - 1, -1, -1):
            detail = self._task_details[index]
            if detail.task_id == self._active_task_detail_id:
                self._task_details[index] = replace(detail, status=normalized)
                break
        self._refresh_task_detail_selector()
        self._sync_task_detail_card_state()
        self.history_changed.emit()

    def _start_task_detail(self, command: str, workspace_path: str, timestamp: str) -> None:
        if self._task_detail_open:
            self.finish_task_detail("interrupted")
        detail = AgentTaskDetail(
            uuid.uuid4().hex,
            command.strip()[:2_000],
            workspace_path.strip(),
            timestamp,
            "",
            "running",
        )
        self._task_details.append(detail)
        self._task_details = self._task_details[-40:]
        self._active_task_detail_id = detail.task_id
        self._task_detail_open = True
        self._active_task_started_monotonic = time.monotonic()
        self._task_duration_timer.start()
        self._refresh_task_detail_selector()
        self._sync_task_detail_card_state()

    def _append_execution_text(self, text: str, *, task_separator: bool = False) -> None:
        self._execution_details_all = (self._execution_details_all + text)[
            -self.max_execution_characters :
        ]
        if self._task_detail_open and self._active_task_detail_id:
            for index in range(len(self._task_details) - 1, -1, -1):
                detail = self._task_details[index]
                if detail.task_id != self._active_task_detail_id:
                    continue
                task_text = text.lstrip("\n") if task_separator else text
                if task_separator and detail.content:
                    task_text = "\n\n" + task_text
                content = detail.content + task_text
                if len(content) > self.max_task_detail_characters:
                    marker = "\n\n... earlier task output shortened ...\n\n"
                    head = content[:2_000].rstrip()
                    tail_limit = self.max_task_detail_characters - len(head) - len(marker)
                    content = head + marker + content[-max(0, tail_limit) :]
                self._task_details[index] = replace(detail, content=content)
                break
        self._bound_task_details()
        self._render_execution_selection()

    def _refresh_task_detail_selector(self) -> None:
        selected = str(self.task_detail_combo.currentData() or "__all__")
        with QSignalBlocker(self.task_detail_combo):
            self.task_detail_combo.clear()
            self.task_detail_combo.addItem("All tasks", "__all__")
            for detail in reversed(self._task_details):
                command = " ".join(detail.command.split())
                if len(command) > 58:
                    command = command[:55].rstrip() + "..."
                time = detail.started_at[11:16] if len(detail.started_at) >= 16 else ""
                workspace = os.path.basename(detail.workspace_path.rstrip("\\/"))
                status = detail.status.replace("waiting_", "").replace("_", " ").upper()
                duration = AgentEventCard._display_duration(detail.duration_seconds)
                prefix = " · ".join(value for value in (status, duration, time, workspace) if value)
                label = f"{prefix} · {command}" if prefix else command
                self.task_detail_combo.addItem(label, detail.task_id)
            index = self.task_detail_combo.findData(selected)
            self.task_detail_combo.setCurrentIndex(max(0, index))
        self._render_execution_selection()

    def _render_execution_selection(self, _index: int = -1) -> None:
        content, detail = self._selected_execution_output()
        selected = str(self.task_detail_combo.currentData() or "__all__")
        if selected == "__all__":
            label = "EXECUTION · ALL"
        else:
            label = "EXECUTION · TASK" if detail is not None else "EXECUTION · ALL"
        if self.action_log.toPlainText() != content:
            self.action_log.setPlainText(content)
        bar = self.action_log.verticalScrollBar()
        bar.setValue(bar.maximum())
        if self.log_stack.currentWidget() is self.execution_details_page:
            self.log_view_label.setText(label)
        self._sync_output_actions()

    def _bound_task_details(self) -> None:
        bounded_reversed: list[AgentTaskDetail] = []
        remaining = self.max_task_detail_total_characters
        for detail in reversed(self._task_details[-40:]):
            content = detail.content
            if len(content) > remaining:
                content = content[-max(0, remaining) :]
            remaining -= len(content)
            bounded_reversed.append(replace(detail, content=content))
        bounded_reversed.reverse()
        self._task_details = bounded_reversed

    def _sync_task_detail_card_state(self) -> None:
        available = {detail.task_id: detail for detail in self._task_details}
        for card in self.task_timeline.cards:
            detail = available.get(card.task_id)
            has_details = detail is not None
            card.show_details_button.setVisible(has_details)
            if detail is not None:
                card.set_task_state(detail.status, detail.duration_seconds)
            else:
                card.task_state_label.setVisible(False)
            card.scope_actions.setVisible(
                card.event_kind == "command" and bool(card.workspace_path or has_details)
            )

    def _selected_execution_output(self) -> tuple[str, AgentTaskDetail | None]:
        selected = str(self.task_detail_combo.currentData() or "__all__")
        if selected == "__all__":
            return self._execution_details_all, None
        detail = next(
            (item for item in self._task_details if item.task_id == selected),
            None,
        )
        return (detail.content, detail) if detail is not None else (self._execution_details_all, None)

    def copy_selected_task_output(self) -> None:
        content, _detail = self._selected_execution_output()
        if not content:
            return
        QApplication.clipboard().setText(content)
        self.copy_task_output_button.setText("Copied")
        self._copy_output_timer.start()

    def request_save_selected_task_output(self) -> None:
        content, detail = self._selected_execution_output()
        if (
            not content
            or self._busy
            or self.permission_mode_combo.currentData() == READ_ONLY_ACCESS
        ):
            return
        if detail is None:
            stem = "agent-output-all"
        else:
            command = "-".join(detail.command.casefold().split())
            stem = "agent-" + "".join(
                character for character in command if character.isalnum() or character in {"-", "_"}
            )
            stem = re.sub(r"[-_]+", "-", stem)
            stem = stem[:64].rstrip("-_") or "agent-task-output"
        self.save_task_output_requested.emit(f"{stem}.txt", content)

    def _sync_output_actions(self) -> None:
        if not hasattr(self, "copy_task_output_button"):
            return
        content, _detail = self._selected_execution_output()
        self.copy_task_output_button.setEnabled(bool(content))
        self.save_task_output_button.setEnabled(
            bool(content)
            and not self._busy
            and self.permission_mode_combo.currentData() != READ_ONLY_ACCESS
        )

    def _reset_copy_output_button(self) -> None:
        self.copy_task_output_button.setText("Copy")

    def _update_active_task_duration(self, *, refresh: bool) -> None:
        if (
            not self._task_detail_open
            or not self._active_task_detail_id
            or self._active_task_started_monotonic <= 0
        ):
            return
        duration = max(0.0, time.monotonic() - self._active_task_started_monotonic)
        for index in range(len(self._task_details) - 1, -1, -1):
            detail = self._task_details[index]
            if detail.task_id == self._active_task_detail_id:
                self._task_details[index] = replace(detail, duration_seconds=round(duration, 3))
                break
        if refresh:
            self._refresh_task_detail_selector()
            self._sync_task_detail_card_state()

    def _refresh_active_task_timing(self) -> None:
        self._update_active_task_duration(refresh=True)

    def _emit_permission_mode(self) -> None:
        mode = str(self.permission_mode_combo.currentData() or STANDARD_ACCESS)
        self.set_permission_mode(mode)
        self.permission_mode_changed.emit(mode)
