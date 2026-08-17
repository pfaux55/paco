from __future__ import annotations

import base64
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import QRect
from PySide6.QtGui import QCloseEvent, QColor, QImage, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from local_matrix_assistant import compact_app
from local_matrix_assistant.core.config import AppConfig
from local_matrix_assistant.core.models import ChatStreamResult
from local_matrix_assistant.services.ollama import OllamaClient, OllamaStatus
from local_matrix_assistant.ui.compact_assistant import (
    COMPACT_SCREEN_MARGIN,
    CompactAssistantWindow,
    compact_geometry_for,
)


def build_config(*, model: str = "") -> AppConfig:
    return AppConfig(
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model=model,
        ollama_windows_path="",
        stt_model_dir="models/stt",
        tts_model_path="models/tts/voice.onnx",
        tts_config_path="models/tts/voice.onnx.json",
        voice_enabled=False,
        auto_speak_responses=False,
        tts_rate=1.0,
        tts_volume=1.0,
        preferred_input_name="",
        playback_output_name="",
        web_search_enabled=False,
        working_folders=[],
        active_working_folder="",
        model_profile="auto",
        theme="matrix",
    )


class FakeScreen:
    def __init__(self, window: CompactAssistantWindow, geometry: QRect) -> None:
        self.window = window
        self.geometry = geometry
        self.hidden_during_capture = False

    def availableGeometry(self) -> QRect:  # noqa: N802
        return self.geometry

    def grabWindow(self, _window_id: int) -> QPixmap:  # noqa: N802
        self.hidden_during_capture = not self.window.isVisible()
        image = QImage(640, 360, QImage.Format.Format_ARGB32)
        image.fill(QColor("#24e081"))
        return QPixmap.fromImage(image)


class RecordingOllama:
    def __init__(self, *, error: str = "", wait_for_cancel: bool = False) -> None:
        self.error = error
        self.wait_for_cancel = wait_for_cancel
        self.payloads: list[dict] = []

    def status(self) -> OllamaStatus:
        return OllamaStatus(True, "Connected", ["gemma3:1b", "qwen3.5:4b"])

    def chat_stream(self, model, messages, on_chunk, should_cancel, *, options=None):
        self.payloads.append(OllamaClient._build_payload(model, messages, True, options))
        if self.error:
            raise RuntimeError(self.error)
        if self.wait_for_cancel:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not should_cancel():
                time.sleep(0.005)
            return ChatStreamResult(content="", canceled=should_cancel())
        on_chunk("Local ")
        on_chunk("answer")
        return ChatStreamResult(content="Local answer")


class CompactAssistantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def wait_until_idle(self, window: CompactAssistantWindow) -> None:
        deadline = time.monotonic() + 3
        while window.is_busy and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(window.is_busy)

    def build_window(self, client=None) -> CompactAssistantWindow:
        return CompactAssistantWindow(
            build_config(),
            ollama_client=client or RecordingOllama(),
            start_status_check=False,
        )

    def test_target_geometry_is_bottom_right_and_bounded_across_work_areas(self) -> None:
        cases = (
            (QRect(100, 50, 1920, 1040), (346, 187)),
            (QRect(-800, 20, 800, 600), (320, 180)),
            (QRect(0, 0, 3840, 2160), (560, 389)),
        )
        for available, expected_size in cases:
            with self.subTest(available=available):
                geometry = compact_geometry_for(available)
                self.assertEqual(expected_size, (geometry.width(), geometry.height()))
                self.assertEqual(COMPACT_SCREEN_MARGIN, available.right() - geometry.right())
                self.assertEqual(COMPACT_SCREEN_MARGIN, available.bottom() - geometry.bottom())

    def test_capture_uses_cursor_screen_hides_overlay_and_is_consumed_once(self) -> None:
        client = RecordingOllama()
        window = self.build_window(client)
        window.available_ollama_models = ["qwen3.5:4b"]
        window.show()
        self.app.processEvents()
        screen = FakeScreen(window, QRect(0, 0, 1280, 720))

        with patch(
            "local_matrix_assistant.ui.compact_assistant.screen_under_cursor",
            return_value=screen,
        ):
            attachment = window.capture_screen()

        self.assertIsNotNone(attachment)
        self.assertTrue(screen.hidden_during_capture)
        self.assertTrue(window.isVisible())
        self.assertEqual("image", attachment.kind)  # type: ignore[union-attr]
        encoded = base64.b64decode(attachment.image_data)  # type: ignore[union-attr]
        self.assertFalse(QImage.fromData(encoded, "JPEG").isNull())

        self.assertTrue(window.submit_prompt("What is visible?"))
        self.assertIsNone(window.pending_screenshot)
        self.wait_until_idle(window)
        self.assertIn("images", client.payloads[0]["messages"][-1])
        self.assertFalse(any(message.metadata.get("attachments") for message in window.messages))
        window.close()

    def test_text_uses_standard_routing_and_missing_vision_model_shows_guidance(self) -> None:
        client = RecordingOllama()
        window = self.build_window(client)
        window.available_ollama_models = ["gemma3:1b"]

        self.assertTrue(window.submit_prompt("Hello"))
        self.wait_until_idle(window)
        self.assertNotIn("images", client.payloads[0]["messages"][-1])
        self.assertEqual("gemma3:1b", client.payloads[0]["model"])

        image = QImage(80, 40, QImage.Format.Format_ARGB32)
        image.fill(QColor("blue"))
        window._pending_screenshot = window.attachment_service.load_clipboard_image(image)
        self.assertFalse(window.submit_prompt("Read this screen"))
        self.assertIn("vision model", window.status_label.text())
        self.assertIsNotNone(window.pending_screenshot)
        window.close()

    def test_stream_cancel_error_and_exit_are_session_only(self) -> None:
        cancel_client = RecordingOllama(wait_for_cancel=True)
        window = self.build_window(cancel_client)
        window.available_ollama_models = ["gemma3:1b"]
        self.assertTrue(window.submit_prompt("Keep working"))
        QTest.qWait(20)
        self.assertTrue(window.cancel_reply())
        self.wait_until_idle(window)
        self.assertTrue(window.messages[-1].metadata.get("canceled"))
        self.assertIn("canceled", window.status_label.text().lower())

        image = QImage(40, 20, QImage.Format.Format_ARGB32)
        image.fill(QColor("red"))
        window._pending_screenshot = window.attachment_service.load_clipboard_image(image)
        window.closeEvent(QCloseEvent())
        self.assertEqual([], window.messages)
        self.assertIsNone(window.pending_screenshot)

        error_window = self.build_window(RecordingOllama(error="model failed"))
        error_window.available_ollama_models = ["gemma3:1b"]
        self.assertTrue(error_window.submit_prompt("Fail"))
        self.wait_until_idle(error_window)
        self.assertIn("model failed", error_window.messages[-1].content)
        self.assertIn("model failed", error_window.status_label.text())
        error_window.close()


class CompactLauncherTests(unittest.TestCase):
    def test_launcher_constructs_only_the_compact_window(self) -> None:
        config = build_config()

        class FakeApplication:
            def __init__(self, _argv) -> None:
                self.icon = object()

            def setApplicationName(self, _name) -> None:  # noqa: N802
                pass

            def setWindowIcon(self, _icon) -> None:  # noqa: N802
                pass

            def setStyleSheet(self, _style) -> None:  # noqa: N802
                pass

            def setFont(self, _font) -> None:  # noqa: N802
                pass

            def windowIcon(self):  # noqa: N802
                return self.icon

            def exec(self) -> int:
                return 17

        window = Mock()
        with (
            patch.object(compact_app.AppPaths, "create", return_value=object()),
            patch.object(compact_app.AppConfig, "load", return_value=config),
            patch.object(compact_app, "QApplication", FakeApplication),
            patch.object(compact_app, "CompactAssistantWindow", return_value=window) as compact_window,
            patch.object(compact_app, "configure_windows_app_identity"),
            patch.object(compact_app, "paco_icon", return_value=object()),
            patch.object(compact_app, "QFont", return_value=object()),
            patch.object(compact_app.QTimer, "singleShot", side_effect=lambda _delay, callback: callback()),
            patch.object(compact_app, "apply_windows_window_icon"),
        ):
            result = compact_app.main()

        self.assertEqual(17, result)
        compact_window.assert_called_once_with(config)
        window.show.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
