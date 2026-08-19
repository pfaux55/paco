from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Iterable

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QCloseEvent, QCursor, QGuiApplication, QImage
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from local_matrix_assistant.core.config import AppConfig
from local_matrix_assistant.core.models import ChatMessage, ChatStreamResult
from local_matrix_assistant.services.attachments import AttachmentService, LocalAttachment
from local_matrix_assistant.services.model_router import ModelRouter, ModelSelection
from local_matrix_assistant.services.ollama import OllamaClient, OllamaStatus
from local_matrix_assistant.ui.brand import paco_icon, paco_mark
from local_matrix_assistant.ui.task_runner import TaskRunner
from local_matrix_assistant.ui.theme import stylesheet_for_theme
from local_matrix_assistant.ui.workers import FunctionWorker, StreamWorker


COMPACT_WIDTH_RATIO = 0.18
COMPACT_HEIGHT_RATIO = 0.40
COMPACT_MIN_WIDTH = 320
COMPACT_MIN_HEIGHT = 300
COMPACT_MAX_WIDTH = 560
COMPACT_MAX_HEIGHT = 520
COMPACT_SCREEN_MARGIN = 16
COMPACT_COLLAPSED_HEIGHT = 64
SCREEN_CAPTURE_DELAY_MS = 150
MAX_SESSION_MESSAGES = 40


def compact_geometry_for(available: QRect) -> QRect:
    """Return a bounded bottom-right geometry for a screen work area."""

    available_width = max(1, available.width())
    available_height = max(1, available.height())
    width = min(
        COMPACT_MAX_WIDTH,
        max(COMPACT_MIN_WIDTH, round(available_width * COMPACT_WIDTH_RATIO)),
    )
    height = min(
        COMPACT_MAX_HEIGHT,
        max(COMPACT_MIN_HEIGHT, round(available_height * COMPACT_HEIGHT_RATIO)),
    )
    width = min(width, max(1, available_width - (2 * COMPACT_SCREEN_MARGIN)))
    height = min(height, max(1, available_height - (2 * COMPACT_SCREEN_MARGIN)))
    x = max(
        available.x(),
        available.x() + available_width - width - COMPACT_SCREEN_MARGIN,
    )
    y = max(
        available.y(),
        available.y() + available_height - height - COMPACT_SCREEN_MARGIN,
    )
    return QRect(x, y, width, height)


def screen_under_cursor():
    screen = QGuiApplication.screenAt(QCursor.pos())
    if screen is not None:
        return screen
    application = QGuiApplication.instance()
    return application.primaryScreen() if application is not None else None


class CompactAssistantWindow(QWidget):
    """Session-only Paco overlay for text and one-shot screen questions."""

    closing = Signal()
    main_mode_requested = Signal()

    def __init__(
        self,
        config: AppConfig,
        *,
        ollama_client: OllamaClient | None = None,
        model_router: ModelRouter | None = None,
        attachment_service: AttachmentService | None = None,
        start_status_check: bool = True,
    ) -> None:
        super().__init__()
        self.config = config
        self.ollama_client = ollama_client or OllamaClient(config.ollama_base_url)
        self.model_router = model_router or ModelRouter()
        self.attachment_service = attachment_service or AttachmentService()
        self.available_ollama_models: list[str] = []
        self.messages: list[ChatMessage] = []
        self._pending_screenshot: LocalAttachment | None = None
        self._active_request_messages: list[ChatMessage] = []
        self._active_stream_worker: StreamWorker | None = None
        self._active_status_worker: FunctionWorker | None = None
        self._active_selection: ModelSelection | None = None
        self._pending_reply_label: QLabel | None = None
        self._pending_reply_text = ""
        self._busy = False
        self._capture_in_progress = False
        self._closing = False
        self._expanded = False
        self._anchor_screen = None
        self._expanded_geometry = QRect()
        self._drag_offset: QPoint | None = None

        self.thread_pool = QThreadPool(self)
        self.task_runner = TaskRunner(self.thread_pool)

        self.setWindowTitle("Paco Compact Assistant")
        self.setWindowIcon(paco_icon())
        self.setObjectName("compactAssistant")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setStyleSheet(
            stylesheet_for_theme(
                config.theme,
                config.chat_font_family,
                config.chat_font_size,
            )
        )
        self._build_ui()
        self.place_on_screen(screen_under_cursor())
        self._set_state("loading", "Checking local Ollama models…")

        if start_status_check:
            QTimer.singleShot(0, self.refresh_model_status)

    @property
    def pending_screenshot(self) -> LocalAttachment | None:
        return self._pending_screenshot

    @property
    def is_busy(self) -> bool:
        return self._busy

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self.status_indicator = QLabel("●")
        self.status_indicator.setObjectName("compactStatusIndicator")
        self.status_indicator.setAccessibleName("Assistant status")
        self.status_indicator.hide()

        self.status_label = QLabel()
        self.status_label.setObjectName("compactStatusLabel")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        root.addStretch(1)

        self.transcript_scroll = QScrollArea()
        self.transcript_scroll.setObjectName("compactTranscript")
        self.transcript_scroll.setWidgetResizable(True)
        self.transcript_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.transcript_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.transcript_scroll.viewport().setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground,
            True,
        )
        self.transcript_scroll.viewport().setStyleSheet("background: transparent;")
        self.transcript_host = QWidget()
        self.transcript_host.setObjectName("compactTranscriptHost")
        self.transcript_layout = QVBoxLayout(self.transcript_host)
        self.transcript_layout.setContentsMargins(4, 4, 4, 4)
        self.transcript_layout.setSpacing(7)
        self.transcript_layout.addStretch(1)
        self.transcript_scroll.setWidget(self.transcript_host)
        self.transcript_scroll.hide()
        root.addWidget(self.transcript_scroll, 1)

        root.addWidget(self.status_label)

        self.input_bar = QFrame()
        self.input_bar.setObjectName("compactInputBar")
        controls = QHBoxLayout(self.input_bar)
        controls.setContentsMargins(8, 6, 7, 6)
        controls.setSpacing(7)

        self.paco_mark = paco_mark(
            34,
            parent=self.input_bar,
            accessible_name="Paco compact assistant",
        )
        self.paco_mark.setCursor(Qt.CursorShape.OpenHandCursor)
        self.paco_mark.setToolTip("Drag Paco")
        self.paco_mark.installEventFilter(self)
        controls.addWidget(self.paco_mark)

        self.capture_button = QPushButton()
        self.capture_button.setObjectName("compactCaptureButton")
        self.capture_button.setAccessibleName("Capture screen")
        self.capture_button.setToolTip("Capture the display under the cursor")
        self.capture_button.setFixedSize(30, 30)
        self.capture_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        )
        self.capture_button.setIconSize(QSize(15, 15))
        self.capture_button.clicked.connect(self.request_screen_capture)

        self.prompt_input = QLineEdit()
        self.prompt_input.setObjectName("compactPromptInput")
        self.prompt_input.setAccessibleName("Question")
        self.prompt_input.setPlaceholderText("Ask Paco...")
        self.prompt_input.setClearButtonEnabled(True)
        self.prompt_input.returnPressed.connect(self.submit_prompt)
        controls.addWidget(self.prompt_input, 1)

        controls.addWidget(self.capture_button)

        self.main_mode_button = QPushButton()
        self.main_mode_button.setObjectName("compactMainButton")
        self.main_mode_button.setAccessibleName("Open main app")
        self.main_mode_button.setToolTip("Main app")
        self.main_mode_button.setFixedSize(24, 30)
        self.main_mode_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarNormalButton)
        )
        self.main_mode_button.setIconSize(QSize(12, 12))
        self.main_mode_button.clicked.connect(self.main_mode_requested.emit)
        controls.addWidget(self.main_mode_button)

        self.close_button = QPushButton()
        self.close_button.setObjectName("compactCloseButton")
        self.close_button.setAccessibleName("Close compact assistant")
        self.close_button.setToolTip("Close")
        self.close_button.setFixedSize(24, 30)
        self.close_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton)
        )
        self.close_button.setIconSize(QSize(12, 12))
        self.close_button.clicked.connect(self.close)
        controls.addWidget(self.close_button)
        root.addWidget(self.input_bar)

        self.capture_note = QLabel()
        self.capture_note.setObjectName("compactCaptureNote")
        self.capture_note.setTextFormat(Qt.TextFormat.PlainText)
        self.capture_note.hide()
        root.insertWidget(root.indexOf(self.input_bar), self.capture_note)

    def place_on_screen(self, screen) -> QRect:
        if screen is None:
            geometry = QRect(0, 0, COMPACT_MIN_WIDTH, COMPACT_MIN_HEIGHT)
        else:
            self._anchor_screen = screen
            geometry = compact_geometry_for(screen.availableGeometry())
        self._expanded_geometry = QRect(geometry)
        if self._expanded:
            visible_geometry = geometry
        else:
            collapsed_height = min(COMPACT_COLLAPSED_HEIGHT, geometry.height())
            visible_geometry = QRect(
                geometry.x(),
                geometry.y() + geometry.height() - collapsed_height,
                geometry.width(),
                collapsed_height,
            )
        self.setGeometry(visible_geometry)
        return visible_geometry

    def toggle_conversation(self) -> None:
        self.set_conversation_expanded(not self._expanded)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.paco_mark:
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self._drag_offset = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
                self.paco_mark.setCursor(Qt.CursorShape.ClosedHandCursor)
                return True
            if (
                event.type() == QEvent.Type.MouseMove
                and self._drag_offset is not None
                and event.buttons() & Qt.MouseButton.LeftButton
            ):
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                if self._expanded:
                    self._expanded_geometry.moveTopLeft(self.frameGeometry().topLeft())
                else:
                    self._expanded_geometry.moveBottomRight(self.frameGeometry().bottomRight())
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_offset = None
                self.paco_mark.setCursor(Qt.CursorShape.OpenHandCursor)
                return True
        return super().eventFilter(watched, event)

    def set_conversation_expanded(self, expanded: bool) -> None:
        if self._expanded == expanded:
            return
        self._expanded = expanded
        self.transcript_scroll.setVisible(expanded)
        if expanded:
            self.setGeometry(self._expanded_geometry)
            return

        expanded_geometry = QRect(self.geometry())
        self._expanded_geometry = expanded_geometry
        collapsed_height = min(COMPACT_COLLAPSED_HEIGHT, expanded_geometry.height())
        self.setGeometry(
            expanded_geometry.x(),
            expanded_geometry.y() + expanded_geometry.height() - collapsed_height,
            expanded_geometry.width(),
            collapsed_height,
        )

    def refresh_model_status(self) -> None:
        if self._closing or self._active_status_worker is not None:
            return
        worker = FunctionWorker(self.ollama_client.status)
        self._active_status_worker = worker
        self.task_runner.start(worker, self._on_status_ready, self._on_status_error)

    def _on_status_ready(self, payload: object) -> None:
        self._active_status_worker = None
        if not isinstance(payload, OllamaStatus):
            self._on_status_error("Ollama returned an invalid status.")
            return
        self.available_ollama_models = list(payload.models)
        if self._busy:
            return
        if not payload.connected:
            self._set_state("error", payload.message)
        elif not payload.models:
            self._set_state("error", "Ollama is connected, but no local models are installed.")
        else:
            self._set_state("ready", f"Ready • {len(payload.models)} local model(s)")

    def _on_status_error(self, message: str) -> None:
        self._active_status_worker = None
        self.available_ollama_models = []
        if self._busy:
            return
        self._set_state("error", f"Could not check Ollama: {message}")

    def capture_screen(self, screen=None) -> LocalAttachment | None:
        if self._busy or self._capture_in_progress:
            self._set_state("loading", "Wait for the current reply before capturing.")
            return None
        target_screen = screen or screen_under_cursor()
        if target_screen is None:
            self._set_state("error", "No display is available for capture.")
            return None

        was_visible = self.isVisible()
        self.hide()
        QApplication.processEvents()
        try:
            attachment = self._capture_screen_image(target_screen)
        except Exception as exc:  # noqa: BLE001
            self._set_state("error", f"Screen capture failed: {exc}")
            return None
        finally:
            if was_visible:
                self.show()
                self.raise_()

        self._accept_screen_capture(attachment)
        return attachment

    def request_screen_capture(self) -> bool:
        """Hide the overlay, then capture after Windows has repainted the desktop."""

        if self._busy or self._capture_in_progress:
            return False
        target_screen = screen_under_cursor()
        if target_screen is None:
            self._set_state("error", "No display is available for capture.")
            return False

        self._capture_in_progress = True
        self.capture_button.setEnabled(False)
        was_visible = self.isVisible()
        self.hide()
        QApplication.processEvents()
        QTimer.singleShot(
            SCREEN_CAPTURE_DELAY_MS,
            lambda: self._complete_screen_capture(target_screen, was_visible),
        )
        return True

    def _complete_screen_capture(self, target_screen, restore_window: bool) -> None:
        if self._closing:
            self._capture_in_progress = False
            return
        attachment: LocalAttachment | None = None
        error = ""
        try:
            if not self._closing:
                attachment = self._capture_screen_image(target_screen)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        finally:
            self._capture_in_progress = False
            self.capture_button.setEnabled(not self._busy)
            if restore_window and not self._closing:
                self.show()
                self.raise_()
                self.activateWindow()

        if error:
            self._set_state("error", f"Screen capture failed: {error}")
        elif attachment is not None:
            self._accept_screen_capture(attachment)

    def _capture_screen_image(self, target_screen) -> LocalAttachment:
        pixmap = target_screen.grabWindow(0)
        image = pixmap.toImage() if hasattr(pixmap, "toImage") else QImage()
        if image.isNull():
            raise ValueError("The display capture was empty.")
        attachment = self.attachment_service.load_clipboard_image(image)
        return replace(
            attachment,
            path="memory://screen-capture",
            name="screen-capture.jpg",
        )

    def _accept_screen_capture(self, attachment: LocalAttachment) -> None:
        self._pending_screenshot = attachment
        self.capture_note.setText(
            f"Screen ready • {attachment.width} × {attachment.height} • used once"
        )
        self.capture_note.show()
        self._set_state("ready", "Screen captured. Ask a question to use it once.")
        self.prompt_input.setFocus()

    def select_model(self, prompt: str, *, requires_vision: bool) -> ModelSelection:
        return self.model_router.select(
            prompt,
            self.config.model_profile,
            list(self.available_ollama_models),
            self.config.ollama_model,
            requires_vision=requires_vision,
        )

    def submit_prompt(self, text: str | None = None) -> bool:
        if self._busy or self._closing:
            return False
        prompt = (self.prompt_input.text() if text is None else text).strip()
        if not prompt:
            self._set_state("error", "Enter a question.")
            return False

        screenshot = self._pending_screenshot
        selection = self.select_model(prompt, requires_vision=screenshot is not None)
        if not selection.model:
            if screenshot is not None:
                self._set_state(
                    "error",
                    "No installed Ollama vision model is available. Install qwen3.5:4b, then retry.",
                )
            else:
                self._set_state("error", "No local Ollama model is available.")
            return False

        timestamp = self._timestamp()
        session_user = ChatMessage(role="user", content=prompt, timestamp=timestamp)
        self.messages.append(session_user)
        self._trim_session_messages()

        request_user = ChatMessage(
            role="user",
            content=prompt,
            timestamp=timestamp,
            metadata=(
                {"attachments": [screenshot.metadata()]}
                if screenshot is not None
                else {}
            ),
        )
        prior_messages = self.messages[:-1]
        request_messages = [
            ChatMessage(
                role="system",
                content=self.model_router.system_prompt(selection.profile),
                timestamp=timestamp,
            ),
            *prior_messages[-(MAX_SESSION_MESSAGES - 2) :],
            request_user,
        ]
        self._active_request_messages = request_messages
        self._active_selection = selection
        self._pending_screenshot = None
        self.capture_note.clear()
        self.capture_note.hide()
        self.prompt_input.clear()

        self._clear_transcript()
        self._pending_reply_label = self._append_message_view("assistant", "Thinking…")
        self._pending_reply_text = ""
        if not self._expanded:
            self.set_conversation_expanded(True)
        self._set_busy(True)
        self._set_state("loading", f"Waiting for {selection.model}…")

        options = {
            "num_ctx": selection.context_window,
            "num_predict": selection.max_output_tokens,
        }
        worker = StreamWorker(
            lambda on_chunk, should_cancel: self._run_request(
                selection.model,
                request_messages,
                on_chunk,
                should_cancel,
                options,
            )
        )
        self._active_stream_worker = worker
        self.task_runner.start_stream(
            worker,
            self._on_stream_chunk,
            self._on_stream_complete,
            self._on_stream_error,
        )
        return True

    def _run_request(
        self,
        model: str,
        request_messages: list[ChatMessage],
        on_chunk,
        should_cancel,
        options: dict,
    ) -> ChatStreamResult:
        try:
            return self.ollama_client.chat_stream(
                model,
                request_messages,
                on_chunk,
                should_cancel,
                options=options,
            )
        finally:
            self._scrub_request_messages(request_messages)

    def _on_stream_chunk(self, chunk: object) -> None:
        text = str(chunk)
        if not text:
            return
        self._pending_reply_text += text
        if self._pending_reply_label is not None:
            self._pending_reply_label.setText(self._pending_reply_text)
        self._scroll_to_latest()
        self._set_state("streaming", "Streaming local reply…")

    def _on_stream_complete(self, payload: object) -> None:
        result = payload if isinstance(payload, ChatStreamResult) else ChatStreamResult(content=str(payload))
        selection = self._active_selection
        if result.canceled:
            content = self._pending_reply_text.strip() or "Response canceled."
            metadata = {"canceled": True}
            state = "Reply canceled."
        else:
            content = result.content.strip() or self._pending_reply_text.strip()
            metadata = {}
            state = f"Ready • {selection.model}" if selection else "Ready"
        if not content:
            content = "Ollama returned an empty response."
            metadata = {"error": True}
            state = content
        self.messages.append(
            ChatMessage(
                role="assistant",
                content=content,
                timestamp=self._timestamp(),
                metadata=metadata,
            )
        )
        self._trim_session_messages()
        if self._pending_reply_label is not None:
            self._pending_reply_label.setText(content)
        self._finish_request("ready" if not metadata.get("error") else "error", state)

    def _on_stream_error(self, message: str) -> None:
        content = f"Error: {message}"
        self.messages.append(
            ChatMessage(
                role="assistant",
                content=content,
                timestamp=self._timestamp(),
                metadata={"error": True},
            )
        )
        self._trim_session_messages()
        if self._pending_reply_label is not None:
            self._pending_reply_label.setText(content)
        self._finish_request("error", content)

    def cancel_reply(self) -> bool:
        if not self._busy or self._active_stream_worker is None:
            return False
        self._active_stream_worker.cancel()
        self._set_state("loading", "Canceling reply…")
        return True

    def _finish_request(self, state: str, message: str) -> None:
        self._scrub_request_messages(self._active_request_messages)
        self._active_request_messages = []
        self._active_stream_worker = None
        self._active_selection = None
        self._pending_reply_label = None
        self._pending_reply_text = ""
        self._set_busy(False)
        self._set_state(state, message)
        self._scroll_to_latest()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.capture_button.setEnabled(not busy and not self._capture_in_progress)
        self.prompt_input.setEnabled(not busy)

    def _set_state(self, state: str, message: str) -> None:
        self.status_indicator.setProperty("assistantState", state)
        self.status_indicator.style().unpolish(self.status_indicator)
        self.status_indicator.style().polish(self.status_indicator)
        self.status_label.setText(message)
        self.status_label.setVisible(state == "error")
        if state == "error" and not self._expanded:
            self.set_conversation_expanded(True)

    def _append_message_view(self, role: str, content: str) -> QLabel:
        frame = QFrame(self.transcript_host)
        frame.setObjectName("compactMessageUser" if role == "user" else "compactMessageAssistant")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(9, 7, 9, 7)
        layout.setSpacing(3)
        heading = QLabel("YOU" if role == "user" else "Paco")
        heading.setObjectName("compactMessageRole")
        layout.addWidget(heading)
        body = QLabel(content)
        body.setObjectName("compactMessageBody")
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(body)
        self.transcript_layout.insertWidget(self.transcript_layout.count() - 1, frame)
        QTimer.singleShot(0, self._scroll_to_latest)
        return body

    def _scroll_to_latest(self) -> None:
        bar = self.transcript_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _trim_session_messages(self) -> None:
        if len(self.messages) > MAX_SESSION_MESSAGES:
            del self.messages[: len(self.messages) - MAX_SESSION_MESSAGES]

    @staticmethod
    def _scrub_request_messages(messages: Iterable[ChatMessage]) -> None:
        for message in messages:
            if message.metadata:
                message.metadata.clear()

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        was_closing = self._closing
        self._closing = True
        if not was_closing:
            self.closing.emit()
        self.task_runner.close()
        if self._active_stream_worker is not None:
            self._active_stream_worker.cancel()
        self._scrub_request_messages(self._active_request_messages)
        self._active_request_messages.clear()
        self._pending_screenshot = None
        self.messages.clear()
        self._clear_transcript()
        self.thread_pool.waitForDone(500)
        event.accept()

    def _clear_transcript(self) -> None:
        while self.transcript_layout.count() > 1:
            item = self.transcript_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
