from __future__ import annotations

import os
import unittest
from dataclasses import replace
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QMainWindow, QScrollArea

from local_matrix_assistant.core.config import AppConfig
from local_matrix_assistant.core.models import (
    ChatMessage,
    ConversationMemory,
    ModelPullProgress,
    WebSearchResponse,
    WebSearchResult,
)
from local_matrix_assistant.services.context_manager import ContextManager
from local_matrix_assistant.services.model_router import ModelRouter, ModelSelection
from local_matrix_assistant.ui.chat_panel import ChatPanel
from local_matrix_assistant.ui.main_window import MainWindow
from local_matrix_assistant.ui.settings_panel import SettingsPanel
from local_matrix_assistant.ui.theme import THEME_OPTIONS, stylesheet_for_theme
from local_matrix_assistant.ui.voice_panel import VoicePanel


def build_config(*, web_search_enabled: bool = False, ollama_model: str = "") -> AppConfig:
    return AppConfig(
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model=ollama_model,
        ollama_windows_path="",
        stt_model_dir="models/stt",
        tts_model_path="models/tts/voice.onnx",
        tts_config_path="models/tts/voice.onnx.json",
        voice_enabled=True,
        auto_speak_responses=True,
        tts_rate=1.0,
        tts_volume=1.0,
        preferred_input_name="",
        playback_output_name="",
        web_search_enabled=web_search_enabled,
        working_folders=[],
        active_working_folder="",
    )


class MainWindowStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_apply_initial_ui_state_uses_saved_web_search_setting(self) -> None:
        config = build_config(web_search_enabled=True)
        window = MainWindow.__new__(MainWindow)
        window.config = config
        window.chat_panel = ChatPanel()
        window.voice_panel = VoicePanel(config)
        window.tts_service = type("FakeTts", (), {"engine_name": "Piper (local)"})()

        window._apply_initial_ui_state()

        self.assertTrue(window.chat_panel.web_search_button.isChecked())
        self.assertEqual("Web Search On", window.chat_panel.web_search_button.text())
        self.assertEqual("Piper (local)", window.voice_panel.engine_value.text())

        window.chat_panel.close()
        window.voice_panel.close()

    def test_header_compact_mode_button_requests_compact_window(self) -> None:
        window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(window)
        header = window._build_header()
        requests: list[bool] = []
        window.compact_mode_requested.connect(lambda: requests.append(True))
        window.compact_mode_button.clicked.connect(window.compact_mode_requested.emit)

        window.compact_mode_button.click()

        self.assertEqual([True], requests)
        self.assertEqual("Compact Mode", window.compact_mode_button.text())
        header.close()
        window.deleteLater()

    def test_sync_available_models_clears_stale_entries_when_none_are_available(self) -> None:
        config = build_config(ollama_model="gemma3:1b")
        window = MainWindow.__new__(MainWindow)
        window.config = config
        window.settings_panel = SettingsPanel(config)
        window.settings_panel.model_combo.addItems(["gemma3:1b", "qwen3:4b"])

        updates: list[dict[str, str]] = []

        def fake_update_config(**changes: str) -> None:
            updates.append(changes)
            window.config = replace(window.config, **changes)

        window._update_config = fake_update_config  # type: ignore[method-assign]

        window._sync_available_models([])

        self.assertEqual(0, window.settings_panel.model_combo.count())
        self.assertEqual([], updates)

        window.settings_panel.close()

    def test_sync_available_models_selects_first_available_when_saved_model_is_missing(self) -> None:
        config = build_config(ollama_model="missing-model")
        window = MainWindow.__new__(MainWindow)
        window.config = config
        window.settings_panel = SettingsPanel(config)

        def fake_update_config(**changes: str) -> None:
            window.config = replace(window.config, **changes)

        window._update_config = fake_update_config  # type: ignore[method-assign]

        window._sync_available_models(["gemma3:1b", "qwen3:4b"])

        self.assertEqual(2, window.settings_panel.model_combo.count())
        self.assertEqual("gemma3:1b", window.settings_panel.model_combo.currentText())
        self.assertEqual("gemma3:1b", window.config.ollama_model)

        window.settings_panel.close()

    def test_model_installer_tracks_installed_progress_and_cancel_states(self) -> None:
        panel = SettingsPanel(build_config())
        panel.model_install_combo.setCurrentIndex(
            panel.model_install_combo.findData("llama3.2:3b")
        )

        panel.set_installed_models([])
        self.assertEqual("Install", panel.model_install_button.text())
        self.assertTrue(panel.model_install_button.isEnabled())

        panel.set_model_install_busy("llama3.2:3b")
        panel.set_model_install_progress(
            ModelPullProgress("llama3.2:3b", "downloading layer", 512, 1024)
        )
        self.assertEqual(50, panel.model_install_progress.value())
        self.assertIn("50%", panel.model_install_status.text())
        self.assertFalse(panel.model_install_combo.isEnabled())
        self.assertFalse(panel.model_cancel_button.isHidden())

        panel.set_model_install_canceled("llama3.2:3b")
        self.assertTrue(panel.model_install_combo.isEnabled())
        self.assertTrue(panel.model_install_progress.isHidden())
        self.assertIn("Canceled", panel.model_install_status.text())

        panel.set_installed_models(["llama3.2:3b"])
        self.assertEqual("Installed", panel.model_install_button.text())
        self.assertFalse(panel.model_install_button.isEnabled())
        panel.close()

    def test_model_installer_has_no_horizontal_overflow_at_compact_width(self) -> None:
        panel = SettingsPanel(build_config())
        panel.resize(796, 640)
        panel.show()
        self.app.processEvents()

        scroll = panel.findChild(QScrollArea)
        self.assertIsNotNone(scroll)
        assert scroll is not None
        self.assertEqual(0, scroll.horizontalScrollBar().maximum())
        self.assertLessEqual(panel.model_install_combo.width(), scroll.viewport().width())
        self.assertGreater(panel.model_install_button.width(), 0)
        panel.close()

    def test_settings_page_uses_grouped_cards_and_a_persistent_action_bar(self) -> None:
        panel = SettingsPanel(build_config())
        cards = panel.findChildren(QFrame, "settingsCard")
        action_bar = panel.findChild(QFrame, "settingsActionBar")
        scroll = panel.findChild(QScrollArea, "settingsScroll")

        self.assertEqual(
            ["Appearance settings", "Local AI settings", "Voice runtime settings"],
            [card.accessibleName() for card in cards],
        )
        self.assertIsNotNone(action_bar)
        self.assertIsNotNone(scroll)
        self.assertIsNone(panel.findChild(QFrame, "settingsHero"))
        self.assertEqual([], panel.findChildren(QLabel, "settingsSectionBadge"))
        assert action_bar is not None and scroll is not None
        self.assertFalse(scroll.isAncestorOf(action_bar))
        self.assertEqual("primaryButton", panel.save_button.objectName())
        panel.close()

    def test_font_options_are_plain_non_editable_aligned_dropdowns(self) -> None:
        panel = SettingsPanel(build_config())
        panel.resize(796, 640)
        panel.show()
        self.app.processEvents()

        self.assertFalse(panel.font_family_combo.isEditable())
        self.assertFalse(panel.font_size_combo.isEditable())
        self.assertEqual(
            panel.font_family_combo.height(),
            panel.font_size_combo.height(),
        )
        self.assertTrue(
            all(
                panel.font_family_combo.itemIcon(index).isNull()
                for index in range(panel.font_family_combo.count())
            )
        )
        self.assertTrue(
            all(
                isinstance(
                    panel.font_family_combo.itemData(
                        index,
                        Qt.ItemDataRole.FontRole,
                    ),
                    QFont,
                )
                and panel.font_family_combo.itemData(
                    index,
                    Qt.ItemDataRole.FontRole,
                ).family()
                == panel.font_family_combo.itemText(index)
                for index in range(panel.font_family_combo.count())
            )
        )
        panel.close()

    def test_theme_selector_applies_and_persists_the_selected_theme(self) -> None:
        config = build_config()
        window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(window)
        window.config = config
        window.settings_panel = SettingsPanel(config)
        window._set_activity = lambda _text: None  # type: ignore[method-assign]

        def fake_update_config(**changes: str) -> bool:
            window.config = replace(window.config, **changes)
            return True

        window._update_config = fake_update_config  # type: ignore[method-assign]
        window.settings_panel.theme_combo.setCurrentIndex(
            window.settings_panel.theme_combo.findData("ocean")
        )
        window._on_theme_changed()

        self.assertEqual("ocean", window.config.theme)
        self.assertEqual(stylesheet_for_theme("ocean"), window.styleSheet())
        window.settings_panel.close()
        window.deleteLater()

    def test_theme_persistence_is_debounced_from_visual_application(self) -> None:
        config = build_config()
        window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(window)
        window.config = config
        window.settings_panel = SettingsPanel(config)
        window._set_activity = lambda _text: None  # type: ignore[method-assign]
        saved_themes: list[str] = []

        class FakeTimer:
            def __init__(self) -> None:
                self.start_calls = 0

            def start(self) -> None:
                self.start_calls += 1

        timer = FakeTimer()
        window._theme_save_timer = timer  # type: ignore[assignment]

        def fake_update_config(**changes: str) -> bool:
            saved_themes.append(changes["theme"])
            return True

        window._update_config = fake_update_config  # type: ignore[method-assign]
        window.settings_panel.theme_combo.setCurrentIndex(
            window.settings_panel.theme_combo.findData("ocean")
        )
        window._on_theme_changed()

        self.assertEqual("ocean", window.config.theme)
        self.assertEqual(1, timer.start_calls)
        self.assertEqual([], saved_themes)

        window._save_theme_selection()

        self.assertEqual(["ocean"], saved_themes)
        window.settings_panel.close()
        window.deleteLater()

    def test_theme_stylesheets_are_cached(self) -> None:
        first = stylesheet_for_theme("violet")
        second = stylesheet_for_theme("violet")

        self.assertIs(first, second)

    def test_theme_selector_shows_previews_for_all_ten_themes(self) -> None:
        panel = SettingsPanel(build_config())
        theme_ids = [theme_id for theme_id, _name in THEME_OPTIONS]

        self.assertEqual(10, panel.theme_combo.count())
        self.assertEqual(
            theme_ids,
            [panel.theme_combo.itemData(index) for index in range(panel.theme_combo.count())],
        )
        self.assertTrue(
            all(
                not panel.theme_combo.itemIcon(index).isNull()
                for index in range(panel.theme_combo.count())
            )
        )
        panel.close()

    def test_font_selector_applies_chat_input_and_compact_typography(self) -> None:
        config = build_config()
        window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(window)
        window.config = config
        window.settings_panel = SettingsPanel(config)
        window._set_activity = lambda _text: None  # type: ignore[method-assign]

        def fake_update_config(**changes) -> bool:
            window.config = replace(window.config, **changes)
            return True

        window._update_config = fake_update_config  # type: ignore[method-assign]
        selected_family = window.settings_panel.font_family_combo.itemText(0)
        window.settings_panel.font_family_combo.setCurrentText(selected_family)
        window.settings_panel.font_size_combo.setCurrentIndex(
            window.settings_panel.font_size_combo.findData(16)
        )
        window._on_font_changed()

        stylesheet = window.styleSheet()
        self.assertEqual(selected_family, window.config.chat_font_family)
        self.assertEqual(16, window.config.chat_font_size)
        self.assertIn(f'font-family: "{selected_family}";', stylesheet)
        self.assertIn("font-size: 16pt;", stylesheet)
        self.assertIn("QLabel#messageBody", stylesheet)
        self.assertIn("QPlainTextEdit#chatInput", stylesheet)
        self.assertIn("QWidget#compactAssistant QLabel", stylesheet)
        window.settings_panel.close()
        window.deleteLater()

    def test_new_theme_stylesheets_are_distinct(self) -> None:
        stylesheets = {
            stylesheet_for_theme(theme)
            for theme in ("cyan", "teal", "pink", "orange", "lime")
        }

        self.assertEqual(5, len(stylesheets))
        self.assertNotIn(stylesheet_for_theme("matrix"), stylesheets)

    def test_auto_routing_updates_composer_and_coding_system_prompt(self) -> None:
        config = build_config(ollama_model="llama3.2:3b")
        window = MainWindow.__new__(MainWindow)
        window.config = config
        window.chat_panel = ChatPanel()
        window.settings_panel = SettingsPanel(config)
        window.voice_panel = VoicePanel(config)
        window.model_router = ModelRouter()
        window.available_ollama_models = ["llama3.2:3b", "qwen3:4b", "qwen2.5-coder:7b"]
        window.settings_panel.model_combo.addItems(window.available_ollama_models)
        window.settings_panel.model_combo.setCurrentText("llama3.2:3b")
        window.chat_panel.set_model_profile("auto")
        window._active_model_selection = None

        selection = window._select_model_for_prompt("Debug this Python traceback and add a unit test")

        self.assertEqual("qwen2.5-coder:7b", selection.model)
        self.assertEqual("coding", selection.profile)
        self.assertIn("Coding -> qwen2.5-coder:7b", window.chat_panel.composer_hint.text())

        window.messages = [ChatMessage(role="user", content="Debug it", timestamp="now")]
        window.history_store = type("FakeHistory", (), {"now_stamp": staticmethod(lambda: "now")})()
        prepared, stats = window._prepare_request_messages(None)

        self.assertEqual(0, stats.trimmed_messages)
        self.assertEqual("system", prepared[0].role)
        self.assertIn("production-ready", prepared[0].content)

        window.chat_panel.close()
        window.settings_panel.close()
        window.voice_panel.close()

    def test_context_budget_preserves_latest_turn_and_omits_complete_old_turns(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window.model_router = ModelRouter()
        window._active_model_selection = ModelSelection(
            "qwen2.5-coder:7b",
            "coding",
            "test",
            automatic=False,
            context_window=1024,
            max_output_tokens=256,
        )
        window.history_store = type("FakeHistory", (), {"now_stamp": staticmethod(lambda: "now")})()
        window.messages = []
        for index in range(5):
            window.messages.extend(
                [
                    ChatMessage("user", f"question-{index} " + "q" * 320, "now"),
                    ChatMessage("assistant", f"answer-{index} " + "a" * 320, "now"),
                ]
            )
        window.messages.append(ChatMessage("user", "latest request " + "z" * 180, "now"))

        prepared, stats = window._prepare_request_messages(None)
        conversation = [item for item in prepared if item.role != "system"]

        self.assertGreater(stats.trimmed_messages, 0)
        self.assertEqual("user", conversation[0].role)
        self.assertIn("latest request", conversation[-1].content)
        self.assertLessEqual(stats.estimated_tokens, stats.token_budget)

    def test_web_context_is_capped_and_kept_separate_from_user_prompt(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window.model_router = ModelRouter()
        window._active_model_selection = ModelSelection(
            "llama3.2:3b",
            "fast",
            "test",
            automatic=False,
            context_window=1024,
            max_output_tokens=256,
        )
        window.history_store = type("FakeHistory", (), {"now_stamp": staticmethod(lambda: "now")})()
        window.web_search_service = type(
            "FakeWebSearch",
            (),
            {"build_prompt_context": staticmethod(lambda _response: "source detail " * 2000)},
        )()
        window.messages = [ChatMessage("user", "What happened?", "now")]
        response = WebSearchResponse(
            provider="test",
            query="event",
            results=[WebSearchResult("Source", "https://example.com", "Snippet")],
        )

        prepared, stats = window._prepare_request_messages(response)

        self.assertEqual(2, len([item for item in prepared if item.role == "system"]))
        self.assertEqual("What happened?", prepared[-1].content)
        self.assertTrue(prepared[1].metadata["context_truncated"])
        self.assertLessEqual(ContextManager.estimate_messages_tokens(prepared), stats.token_budget)

    def test_persisted_memory_replaces_covered_messages_without_overlap(self) -> None:
        window = MainWindow.__new__(MainWindow)
        window.model_router = ModelRouter()
        window._active_model_selection = ModelSelection(
            "llama3.2:3b",
            "fast",
            "test",
            automatic=False,
            context_window=4096,
            max_output_tokens=768,
        )
        window.history_store = type("FakeHistory", (), {"now_stamp": staticmethod(lambda: "now")})()
        window.messages = [
            ChatMessage("user", "old secret requirement", "now"),
            ChatMessage("assistant", "old acknowledgement", "now"),
            ChatMessage("user", "latest request", "now"),
        ]
        window.conversation_memory = ConversationMemory(
            "- User has an earlier project requirement.",
            2,
            "now",
            "local_model",
        )

        prepared, stats = window._prepare_request_messages(None)
        combined = "\n".join(message.content for message in prepared)

        self.assertNotIn("old secret requirement", combined)
        self.assertIn("earlier project requirement", combined)
        self.assertEqual("latest request", prepared[-1].content)
        self.assertEqual(2, stats.memory_messages)
        self.assertEqual(0, stats.unsummarized_messages)



if __name__ == "__main__":
    unittest.main()
