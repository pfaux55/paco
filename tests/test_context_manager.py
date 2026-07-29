from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.core.models import ChatMessage
from local_matrix_assistant.services.context_manager import ContextManager


def message(role: str, content: str) -> ChatMessage:
    return ChatMessage(role=role, content=content, timestamp="now")


class ContextManagerTests(unittest.TestCase):
    def test_short_conversation_is_retained_fully(self) -> None:
        messages = [message("user", "Hello"), message("assistant", "Hi there")]

        selection = ContextManager.select_recent_turns(messages, 500)

        self.assertEqual(messages, selection.messages)
        self.assertEqual(0, selection.stats.trimmed_messages)
        self.assertFalse(selection.stats.latest_message_truncated)

    def test_old_messages_are_omitted_as_complete_turns(self) -> None:
        messages: list[ChatMessage] = []
        for index in range(4):
            messages.extend(
                [
                    message("user", f"user-{index} " + "u" * 360),
                    message("assistant", f"assistant-{index} " + "a" * 360),
                ]
            )

        selection = ContextManager.select_recent_turns(messages, 240)

        self.assertGreater(selection.stats.trimmed_messages, 0)
        self.assertEqual("user", selection.messages[0].role)
        self.assertEqual(["user", "assistant"], [item.role for item in selection.messages])
        self.assertIn("user-3", selection.messages[0].content)
        self.assertLessEqual(selection.stats.estimated_tokens, selection.stats.token_budget)

    def test_oversized_latest_prompt_keeps_beginning_and_end(self) -> None:
        content = "BEGIN-SENTINEL " + "middle " * 1500 + " END-SENTINEL"

        selection = ContextManager.select_recent_turns([message("user", content)], 180)

        self.assertTrue(selection.stats.latest_message_truncated)
        self.assertIn("BEGIN-SENTINEL", selection.messages[0].content)
        self.assertIn("END-SENTINEL", selection.messages[0].content)
        self.assertIn("middle omitted", selection.messages[0].content)
        self.assertLessEqual(selection.stats.estimated_tokens, selection.stats.token_budget)

    def test_code_estimate_is_more_conservative_than_plain_text(self) -> None:
        plain = "x" * 620
        code = "def function(): { return value; } " + "x" * (620 - 34)

        self.assertGreater(ContextManager.estimate_text_tokens(code), ContextManager.estimate_text_tokens(plain))

    def test_image_attachment_reserves_vision_context_tokens(self) -> None:
        plain = message("user", "Describe this image")
        image = ChatMessage(
            "user",
            "Describe this image",
            "now",
            metadata={"attachments": [{"name": "image.jpg", "image_data": "encoded"}]},
        )

        self.assertEqual(
            ContextManager.estimate_message_tokens(plain) + 1024,
            ContextManager.estimate_message_tokens(image),
        )

    def test_note_reports_adjustments(self) -> None:
        messages = [message("user", "x" * 3000)]
        stats = ContextManager.select_recent_turns(messages, 150).stats

        note = stats.note()

        self.assertIn("input tokens", note)
        self.assertIn("newest message shortened", note)

    def test_note_distinguishes_summarized_memory_from_omitted_messages(self) -> None:
        stats = ContextManager.select_recent_turns([message("user", "hello")], 150).stats
        stats = type(stats)(
            total_messages=5,
            retained_messages=1,
            trimmed_messages=4,
            estimated_tokens=80,
            token_budget=150,
            memory_messages=4,
            memory_tokens=40,
        )

        self.assertIn("4 older messages summarized", stats.note())
        self.assertNotIn("omitted", stats.note())


if __name__ == "__main__":
    unittest.main()
