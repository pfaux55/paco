from __future__ import annotations

import os
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QFileDialog
from PySide6.QtTest import QTest

from local_matrix_assistant.core.config import AppConfig, AppPaths
from local_matrix_assistant.core.models import (
    ChatMessage,
    ChatStreamResult,
    ConversationMemory,
    ModelPullProgress,
    ModelPullResult,
    StatusSnapshot,
    WebSearchResponse,
)
from local_matrix_assistant.services.audio import AudioPlayer, AudioRecorder
from local_matrix_assistant.services.agent_permissions import READ_ONLY_ACCESS, STANDARD_ACCESS
from local_matrix_assistant.services.history import HistoryStore
from local_matrix_assistant.services.context_manager import ContextStats
from local_matrix_assistant.services.model_router import ModelSelection
from local_matrix_assistant.services.ollama import OllamaClient
from local_matrix_assistant.ui.main_window import MainWindow


class ImmediateTaskRunner:
    @staticmethod
    def start(worker, on_result, on_error) -> None:
        try:
            on_result(worker.fn())
        except Exception as exc:  # noqa: BLE001
            on_error(str(exc))

    def close(self) -> None:
        pass

    @staticmethod
    def start_stream(worker, on_chunk, on_result, on_error) -> None:
        try:
            on_result(worker.fn(on_chunk, worker.is_cancelled))
        except Exception as exc:  # noqa: BLE001
            on_error(str(exc))

    def wait_for_done(self, _timeout_ms: int) -> None:
        pass


class DeferredTaskRunner:
    def __init__(self) -> None:
        self.worker = None
        self.on_chunk = None
        self.on_result = None
        self.on_error = None

    def start(self, worker, on_result, on_error) -> None:
        self.worker = worker
        self.on_result = on_result
        self.on_error = on_error

    def start_stream(self, worker, on_chunk, on_result, on_error) -> None:
        self.worker = worker
        self.on_chunk = on_chunk
        self.on_result = on_result
        self.on_error = on_error

    def close(self) -> None:
        pass

    def wait_for_done(self, _timeout_ms: int) -> None:
        pass


def build_paths(root: Path) -> AppPaths:
    paths = AppPaths(
        root=root,
        data_dir=root / "data",
        models_dir=root / "models",
        stt_dir=root / "models" / "stt",
        tts_dir=root / "models" / "tts",
        cache_dir=root / "cache",
        chats_dir=root / "data" / "chats",
        history_file=root / "data" / "history.json",
        settings_file=root / "data" / "settings.json",
    )
    for folder in (paths.data_dir, paths.models_dir, paths.stt_dir, paths.tts_dir, paths.cache_dir, paths.chats_dir):
        folder.mkdir(parents=True, exist_ok=True)
    return paths


def wait_until(predicate, timeout_ms: int = 1500) -> bool:
    elapsed = 0
    while elapsed < timeout_ms:
        if predicate():
            return True
        QTest.qWait(10)
        elapsed += 10
    return predicate()


class MainWindowIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_navigation_agent_execution_chat_separation_and_folder_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            working_folder = root / "work"
            working_folder.mkdir()
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"
            config.working_folders = [str(working_folder)]
            config.active_working_folder = str(working_folder)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            window.task_runner = ImmediateTaskRunner()
            self.assertEqual(4, window.page_stack.count())
            self.assertEqual("Agent", window.agent_nav_button.text())

            window.agent_nav_button.click()
            self.assertEqual(1, window.page_stack.currentIndex())
            message_count = len(window.messages)
            window.agent_panel.command_input.setPlainText("create file agent.txt with content created")
            window._run_agent_command()
            self.assertEqual("created", (working_folder / "agent.txt").read_text(encoding="utf-8"))
            self.assertEqual(message_count, len(window.messages))

            window.ollama_client.chat_stream = (  # type: ignore[method-assign]
                lambda _model, _messages, _on_chunk, _should_cancel: ChatStreamResult(
                    "## Overview\nA generated document body."
                )
            )
            window.agent_panel.command_input.setPlainText("create a word document outlining open source models")
            window._run_agent_command()
            word_path = working_folder / "open-source-models.docx"
            self.assertTrue(word_path.exists())
            with ZipFile(word_path) as archive:
                self.assertIn("A generated document body.", archive.read("word/document.xml").decode("utf-8"))
            word_card = window.agent_panel.task_timeline.cards[-1]
            self.assertEqual(str(word_path), word_card.artifact_path)
            opened_artifacts: list[tuple[str, str]] = []
            window.desktop_action_service.open_artifact_file = (  # type: ignore[method-assign]
                lambda path: opened_artifacts.append(("file", path)) or Path(path)
            )
            window.desktop_action_service.open_artifact_folder = (  # type: ignore[method-assign]
                lambda path: opened_artifacts.append(("folder", path)) or Path(path).parent
            )
            word_card.open_file_button.click()
            word_card.open_folder_button.click()
            self.assertEqual(
                [("file", str(word_path)), ("folder", str(word_path))],
                opened_artifacts,
            )
            self.assertEqual(message_count, len(window.messages))

            chat_requests: list[tuple[str, str]] = []
            window._begin_assistant_response = lambda model, text: chat_requests.append((model, text))  # type: ignore[method-assign]
            window.chat_nav_button.click()
            window.chat_panel.input_box.setPlainText("create file chat-only.txt")
            window._send_from_input()
            self.assertEqual([("test-model", "create file chat-only.txt")], chat_requests)
            self.assertFalse((working_folder / "chat-only.txt").exists())
            self.assertEqual(message_count + 1, len(window.messages))

            second_folder = root / "second"
            second_folder.mkdir()
            window.agent_nav_button.click()
            window._on_agent_folder_selected([str(second_folder)])
            self.assertEqual([str(second_folder)], window.config.working_folders)
            self.assertEqual(str(second_folder), window.config.active_working_folder)
            self.assertEqual(str(second_folder), window.agent_panel.active_folder_label.text())
            self.assertEqual("Choose Folder", window.agent_panel.choose_folder_button.text())
            self.assertFalse(hasattr(window.settings_panel, "working_folders_list"))
            self.assertEqual(1, window.page_stack.currentIndex())
            original_command = next(
                card
                for card in window.agent_panel.task_timeline.cards
                if card.role == "Command" and card.full_text.startswith("create file agent.txt")
            )
            original_command.reuse_command_button.click()
            self.assertEqual("", window.agent_panel.command_input.toPlainText())
            self.assertIn(
                "switch folders before reusing",
                window.agent_panel.status_panel.status_label.text(),
            )
            window.close()

    def test_agent_access_mode_persists_per_workspace_and_restores_on_folder_switch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            config = AppConfig.defaults(paths)
            config.working_folders = [str(first)]
            config.active_working_folder = str(first)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)
                read_only_index = window.agent_panel.permission_mode_combo.findData(READ_ONLY_ACCESS)
                window.agent_panel.permission_mode_combo.setCurrentIndex(read_only_index)

                self.assertEqual(READ_ONLY_ACCESS, window._agent_permission_mode)
                self.assertEqual(READ_ONLY_ACCESS, window.agent_permission_store.mode_for(first))

                window._on_agent_folder_selected([str(second)])
                self.assertEqual(STANDARD_ACCESS, window._agent_permission_mode)
                self.assertEqual(STANDARD_ACCESS, window.agent_panel.permission_mode_combo.currentData())

                window._on_agent_folder_selected([str(first)])
                self.assertEqual(READ_ONLY_ACCESS, window._agent_permission_mode)
                self.assertEqual(READ_ONLY_ACCESS, window.agent_panel.permission_mode_combo.currentData())
                window.close()

                restored_config = AppConfig.load(paths)
                restored = MainWindow(paths, restored_config)
                self.assertEqual(READ_ONLY_ACCESS, restored._agent_permission_mode)
                self.assertEqual(READ_ONLY_ACCESS, restored.agent_panel.permission_mode_combo.currentData())
                restored.close()

    def test_settings_installs_model_with_progress_and_selects_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            refresh_calls: list[bool] = []

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            window.task_runner = ImmediateTaskRunner()
            window.refresh_status = lambda: refresh_calls.append(True)  # type: ignore[method-assign]
            window.available_ollama_models = []
            window.settings_panel.set_installed_models([])
            window.settings_panel.model_install_combo.setCurrentIndex(
                window.settings_panel.model_install_combo.findData("qwen3.5:4b")
            )

            def pull_model(model_name, on_progress, _should_cancel):
                on_progress(ModelPullProgress(model_name, "downloading layer", 75, 100))
                on_progress(ModelPullProgress(model_name, "success"))
                return ModelPullResult(model_name)

            window.ollama_client.pull_model = pull_model  # type: ignore[method-assign]
            window._start_model_install()

            self.assertEqual("qwen3.5:4b", window.config.ollama_model)
            self.assertEqual("Installed", window.settings_panel.model_install_button.text())
            self.assertEqual(100, window.settings_panel.model_install_progress.value())
            self.assertEqual([True], refresh_calls)
            self.assertIsNone(window._active_model_pull_worker)
            window.close()

    def test_settings_can_cancel_an_active_model_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            runner = DeferredTaskRunner()
            window.task_runner = runner
            window.settings_panel.model_install_combo.setCurrentIndex(
                window.settings_panel.model_install_combo.findData("llama3.2:3b")
            )
            window.settings_panel.model_install_button.click()

            self.assertIsNotNone(runner.worker)
            self.assertFalse(runner.worker.is_cancelled())
            window.settings_panel.model_cancel_button.click()
            self.assertTrue(runner.worker.is_cancelled())
            self.assertFalse(window.settings_panel.model_cancel_button.isEnabled())
            self.assertIn("Canceling", window.settings_panel.model_install_status.text())

            assert runner.on_result is not None
            runner.on_result(ModelPullResult("llama3.2:3b", canceled=True))
            self.assertIsNone(window._active_model_pull_worker)
            self.assertTrue(window.settings_panel.model_install_combo.isEnabled())
            self.assertIn("Canceled", window.settings_panel.model_install_status.text())
            window.close()

    def test_chat_deletion_requires_a_time_limited_second_click_and_escape_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            deleted_id = window.active_conversation_id
            deleted_path = paths.chats_dir / f"{deleted_id}.json"
            self.assertTrue(deleted_path.exists())
            window.chat_panel.input_box.setPlainText("Draft that belongs to the deleted chat")
            window._chat_draft_save_timer.timeout.emit()
            self.assertIn(deleted_id, window.config.chat_drafts)

            window.delete_chat_button.click()

            self.assertTrue(deleted_path.exists())
            self.assertEqual(deleted_id, window._delete_confirmation_conversation_id)
            self.assertTrue(window._delete_confirmation_timer.isActive())
            self.assertEqual("Confirm Delete", window.delete_chat_button.text())
            self.assertEqual("sidebarDangerButton", window.delete_chat_button.objectName())

            window._handle_escape_shortcut()

            self.assertTrue(deleted_path.exists())
            self.assertEqual("", window._delete_confirmation_conversation_id)
            self.assertFalse(window._delete_confirmation_timer.isActive())
            self.assertEqual("Delete Chat", window.delete_chat_button.text())
            self.assertIn("deletion canceled", window.chat_panel.status_panel.status_label.text().lower())

            window.delete_chat_button.click()
            window._delete_confirmation_timer.timeout.emit()

            self.assertTrue(deleted_path.exists())
            self.assertEqual("", window._delete_confirmation_conversation_id)
            self.assertEqual("Delete Chat", window.delete_chat_button.text())

            window.delete_chat_button.click()
            window.delete_chat_button.click()

            self.assertFalse(deleted_path.exists())
            self.assertNotIn(deleted_id, window.config.chat_drafts)
            self.assertNotEqual(deleted_id, window.active_conversation_id)
            self.assertEqual("Delete Chat", window.delete_chat_button.text())
            self.assertFalse(window._delete_confirmation_timer.isActive())
            window.close()

    def test_settings_write_failure_keeps_session_change_and_reports_it_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            with patch.object(AppConfig, "save", side_effect=OSError("disk full")):
                window._on_web_search_toggled(True)

            self.assertTrue(window.config.web_search_enabled)
            self.assertTrue(window.chat_panel.web_search_button.isChecked())
            self.assertIn("applied for this session", window.chat_panel.status_panel.status_label.text())
            self.assertIn("disk full", window.chat_panel.status_panel.status_label.text())
            self.assertTrue(window._settings_save_pending)
            self.assertEqual("settings_unsaved", window.system_notice.notice_key)
            self.assertEqual("Retry Save", window.system_notice.action_button.text())
            self.assertTrue(window.system_notice.dismiss_button.isHidden())

            window._on_status_error("status timeout")
            self.assertEqual("settings_unsaved", window.system_notice.notice_key)

            window.system_notice.action_button.click()

            self.assertFalse(window._settings_save_pending)
            self.assertTrue(window.system_notice.isHidden())
            self.assertTrue(paths.settings_file.exists())
            window.close()

    def test_web_search_preparation_can_be_canceled_without_starting_ollama(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"
            config.web_search_enabled = True

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            class CancelAwareSearch:
                @staticmethod
                def search(query: str, *, should_cancel) -> WebSearchResponse:
                    return WebSearchResponse(
                        provider="test",
                        query=query,
                        results=[],
                        canceled=should_cancel(),
                    )

            runner = DeferredTaskRunner()
            window.task_runner = runner
            window.web_search_service = CancelAwareSearch()
            window._append_message("user", "Search for current local model news")

            window._begin_assistant_response("test-model", window.messages[-1].content)

            self.assertEqual("web_search", window._active_reply_stage)
            self.assertTrue(window.chat_panel.cancel_button.isEnabled())
            self.assertIsNotNone(runner.worker)
            window._cancel_active_reply()
            assert runner.worker is not None
            assert runner.on_chunk is not None
            assert runner.on_result is not None
            runner.on_result(runner.worker.fn(runner.on_chunk, runner.worker.is_cancelled))

            self.assertFalse(window._awaiting_response)
            self.assertIsNone(window._active_stream_worker)
            self.assertIsNone(window._pending_assistant_record)
            self.assertEqual(["user"], [message.role for message in window.messages])
            self.assertIn("Web search canceled", window.sidebar_activity_label.text())
            window.close()

    def test_runtime_notices_offer_retry_dismiss_and_voice_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            window._refresh_input_device_options = lambda: None  # type: ignore[method-assign]
            window._refresh_output_device_options = lambda: None  # type: ignore[method-assign]
            window._populate_voice_options = lambda: None  # type: ignore[method-assign]
            offline = StatusSnapshot(
                False,
                "Connection refused",
                [],
                False,
                "test-model",
                "Unavailable",
                True,
                "Ready",
                True,
                "Ready",
                True,
                "Ready",
                True,
                "Ready",
                "Start Ollama locally, then refresh status.",
            )
            window._apply_status_snapshot(offline)

            self.assertEqual("ollama_offline", window.system_notice.notice_key)
            self.assertEqual("Retry", window.system_notice.action_button.text())
            retries: list[bool] = []
            window.refresh_status = lambda: retries.append(True)  # type: ignore[method-assign]
            window.system_notice.action_button.click()
            self.assertEqual([True], retries)

            window.system_notice.dismiss_button.click()
            self.assertTrue(window.system_notice.isHidden())
            window._apply_status_snapshot(offline)
            self.assertTrue(window.system_notice.isHidden())

            microphone_missing = StatusSnapshot(
                True,
                "Connected",
                ["test-model"],
                True,
                "test-model",
                "Using test-model",
                False,
                "No input device",
                True,
                "Ready",
                True,
                "Ready",
                True,
                "Ready",
                "Connect a microphone.",
            )
            window._apply_status_snapshot(microphone_missing)

            self.assertEqual("microphone_missing", window.system_notice.notice_key)
            self.assertEqual("Open Voice", window.system_notice.action_button.text())
            window.system_notice.action_button.click()
            self.assertEqual(2, window.page_stack.currentIndex())
            window.close()

    def test_voice_tuning_persistence_is_debounced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            with patch.object(AppConfig, "save", autospec=True) as save:
                window.voice_panel.rate_slider.setValue(110)
                window.voice_panel.rate_slider.setValue(120)
                window.voice_panel.rate_slider.setValue(130)

                self.assertTrue(window._voice_tuning_save_timer.isActive())
                self.assertEqual(0, save.call_count)
                QTest.qWait(300)

                self.assertEqual(1, save.call_count)
                self.assertEqual(1.3, window.config.tts_rate)
                self.assertFalse(window._voice_tuning_save_timer.isActive())
            window.close()

    def test_window_geometry_and_active_tab_restore_across_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                first = MainWindow(paths, config)

            first.resize(960, 700)
            first.show()
            self.app.processEvents()
            first.agent_nav_button.click()
            self.assertEqual(1, first.config.active_page)
            encoded_geometry = bytes(first.saveGeometry().toBase64()).decode("ascii")
            first._update_config(window_geometry=encoded_geometry)

            loaded = AppConfig.load(paths)
            self.assertEqual(1, loaded.active_page)
            self.assertTrue(loaded.window_geometry)

            first.config = loaded
            first.page_stack.setCurrentIndex(0)
            first._set_nav_state(first.chat_nav_button)
            first.resize(1200, 700)
            self.app.processEvents()
            self.assertFalse(first._compact_layout)
            first._restore_window_session()

            self.assertEqual(1, first.page_stack.currentIndex())
            self.assertTrue(first.agent_nav_button.isChecked())
            self.assertGreaterEqual(first.width(), 820)
            self.assertEqual(700, first.height())
            self.assertTrue(first._compact_layout)
            first.close()

    def test_restored_geometry_outside_available_screens_falls_back_to_maximized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.window_geometry = "c2F2ZWQtZ2VvbWV0cnk="
            maximize_calls: list[bool] = []

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: maximize_calls.append(True)),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            self.assertEqual([True], maximize_calls)
            window.close()

    def test_last_opened_chat_and_per_conversation_text_drafts_restore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            store = HistoryStore(paths.chats_dir, paths.history_file)
            with patch.object(HistoryStore, "now_stamp", return_value="2026-01-01 10:00:00"):
                first = store.create_conversation("First")
            with patch.object(HistoryStore, "now_stamp", return_value="2026-01-01 11:00:00"):
                second = store.create_conversation("Second")
            stale_id = "f" * 32
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"
            config.last_conversation_id = first.summary.conversation_id
            config.chat_drafts = {
                first.summary.conversation_id: "First unfinished draft",
                second.summary.conversation_id: "Second unfinished draft",
                stale_id: "Stale draft",
            }
            config.save(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, AppConfig.load(paths))

            self.assertEqual(first.summary.conversation_id, window.active_conversation_id)
            self.assertEqual("First unfinished draft", window.chat_panel.input_box.toPlainText())
            self.assertTrue(window.chat_panel.send_button.isEnabled())
            self.assertIn("[Draft]", window.history_list.currentItem().text())
            self.assertNotIn(stale_id, window.config.chat_drafts)

            window._open_conversation(second.summary.conversation_id)
            self.assertEqual("Second unfinished draft", window.chat_panel.input_box.toPlainText())
            window.chat_panel.input_box.setPlainText("Updated second draft")
            self.assertTrue(window._chat_draft_save_timer.isActive())
            window._chat_draft_save_timer.timeout.emit()
            self.assertEqual(
                "Updated second draft",
                window.config.chat_drafts[second.summary.conversation_id],
            )

            window._open_conversation(first.summary.conversation_id)
            self.assertEqual("First unfinished draft", window.chat_panel.input_box.toPlainText())
            target = window._append_message("user", "Earlier request")
            window._begin_message_edit(target)
            window.chat_panel.input_box.setPlainText("Edited earlier request")
            window._save_current_chat_draft()
            self.assertEqual(
                "First unfinished draft",
                window.config.chat_drafts[first.summary.conversation_id],
            )
            window._cancel_message_edit()

            requests: list[tuple[str, str]] = []
            window._begin_assistant_response = lambda model, text: requests.append((model, text))  # type: ignore[method-assign]
            window._send_from_input()

            self.assertEqual([("test-model", "First unfinished draft")], requests)
            self.assertNotIn(first.summary.conversation_id, window.config.chat_drafts)
            self.assertEqual("", window.chat_panel.input_box.toPlainText())
            self.assertNotIn("[Draft]", window.history_list.currentItem().text())
            self.assertEqual(first.summary.conversation_id, window.config.last_conversation_id)
            window.close()

    def test_stream_chunks_are_coalesced_into_one_ui_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            window._active_reply_metadata = {"model_name": "test-model"}
            bubble = window._insert_bubble(
                ChatMessage("assistant", "Thinking...", "now", metadata={"pending": True}),
                register=False,
            )
            window._pending_assistant_bubble = bubble
            window._pending_assistant_text = ""
            rendered: list[str] = []
            original_update = bubble.update_message

            def record_update(message: ChatMessage) -> None:
                rendered.append(message.content)
                original_update(message)

            bubble.update_message = record_update  # type: ignore[method-assign]

            for chunk in ("Local", " ", "streaming", " ", "reply"):
                window._on_stream_chunk(chunk)

            self.assertEqual([], rendered)
            self.assertTrue(window._stream_render_timer.isActive())

            QTest.qWait(70)

            self.assertEqual(["Local streaming reply"], rendered)
            self.assertFalse(window._stream_render_timer.isActive())
            window._pending_assistant_bubble = None
            window.close()

    def test_long_history_renders_latest_page_then_loads_earlier_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            window.messages = [
                ChatMessage(
                    "user" if index % 2 == 0 else "assistant",
                    f"message-{index}",
                    "now",
                )
                for index in range(100)
            ]

            window._render_history()

            self.assertEqual(40, len(window._message_bubbles))
            self.assertEqual(60, window._rendered_message_start)
            self.assertEqual(
                [f"message-{index}" for index in range(60, 100)],
                [message.content for _bubble, message in window._message_bubbles],
            )
            self.assertFalse(window.chat_panel.load_earlier_button.isHidden())
            self.assertIn("60 not shown", window.chat_panel.load_earlier_button.text())
            self.assertFalse(window._message_bubbles[-1][0].regenerate_button.isHidden())

            window._set_interaction_busy(True)
            self.assertFalse(window.chat_panel.load_earlier_button.isEnabled())
            window.chat_panel.load_earlier_button.click()
            self.assertEqual(40, len(window._message_bubbles))
            window._set_interaction_busy(False)
            self.assertTrue(window.chat_panel.load_earlier_button.isEnabled())

            window.chat_panel.load_earlier_button.click()

            self.assertEqual(70, len(window._message_bubbles))
            self.assertEqual(30, window._rendered_message_start)
            self.assertEqual("message-30", window._message_bubbles[0][1].content)
            self.assertEqual("message-99", window._message_bubbles[-1][1].content)
            self.assertFalse(window._message_bubbles[0][0].edit_message_button.isHidden())
            self.assertFalse(window._message_bubbles[-1][0].regenerate_button.isHidden())

            window.chat_panel.load_earlier_button.click()

            self.assertEqual(100, len(window._message_bubbles))
            self.assertEqual(0, window._rendered_message_start)
            self.assertTrue(window.chat_panel.load_earlier_button.isHidden())
            self.assertEqual(
                [f"message-{index}" for index in range(100)],
                [message.content for _bubble, message in window._message_bubbles],
            )
            self.assertEqual(100, len(window.messages))
            window.close()

    def test_ollama_error_preserves_partial_reply_and_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            window._append_message("user", "Explain local inference")
            bubble = window._insert_bubble(
                ChatMessage("assistant", "Thinking...", "now", metadata={"pending": True}),
                register=False,
            )
            window._pending_assistant_bubble = bubble
            window._pending_assistant_text = "Partial explanation"
            window._active_reply_metadata = {"model_name": "test-model"}
            window._awaiting_response = True

            window._on_assistant_error("Connection closed while generating.")

            self.assertIsNone(window._failed_assistant_bubble)
            self.assertEqual("error", bubble.property("messageState"))
            self.assertEqual("Partial explanation", bubble.body_label.text())
            self.assertEqual("Connection closed while generating.", bubble.error_detail_label.text())
            self.assertFalse(bubble.retry_button.isHidden())
            self.assertEqual(["user", "assistant"], [message.role for message in window.messages])
            self.assertTrue(window.messages[-1].metadata["error"])
            reloaded = window.history_store.load_conversation(window.active_conversation_id)
            self.assertEqual("Partial explanation", reloaded.messages[-1].content)
            self.assertEqual(
                "Connection closed while generating.",
                reloaded.messages[-1].metadata["error_message"],
            )

            window.messages = reloaded.messages
            window._render_history()
            restored_bubble = window._message_bubbles[-1][0]
            self.assertEqual("error", restored_bubble.property("messageState"))
            self.assertFalse(restored_bubble.retry_button.isHidden())

            requests: list[tuple[str, str]] = []
            window._select_model_for_prompt = (  # type: ignore[method-assign]
                lambda _prompt, requires_vision=False: ModelSelection(
                    "test-model",
                    "balanced",
                    "retry",
                    automatic=True,
                )
            )
            window._begin_assistant_response = (  # type: ignore[method-assign]
                lambda model, text: requests.append((model, text))
            )

            restored_bubble.retry_button.click()

            self.assertEqual([("test-model", "Explain local inference")], requests)
            self.assertIsNone(window._failed_assistant_bubble)
            self.assertEqual(["user"], [message.role for message in window.messages])
            reloaded = window.history_store.load_conversation(window.active_conversation_id)
            self.assertEqual(["user"], [message.role for message in reloaded.messages])
            window.close()

    def test_inflight_reply_is_persisted_then_replaced_by_the_final_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            runner = DeferredTaskRunner()
            window.task_runner = runner
            window._active_model_selection = ModelSelection(
                "test-model",
                "balanced",
                "test",
                automatic=False,
            )
            window._append_message("user", "Explain persistence")

            window._request_assistant_response("test-model", None)

            self.assertEqual(2, len(window.messages))
            self.assertTrue(window.messages[-1].metadata["pending"])
            self.assertIs(window.messages[-1], window._pending_assistant_record)
            conversation_path = paths.chats_dir / f"{window.active_conversation_id}.json"
            raw = json.loads(conversation_path.read_text(encoding="utf-8"))
            self.assertTrue(raw["messages"][-1]["metadata"]["pending"])

            assert runner.on_result is not None
            runner.on_result(
                (
                    ChatStreamResult("Completed reply"),
                    ContextStats(1, 1, 0, 20, 1_000),
                    {"model_name": "test-model"},
                )
            )

            self.assertEqual(2, len(window.messages))
            self.assertEqual("Completed reply", window.messages[-1].content)
            self.assertNotIn("pending", window.messages[-1].metadata)
            self.assertIsNone(window._pending_assistant_record)
            raw = json.loads(conversation_path.read_text(encoding="utf-8"))
            self.assertEqual("Completed reply", raw["messages"][-1]["content"])
            self.assertNotIn("pending", raw["messages"][-1]["metadata"])
            window.close()

    def test_reply_progress_reports_loading_streaming_and_stalls_then_clears(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            runner = DeferredTaskRunner()
            window.task_runner = runner
            window._active_model_selection = ModelSelection(
                "test-model",
                "balanced",
                "test",
                automatic=False,
            )
            window._append_message("user", "Explain slow local inference")
            window._request_assistant_response("test-model", None)
            window._reply_progress_timer.stop()
            bubble = window._pending_assistant_bubble
            self.assertIsNotNone(bubble)
            assert bubble is not None

            window._reply_progress_started_at = 100.0
            window._reply_stage_started_at = 100.0
            window._reply_last_chunk_at = None
            with patch(
                "local_matrix_assistant.ui.main_window_chat.time.monotonic",
                return_value=120.0,
            ):
                window._refresh_reply_progress()

            self.assertIn("Still loading test-model", bubble.source_label.text())
            self.assertEqual("stalled", bubble.source_label.property("progressState"))
            self.assertIn("Stop is available", window.sidebar_activity_label.text())

            with patch(
                "local_matrix_assistant.ui.main_window_chat.time.monotonic",
                return_value=121.0,
            ):
                window._on_stream_chunk("First token")
            window._flush_pending_stream_render()

            self.assertEqual("First token", bubble.body_label.text())
            self.assertIn("Streaming reply", bubble.source_label.text())
            self.assertEqual("streaming", bubble.source_label.property("progressState"))

            window._reply_last_chunk_at = 121.0
            with patch(
                "local_matrix_assistant.ui.main_window_chat.time.monotonic",
                return_value=131.0,
            ):
                window._refresh_reply_progress()

            self.assertIn("Reply paused for 10s", bubble.source_label.text())
            self.assertEqual("stalled", bubble.source_label.property("progressState"))

            assert runner.on_result is not None
            with patch(
                "local_matrix_assistant.ui.main_window_chat.time.monotonic",
                return_value=135.0,
            ):
                runner.on_result(
                    (
                        ChatStreamResult(
                            "Completed reply",
                            total_duration_ns=4_200_000_000,
                            load_duration_ns=500_000_000,
                            prompt_eval_count=80,
                            prompt_eval_duration_ns=400_000_000,
                            eval_count=120,
                            eval_duration_ns=3_000_000_000,
                        ),
                        ContextStats(1, 1, 0, 20, 1_000),
                        {"model_name": "test-model"},
                    )
                )

            self.assertFalse(window._reply_progress_timer.isActive())
            self.assertIsNone(window._reply_progress_started_at)
            self.assertEqual("", window._active_reply_stage)
            self.assertEqual("Completed reply", window.messages[-1].content)
            self.assertNotIn("pending_state", window.messages[-1].metadata)
            self.assertEqual(21.0, window.messages[-1].metadata["reply_time_to_first_token_seconds"])
            self.assertEqual(4.2, window.messages[-1].metadata["ollama_total_seconds"])
            self.assertEqual(120, window.messages[-1].metadata["ollama_generated_tokens"])
            self.assertEqual(40.0, window.messages[-1].metadata["ollama_tokens_per_second"])
            self.assertIn("40.0 tok/s", bubble.source_label.text())
            reloaded = window.history_store.load_conversation(window.active_conversation_id)
            self.assertEqual(40.0, reloaded.messages[-1].metadata["ollama_tokens_per_second"])
            window.close()

    def test_user_message_save_failure_keeps_composer_and_never_starts_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            window.available_ollama_models = ["test-model"]
            requests: list[tuple[str, str]] = []
            window._begin_assistant_response = (  # type: ignore[method-assign]
                lambda model, text: requests.append((model, text))
            )
            initial_messages = list(window.messages)
            window.chat_panel.input_box.setPlainText("Keep this unsent request")

            with patch.object(
                window.history_store,
                "save_conversation",
                side_effect=OSError("disk full"),
            ):
                window._send_from_input()

            self.assertEqual(initial_messages, window.messages)
            self.assertEqual("Keep this unsent request", window.chat_panel.input_box.toPlainText())
            self.assertEqual([], requests)
            self.assertTrue(window.chat_panel.send_button.isEnabled())
            self.assertIn("nothing was sent", window.chat_panel.status_panel.status_label.text())
            window.close()

    def test_pending_reply_save_failure_does_not_start_model_and_can_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            runner = DeferredTaskRunner()
            window.task_runner = runner
            window.available_ollama_models = ["test-model"]
            window._active_model_selection = ModelSelection(
                "test-model",
                "balanced",
                "test",
                automatic=False,
            )
            window._append_message("user", "Retry after save failure")

            with patch.object(
                window.history_store,
                "save_conversation",
                side_effect=OSError("permission denied"),
            ):
                window._request_assistant_response("test-model", None)

            self.assertFalse(window._awaiting_response)
            self.assertIsNone(window._pending_assistant_record)
            self.assertEqual(["user"], [message.role for message in window.messages])
            self.assertIsNone(runner.worker)
            failed_bubble = window._failed_assistant_bubble
            self.assertIsNotNone(failed_bubble)
            assert failed_bubble is not None
            self.assertFalse(failed_bubble.retry_button.isHidden())
            self.assertIn("model was not started", failed_bubble.error_detail_label.text())

            failed_bubble.retry_button.click()

            self.assertIsNotNone(runner.worker)
            self.assertTrue(window.messages[-1].metadata["pending"])
            window._interrupt_pending_assistant_reply_for_shutdown()
            window.close()

    def test_final_reply_save_failure_exposes_retry_save_and_blocks_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            runner = DeferredTaskRunner()
            window.task_runner = runner
            window._active_model_selection = ModelSelection(
                "test-model",
                "balanced",
                "test",
                automatic=False,
            )
            window._append_message("user", "Keep the completed reply")
            window._request_assistant_response("test-model", None)
            assert runner.on_result is not None

            with patch.object(
                window.history_store,
                "save_conversation",
                side_effect=OSError("disk full"),
            ):
                runner.on_result(
                    (
                        ChatStreamResult("Completed but initially unsaved"),
                        ContextStats(1, 1, 0, 20, 1_000),
                        {"model_name": "test-model"},
                    )
                )

            reply = window.messages[-1]
            bubble = window._message_bubbles[-1][0]
            self.assertIs(reply, window._unsaved_reply_message)
            self.assertTrue(reply.metadata["save_error"])
            self.assertEqual("HISTORY SAVE FAILED", bubble.error_title_label.text())
            self.assertEqual("Retry Save", bubble.retry_button.text())
            self.assertTrue(bubble.retry_button.isEnabled())
            window.chat_panel.input_box.setPlainText("Do not send yet")
            self.assertFalse(window.chat_panel.send_button.isEnabled())
            self.assertFalse(window.new_chat_button.isEnabled())
            self.assertFalse(window.history_list.isEnabled())
            conversation_path = paths.chats_dir / f"{window.active_conversation_id}.json"
            raw = json.loads(conversation_path.read_text(encoding="utf-8"))
            self.assertTrue(raw["messages"][-1]["metadata"]["pending"])

            bubble.retry_button.click()

            self.assertIsNone(window._unsaved_reply_message)
            self.assertEqual("Completed but initially unsaved", window.messages[-1].content)
            self.assertNotIn("save_error", window.messages[-1].metadata)
            self.assertTrue(window.chat_panel.send_button.isEnabled())
            self.assertTrue(window.new_chat_button.isEnabled())
            self.assertTrue(window.history_list.isEnabled())
            reloaded = window.history_store.load_conversation(window.active_conversation_id)
            self.assertEqual("Completed but initially unsaved", reloaded.messages[-1].content)
            self.assertNotIn("save_error", reloaded.messages[-1].metadata)
            window.close()

    def test_shutdown_checkpoints_partial_reply_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            window.task_runner = DeferredTaskRunner()
            window._active_model_selection = ModelSelection(
                "test-model",
                "balanced",
                "test",
                automatic=False,
            )
            window._append_message("user", "Explain shutdown")
            window._request_assistant_response("test-model", None)
            window._on_stream_chunk("Checkpointed partial response")
            window._flush_pending_stream_render()

            window._interrupt_pending_assistant_reply_for_shutdown()

            recovered = window.history_store.load_conversation(window.active_conversation_id)
            reply = recovered.messages[-1]
            self.assertEqual("Checkpointed partial response", reply.content)
            self.assertTrue(reply.metadata["error"])
            self.assertTrue(reply.metadata["interrupted"])
            self.assertNotIn("pending", reply.metadata)
            window.close()

    def test_startup_recovers_crashed_pending_reply_with_retry_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            store = HistoryStore(paths.chats_dir, paths.history_file)
            record = store.create_conversation()
            store.save_conversation(
                record.summary.conversation_id,
                [
                    ChatMessage("user", "Resume after a crash", "now"),
                    ChatMessage(
                        "assistant",
                        "Thinking...",
                        "now",
                        metadata={"pending": True, "model_name": "test-model"},
                    ),
                ],
            )
            config.last_conversation_id = record.summary.conversation_id

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            reply = window.messages[-1]
            bubble = window._message_bubbles[-1][0]
            self.assertTrue(reply.metadata["interrupted"])
            self.assertNotIn("pending", reply.metadata)
            self.assertEqual("interrupted", bubble.property("messageState"))
            self.assertEqual("REPLY INTERRUPTED", bubble.error_title_label.text())
            self.assertFalse(bubble.retry_button.isHidden())
            window.close()

    def test_canceled_partial_reply_persists_and_restores_regenerate_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            window._append_message("user", "Explain model routing")
            bubble = window._insert_bubble(
                ChatMessage("assistant", "Thinking...", "now", metadata={"pending": True}),
                register=False,
            )
            window._pending_assistant_bubble = bubble
            window._pending_assistant_text = "Partial routed answer"
            window._active_reply_metadata = {"model_name": "test-model"}
            window._awaiting_response = True
            window._cancel_requested = False

            window._on_stream_complete(
                (
                    ChatStreamResult("Partial routed answer", canceled=True),
                    ContextStats(1, 1, 0, 20, 1_000),
                    {"model_name": "test-model"},
                )
            )

            self.assertEqual(["user", "assistant"], [message.role for message in window.messages])
            self.assertTrue(window.messages[-1].metadata["canceled"])
            self.assertEqual("test-model", window.messages[-1].metadata["model_name"])
            reloaded = window.history_store.load_conversation(window.active_conversation_id)
            self.assertEqual("Partial routed answer", reloaded.messages[-1].content)
            self.assertTrue(reloaded.messages[-1].metadata["canceled"])

            window.messages = reloaded.messages
            window._render_history()
            restored_bubble = window._message_bubbles[-1][0]
            self.assertIn("Response canceled", restored_bubble.source_label.text())
            self.assertFalse(restored_bubble.regenerate_button.isHidden())
            self.assertTrue(restored_bubble.retry_button.isHidden())
            window.close()

    def test_responsive_shell_collapses_into_a_full_width_navigation_screen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)
            window.startup_sequence.begin = lambda: None  # type: ignore[method-assign]
            window.show()
            QTest.qWait(10)

            self.assertFalse(window._compact_layout)
            self.assertFalse(window.sidebar.isHidden())
            self.assertEqual(820, window.minimumWidth())

            window.resize(900, 700)
            QTest.qWait(20)

            self.assertTrue(window._compact_layout)
            self.assertTrue(window.sidebar.isHidden())
            self.assertFalse(window.page_stack.isHidden())
            self.assertTrue(window.chat_panel._compact_mode)
            self.assertEqual(12, window.root_layout.contentsMargins().left())

            window.sidebar_toggle_button.click()
            self.assertFalse(window.sidebar.isHidden())
            self.assertTrue(window.page_stack.isHidden())
            self.assertEqual("Hide Menu", window.sidebar_toggle_button.text())

            window.agent_nav_button.click()
            self.assertEqual(1, window.page_stack.currentIndex())
            self.assertTrue(window.sidebar.isHidden())
            self.assertFalse(window.page_stack.isHidden())

            window.resize(1300, 800)
            QTest.qWait(20)
            self.assertFalse(window._compact_layout)
            self.assertFalse(window.sidebar.isHidden())
            self.assertFalse(window.chat_panel._compact_mode)

            window.sidebar_toggle_button.click()
            self.assertTrue(window.sidebar.isHidden())
            self.assertTrue(window.config.sidebar_collapsed)
            self.assertTrue(AppConfig.load(paths).sidebar_collapsed)

            window._focus_history_search()
            self.assertFalse(window.sidebar.isHidden())
            self.assertFalse(window.config.sidebar_collapsed)
            window.close()

    def test_agent_history_survives_restart_and_clear_removes_saved_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            work = Path(tmp) / "work"
            work.mkdir()
            artifact = work / "report.txt"
            artifact.write_text("saved artifact", encoding="utf-8")
            config.working_folders = [str(work)]
            config.active_working_folder = str(work)

            def create_window(current_config: AppConfig) -> MainWindow:
                with (
                    patch.object(MainWindow, "refresh_status", lambda _self: None),
                    patch.object(MainWindow, "showMaximized", lambda _self: None),
                    patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                    patch.object(AudioPlayer, "list_outputs", lambda _self: []),
                ):
                    return MainWindow(paths, current_config)

            first = create_window(config)
            first.agent_panel.append_log("Command", "run tests")
            first.agent_panel.append_task_output("test_example ... ok\n")
            first.agent_panel.append_log(
                "Agent",
                "Tests passed.",
                artifact_path=str(artifact),
                artifact_kind="file",
            )
            history_path = paths.data_dir / "agent_history.json"
            QTest.qWait(350)
            self.assertTrue(history_path.exists())
            self.assertIn("test_example ... ok", history_path.read_text(encoding="utf-8"))
            first.close()

            restored = create_window(AppConfig.load(paths))

            self.assertEqual(2, restored.agent_panel.task_timeline.entry_count)
            self.assertEqual("run tests", restored.agent_panel.task_timeline.cards[0].full_text)
            self.assertIn("test_example ... ok", restored.agent_panel.action_log.toPlainText())
            self.assertEqual(2, restored.agent_panel.task_detail_combo.count())
            self.assertTrue(restored.agent_panel.clear_history_button.isEnabled())
            restored_card = restored.agent_panel.task_timeline.cards[-1]
            self.assertEqual(str(artifact), restored_card.artifact_path)
            self.assertFalse(restored_card.open_file_button.isHidden())

            artifact.unlink()
            restored_card.open_file_button.click()
            self.assertIn("no longer exists", restored.agent_panel.task_timeline.cards[-1].full_text)
            self.assertEqual("error", restored.agent_panel.task_timeline.cards[-1].event_kind)

            restored.agent_panel.clear_history_button.click()
            self.assertTrue(history_path.exists())
            restored.agent_panel.clear_history_button.click()
            restored.close()
            self.assertFalse(history_path.exists())

            empty = create_window(AppConfig.load(paths))
            self.assertEqual(0, empty.agent_panel.task_timeline.entry_count)
            self.assertEqual("", empty.agent_panel.action_log.toPlainText())
            empty.close()

    def test_escape_cancels_agent_history_clear_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)
            window.agent_panel.append_log("Command", "run tests")
            window.agent_panel.clear_history_button.click()

            window._handle_escape_shortcut()

            self.assertEqual(1, window.agent_panel.task_timeline.entry_count)
            self.assertEqual("Clear All", window.agent_panel.clear_history_button.text())
            self.assertIn(
                "deletion canceled",
                window.agent_panel.status_panel.status_label.text(),
            )
            window.close()

    def test_chat_attachment_is_previewed_persisted_and_sent_as_hidden_model_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"
            source = root / "review.py"
            source.write_text("def answer():\n    return 42\n", encoding="utf-8")

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            window.task_runner = ImmediateTaskRunner()
            window._add_chat_attachment_paths([str(source)])
            self.assertEqual(1, window.chat_panel.attachment_count())
            self.assertTrue(window.chat_panel.send_button.isEnabled())

            requests: list[tuple[str, str]] = []
            window._begin_assistant_response = lambda model, text: requests.append((model, text))  # type: ignore[method-assign]
            window._send_from_input()

            self.assertEqual([("test-model", "Review the attached file.")], requests)
            self.assertEqual("coding", window._active_model_selection.profile)
            self.assertEqual("Review the attached file.", window.messages[-1].content)
            attachment_metadata = window.messages[-1].metadata["attachments"][0]
            self.assertEqual("review.py", attachment_metadata["name"])
            self.assertNotIn("path", attachment_metadata)
            self.assertEqual(0, window.chat_panel.attachment_count())
            self.assertTrue(window.chat_panel.attachment_tray.isHidden())

            prepared, _stats = window._prepare_request_messages(None)
            self.assertTrue(
                any(message.metadata.get("attachment_safety") for message in prepared if message.role == "system")
            )
            user_prompt = next(message.content for message in reversed(prepared) if message.role == "user")
            self.assertIn('ATTACHMENT 1: "review.py"', user_prompt)
            self.assertIn("return 42", user_prompt)
            reloaded = window.history_store.load_conversation(window.active_conversation_id)
            self.assertEqual("review.py", reloaded.messages[-1].metadata["attachments"][0]["name"])
            window.close()

    def test_chat_file_picker_is_non_native_and_non_modal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
                patch.object(QFileDialog, "show", lambda _self: None),
                patch.object(QFileDialog, "raise_", lambda _self: None),
                patch.object(QFileDialog, "activateWindow", lambda _self: None),
            ):
                window = MainWindow(paths, config)
                window._choose_chat_attachments()

            dialog = window._chat_file_dialog
            self.assertIsNotNone(dialog)
            self.assertEqual(QFileDialog.FileMode.ExistingFiles, dialog.fileMode())
            self.assertTrue(dialog.testOption(QFileDialog.Option.DontUseNativeDialog))
            self.assertFalse(dialog.isModal())
            window._on_chat_attachment_dialog_finished(0)
            self.assertIsNone(window._chat_file_dialog)
            window.close()

    def test_attachment_extraction_runs_without_blocking_the_ui_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)
            source = root / "large-notes.txt"
            source.write_text("local attachment", encoding="utf-8")

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            runner = DeferredTaskRunner()
            window.task_runner = runner
            window._add_chat_attachment_paths([str(source)])

            self.assertTrue(window._awaiting_response)
            self.assertFalse(window.chat_panel.attach_button.isEnabled())
            self.assertTrue(
                all(not remove_button.isEnabled() for _row, _preview, _label, remove_button in window.chat_panel._attachment_rows)
            )
            self.assertEqual(0, window.chat_panel.attachment_count())
            self.assertIn("Reading 1 local file", window.sidebar_activity_label.text())

            payload = runner.worker.fn()
            runner.on_result(payload)

            self.assertFalse(window._awaiting_response)
            self.assertTrue(window.chat_panel.attach_button.isEnabled())
            self.assertEqual(1, window.chat_panel.attachment_count())
            window.close()

    def test_image_attachment_routes_to_vision_model_and_followups_keep_image_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)
            config.ollama_model = "llama3.2:3b"
            source = root / "screen.png"
            image = QImage(320, 180, QImage.Format.Format_RGB32)
            image.fill(QColor("#23c976"))
            self.assertTrue(image.save(str(source), "PNG"))

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            window.task_runner = ImmediateTaskRunner()
            window.available_ollama_models = ["llama3.2:3b", "qwen3.5:4b"]
            requests: list[tuple[str, str]] = []
            window._begin_assistant_response = lambda model, text: requests.append((model, text))  # type: ignore[method-assign]
            window._add_chat_attachment_paths([str(source)])
            window.chat_panel.input_box.setPlainText("Describe this screenshot")
            window._send_from_input()

            self.assertEqual([("qwen3.5:4b", "Describe this screenshot")], requests)
            self.assertEqual("vision", window._active_model_selection.profile)
            self.assertTrue(window.messages[-1].metadata["attachments"][0]["image_data"])

            prepared, _stats = window._prepare_request_messages(None)
            payload = OllamaClient._build_payload("qwen3.5:4b", prepared, stream=True)
            image_messages = [message for message in payload["messages"] if message.get("images")]
            self.assertEqual(1, len(image_messages))

            window.chat_panel.input_box.setPlainText("What color dominates it?")
            window._send_from_input()
            self.assertEqual(("qwen3.5:4b", "What color dominates it?"), requests[-1])
            self.assertEqual("vision", window._active_model_selection.profile)
            window.close()

    def test_clipboard_image_is_added_to_chat_without_a_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            window.task_runner = ImmediateTaskRunner()
            image = QImage(120, 60, QImage.Format.Format_RGB32)
            image.fill(QColor("purple"))

            window._add_chat_clipboard_image(image)

            self.assertEqual(1, window.chat_panel.attachment_count())
            attachment = window._pending_chat_attachments[0]
            self.assertEqual("clipboard-image.png", attachment.name)
            self.assertTrue(attachment.image_data)
            self.assertFalse(Path(attachment.path).is_absolute())
            self.assertIn("Pasted clipboard image", window.sidebar_activity_label.text())
            window.close()

    def test_image_send_is_preserved_when_no_vision_model_is_installed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)
            config.ollama_model = "llama3.2:3b"
            source = root / "screen.png"
            image = QImage(64, 64, QImage.Format.Format_RGB32)
            image.fill(QColor("green"))
            self.assertTrue(image.save(str(source), "PNG"))

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            window.task_runner = ImmediateTaskRunner()
            window.available_ollama_models = ["llama3.2:3b"]
            original_count = len(window.messages)
            window._add_chat_attachment_paths([str(source)])
            window.chat_panel.input_box.setPlainText("Describe it")
            window._send_from_input()

            self.assertEqual(original_count, len(window.messages))
            self.assertEqual("Describe it", window.chat_panel.input_box.toPlainText())
            self.assertEqual(1, window.chat_panel.attachment_count())
            self.assertIn("No installed Ollama vision model", window.sidebar_activity_label.text())
            window.close()

    def test_long_chat_builds_persisted_memory_before_streaming_reply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"
            config.voice_enabled = False

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            window.task_runner = ImmediateTaskRunner()
            window._active_model_selection = ModelSelection(
                "test-model",
                "fast",
                "test",
                automatic=False,
                context_window=1024,
                max_output_tokens=256,
            )
            window.messages = []
            for index in range(6):
                window.messages.extend(
                    [
                        ChatMessage("user", f"requirement-{index} " + "u" * 260, "now"),
                        ChatMessage("assistant", f"decision-{index} " + "a" * 260, "now"),
                    ]
                )
            window.messages.append(ChatMessage("user", "latest question", "now"))
            window.ollama_client.chat = (  # type: ignore[method-assign]
                lambda _model, _messages, options=None: "- Earlier requirements and decisions remain active."
            )
            window.ollama_client.chat_stream = (  # type: ignore[method-assign]
                lambda _model, _messages, _on_chunk, _should_cancel, options=None: ChatStreamResult("final reply")
            )

            window._request_assistant_response("test-model", None)

            self.assertGreater(window.conversation_memory.covered_messages, 0)
            self.assertEqual("local_model", window.conversation_memory.source)
            self.assertEqual("final reply", window.messages[-1].content)
            self.assertGreater(window.messages[-1].metadata["context_memory_messages"], 0)
            reloaded = window.history_store.load_conversation(window.active_conversation_id)
            self.assertEqual(window.conversation_memory, reloaded.memory)
            window.close()

    def test_long_chat_memory_preparation_can_be_canceled_before_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"
            config.voice_enabled = False

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            runner = DeferredTaskRunner()
            window.task_runner = runner
            window._active_model_selection = ModelSelection(
                "test-model",
                "fast",
                "test",
                automatic=False,
                context_window=1024,
                max_output_tokens=256,
            )
            window.messages = []
            for index in range(6):
                window.messages.extend(
                    [
                        ChatMessage("user", f"requirement-{index} " + "u" * 260, "now"),
                        ChatMessage("assistant", f"decision-{index} " + "a" * 260, "now"),
                    ]
                )
            window.messages.append(ChatMessage("user", "latest question", "now"))

            window._request_assistant_response("test-model", None)

            self.assertEqual("memory", window._active_reply_stage)
            self.assertTrue(window.chat_panel.cancel_button.isEnabled())
            window._cancel_active_reply()
            assert runner.worker is not None
            assert runner.on_chunk is not None
            assert runner.on_result is not None
            runner.on_result(runner.worker.fn(runner.on_chunk, runner.worker.is_cancelled))

            self.assertFalse(window._awaiting_response)
            self.assertIsNone(window._active_stream_worker)
            self.assertIsNone(window._pending_assistant_record)
            self.assertEqual(ConversationMemory(), window.conversation_memory)
            self.assertEqual("user", window.messages[-1].role)
            self.assertIn("preparation canceled", window.sidebar_activity_label.text().lower())
            window.close()

    def test_history_search_and_inline_rename_persist_across_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            first_id = window.active_conversation_id
            window._append_message("user", "A unique SQLite migration requirement")
            window._start_new_chat()
            second_id = window.active_conversation_id
            window._append_message("user", "General discussion")

            window._begin_conversation_rename()
            self.assertFalse(window.rename_chat_panel.isHidden())
            window.rename_chat_input.setText("Release Planning")
            window._commit_conversation_rename()

            self.assertEqual("Release Planning", window.history_store.load_conversation(second_id).summary.title)
            self.assertTrue(window.rename_chat_panel.isHidden())

            window.history_search_input.setText("sqlite migration")
            window._apply_history_filter()

            self.assertTrue(wait_until(lambda: not window._history_search_inflight))
            self.assertEqual(1, window.history_list.count())
            self.assertEqual(first_id, window.history_list.item(0).data(Qt.ItemDataRole.UserRole))
            window.history_list.setCurrentRow(0)
            self.assertEqual(first_id, window.active_conversation_id)

            window.history_search_input.setText("no matching conversation")
            window._apply_history_filter()
            self.assertTrue(wait_until(lambda: not window._history_search_inflight))
            self.assertEqual(0, window.history_list.count())
            self.assertFalse(window.history_empty_label.isHidden())
            self.assertFalse(window.rename_chat_button.isEnabled())
            window.close()

    def test_history_search_runs_in_background_and_rejects_stale_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            first = window.history_store.create_conversation("First result")
            second = window.history_store.create_conversation("Second result")
            runner = DeferredTaskRunner()
            window.task_runner = runner

            window.history_search_input.setText("first")
            window._apply_history_filter()
            first_result = runner.on_result
            self.assertTrue(window._history_search_inflight)
            self.assertFalse(window.history_list.isEnabled())
            self.assertEqual("Searching chats locally...", window.history_empty_label.text())
            self.assertEqual("busy", window.history_search_input.property("searchState"))

            window.history_search_input.setText("second")
            window._apply_history_filter()
            second_result = runner.on_result
            self.assertIsNot(first_result, second_result)

            assert first_result is not None
            first_result([first.summary])

            self.assertTrue(window._history_search_inflight)
            self.assertNotEqual("First result", window.history_list.currentItem().text().splitlines()[0])

            assert second_result is not None
            second_result([second.summary])

            self.assertFalse(window._history_search_inflight)
            self.assertTrue(window.history_list.isEnabled())
            self.assertEqual("idle", window.history_search_input.property("searchState"))
            self.assertEqual(1, window.history_list.count())
            self.assertEqual("Second result", window.history_list.item(0).text().splitlines()[0])

            window.history_search_input.setText("broken")
            window._apply_history_filter()
            search_error = runner.on_error
            assert search_error is not None
            search_error("simulated read failure")

            self.assertFalse(window._history_search_inflight)
            self.assertEqual("error", window.history_search_input.property("searchState"))
            self.assertEqual("Chat search unavailable.", window.history_empty_label.text())
            self.assertTrue(window.history_list.isEnabled())
            window.close()

    def test_global_shortcuts_navigate_and_manage_history_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            self.assertEqual(
                {
                    "Ctrl+N",
                    "Ctrl+K",
                    "Ctrl+L",
                    "Ctrl+O",
                    "Ctrl+B",
                    "Ctrl+Shift+R",
                    "Ctrl+Shift+Space",
                    "Ctrl+Shift+M",
                    "Ctrl+Shift+X",
                    "F2",
                    "Alt+1",
                    "Alt+2",
                    "Alt+3",
                    "Alt+4",
                    "Ctrl+,",
                    "Ctrl+/",
                    "Escape",
                },
                set(window._shortcuts),
            )
            previous_id = window.active_conversation_id
            window._shortcuts["Ctrl+N"].activated.emit()
            self.assertNotEqual(previous_id, window.active_conversation_id)

            window._shortcuts["Alt+2"].activated.emit()
            self.assertEqual(1, window.page_stack.currentIndex())

            window.chat_panel.input_box.setPlainText("Keep this draft unchanged")
            voice_calls: list[str] = []
            window._toggle_voice_mode = lambda: voice_calls.append("toggle")  # type: ignore[method-assign]
            window._shortcuts["Ctrl+Shift+Space"].activated.emit()
            self.assertEqual(0, window.page_stack.currentIndex())
            self.assertEqual(["toggle"], voice_calls)

            window._shortcuts["Ctrl+Shift+M"].activated.emit()
            self.assertTrue(window.config.microphone_muted)
            self.assertTrue(window.voice_panel.microphone_muted_checkbox.isChecked())
            self.assertTrue(window.chat_panel.voice_only_panel.mute_button.isChecked())

            stop_calls: list[str] = []
            window._stop_voice_output = lambda: stop_calls.append("stop")  # type: ignore[method-assign]
            window._shortcuts["Ctrl+Shift+X"].activated.emit()
            self.assertEqual(["stop"], stop_calls)
            self.assertEqual("Keep this draft unchanged", window.chat_panel.input_box.toPlainText())

            window._shortcuts["Ctrl+L"].activated.emit()
            self.assertEqual(0, window.page_stack.currentIndex())

            window._shortcuts["F2"].activated.emit()
            self.assertFalse(window.rename_chat_panel.isHidden())
            window._shortcuts["Escape"].activated.emit()
            self.assertTrue(window.rename_chat_panel.isHidden())

            window.history_search_input.setText("needle")
            window._shortcuts["Escape"].activated.emit()
            self.assertEqual("", window.history_search_input.text())

            window._shortcuts["Ctrl+/"].activated.emit()
            self.assertFalse(window._shortcut_help_dialog.isHidden())
            window._shortcut_help_dialog.close()
            window.close()

    def test_edit_and_resend_restores_snapshot_cancels_safely_and_replaces_later_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"
            original_path = root / "original.txt"
            original_path.write_text("saved snapshot", encoding="utf-8")
            draft_path = root / "draft.txt"
            draft_path.write_text("draft snapshot", encoding="utf-8")

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            original = window.attachment_service.load(str(original_path))
            draft = window.attachment_service.load(str(draft_path))
            target = window._append_message(
                "user",
                "Original request",
                metadata={"attachments": [original.metadata()]},
            )
            window._append_message("assistant", "Original answer")
            window._append_message("user", "Later request")
            window._append_message("assistant", "Later answer")
            original_messages = list(window.messages)
            window.conversation_memory = ConversationMemory("stale summary", 4, "now", "model")
            window._persist_current_conversation()
            window.chat_panel.input_box.setPlainText("Unsent draft")
            window._pending_chat_attachments = [draft]
            window.chat_panel.set_pending_attachments([draft])

            window._begin_message_edit(target)
            self.assertEqual(original_messages, window.messages)
            self.assertEqual("Original request", window.chat_panel.input_box.toPlainText())
            self.assertEqual("original.txt", window._pending_chat_attachments[0].name)
            self.assertIn("3 later messages", window.chat_panel.edit_message_label.text())
            original_path.unlink()

            window._shortcuts["Escape"].activated.emit()
            self.assertEqual("Unsent draft", window.chat_panel.input_box.toPlainText())
            self.assertEqual("draft.txt", window._pending_chat_attachments[0].name)
            self.assertEqual(original_messages, window.messages)

            requests: list[tuple[str, str]] = []
            window._begin_assistant_response = lambda model, text: requests.append((model, text))  # type: ignore[method-assign]
            window._begin_message_edit(target)
            window.chat_panel.input_box.setPlainText("Revised request")
            selector = window._select_model_for_prompt
            window._select_model_for_prompt = (  # type: ignore[method-assign]
                lambda _prompt, requires_vision=False: ModelSelection(
                    "", "manual", "No model", automatic=False
                )
            )
            window._send_from_input()
            self.assertEqual(original_messages, window.messages)
            self.assertIsNotNone(window._editing_message_index)
            self.assertEqual("Revised request", window.chat_panel.input_box.toPlainText())
            window._select_model_for_prompt = selector  # type: ignore[method-assign]
            window._send_from_input()

            self.assertEqual([("test-model", "Revised request")], requests)
            self.assertEqual(1, len(window.messages))
            self.assertEqual("Revised request", window.messages[0].content)
            self.assertEqual("original.txt", window.messages[0].metadata["attachments"][0]["name"])
            self.assertEqual("saved snapshot", window.messages[0].metadata["attachments"][0]["content"])
            self.assertEqual(ConversationMemory(), window.conversation_memory)
            self.assertIsNone(window._editing_message_index)
            self.assertTrue(window.chat_panel.edit_message_banner.isHidden())
            reloaded = window.history_store.load_conversation(window.active_conversation_id)
            self.assertEqual(["Revised request"], [message.content for message in reloaded.messages])
            self.assertEqual(ConversationMemory(), reloaded.memory)
            window.close()

    def test_regenerate_removes_only_latest_response_and_persists_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            user_message = window._append_message("user", "Try this again")
            old_response = window._append_message("assistant", "First response")
            requests: list[tuple[str, str]] = []
            window._begin_assistant_response = lambda model, text: requests.append((model, text))  # type: ignore[method-assign]

            latest_bubble = window._message_bubbles[-1][0]
            self.assertFalse(latest_bubble.regenerate_button.isHidden())
            latest_bubble.regenerate_button.click()

            self.assertEqual([("test-model", "Try this again")], requests)
            self.assertEqual([user_message], window.messages)
            reloaded = window.history_store.load_conversation(window.active_conversation_id)
            self.assertEqual(["Try this again"], [message.content for message in reloaded.messages])

            window._append_message("assistant", "Second response")
            window._append_message("user", "A newer request")
            window._append_message("assistant", "Newest response")
            before = list(window.messages)
            window._regenerate_response(old_response)
            self.assertEqual(before, window.messages)

            window._shortcuts["Ctrl+Shift+R"].activated.emit()
            self.assertEqual(("test-model", "A newer request"), requests[-1])
            self.assertEqual("A newer request", window.messages[-1].content)
            window.close()

    def test_completed_stream_registers_latest_response_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = build_paths(root)
            config = AppConfig.defaults(paths)
            config.ollama_model = "test-model"
            config.voice_enabled = False

            with (
                patch.object(MainWindow, "refresh_status", lambda _self: None),
                patch.object(MainWindow, "showMaximized", lambda _self: None),
                patch.object(AudioRecorder, "list_inputs", lambda _self: []),
                patch.object(AudioPlayer, "list_outputs", lambda _self: []),
            ):
                window = MainWindow(paths, config)

            window.task_runner = ImmediateTaskRunner()
            window.ollama_client.chat_stream = (  # type: ignore[method-assign]
                lambda _model, _messages, on_chunk, _should_cancel, options=None: (
                    on_chunk("Streamed "),
                    on_chunk("answer"),
                    ChatStreamResult("Streamed answer"),
                )[-1]
            )
            window.chat_panel.input_box.setPlainText("Stream this")
            window._send_from_input()

            self.assertEqual(["Stream this", "Streamed answer"], [message.content for message in window.messages])
            self.assertEqual(2, len(window._message_bubbles))
            response_bubble = window._message_bubbles[-1][0]
            self.assertFalse(response_bubble.regenerate_button.isHidden())
            self.assertTrue(response_bubble.regenerate_button.isEnabled())
            self.assertIsNone(window._pending_assistant_bubble)
            window.close()


if __name__ == "__main__":
    unittest.main()
