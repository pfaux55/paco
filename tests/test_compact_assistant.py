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

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QCloseEvent, QColor, QImage, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from local_matrix_assistant import compact_app
from local_matrix_assistant.core.config import AppConfig
from local_matrix_assistant.core.models import ChatStreamResult
from local_matrix_assistant.services.ollama import OllamaClient, OllamaStatus
from local_matrix_assistant.ui.compact_assistant import (
    COMPACT_SCREEN_MARGIN,
    SCREEN_CAPTURE_DELAY_MS,
    CompactAssistantWindow,
    compact_geometry_for,
)
from local_matrix_assistant.ui.theme import stylesheet_for_theme


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


class DeferredStreamRunner:
    def __init__(self) -> None:
        self.worker = None
        self.on_chunk = None
        self.on_result = None
        self.on_error = None

    def start_stream(self, worker, on_chunk, on_result, on_error) -> None:
        self.worker = worker
        self.on_chunk = on_chunk
        self.on_result = on_result
        self.on_error = on_error

    def close(self) -> None:
        pass


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

    def test_overlay_is_transparent_frameless_and_starts_as_an_input_bar(self) -> None:
        window = self.build_window()

        self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertTrue(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertEqual("compactInputBar", window.input_bar.objectName())
        self.assertTrue(window.transcript_scroll.isHidden())
        self.assertFalse(hasattr(window, "send_stop_button"))
        self.assertLessEqual(window.height(), 64)
        window.close()

    def test_small_main_app_button_requests_full_mode(self) -> None:
        window = self.build_window()
        requests: list[bool] = []
        window.main_mode_requested.connect(lambda: requests.append(True))

        window.main_mode_button.click()

        self.assertEqual([True], requests)
        self.assertEqual(
            (24, 30),
            (window.main_mode_button.width(), window.main_mode_button.height()),
        )
        self.assertEqual("Main app", window.main_mode_button.toolTip())
        window.close()

    def test_ctrl_c_and_ctrl_v_copy_and_paste_in_compact_prompt(self) -> None:
        window = self.build_window()
        window.show()
        window.prompt_input.setText("copy compact text")
        window.prompt_input.selectAll()
        window.prompt_input.setFocus()

        QTest.keyClick(window.prompt_input, Qt.Key_C, Qt.ControlModifier)
        self.assertEqual("copy compact text", QApplication.clipboard().text())

        window.prompt_input.clear()
        QApplication.clipboard().setText("paste compact text")
        QTest.keyClick(window.prompt_input, Qt.Key_V, Qt.ControlModifier)
        self.assertEqual("paste compact text", window.prompt_input.text())
        window.close()

    def test_compact_message_has_working_copy_action(self) -> None:
        window = self.build_window()
        body = window._append_message_view("assistant", "copy compact response")
        copy_button = body.parentWidget().findChild(QPushButton, "messageCopyButton")

        self.assertIsNotNone(copy_button)
        copy_button.click()

        self.assertEqual("copy compact response", QApplication.clipboard().text())
        self.assertEqual("Copied", copy_button.text())
        window.close()
        window.deleteLater()
        QApplication.processEvents()
        QApplication.clipboard().clear()

    def test_close_button_requests_process_exit(self) -> None:
        window = self.build_window()
        requests: list[bool] = []
        window.exit_requested.connect(lambda: requests.append(True))

        window.close_button.click()

        self.assertEqual([True], requests)

    def test_programmatic_close_for_mode_switch_does_not_request_exit(self) -> None:
        window = self.build_window()
        requests: list[bool] = []
        window.exit_requested.connect(lambda: requests.append(True))

        window.close()

        self.assertEqual([], requests)

    def test_target_geometry_is_bottom_right_and_bounded_across_work_areas(self) -> None:
        cases = (
            (QRect(100, 50, 1920, 1040), (346, 416)),
            (QRect(-800, 20, 800, 600), (320, 300)),
            (QRect(0, 0, 3840, 2160), (560, 520)),
        )
        for available, expected_size in cases:
            with self.subTest(available=available):
                geometry = compact_geometry_for(available)
                self.assertEqual(expected_size, (geometry.width(), geometry.height()))
                self.assertEqual(COMPACT_SCREEN_MARGIN, available.right() - geometry.right())
                self.assertEqual(COMPACT_SCREEN_MARGIN, available.bottom() - geometry.bottom())

    def test_every_scrollbar_uses_the_compact_slim_rounded_style(self) -> None:
        stylesheet = stylesheet_for_theme("matrix")

        self.assertIn("QScrollBar:vertical", stylesheet)
        self.assertIn("QScrollBar:horizontal", stylesheet)
        self.assertNotIn("QScrollArea#compactTranscript QScrollBar", stylesheet)
        self.assertIn("border-radius: 4px", stylesheet)
        self.assertIn("width: 8px", stylesheet)
        self.assertIn("height: 8px", stylesheet)

    def test_compact_mode_uses_saved_font_family_and_size(self) -> None:
        config = build_config()
        config.chat_font_family = "Arial"
        config.chat_font_size = 18
        window = CompactAssistantWindow(
            config,
            ollama_client=RecordingOllama(),
            start_status_check=False,
        )

        self.assertIn('font-family: "Arial";', window.styleSheet())
        self.assertIn("font-size: 18pt;", window.styleSheet())
        window.close()

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

    def test_capture_button_waits_for_desktop_repaint_before_grabbing_screen(self) -> None:
        window = self.build_window()
        window.show()
        self.app.processEvents()
        screen = FakeScreen(window, QRect(0, 0, 1280, 720))

        with patch(
            "local_matrix_assistant.ui.compact_assistant.screen_under_cursor",
            return_value=screen,
        ):
            window.capture_button.click()
            self.assertTrue(window._capture_in_progress)
            self.assertFalse(window.isVisible())
            self.assertIsNone(window.pending_screenshot)
            QTest.qWait(SCREEN_CAPTURE_DELAY_MS + 50)

        self.assertFalse(window._capture_in_progress)
        self.assertTrue(screen.hidden_during_capture)
        self.assertTrue(window.isVisible())
        self.assertIsNotNone(window.pending_screenshot)
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

    def test_hidden_or_inactive_window_keeps_reply_running(self) -> None:
        window = self.build_window()
        window.available_ollama_models = ["gemma3:1b"]
        runner = DeferredStreamRunner()
        window.task_runner = runner

        self.assertTrue(window.submit_prompt("Keep working in the background"))
        assert runner.worker is not None
        window.hide()
        QApplication.sendEvent(window, QEvent(QEvent.Type.WindowDeactivate))

        self.assertFalse(runner.worker.is_cancelled())
        assert runner.on_chunk is not None
        assert runner.on_result is not None
        result = runner.worker.fn(runner.on_chunk, runner.worker.is_cancelled)
        runner.on_result(result)

        self.assertFalse(window.is_busy)
        self.assertEqual("Local answer", window.messages[-1].content)
        self.assertFalse(window.messages[-1].metadata.get("canceled", False))
        window.close()


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

            def installEventFilter(self, _filter) -> None:  # noqa: N802
                pass

            def windowIcon(self):  # noqa: N802
                return self.icon

            def quit(self) -> None:
                pass

            def exec(self) -> int:
                return 17

        window = Mock()
        main_window = Mock()
        with (
            patch.object(compact_app.AppPaths, "create", return_value=object()),
            patch.object(compact_app.AppConfig, "load", return_value=config),
            patch.object(compact_app, "QApplication", FakeApplication),
            patch.object(compact_app, "CompactAssistantWindow", return_value=window) as compact_window,
            patch.object(compact_app, "MainWindow", return_value=main_window),
            patch.object(compact_app, "configure_windows_app_identity"),
            patch.object(compact_app, "paco_icon", return_value=object()),
            patch.object(compact_app, "QFont", return_value=object()),
            patch.object(compact_app.QTimer, "singleShot", side_effect=lambda _delay, callback: callback()),
            patch.object(compact_app, "apply_windows_window_icon"),
        ):
            result = compact_app.main()
            show_main_mode = window.main_mode_requested.connect.call_args.args[0]
            show_main_mode()

        self.assertEqual(17, result)
        compact_window.assert_called_once_with(config)
        window.exit_requested.connect.assert_called_once()
        window.show.assert_called_once_with()
        window.hide.assert_called_once_with()
        window.close.assert_not_called()
        main_window.show.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
