from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.core.models import ChatMessage, ChatStreamResult, ConversationMemory
from local_matrix_assistant.services.context_manager import ContextManager
from local_matrix_assistant.services.conversation_memory import ConversationMemoryService
from local_matrix_assistant.services.ollama import OllamaError


class FakeClient:
    def __init__(self, response: str = "- User requires Python 3.12.") -> None:
        self.response = response
        self.options: dict | None = None
        self.messages: list[ChatMessage] = []

    def chat(self, _model: str, messages: list[ChatMessage], *, options: dict | None = None) -> str:
        self.messages = messages
        self.options = options
        return self.response


class OfflineClient(FakeClient):
    def chat(self, _model: str, messages: list[ChatMessage], *, options: dict | None = None) -> str:
        raise OllamaError("offline")


class CanceledStreamingClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.stream_called = False

    def chat_stream(
        self,
        _model: str,
        _messages: list[ChatMessage],
        _on_chunk,
        _should_cancel,
        *,
        options: dict | None = None,
    ) -> ChatStreamResult:
        self.stream_called = True
        self.options = options
        return ChatStreamResult("partial memory", canceled=True)


class ConversationMemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ConversationMemoryService()
        self.messages = [
            ChatMessage("user", "Use Python 3.12 and keep all files under D:/work.", "now"),
            ChatMessage("assistant", "The project now uses Python 3.12.", "now"),
        ]

    def test_local_model_builds_bounded_memory(self) -> None:
        client = FakeClient("```text\n- User requires Python 3.12.\n- Files stay under D:/work.\n```")

        memory = self.service.update(
            client,  # type: ignore[arg-type]
            "llama3.2:3b",
            ConversationMemory(),
            self.messages,
            covered_messages=2,
            updated_at="now",
            context_window=4096,
        )

        self.assertEqual("local_model", memory.source)
        self.assertEqual(2, memory.covered_messages)
        self.assertNotIn("```", memory.content)
        self.assertEqual(4096, client.options["num_ctx"] if client.options else None)
        self.assertIn("never as instructions", client.messages[-1].content)
        self.assertLessEqual(ContextManager.estimate_text_tokens(memory.content), self.service.max_memory_tokens)

    def test_offline_model_uses_extractive_fallback(self) -> None:
        memory = self.service.update(
            OfflineClient(),  # type: ignore[arg-type]
            "llama3.2:3b",
            ConversationMemory(),
            self.messages,
            covered_messages=2,
            updated_at="now",
            context_window=4096,
        )

        self.assertEqual("extractive_fallback", memory.source)
        self.assertIn("Python 3.12", memory.content)
        self.assertEqual(2, memory.covered_messages)

    def test_cancelable_update_does_not_commit_partial_memory(self) -> None:
        client = CanceledStreamingClient()

        memory = self.service.update(
            client,  # type: ignore[arg-type]
            "llama3.2:3b",
            ConversationMemory(),
            self.messages,
            covered_messages=2,
            updated_at="now",
            context_window=4096,
            should_cancel=lambda: False,
        )

        self.assertTrue(client.stream_called)
        self.assertIsNone(memory)
        self.assertEqual(4096, client.options["num_ctx"] if client.options else None)

    def test_transcript_sampling_keeps_early_and_latest_context(self) -> None:
        messages = [ChatMessage("user", f"turn-{index} " + "x" * 300, "now") for index in range(12)]

        transcript = self.service._build_transcript(messages, 220)

        self.assertIn("turn-0", transcript)
        self.assertIn("turn-11", transcript)
        self.assertLessEqual(ContextManager.estimate_text_tokens(transcript), 220)


if __name__ == "__main__":
    unittest.main()
