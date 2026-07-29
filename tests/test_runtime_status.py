from __future__ import annotations

import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.services.runtime_status import RuntimeStatusService


class FakeOllamaClient:
    def __init__(self, connected: bool, models: list[str], message: str) -> None:
        self._status = type("Status", (), {"connected": connected, "models": models, "message": message})()

    def status(self):
        return self._status


class FakeSttService:
    def __init__(self, ready: bool, message: str = "Ready") -> None:
        self._ready = ready
        self._message = message

    def ready(self):
        return self._ready, self._message

    def load(self) -> None:
        if not self._ready:
            raise FileNotFoundError(self._message)


class FakeTtsService(FakeSttService):
    pass


class FakeRecorder:
    def __init__(self, ready: bool, message: str) -> None:
        self._ready = ready
        self._message = message

    def has_input(self):
        return self._ready, self._message


class FakePlayer:
    def __init__(self, ready: bool, message: str) -> None:
        self._ready = ready
        self._message = message

    def has_output(self):
        return self._ready, self._message


class RuntimeStatusTests(unittest.TestCase):
    def test_offline_guidance_prioritizes_ollama(self) -> None:
        service = RuntimeStatusService(
            FakeOllamaClient(False, [], "Offline"),
            FakeSttService(True),
            FakeTtsService(True),
            FakeRecorder(True, "Using Mic"),
            FakePlayer(True, "Using Speakers"),
        )

        snapshot = service.build_snapshot("gemma3:1b")
        self.assertFalse(snapshot.ollama_connected)
        self.assertIn("Start Ollama locally", snapshot.guidance_message)

    def test_missing_voice_models_guidance(self) -> None:
        service = RuntimeStatusService(
            FakeOllamaClient(True, ["gemma3:1b"], "Connected"),
            FakeSttService(False, "Missing STT"),
            FakeTtsService(True),
            FakeRecorder(True, "Using Mic"),
            FakePlayer(True, "Using Speakers"),
        )

        snapshot = service.build_snapshot("gemma3:1b")
        self.assertIn("Download the local STT/TTS models", snapshot.guidance_message)

    def test_audio_probe_failures_become_actionable_status_instead_of_aborting_refresh(self) -> None:
        class BrokenRecorder:
            @staticmethod
            def has_input():
                raise RuntimeError("microphone service unavailable")

        class BrokenPlayer:
            @staticmethod
            def has_output():
                raise RuntimeError("speaker service unavailable")

        service = RuntimeStatusService(
            FakeOllamaClient(True, ["gemma3:1b"], "Connected"),
            FakeSttService(True),
            FakeTtsService(True),
            BrokenRecorder(),  # type: ignore[arg-type]
            BrokenPlayer(),  # type: ignore[arg-type]
        )

        snapshot = service.build_snapshot("gemma3:1b")

        self.assertFalse(snapshot.mic_available)
        self.assertFalse(snapshot.output_available)
        self.assertIn("microphone service unavailable", snapshot.mic_message)
        self.assertIn("speaker service unavailable", snapshot.output_message)


if __name__ == "__main__":
    unittest.main()
