from __future__ import annotations

import base64
import binascii
from pathlib import Path

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from local_matrix_assistant.core.constants import MAX_COMPOSER_HEIGHT, MIN_COMPOSER_HEIGHT, SEND_GLYPH
from local_matrix_assistant.core.models import ConversationSummary
from local_matrix_assistant.services.attachments import AttachmentService, LocalAttachment
from local_matrix_assistant.ui.animated import fade_in_widget
from local_matrix_assistant.ui.brand import paco_mark
from local_matrix_assistant.ui.inputs import NoWheelComboBox
from local_matrix_assistant.ui.status_panel import StatusPanel
from local_matrix_assistant.ui.voice_only_panel import VoiceOnlyPanel
from local_matrix_assistant.services.model_router import MODEL_PROFILES, PROFILE_LABELS


class MessageInput(QPlainTextEdit):
    submit_requested = Signal()
    file_paths_dropped = Signal(list)
    clipboard_image_pasted = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.accept_clipboard_images = False

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        is_enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        no_modifiers = event.modifiers() == Qt.KeyboardModifier.NoModifier
        if is_enter and no_modifiers:
            self.submit_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def preferred_height(self) -> int:
        document_height = self.document().size().height()
        margins = self.contentsMargins().top() + self.contentsMargins().bottom() + 18
        return max(MIN_COMPOSER_HEIGHT, min(MAX_COMPOSER_HEIGHT, int(document_height + margins)))

    def insertFromMimeData(self, source) -> None:  # type: ignore[override]
        if self.accept_clipboard_images and source is not None and source.hasImage():
            image_data = source.imageData()
            image = image_data.toImage() if isinstance(image_data, QPixmap) else image_data
            if isinstance(image, QImage) and not image.isNull():
                self.clipboard_image_pasted.emit(image.copy())
                return
        super().insertFromMimeData(source)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if self._local_file_paths(event.mimeData()):
            self._set_drag_active(True)
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._local_file_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths = self._local_file_paths(event.mimeData())
        self._set_drag_active(False)
        if paths:
            self.file_paths_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    @staticmethod
    def _local_file_paths(mime_data) -> list[str]:
        if mime_data is None or not mime_data.hasUrls():
            return []
        return [url.toLocalFile() for url in mime_data.urls() if url.isLocalFile() and url.toLocalFile()]

    def _set_drag_active(self, active: bool) -> None:
        self.setProperty("dragActive", active)
        self.style().unpolish(self)
        self.style().polish(self)


class FileDropTargetMixin:
    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if MessageInput._local_file_paths(event.mimeData()):
            self._set_drag_active(True)
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if MessageInput._local_file_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self._set_drag_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths = MessageInput._local_file_paths(event.mimeData())
        self._set_drag_active(False)
        if paths:
            self.file_paths_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _set_drag_active(self, active: bool) -> None:
        target = getattr(self, "composer_panel", None) or getattr(
            self,
            "command_panel",
            self,
        )
        target.setProperty("dragActive", active)
        target.style().unpolish(target)
        target.style().polish(target)


class FileDropFrame(FileDropTargetMixin, QFrame):
    file_paths_dropped = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)


class ChatPanel(FileDropTargetMixin, QWidget):
    file_paths_dropped = Signal(list)
    attachment_remove_requested = Signal(str)
    edit_cancel_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        icon_path = Path(__file__).resolve().parents[1] / "assets" / "mic_icon.svg"
        wave_icon_path = Path(__file__).resolve().parents[1] / "assets" / "voice_wave_icon.svg"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.new_chat_button = QPushButton("New Chat")
        self.delete_chat_button = QPushButton("Delete Chat")
        self.history_list = QListWidget()
        self.history_list.setObjectName("historyList")
        self.history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.history_list.setTextElideMode(Qt.TextElideMode.ElideRight)

        main_panel = QFrame()
        main_panel.setObjectName("chatCanvas")
        panel_layout = QVBoxLayout(main_panel)
        panel_layout.setContentsMargins(22, 22, 22, 22)
        panel_layout.setSpacing(18)

        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("chatContentStack")
        self.content_stack.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.content_stack.currentChanged.connect(self._animate_current_screen)
        panel_layout.addWidget(self.content_stack, stretch=1)

        self.chat_screen = QWidget()
        self.chat_screen.setObjectName("chatScreen")
        self.chat_screen.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        chat_screen_layout = QVBoxLayout(self.chat_screen)
        chat_screen_layout.setContentsMargins(0, 0, 0, 0)
        chat_screen_layout.setSpacing(18)
        chat_header = QHBoxLayout()
        chat_header.setContentsMargins(0, 0, 0, 0)
        chat_header.setSpacing(8)
        self.paco_mark = paco_mark(20, accessible_name="Paco chat")
        chat_header.addWidget(self.paco_mark)
        chat_header.addWidget(self._header_label("Conversation Log"))
        chat_header.addStretch(1)
        chat_screen_layout.addLayout(chat_header)

        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName("conversationScroll")
        self.chat_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.chat_scroll.viewport().setObjectName("conversationViewport")
        self.chat_scroll.viewport().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.chat_container = QWidget()
        self.chat_container.setObjectName("conversationSurface")
        self.chat_container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(0, 0, 0, 0)
        self.chat_layout.setSpacing(10)
        self.load_earlier_button = QPushButton("Load earlier messages")
        self.load_earlier_button.setObjectName("loadEarlierMessages")
        self.load_earlier_button.setMaximumWidth(360)
        self.load_earlier_button.setAccessibleName("Load earlier chat messages")
        self.load_earlier_button.setVisible(False)
        self.chat_layout.addWidget(
            self.load_earlier_button,
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )
        self.chat_layout.addItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))
        self.chat_scroll.setWidget(self.chat_container)
        chat_screen_layout.addWidget(self.chat_scroll, stretch=1)

        self.jump_to_latest_button = QPushButton("Jump to latest")
        self.jump_to_latest_button.setObjectName("jumpToLatestButton")
        self.jump_to_latest_button.setToolTip("Return to the newest message")
        self.jump_to_latest_button.setAccessibleName("Jump to latest chat message")
        self.jump_to_latest_button.setVisible(False)
        self.jump_to_latest_button.clicked.connect(self.scroll_to_latest)
        chat_screen_layout.addWidget(
            self.jump_to_latest_button,
            alignment=Qt.AlignmentFlag.AlignRight,
        )
        chat_bar = self.chat_scroll.verticalScrollBar()
        chat_bar.valueChanged.connect(self._sync_jump_to_latest_button)
        chat_bar.rangeChanged.connect(
            lambda _minimum, _maximum: self._sync_jump_to_latest_button()
        )

        self.composer_panel = FileDropFrame()
        self.composer_panel.setObjectName("composerDock")
        composer_layout = QVBoxLayout(self.composer_panel)
        composer_layout.setContentsMargins(18, 18, 18, 14)
        composer_layout.setSpacing(12)

        self.composer_controls_layout = QGridLayout()
        self.composer_controls_layout.setHorizontalSpacing(10)
        self.composer_controls_layout.setVerticalSpacing(8)
        self.cancel_button = QPushButton("Cancel Reply")
        self.stop_audio_button = QPushButton("Stop Voice")
        self.stop_audio_button.setToolTip("Stop spoken output (Ctrl+Shift+X)")
        self.stop_audio_button.setAccessibleName("Stop spoken output")
        self.web_search_button = QPushButton("Web Search Off")
        self.web_search_button.setObjectName("togglePillOff")
        self.web_search_button.setCheckable(True)
        self.model_profile_combo = NoWheelComboBox()
        self.model_profile_combo.setObjectName("modelProfileCombo")
        self.model_profile_combo.setToolTip("Choose automatic or task-specific local model routing")
        self.model_profile_combo.setMinimumWidth(132)
        for profile in MODEL_PROFILES:
            self.model_profile_combo.addItem(PROFILE_LABELS[profile], profile)
        self.voice_only_button = QPushButton("Voice Only")
        self.voice_only_button.setObjectName("voiceOnlyButton")
        self.voice_only_button.setIcon(QIcon(str(wave_icon_path)))
        self.voice_only_button.setIconSize(QSize(18, 18))
        self.voice_only_button.setToolTip("Open the keyboard-accessible voice-only view")
        self.voice_only_button.setAccessibleName("Open voice-only view")
        self.cancel_button.setEnabled(False)
        self._composer_control_widgets = (
            self.cancel_button,
            self.stop_audio_button,
            self.web_search_button,
            self.model_profile_combo,
            self.voice_only_button,
        )
        self._compact_mode = False
        self._layout_composer_controls()
        composer_layout.addLayout(self.composer_controls_layout)

        self.composer_hint = QLabel("Model: none selected")
        self.composer_hint.setObjectName("statusLabel")
        composer_layout.addWidget(self.composer_hint)

        self.audio_state_label = QLabel("Audio: Idle")
        self.audio_state_label.setObjectName("statusLabel")
        self.audio_state_label.setAccessibleName("Voice status: Idle")
        composer_layout.addWidget(self.audio_state_label)

        self.context_note = QLabel("")
        self.context_note.setObjectName("statusLabel")
        self.context_note.setWordWrap(True)
        self.context_note.setVisible(False)
        composer_layout.addWidget(self.context_note)

        self.edit_message_banner = QFrame()
        self.edit_message_banner.setObjectName("editMessageBanner")
        edit_banner_layout = QHBoxLayout(self.edit_message_banner)
        edit_banner_layout.setContentsMargins(12, 9, 9, 9)
        edit_banner_layout.setSpacing(10)
        self.edit_message_label = QLabel("")
        self.edit_message_label.setObjectName("editMessageLabel")
        self.edit_message_label.setWordWrap(True)
        edit_banner_layout.addWidget(self.edit_message_label, stretch=1)
        self.cancel_message_edit_button = QPushButton("Cancel edit")
        self.cancel_message_edit_button.setObjectName("messageEditCancelButton")
        self.cancel_message_edit_button.clicked.connect(self.edit_cancel_requested.emit)
        edit_banner_layout.addWidget(self.cancel_message_edit_button)
        self.edit_message_banner.setVisible(False)
        composer_layout.addWidget(self.edit_message_banner)

        self.attachment_tray = QFrame()
        self.attachment_tray.setObjectName("attachmentTray")
        attachment_tray_layout = QVBoxLayout(self.attachment_tray)
        attachment_tray_layout.setContentsMargins(12, 10, 12, 10)
        attachment_tray_layout.setSpacing(6)
        attachment_heading = QLabel("ATTACHED FILES · LOCAL SNAPSHOTS")
        attachment_heading.setObjectName("attachmentHeading")
        attachment_tray_layout.addWidget(attachment_heading)
        self.attachment_items_host = QWidget()
        self.attachment_items_host.setObjectName("attachmentItemsHost")
        self.attachment_items_layout = QVBoxLayout(self.attachment_items_host)
        self.attachment_items_layout.setContentsMargins(0, 0, 0, 0)
        self.attachment_items_layout.setSpacing(5)
        self.attachment_scroll = QScrollArea()
        self.attachment_scroll.setObjectName("attachmentScroll")
        self.attachment_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.attachment_scroll.setWidgetResizable(True)
        self.attachment_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.attachment_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.attachment_scroll.setWidget(self.attachment_items_host)
        attachment_tray_layout.addWidget(self.attachment_scroll)
        self._attachment_rows: list[tuple[QFrame, QLabel, QLabel, QPushButton]] = []
        for index in range(AttachmentService.max_files):
            row = QFrame()
            row.setObjectName("attachmentChip")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 5, 6, 5)
            row_layout.setSpacing(8)
            preview = QLabel("")
            preview.setObjectName("attachmentThumbnail")
            preview.setFixedSize(34, 34)
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview.setVisible(False)
            row_layout.addWidget(preview)
            label = QLabel("")
            label.setObjectName("attachmentName")
            row_layout.addWidget(label, stretch=1)
            remove_button = QPushButton("×")
            remove_button.setObjectName("attachmentRemoveButton")
            remove_button.setFixedSize(26, 26)
            remove_button.clicked.connect(
                lambda _checked=False, item_index=index: self._request_attachment_remove(item_index)
            )
            row_layout.addWidget(remove_button)
            row.setVisible(False)
            self.attachment_items_layout.addWidget(row)
            self._attachment_rows.append((row, preview, label, remove_button))
        self.attachment_tray.setVisible(False)
        composer_layout.addWidget(self.attachment_tray)

        input_row = QHBoxLayout()
        input_row.setSpacing(12)

        self.attach_button = QPushButton("+ File")
        self.attach_button.setObjectName("attachmentButton")
        self.attach_button.setToolTip("Attach local documents, code, PDFs, or images (Ctrl+O)")
        self.attach_button.setAccessibleName("Attach files")
        self.attach_button.setFixedWidth(72)
        input_row.addWidget(self.attach_button, alignment=Qt.AlignmentFlag.AlignBottom)

        self.input_box = MessageInput()
        self.input_box.accept_clipboard_images = True
        self.input_box.setObjectName("chatInput")
        self.input_box.setPlaceholderText(
            "Type here or paste an image. Enter sends. Shift+Enter adds a new line."
        )
        self.input_box.setMinimumHeight(MIN_COMPOSER_HEIGHT)
        self.input_box.setMaximumHeight(130)
        self.input_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.input_box.setTabChangesFocus(False)
        input_row.addWidget(self.input_box, stretch=1)

        action_stack = QVBoxLayout()
        action_stack.setSpacing(8)
        action_stack.addStretch(1)

        self.send_button = QPushButton(SEND_GLYPH)
        self.send_button.setObjectName("sendCircleButton")
        self.send_button.setFixedSize(48, 48)
        self.send_button.setToolTip("Send message")
        self.send_button.setEnabled(False)
        action_stack.addWidget(self.send_button, alignment=Qt.AlignmentFlag.AlignRight)

        self.voice_button = QPushButton("")
        self.voice_button.setObjectName("micCircleButton")
        self.voice_button.setFixedSize(48, 48)
        self.voice_button.setToolTip("Start voice capture (Ctrl+Shift+Space)")
        self.voice_button.setAccessibleName("Start voice capture")
        self.voice_button.setAccessibleDescription(
            "Current voice state: Idle. Shortcut Ctrl+Shift+Space."
        )
        self.voice_button.setIcon(QIcon(str(icon_path)))
        self.voice_button.setIconSize(QSize(22, 22))
        action_stack.addWidget(self.voice_button, alignment=Qt.AlignmentFlag.AlignRight)

        input_row.addLayout(action_stack)
        composer_layout.addLayout(input_row)

        self.status_panel = StatusPanel()
        composer_layout.addWidget(self.status_panel)
        chat_screen_layout.addWidget(self.composer_panel)

        self.voice_only_panel = VoiceOnlyPanel()
        self.content_stack.addWidget(self.chat_screen)
        self.content_stack.addWidget(self.voice_only_panel)
        self.content_stack.setCurrentWidget(self.chat_screen)
        layout.addWidget(main_panel, stretch=1)

        self.input_box.textChanged.connect(self._sync_input_state)
        self._pending_attachments: list[LocalAttachment] = []
        self._sync_input_state()

    def set_compact_mode(self, compact: bool) -> None:
        if self._compact_mode == compact:
            return
        self._compact_mode = compact
        self._layout_composer_controls()

    def is_near_latest(self, threshold: int = 80) -> bool:
        bar = self.chat_scroll.verticalScrollBar()
        return bar.maximum() - bar.value() <= max(0, threshold)

    def scroll_to_latest(self) -> None:
        bar = self.chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        self._sync_jump_to_latest_button()
        QTimer.singleShot(0, self._finish_scroll_to_latest)

    def _finish_scroll_to_latest(self) -> None:
        bar = self.chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        self._sync_jump_to_latest_button()

    def _sync_jump_to_latest_button(self, _value: int = -1) -> None:
        bar = self.chat_scroll.verticalScrollBar()
        self.jump_to_latest_button.setVisible(
            bar.maximum() > 0 and not self.is_near_latest()
        )

    def _layout_composer_controls(self) -> None:
        for widget in self._composer_control_widgets:
            self.composer_controls_layout.removeWidget(widget)
        for column in range(6):
            self.composer_controls_layout.setColumnStretch(column, 0)

        if self._compact_mode:
            self.model_profile_combo.setMinimumWidth(120)
            self.composer_controls_layout.addWidget(self.web_search_button, 0, 0)
            self.composer_controls_layout.addWidget(self.model_profile_combo, 0, 1)
            self.composer_controls_layout.addWidget(self.voice_only_button, 0, 2)
            self.composer_controls_layout.addWidget(self.cancel_button, 1, 0)
            self.composer_controls_layout.addWidget(self.stop_audio_button, 1, 1)
            self.composer_controls_layout.setColumnStretch(3, 1)
            return

        self.model_profile_combo.setMinimumWidth(132)
        for column, widget in enumerate(self._composer_control_widgets):
            self.composer_controls_layout.addWidget(widget, 0, column)
        self.composer_controls_layout.setColumnStretch(5, 1)

    @staticmethod
    def _header_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("messageRole")
        return label

    def _sync_input_state(self) -> None:
        has_content = bool(self.input_box.toPlainText().strip()) or bool(self._pending_attachments)
        self.send_button.setEnabled(has_content)
        self.input_box.setFixedHeight(self.input_box.preferred_height())

    def set_pending_attachments(self, attachments: list[LocalAttachment]) -> None:
        self._pending_attachments = list(attachments)
        for index, (row, preview, label, remove_button) in enumerate(self._attachment_rows):
            if index >= len(attachments):
                row.setVisible(False)
                preview.clear()
                preview.setVisible(False)
                label.clear()
                label.setToolTip("")
                continue
            attachment = attachments[index]
            thumbnail = self._decode_thumbnail(attachment.thumbnail_data or attachment.image_data)
            preview.setPixmap(thumbnail)
            preview.setVisible(not thumbnail.isNull())
            state = " · truncated" if attachment.truncated else ""
            kind = "Image" if attachment.image_data else attachment.kind.title()
            label.setText(
                f"{attachment.name} · {kind} · {AttachmentService.format_size(attachment.size_bytes)}{state}"
            )
            label.setToolTip(attachment.path if Path(attachment.path).is_absolute() else "Stored local snapshot")
            remove_button.setToolTip(f"Remove {attachment.name}")
            remove_button.setAccessibleName(f"Remove attached file {attachment.name}")
            row.setVisible(True)
        self.attachment_tray.setVisible(bool(attachments))
        row_height = 47 if any(attachment.image_data for attachment in attachments) else 37
        self.attachment_scroll.setFixedHeight(min(106, max(36, len(attachments) * row_height)))
        self._sync_input_state()

    def attachment_count(self) -> int:
        return len(self._pending_attachments)

    def set_attachment_controls_enabled(self, enabled: bool) -> None:
        self.attach_button.setEnabled(enabled)
        for _row, _preview, _label, remove_button in self._attachment_rows:
            remove_button.setEnabled(enabled)

    def set_message_edit_state(self, active: bool, *, later_messages: int = 0) -> None:
        if active:
            suffix = (
                f" and remove {later_messages} later message{'s' if later_messages != 1 else ''}"
                if later_messages
                else ""
            )
            self.edit_message_label.setText(f"Editing message · Send will replace it{suffix}.")
        else:
            self.edit_message_label.clear()
        self.edit_message_banner.setVisible(active)

    def _request_attachment_remove(self, index: int) -> None:
        if 0 <= index < len(self._pending_attachments):
            self.attachment_remove_requested.emit(self._pending_attachments[index].path)

    @staticmethod
    def _decode_thumbnail(image_data: str) -> QPixmap:
        if not image_data:
            return QPixmap()
        try:
            payload = base64.b64decode(image_data, validate=True)
        except (ValueError, binascii.Error):
            return QPixmap()
        pixmap = QPixmap()
        if not pixmap.loadFromData(payload):
            return QPixmap()
        return pixmap.scaled(
            34,
            34,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def set_context_note(self, text: str) -> None:
        self.context_note.setVisible(bool(text))
        self.context_note.setText(text)

    def set_audio_state(self, text: str) -> None:
        self.audio_state_label.setText(f"Audio: {text}")
        self.audio_state_label.setAccessibleName(f"Voice status: {text}")
        self.voice_only_panel.set_audio_state(text)

    def set_web_search_enabled(self, enabled: bool) -> None:
        self.web_search_button.blockSignals(True)
        self.web_search_button.setChecked(enabled)
        self.web_search_button.setText("Web Search On" if enabled else "Web Search Off")
        self.web_search_button.setObjectName("togglePillOn" if enabled else "togglePillOff")
        self.web_search_button.style().unpolish(self.web_search_button)
        self.web_search_button.style().polish(self.web_search_button)
        self.web_search_button.blockSignals(False)

    def set_model_profile(self, profile: str) -> None:
        index = self.model_profile_combo.findData(profile)
        self.model_profile_combo.blockSignals(True)
        self.model_profile_combo.setCurrentIndex(index if index >= 0 else self.model_profile_combo.findData("auto"))
        self.model_profile_combo.blockSignals(False)

    def current_model_profile(self) -> str:
        return str(self.model_profile_combo.currentData() or "auto")

    def set_conversations(
        self,
        conversations: list[ConversationSummary],
        active_conversation_id: str | None,
        draft_conversation_ids: set[str] | None = None,
    ) -> None:
        draft_ids = draft_conversation_ids or set()
        self.history_list.blockSignals(True)
        self.history_list.clear()
        for summary in conversations:
            has_draft = summary.conversation_id in draft_ids
            status = f"{summary.updated_at}  [Draft]" if has_draft else summary.updated_at
            item = QListWidgetItem(f"{summary.title}\n{status}")
            item.setData(Qt.ItemDataRole.UserRole, summary.conversation_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, summary.title)
            item.setData(Qt.ItemDataRole.UserRole + 2, summary.updated_at)
            item.setData(Qt.ItemDataRole.UserRole + 3, summary.preview)
            item.setToolTip(
                f"Unsent draft saved locally\n{summary.preview or summary.title}"
                if has_draft
                else summary.preview or summary.title
            )
            self.history_list.addItem(item)
            if summary.conversation_id == active_conversation_id:
                self.history_list.setCurrentItem(item)
        self.history_list.blockSignals(False)

    def set_conversation_draft_state(self, conversation_id: str, has_draft: bool) -> None:
        for index in range(self.history_list.count()):
            item = self.history_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) != conversation_id:
                continue
            title = str(item.data(Qt.ItemDataRole.UserRole + 1) or "New Chat")
            updated_at = str(item.data(Qt.ItemDataRole.UserRole + 2) or "")
            preview = str(item.data(Qt.ItemDataRole.UserRole + 3) or "")
            status = f"{updated_at}  [Draft]" if has_draft else updated_at
            item.setText(f"{title}\n{status}")
            item.setToolTip(
                f"Unsent draft saved locally\n{preview or title}"
                if has_draft
                else preview or title
            )
            return

    def show_voice_only_mode(self, enabled: bool) -> None:
        self.content_stack.setCurrentWidget(self.voice_only_panel if enabled else self.chat_screen)

    def voice_only_mode_active(self) -> bool:
        return self.content_stack.currentWidget() is self.voice_only_panel

    def _animate_current_screen(self, index: int) -> None:
        widget = self.content_stack.widget(index)
        if widget is not None:
            fade_in_widget(widget, duration=260, start=0.1)
