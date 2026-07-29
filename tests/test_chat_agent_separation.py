from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.ui.main_window_chat import ChatWindowMixin


class FakeInput:
    def __init__(self, text: str) -> None:
        self.text = text

    def toPlainText(self) -> str:
        return self.text

    def clear(self) -> None:
        self.text = ""


class FakeCombo:
    def __init__(self, text: str) -> None:
        self.text = text

    def currentText(self) -> str:
        return self.text


class ChatHarness(ChatWindowMixin):
    def __init__(self, text: str, model: str) -> None:
        self._awaiting_response = False
        self.chat_panel = type("FakeChatPanel", (), {"input_box": FakeInput(text)})()
        self.settings_panel = type("FakeSettingsPanel", (), {"model_combo": FakeCombo(model)})()
        self.config = type("FakeConfig", (), {"ollama_model": model})()
        self.messages: list[tuple[str, str]] = []
        self.requests: list[tuple[str, str]] = []
        self.activities: list[str] = []

    def _append_message(self, role: str, content: str, metadata=None):
        self.messages.append((role, content))

    def _enable_web_search_if_requested(self, _text: str):
        return None

    def _begin_assistant_response(self, model: str, text: str) -> None:
        self.requests.append((model, text))

    def _set_activity(self, text: str) -> None:
        self.activities.append(text)


class ChatAgentSeparationTests(unittest.TestCase):
    def test_file_command_in_chat_is_sent_as_conversation(self) -> None:
        harness = ChatHarness("create file chat.txt", "test-model")

        harness._send_from_input()

        self.assertEqual([("user", "create file chat.txt")], harness.messages)
        self.assertEqual([("test-model", "create file chat.txt")], harness.requests)

    def test_missing_model_preserves_unsent_chat_input(self) -> None:
        harness = ChatHarness("hello", "")

        harness._send_from_input()

        self.assertEqual([], harness.messages)
        self.assertEqual("hello", harness.chat_panel.input_box.toPlainText())
        self.assertIn("No Ollama model", harness.activities[-1])


if __name__ == "__main__":
    unittest.main()
