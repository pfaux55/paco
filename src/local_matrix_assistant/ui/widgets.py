from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import math
import re

from PySide6.QtCore import QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from local_matrix_assistant.core.constants import APP_NAME
from local_matrix_assistant.core.models import ChatMessage
from local_matrix_assistant.ui.brand import jarvis_mark
from local_matrix_assistant.services.attachments import AttachmentService
from local_matrix_assistant.ui.code_highlighter import CodeSyntaxHighlighter


MARKUP_PATTERNS = (
    re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE),
    re.compile(r"^\s*[-*+]\s+\S", re.MULTILINE),
    re.compile(r"^\s*\d+\.\s+\S", re.MULTILINE),
    re.compile(r"`[^`\n]+`"),
    re.compile(r"```"),
    re.compile(r"\*\*[^*\n]+\*\*"),
    re.compile(r"\*[^*\n]+\*"),
    re.compile(r"\[[^\]]+\]\([^)]+\)"),
)


def looks_like_markup(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return any(pattern.search(stripped) for pattern in MARKUP_PATTERNS)


_EMBEDDED_MARKDOWN_IMAGE = re.compile(r"(?<!\\)!\[")
_EMBEDDED_HTML_RESOURCE = re.compile(
    r"<(\s*(?:img|object|embed|iframe)\b)",
    re.IGNORECASE,
)


def safe_markdown_text(text: str) -> str:
    """Keep generated Markdown readable without loading embedded resources."""
    text = _EMBEDDED_MARKDOWN_IMAGE.sub(r"\\![", text)
    return _EMBEDDED_HTML_RESOURCE.sub(r"&lt;\1", text)


def safe_markdown_link(label: str, href: str) -> str:
    clean_label = label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    clean_label = " ".join(clean_label.splitlines()).strip() or "Source"
    encoded_href = bytes(QUrl(href).toEncoded()).decode("ascii", errors="ignore")
    encoded_href = encoded_href.replace("(", "%28").replace(")", "%29")
    return f"[{clean_label}]({encoded_href})"


class SafeMarkdownLabel(QLabel):
    link_error = Signal(str)

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        self.setOpenExternalLinks(False)
        self.linkActivated.connect(self._open_link)

    def _open_link(self, href: str) -> None:
        url = QUrl(href)
        if (
            not url.isValid()
            or url.scheme().casefold() not in {"http", "https"}
            or not url.host().strip()
        ):
            self.link_error.emit("Blocked a non-web link. Only http:// and https:// links can open.")
            return
        if not QDesktopServices.openUrl(url):
            self.link_error.emit("Windows could not open this web link.")


@dataclass(frozen=True, slots=True)
class ContentSegment:
    kind: str
    text: str
    language: str = ""


_FENCE_OPEN = re.compile(r"^\s*```\s*([A-Za-z0-9_.+#-]*)\s*(?:\r?\n)?$")
_FENCE_CLOSE = re.compile(r"^\s*```\s*(?:\r?\n)?$")


def split_fenced_content(text: str) -> list[ContentSegment]:
    """Split complete Markdown fences while leaving unfinished fences as text."""
    segments: list[ContentSegment] = []
    text_lines: list[str] = []
    code_lines: list[str] | None = None
    language = ""
    opening_line = ""

    def flush_text() -> None:
        if text_lines:
            segments.append(ContentSegment("text", "".join(text_lines).strip("\r\n")))
            text_lines.clear()

    for line in text.splitlines(keepends=True):
        if code_lines is None:
            match = _FENCE_OPEN.match(line)
            if match:
                flush_text()
                code_lines = []
                language = match.group(1).strip()
                opening_line = line
            else:
                text_lines.append(line)
            continue

        if _FENCE_CLOSE.match(line):
            segments.append(ContentSegment("code", "".join(code_lines).rstrip("\r\n"), language))
            code_lines = None
            language = ""
            opening_line = ""
        else:
            code_lines.append(line)

    if code_lines is not None:
        text_lines.append(opening_line)
        text_lines.extend(code_lines)
    flush_text()
    return [segment for segment in segments if segment.text or segment.kind == "code"]


class CodeBlockWidget(QFrame):
    def __init__(self, code: str, language: str = "") -> None:
        super().__init__()
        self.code = code
        self.language = language
        self.setObjectName("codeBlock")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("codeHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 7, 8, 7)
        header_layout.setSpacing(8)

        self.language_label = QLabel(language or "code")
        self.language_label.setObjectName("codeLanguage")
        header_layout.addWidget(self.language_label)
        header_layout.addStretch(1)

        self.copy_button = QPushButton("Copy")
        self.copy_button.setObjectName("copyCodeButton")
        self.copy_button.setToolTip("Copy code")
        self.copy_button.setAccessibleName("Copy code")
        self.copy_button.clicked.connect(self.copy_code)
        header_layout.addWidget(self.copy_button)
        layout.addWidget(header)

        self.code_view = QPlainTextEdit()
        self.code_view.setObjectName("codeEditor")
        self.code_view.setReadOnly(True)
        self.code_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.code_view.setPlainText(code)
        self.syntax_highlighter = CodeSyntaxHighlighter(self.code_view.document(), language)
        self.code_view.setTabStopDistance(self.code_view.fontMetrics().horizontalAdvance(" ") * 4)
        self.code_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        line_count = max(1, code.count("\n") + 1)
        line_height = self.code_view.fontMetrics().lineSpacing()
        self.code_view.setFixedHeight(min(420, max(82, (line_count * line_height) + 48)))
        layout.addWidget(self.code_view)

    def copy_code(self) -> None:
        QApplication.clipboard().setText(self.code)
        self.copy_button.setText("Copied")
        self.copy_button.setEnabled(False)
        QTimer.singleShot(1200, self._restore_copy_button)

    def _restore_copy_button(self) -> None:
        self.copy_button.setText("Copy")
        self.copy_button.setEnabled(True)


class MessageContentWidget(QWidget):
    link_error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("messageContent")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.body_label = self._text_label()
        layout.addWidget(self.body_label)

        self.segment_host = QWidget()
        self.segment_host.setObjectName("messageSegments")
        self.segment_layout = QVBoxLayout(self.segment_host)
        self.segment_layout.setContentsMargins(0, 0, 0, 0)
        self.segment_layout.setSpacing(10)
        self.segment_host.setVisible(False)
        layout.addWidget(self.segment_host)
        self.code_blocks: list[CodeBlockWidget] = []

    def _text_label(self) -> SafeMarkdownLabel:
        label = SafeMarkdownLabel("")
        label.setObjectName("messageBody")
        label.setWordWrap(True)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        label.link_error.connect(self.link_error.emit)
        return label

    @staticmethod
    def _set_label_content(label: QLabel, content: str, *, structured: bool = True) -> None:
        is_markup = structured and looks_like_markup(content)
        label.setTextFormat(Qt.TextFormat.MarkdownText if is_markup else Qt.TextFormat.PlainText)
        label.setText(safe_markdown_text(content) if is_markup else content)

    def set_content(self, content: str, *, structured: bool) -> None:
        segments = split_fenced_content(content) if structured else []
        has_code = any(segment.kind == "code" for segment in segments)
        if not has_code:
            self._clear_segments()
            self.segment_host.setVisible(False)
            self.body_label.setVisible(True)
            self._set_label_content(self.body_label, content, structured=structured)
            return

        self.body_label.setVisible(False)
        self._clear_segments()
        for segment in segments:
            if segment.kind == "code":
                block = CodeBlockWidget(segment.text, segment.language)
                self.code_blocks.append(block)
                self.segment_layout.addWidget(block)
            else:
                label = self._text_label()
                self._set_label_content(label, segment.text)
                self.segment_layout.addWidget(label)
        self.segment_host.setVisible(True)

    def _clear_segments(self) -> None:
        while self.segment_layout.count():
            item = self.segment_layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()
        self.code_blocks = []


class MessageBubble(QFrame):
    action_requested = Signal(str)

    def __init__(self, message: ChatMessage) -> None:
        super().__init__()
        self._actions_enabled = True
        self._retry_allowed = True
        self._message_metadata: dict = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        self.source_label = QLabel("")
        self.source_label.setObjectName("statusLabel")
        self.source_label.setWordWrap(True)
        self.source_label.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Maximum,
        )
        self.source_label.setVisible(False)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.role_icon = jarvis_mark(16, accessible_name="Jarvis message")
        header_layout.addWidget(self.role_icon)

        self.role_label = QLabel("")
        self.role_label.setObjectName("messageRole")
        header_layout.addWidget(self.role_label)
        header_layout.addStretch(1)

        self.edit_message_button = QPushButton("Edit")
        self.edit_message_button.setObjectName("messageActionButton")
        self.edit_message_button.setToolTip("Edit this message and resend from here")
        self.edit_message_button.setVisible(False)
        self.edit_message_button.clicked.connect(lambda: self.action_requested.emit("edit"))
        header_layout.addWidget(self.edit_message_button)

        self.regenerate_button = QPushButton("Regenerate")
        self.regenerate_button.setObjectName("messageActionButton")
        self.regenerate_button.setToolTip("Generate a new response to the latest user message")
        self.regenerate_button.setVisible(False)
        self.regenerate_button.clicked.connect(lambda: self.action_requested.emit("regenerate"))
        header_layout.addWidget(self.regenerate_button)

        self.copy_message_button = QPushButton("Copy")
        self.copy_message_button.setObjectName("messageCopyButton")
        self.copy_message_button.setToolTip("Copy message")
        self.copy_message_button.setAccessibleName("Copy message")
        self.copy_message_button.clicked.connect(self._copy_message)
        header_layout.addWidget(self.copy_message_button)

        self.content_widget = MessageContentWidget()
        self.content_widget.link_error.connect(self._show_link_error)
        self.body_label = self.content_widget.body_label
        self._message_content = ""

        self.attachments_label = QLabel("")
        self.attachments_label.setObjectName("messageAttachmentSummary")
        self.attachments_label.setWordWrap(True)
        self.attachments_label.setVisible(False)

        self.attachment_preview_host = QFrame()
        self.attachment_preview_host.setObjectName("messageAttachmentPreviews")
        attachment_preview_layout = QHBoxLayout(self.attachment_preview_host)
        attachment_preview_layout.setContentsMargins(0, 0, 0, 0)
        attachment_preview_layout.setSpacing(8)
        self.attachment_preview_labels: list[QLabel] = []
        for _index in range(AttachmentService.max_images):
            preview = QLabel("")
            preview.setObjectName("messageAttachmentThumbnail")
            preview.setFixedSize(96, 72)
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview.setVisible(False)
            attachment_preview_layout.addWidget(preview)
            self.attachment_preview_labels.append(preview)
        attachment_preview_layout.addStretch(1)
        self.attachment_preview_host.setVisible(False)

        self.sources_label = SafeMarkdownLabel("")
        self.sources_label.setObjectName("statusLabel")
        self.sources_label.setWordWrap(True)
        self.sources_label.setVisible(False)
        self.sources_label.setTextFormat(Qt.TextFormat.MarkdownText)
        self.sources_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self.sources_label.link_error.connect(self._show_link_error)

        self.link_notice = QLabel("")
        self.link_notice.setObjectName("messageLinkNotice")
        self.link_notice.setWordWrap(True)
        self.link_notice.setVisible(False)
        self._link_notice_timer = QTimer(self)
        self._link_notice_timer.setSingleShot(True)
        self._link_notice_timer.setInterval(3500)
        self._link_notice_timer.timeout.connect(self.link_notice.hide)

        self.error_panel = QFrame()
        self.error_panel.setObjectName("messageErrorPanel")
        self.error_panel.setAccessibleName("Reply failed")
        error_layout = QHBoxLayout(self.error_panel)
        error_layout.setContentsMargins(11, 9, 9, 9)
        error_layout.setSpacing(10)
        error_copy = QVBoxLayout()
        error_copy.setSpacing(2)
        self.error_title_label = QLabel("LOCAL MODEL ERROR")
        self.error_title_label.setObjectName("messageErrorTitle")
        error_copy.addWidget(self.error_title_label)
        self.error_detail_label = QLabel("")
        self.error_detail_label.setObjectName("messageErrorDetail")
        self.error_detail_label.setWordWrap(True)
        self.error_detail_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        error_copy.addWidget(self.error_detail_label)
        error_layout.addLayout(error_copy, stretch=1)
        self.retry_button = QPushButton("Retry")
        self.retry_button.setObjectName("messageRetryButton")
        self.retry_button.setToolTip("Retry the last request")
        self.retry_button.setAccessibleName("Retry failed reply")
        self.retry_button.clicked.connect(self._request_retry_action)
        error_layout.addWidget(self.retry_button)
        self.error_panel.setVisible(False)

        layout.addWidget(self.source_label)
        layout.addLayout(header_layout)
        layout.addWidget(self.attachment_preview_host)
        layout.addWidget(self.attachments_label)
        layout.addWidget(self.content_widget)
        layout.addWidget(self.error_panel)
        layout.addWidget(self.sources_label)
        layout.addWidget(self.link_notice)
        self.update_message(message)

    def set_actions(
        self,
        *,
        can_edit: bool,
        can_regenerate: bool,
        can_retry: bool | None = None,
        enabled: bool = True,
    ) -> None:
        self._actions_enabled = enabled
        if can_retry is not None:
            self._retry_allowed = can_retry
        self.edit_message_button.setVisible(can_edit)
        self.edit_message_button.setEnabled(enabled)
        self.regenerate_button.setVisible(can_regenerate)
        self.regenerate_button.setEnabled(enabled)
        is_error = not self.error_panel.isHidden()
        self.retry_button.setVisible(is_error and self._retry_allowed)
        self.retry_button.setEnabled(is_error and self._retry_allowed and enabled)

    def update_message(self, message: ChatMessage) -> None:
        self._link_notice_timer.stop()
        self.link_notice.hide()
        is_user = message.role == "user"
        self.role_icon.setVisible(not is_user)
        self.setObjectName("messageUser" if is_user else "messageAssistant")
        role_label = "USER" if is_user else APP_NAME.upper()
        self.role_label.setText(f"{role_label}  {message.timestamp}")
        self._message_content = message.content
        self._message_metadata = dict(message.metadata)
        is_pending = bool(message.metadata.get("pending"))
        is_error = bool(message.metadata.get("error"))
        is_interrupted = bool(message.metadata.get("interrupted"))
        is_save_error = bool(message.metadata.get("save_error"))
        self.setProperty(
            "messageState",
            "save_error"
            if is_save_error
            else "interrupted"
            if is_interrupted
            else "error"
            if is_error
            else "normal",
        )
        self.content_widget.set_content(message.content, structured=not is_pending)
        self.content_widget.setVisible(bool(message.content.strip()) or not is_error)
        self.copy_message_button.setVisible(bool(message.content.strip()) and not is_pending)
        error_detail = str(message.metadata.get("error_message", "")).strip()[:2_000]
        self.error_detail_label.setText(error_detail or "The local model could not complete this reply.")
        self.error_title_label.setText(
            "HISTORY SAVE FAILED"
            if is_save_error
            else "REPLY INTERRUPTED"
            if is_interrupted
            else "LOCAL MODEL ERROR"
        )
        self.error_panel.setProperty(
            "recoveryState",
            "save_error" if is_save_error else "interrupted" if is_interrupted else "error",
        )
        self.retry_button.setText("Retry Save" if is_save_error else "Retry")
        self.retry_button.setToolTip(
            "Try saving this completed reply again"
            if is_save_error
            else "Retry the last request"
        )
        self.error_panel.setVisible(is_error)
        self.retry_button.setVisible(is_error and self._retry_allowed)
        self.retry_button.setEnabled(
            is_error and self._retry_allowed and self._actions_enabled and not is_pending
        )

        attachments = AttachmentService.metadata_attachments(message.metadata)
        attachment_lines: list[str] = []
        image_attachments = [attachment for attachment in attachments if attachment.get("image_data")]
        for index, preview in enumerate(self.attachment_preview_labels):
            if index >= len(image_attachments):
                preview.clear()
                preview.setVisible(False)
                continue
            attachment = image_attachments[index]
            pixmap = self._decode_attachment_pixmap(
                str(attachment.get("thumbnail_data") or attachment.get("image_data", ""))
            )
            preview.setPixmap(pixmap)
            preview.setToolTip(str(attachment.get("name", "Attached image")))
            preview.setVisible(not pixmap.isNull())
        self.attachment_preview_host.setVisible(
            any(not preview.isHidden() for preview in self.attachment_preview_labels)
        )
        for attachment in attachments:
            name = str(attachment.get("name", "Attached file"))
            try:
                size = AttachmentService.format_size(int(attachment.get("size_bytes", 0)))
            except (TypeError, ValueError):
                size = "unknown size"
            state = " · truncated snapshot" if attachment.get("truncated") else ""
            prefix = "IMAGE" if attachment.get("image_data") else "FILE"
            attachment_lines.append(f"{prefix}  {name} · {size}{state}")
        self.attachments_label.setText("\n".join(attachment_lines))
        self.attachments_label.setVisible(bool(attachment_lines))

        status_parts: list[str] = []
        if message.metadata.get("source") == "voice":
            status_parts.append("Voice transcription")
        if message.metadata.get("pending"):
            status_parts.append(str(message.metadata.get("pending_label", "Waiting for local model...")))
        elif message.metadata.get("error"):
            failed_model = str(message.metadata.get("model_name", "")).strip()
            if failed_model:
                status_parts.append(failed_model)
            status_parts.append(
                "Reply not saved"
                if is_save_error
                else "Reply interrupted"
                if is_interrupted
                else "Reply failed"
            )
        elif message.metadata.get("canceled"):
            status_parts.append("Response canceled")
        else:
            model_name = str(message.metadata.get("model_name", "")).strip()
            model_profile = str(message.metadata.get("model_profile", "")).strip()
            if model_name:
                route_label = f"{model_profile} -> {model_name}" if model_profile else model_name
                if message.metadata.get("model_automatic"):
                    route_label = f"Auto: {route_label}"
                status_parts.append(route_label)
            if message.metadata.get("web_search_used"):
                provider = message.metadata.get("web_search_provider", "web search")
                status_parts.append(f"Used {provider}")
        performance_summary, performance_detail = self._performance_details(message.metadata)
        if performance_summary and not is_pending:
            status_parts.append(performance_summary)
        self.source_label.setText("  |  ".join(status_parts))
        self.source_label.setVisible(bool(status_parts))
        tooltip_parts = [str(message.metadata.get("model_reason", "")).strip(), performance_detail]
        self.source_label.setToolTip("\n".join(part for part in tooltip_parts if part))
        self.source_label.setProperty(
            "progressState",
            str(message.metadata.get("pending_state", "")) if is_pending else "",
        )

        web_sources = message.metadata.get("web_sources", [])
        if isinstance(web_sources, list) and web_sources:
            self.sources_label.setVisible(True)
            lines = ["**Sources**"]
            for index, source in enumerate(web_sources, start=1):
                if not isinstance(source, dict):
                    continue
                title = str(source.get("title", source.get("url", "Source")))
                url = str(source.get("url", ""))
                snippet = str(source.get("snippet", "")).strip()
                lines.append(f"{index}. {safe_markdown_link(title, url)}")
                if snippet:
                    lines.append(f"   {snippet}")
            self.sources_label.setText(safe_markdown_text("\n".join(lines)))
        else:
            self.sources_label.setVisible(False)
            self.sources_label.setText("")

        self.style().unpolish(self)
        self.style().polish(self)

    def update_pending_status(
        self,
        label: str,
        *,
        state: str = "waiting",
        tooltip: str = "",
    ) -> None:
        if not self._message_metadata.get("pending"):
            return
        normalized = str(label).strip() or "Waiting for local model..."
        self._message_metadata["pending_label"] = normalized
        self._message_metadata["pending_state"] = state
        self.source_label.setText(normalized)
        self.source_label.setVisible(True)
        self.source_label.setToolTip(tooltip)
        self.source_label.setProperty("progressState", state)
        self.source_label.style().unpolish(self.source_label)
        self.source_label.style().polish(self.source_label)

    @classmethod
    def _performance_details(cls, metadata: dict) -> tuple[str, str]:
        model_seconds = cls._bounded_metric(
            metadata.get("reply_model_elapsed_seconds"),
            maximum=86_400,
        )
        if model_seconds is None:
            model_seconds = cls._bounded_metric(
                metadata.get("ollama_total_seconds"),
                maximum=86_400,
            )
        total_seconds = cls._bounded_metric(
            metadata.get("reply_elapsed_seconds"),
            maximum=86_400,
        )
        first_token_seconds = cls._bounded_metric(
            metadata.get("reply_time_to_first_token_seconds"),
            maximum=86_400,
        )
        load_seconds = cls._bounded_metric(
            metadata.get("ollama_load_seconds"),
            maximum=86_400,
        )
        tokens_per_second = cls._bounded_metric(
            metadata.get("ollama_tokens_per_second"),
            maximum=100_000,
        )
        prompt_tokens = cls._bounded_count(metadata.get("ollama_prompt_tokens"))
        generated_tokens = cls._bounded_count(metadata.get("ollama_generated_tokens"))
        if generated_tokens is None or generated_tokens < 8:
            tokens_per_second = None

        summary_parts: list[str] = []
        if model_seconds is not None:
            summary_parts.append(f"{cls._format_seconds(model_seconds)} local response")
        if tokens_per_second is not None:
            summary_parts.append(f"{tokens_per_second:.1f} tok/s")

        detail_parts: list[str] = []
        if total_seconds is not None:
            detail_parts.append(f"Total workflow: {cls._format_seconds(total_seconds)}")
        if model_seconds is not None:
            detail_parts.append(f"Model response: {cls._format_seconds(model_seconds)}")
        if first_token_seconds is not None:
            detail_parts.append(f"First token: {cls._format_seconds(first_token_seconds)}")
        if load_seconds is not None:
            detail_parts.append(f"Model load: {cls._format_seconds(load_seconds)}")
        if prompt_tokens is not None:
            detail_parts.append(f"Prompt tokens: {prompt_tokens:,}")
        if generated_tokens is not None:
            detail_parts.append(f"Generated tokens: {generated_tokens:,}")
        if tokens_per_second is not None:
            detail_parts.append(f"Generation speed: {tokens_per_second:.1f} tok/s")
        return ", ".join(summary_parts), "\n".join(detail_parts)

    @staticmethod
    def _bounded_metric(value: object, *, maximum: float) -> float | None:
        if isinstance(value, bool):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed) or parsed <= 0:
            return None
        return min(parsed, maximum)

    @staticmethod
    def _bounded_count(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        if parsed <= 0:
            return None
        return min(parsed, 10_000_000)

    @staticmethod
    def _format_seconds(value: float) -> str:
        return f"{value:.2f}s" if value < 1 else f"{value:.1f}s"

    def _request_retry_action(self) -> None:
        self.action_requested.emit(
            "retry_save" if self._message_metadata.get("save_error") else "retry"
        )

    def _copy_message(self) -> None:
        QApplication.clipboard().setText(self._message_content)
        self.copy_message_button.setText("Copied")
        self.copy_message_button.setEnabled(False)
        QTimer.singleShot(1200, self._restore_copy_message_button)

    def _restore_copy_message_button(self) -> None:
        self.copy_message_button.setText("Copy")
        self.copy_message_button.setEnabled(True)

    def _show_link_error(self, message: str) -> None:
        self.link_notice.setText(message)
        self.link_notice.show()
        self._link_notice_timer.start()

    @staticmethod
    def _decode_attachment_pixmap(image_data: str) -> QPixmap:
        try:
            payload = base64.b64decode(image_data, validate=True)
        except (ValueError, binascii.Error):
            return QPixmap()
        pixmap = QPixmap()
        if not pixmap.loadFromData(payload):
            return QPixmap()
        return pixmap.scaled(
            96,
            72,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
