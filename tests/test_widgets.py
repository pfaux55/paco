from __future__ import annotations

import base64
import os
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QImage, QTextDocument
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from local_matrix_assistant.core.models import ChatMessage
from local_matrix_assistant.ui.widgets import (
    CodeBlockWidget,
    MessageBubble,
    SafeMarkdownLabel,
    looks_like_markup,
    safe_markdown_link,
    safe_markdown_text,
    split_fenced_content,
)


class WidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_detects_markdown_like_content(self) -> None:
        self.assertTrue(looks_like_markup("## Heading"))
        self.assertTrue(looks_like_markup("- item"))
        self.assertTrue(looks_like_markup("Use `code` here"))
        self.assertFalse(looks_like_markup("plain sentence only"))

    def test_message_bubble_uses_markdown_for_markup_content(self) -> None:
        bubble = MessageBubble(
            ChatMessage(role="assistant", content="## Heading\n- item", timestamp="2026-03-21 12:00:00")
        )
        self.assertEqual(Qt.TextFormat.MarkdownText, bubble.body_label.textFormat())

        bubble.update_message(
            ChatMessage(role="assistant", content="plain sentence only", timestamp="2026-03-21 12:00:01")
        )
        self.assertEqual(Qt.TextFormat.PlainText, bubble.body_label.textFormat())

    def test_paco_mark_only_appears_on_assistant_messages(self) -> None:
        assistant = MessageBubble(ChatMessage("assistant", "Ready", "now"))
        user = MessageBubble(ChatMessage("user", "Hello", "now"))

        self.assertFalse(assistant.role_icon.pixmap().isNull())
        self.assertFalse(assistant.role_icon.isHidden())
        self.assertTrue(user.role_icon.isHidden())

    def test_thinking_is_only_visible_when_enabled(self) -> None:
        message = ChatMessage(
            "assistant",
            "Answer",
            "now",
            metadata={"thinking": "Private reasoning"},
        )
        bubble = MessageBubble(message)

        self.assertTrue(bubble.thinking_panel.isHidden())

        bubble.set_show_thinking(True)

        self.assertFalse(bubble.thinking_panel.isHidden())
        self.assertEqual("Private reasoning", bubble.thinking_label.text())

    def test_fenced_content_is_split_without_losing_surrounding_markdown(self) -> None:
        segments = split_fenced_content("Before\n\n```python\nprint('ready')\n```\n\nAfter")

        self.assertEqual(["text", "code", "text"], [segment.kind for segment in segments])
        self.assertEqual("python", segments[1].language)
        self.assertEqual("print('ready')", segments[1].text)

    def test_unfinished_fence_remains_streamable_text(self) -> None:
        segments = split_fenced_content("Starting\n```python\nprint('partial')")

        self.assertEqual(["text", "text"], [segment.kind for segment in segments])
        self.assertIn("```python", segments[1].text)

    def test_completed_code_block_has_language_copy_and_horizontal_layout(self) -> None:
        content = "Use this:\n\n```python\nprint('ready')\n```"
        bubble = MessageBubble(ChatMessage(role="assistant", content=content, timestamp="now"))

        self.assertEqual(1, len(bubble.content_widget.code_blocks))
        block = bubble.content_widget.code_blocks[0]
        self.assertIsInstance(block, CodeBlockWidget)
        self.assertEqual("python", block.language_label.text())
        self.assertEqual(QPlainTextEdit.LineWrapMode.NoWrap, block.code_view.lineWrapMode())

        block.copy_button.click()
        self.assertEqual("print('ready')", QApplication.clipboard().text())
        self.assertEqual("Copied", block.copy_button.text())

    def test_code_block_applies_language_specific_syntax_highlighting(self) -> None:
        block = CodeBlockWidget('def ready(value: int):\n    return "ok"', "py")
        block.syntax_highlighter.rehighlight()
        self.app.processEvents()

        self.assertEqual("python", block.syntax_highlighter.family)
        format_colors: set[str] = set()
        text_block = block.code_view.document().firstBlock()
        while text_block.isValid():
            format_colors.update(
                item.format.foreground().color().name()
                for item in text_block.layout().formats()
            )
            text_block = text_block.next()
        self.assertIn("#5de392", format_colors)
        self.assertIn("#d7bd74", format_colors)

    def test_generated_markdown_cannot_load_embedded_resources(self) -> None:
        sanitized = safe_markdown_text(
            '![private](file:///C:/private.png) <IMG src="https://example.com/pixel.png">'
        )

        self.assertIn(r"\![private]", sanitized)
        self.assertIn("&lt;IMG", sanitized)
        self.assertNotIn("<IMG", sanitized)
        document = QTextDocument()
        document.setMarkdown(sanitized)
        self.assertNotIn("<img", document.toHtml().casefold())

    def test_markdown_links_open_only_http_and_https(self) -> None:
        label = SafeMarkdownLabel()
        errors: list[str] = []
        opened: list[str] = []
        label.link_error.connect(errors.append)

        with patch(
            "local_matrix_assistant.ui.widgets.QDesktopServices.openUrl",
            side_effect=lambda url: opened.append(url.toString()) or True,
        ):
            label._open_link("https://example.com/docs")
            label._open_link("file:///C:/private.txt")
            label._open_link("javascript:alert(1)")

        self.assertEqual(["https://example.com/docs"], opened)
        self.assertEqual(2, len(errors))
        self.assertTrue(all("Only http:// and https://" in message for message in errors))

    def test_unsafe_message_link_shows_inline_feedback(self) -> None:
        bubble = MessageBubble(
            ChatMessage("assistant", "[Open](file:///C:/private.txt)", "now")
        )

        bubble.body_label._open_link("file:///C:/private.txt")

        self.assertFalse(bubble.link_notice.isHidden())
        self.assertIn("Blocked", bubble.link_notice.text())

    def test_source_markdown_escapes_titles_and_balances_destinations(self) -> None:
        link = safe_markdown_link(
            "Title ](file:///C:/private.txt)",
            "https://example.com/a_(b)",
        )

        self.assertIn(r"\]", link)
        self.assertIn("a_%28b%29", link)

    def test_streaming_code_stays_lightweight_until_message_finishes(self) -> None:
        content = "```python\nprint('ready')\n```"
        bubble = MessageBubble(
            ChatMessage(role="assistant", content=content, timestamp="now", metadata={"pending": True})
        )
        self.assertEqual([], bubble.content_widget.code_blocks)
        self.assertTrue(bubble.copy_message_button.isHidden())
        self.assertEqual(Qt.TextFormat.PlainText, bubble.body_label.textFormat())

        bubble.update_message(ChatMessage(role="assistant", content=content, timestamp="now"))

        self.assertEqual(1, len(bubble.content_widget.code_blocks))
        self.assertFalse(bubble.copy_message_button.isHidden())

    def test_pending_status_updates_without_rebuilding_message_content(self) -> None:
        message = ChatMessage(
            role="assistant",
            content="Partial local reply",
            timestamp="now",
            metadata={"pending": True, "pending_label": "Waiting..."},
        )
        bubble = MessageBubble(message)
        content_widget = bubble.content_widget

        bubble.update_pending_status(
            "Still loading test-model... 18s - Stop is available",
            state="stalled",
            tooltip="Keep waiting or stop safely.",
        )

        self.assertIs(content_widget, bubble.content_widget)
        self.assertEqual("Partial local reply", bubble.body_label.text())
        self.assertEqual(
            "Still loading test-model... 18s - Stop is available",
            bubble.source_label.text(),
        )
        self.assertEqual("stalled", bubble.source_label.property("progressState"))
        self.assertEqual("Keep waiting or stop safely.", bubble.source_label.toolTip())

    def test_copy_message_preserves_original_markdown(self) -> None:
        content = "## Heading\n\n```python\nprint('ready')\n```"
        bubble = MessageBubble(ChatMessage(role="assistant", content=content, timestamp="now"))

        bubble.copy_message_button.click()

        self.assertEqual(content, QApplication.clipboard().text())

    def test_message_shows_automatic_model_route(self) -> None:
        bubble = MessageBubble(
            ChatMessage(
                role="assistant",
                content="Done.",
                timestamp="now",
                metadata={
                    "model_name": "qwen2.5-coder:7b",
                    "model_profile": "Coding",
                    "model_reason": "Code-related request",
                    "model_automatic": True,
                },
            )
        )

        self.assertEqual("Auto: Coding -> qwen2.5-coder:7b", bubble.source_label.text())
        self.assertEqual("Code-related request", bubble.source_label.toolTip())

    def test_message_shows_bounded_local_performance_summary_and_tooltip(self) -> None:
        bubble = MessageBubble(
            ChatMessage(
                role="assistant",
                content="Done.",
                timestamp="now",
                metadata={
                    "model_name": "qwen3.5:4b",
                    "model_reason": "Balanced local response",
                    "reply_elapsed_seconds": 5.2,
                    "reply_model_elapsed_seconds": 4.4,
                    "reply_time_to_first_token_seconds": 1.1,
                    "ollama_load_seconds": 0.6,
                    "ollama_prompt_tokens": 240,
                    "ollama_generated_tokens": 96,
                    "ollama_tokens_per_second": 24.75,
                },
            )
        )

        self.assertIn("4.4s local response", bubble.source_label.text())
        self.assertIn("24.8 tok/s", bubble.source_label.text())
        tooltip = bubble.source_label.toolTip()
        self.assertIn("Balanced local response", tooltip)
        self.assertIn("First token: 1.1s", tooltip)
        self.assertIn("Model load: 0.60s", tooltip)
        self.assertIn("Prompt tokens: 240", tooltip)
        self.assertIn("Generated tokens: 96", tooltip)

    def test_failed_reply_has_distinct_error_state_and_retry_action(self) -> None:
        bubble = MessageBubble(
            ChatMessage(
                role="assistant",
                content="A partial response",
                timestamp="now",
                metadata={"error": True, "error_message": "Ollama connection was lost."},
            )
        )
        actions: list[str] = []
        bubble.action_requested.connect(actions.append)

        self.assertEqual("error", bubble.property("messageState"))
        self.assertFalse(bubble.error_panel.isHidden())
        self.assertEqual("Ollama connection was lost.", bubble.error_detail_label.text())
        self.assertIn("Reply failed", bubble.source_label.text())

        bubble.retry_button.click()

        self.assertEqual(["retry"], actions)

        bubble.update_message(
            ChatMessage(
                role="assistant",
                content="",
                timestamp="now",
                metadata={"error": True, "error_message": "Model did not start."},
            )
        )
        self.assertTrue(bubble.content_widget.isHidden())
        self.assertFalse(bubble.error_panel.isHidden())

        bubble.update_message(
            ChatMessage(
                role="assistant",
                content="",
                timestamp="now",
                metadata={"error": True, "error_message": "x" * 2_500},
            )
        )
        self.assertEqual(2_000, len(bubble.error_detail_label.text()))

        bubble.set_actions(
            can_edit=False,
            can_regenerate=False,
            can_retry=False,
        )
        self.assertTrue(bubble.retry_button.isHidden())

    def test_interrupted_reply_has_distinct_recovery_state(self) -> None:
        bubble = MessageBubble(
            ChatMessage(
                role="assistant",
                content="Partial answer",
                timestamp="now",
                metadata={
                    "error": True,
                    "interrupted": True,
                    "error_message": "The app closed before this reply finished.",
                },
            )
        )

        self.assertEqual("interrupted", bubble.property("messageState"))
        self.assertEqual("interrupted", bubble.error_panel.property("recoveryState"))
        self.assertEqual("REPLY INTERRUPTED", bubble.error_title_label.text())
        self.assertIn("Reply interrupted", bubble.source_label.text())
        self.assertFalse(bubble.retry_button.isHidden())

    def test_unsaved_reply_exposes_retry_save_action(self) -> None:
        bubble = MessageBubble(
            ChatMessage(
                role="assistant",
                content="Completed answer",
                timestamp="now",
                metadata={
                    "error": True,
                    "save_error": True,
                    "error_message": "Disk is full.",
                },
            )
        )
        actions: list[str] = []
        bubble.action_requested.connect(actions.append)

        self.assertEqual("save_error", bubble.property("messageState"))
        self.assertEqual("save_error", bubble.error_panel.property("recoveryState"))
        self.assertEqual("HISTORY SAVE FAILED", bubble.error_title_label.text())
        self.assertEqual("Retry Save", bubble.retry_button.text())
        bubble.retry_button.click()

        self.assertEqual(["retry_save"], actions)

    def test_user_message_shows_attachment_summary_without_exposing_snapshot_content(self) -> None:
        bubble = MessageBubble(
            ChatMessage(
                role="user",
                content="Review this file",
                timestamp="now",
                metadata={
                    "attachments": [
                        {
                            "name": "main.py",
                            "size_bytes": 2048,
                            "content": "private snapshot content",
                            "truncated": False,
                        }
                    ]
                },
            )
        )

        self.assertFalse(bubble.attachments_label.isHidden())
        self.assertIn("main.py", bubble.attachments_label.text())
        self.assertIn("2.0 KB", bubble.attachments_label.text())
        self.assertNotIn("private snapshot content", bubble.attachments_label.text())

    def test_sent_image_attachment_shows_thumbnail(self) -> None:
        image = QImage(24, 16, QImage.Format.Format_RGB32)
        image.fill(QColor("blue"))
        payload = QByteArray()
        buffer = QBuffer(payload)
        self.assertTrue(buffer.open(QIODevice.OpenModeFlag.WriteOnly))
        self.assertTrue(image.save(buffer, "JPEG"))
        buffer.close()
        bubble = MessageBubble(
            ChatMessage(
                "user",
                "Describe it",
                "now",
                metadata={
                    "attachments": [
                        {
                            "name": "screen.png",
                            "size_bytes": 100,
                            "content": "Local image snapshot.",
                            "kind": "image",
                            "image_data": base64.b64encode(bytes(payload)).decode("ascii"),
                            "width": 24,
                            "height": 16,
                        }
                    ]
                },
            )
        )

        self.assertFalse(bubble.attachment_preview_host.isHidden())
        self.assertFalse(bubble.attachment_preview_labels[0].pixmap().isNull())
        self.assertIn("IMAGE", bubble.attachments_label.text())

    def test_message_revision_actions_are_explicit_and_emit_requests(self) -> None:
        bubble = MessageBubble(ChatMessage("user", "Revise me", "now"))
        actions: list[str] = []
        bubble.action_requested.connect(actions.append)

        bubble.set_actions(can_edit=True, can_regenerate=False)
        self.assertFalse(bubble.edit_message_button.isHidden())
        self.assertTrue(bubble.regenerate_button.isHidden())
        bubble.edit_message_button.click()

        bubble.set_actions(can_edit=False, can_regenerate=True)
        self.assertTrue(bubble.edit_message_button.isHidden())
        self.assertFalse(bubble.regenerate_button.isHidden())
        bubble.regenerate_button.click()

        self.assertEqual(["edit", "regenerate"], actions)


if __name__ == "__main__":
    unittest.main()
