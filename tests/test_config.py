from __future__ import annotations

import base64
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.core.config import AppConfig, AppPaths


def build_paths(root: Path) -> AppPaths:
    paths = AppPaths(
        root=root,
        data_dir=root / "data",
        models_dir=root / "models",
        stt_dir=root / "models" / "stt",
        tts_dir=root / "models" / "tts",
        cache_dir=root / "cache",
        chats_dir=root / "data" / "chats",
        history_file=root / "data" / "conversation_history.json",
        settings_file=root / "data" / "settings.json",
    )
    for directory in (paths.data_dir, paths.models_dir, paths.stt_dir, paths.tts_dir, paths.cache_dir, paths.chats_dir):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


class ConfigTests(unittest.TestCase):
    def test_window_session_settings_round_trip_and_reject_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.window_geometry = base64.b64encode(b"bounded-qt-geometry").decode("ascii")
            config.active_page = 2
            config.save(paths)

            loaded = AppConfig.load(paths)

            self.assertEqual(config.window_geometry, loaded.window_geometry)
            self.assertEqual(2, loaded.active_page)

            paths.settings_file.write_text(
                '{"window_geometry": "not base64%%%", "active_page": 9}',
                encoding="utf-8",
            )
            self.assertEqual("", AppConfig.load(paths).window_geometry)
            self.assertEqual(0, AppConfig.load(paths).active_page)

    def test_chat_drafts_are_validated_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            valid_id = "a" * 32
            config = AppConfig.defaults(paths)
            config.last_conversation_id = valid_id
            config.chat_drafts = {valid_id: "  unfinished\nmessage  "}
            config.save(paths)

            loaded = AppConfig.load(paths)

            self.assertEqual(valid_id, loaded.last_conversation_id)
            self.assertEqual("  unfinished\nmessage  ", loaded.chat_drafts[valid_id])

            oversized_drafts = {
                f"{index:032x}": "x" * 25_000
                for index in range(25)
            }
            oversized_drafts["invalid/path"] = "unsafe"
            paths.settings_file.write_text(
                json.dumps(
                    {
                        "last_conversation_id": "../invalid",
                        "chat_drafts": oversized_drafts,
                    }
                ),
                encoding="utf-8",
            )

            bounded = AppConfig.load(paths)

            self.assertEqual("", bounded.last_conversation_id)
            self.assertLessEqual(len(bounded.chat_drafts), 20)
            self.assertTrue(all(len(text) <= 20_000 for text in bounded.chat_drafts.values()))
            self.assertLessEqual(sum(map(len, bounded.chat_drafts.values())), 100_000)
            self.assertNotIn("invalid/path", bounded.chat_drafts)

    def test_save_is_atomic_and_keeps_a_last_known_good_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.ollama_model = "qwen3.5:4b"

            config.save(paths)

            backup_path = paths.settings_file.with_suffix(".json.bak")
            self.assertEqual(
                json.loads(paths.settings_file.read_text(encoding="utf-8")),
                json.loads(backup_path.read_text(encoding="utf-8")),
            )
            self.assertFalse(paths.settings_file.with_suffix(".json.tmp").exists())
            self.assertFalse(backup_path.with_suffix(".bak.tmp").exists())

    def test_corrupt_primary_settings_recover_from_backup_and_repair_the_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.ollama_model = "qwen3.5:4b"
            config.web_search_enabled = True
            config.save(paths)
            paths.settings_file.write_text('{"ollama_model":', encoding="utf-8")

            loaded = AppConfig.load(paths)

            self.assertEqual("qwen3.5:4b", loaded.ollama_model)
            self.assertTrue(loaded.web_search_enabled)
            repaired = json.loads(paths.settings_file.read_text(encoding="utf-8"))
            self.assertEqual("qwen3.5:4b", repaired["ollama_model"])

    def test_failed_atomic_replace_preserves_existing_settings_and_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.ollama_model = "llama3.2:3b"
            config.save(paths)
            original = paths.settings_file.read_text(encoding="utf-8")
            config.ollama_model = "qwen3.5:4b"

            with patch("local_matrix_assistant.core.config.os.replace", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    config.save(paths)

            self.assertEqual(original, paths.settings_file.read_text(encoding="utf-8"))
            self.assertFalse(paths.settings_file.with_suffix(".json.tmp").exists())

    def test_load_bounds_invalid_scalar_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            paths.settings_file.write_text(
                json.dumps(
                    {
                        "ollama_base_url": 42,
                        "voice_enabled": "yes",
                        "auto_speak_responses": 0,
                        "web_search_enabled": "false",
                        "microphone_muted": [],
                        "tts_rate": 99,
                        "tts_volume": -5,
                        "preferred_input_name": 7,
                    }
                ),
                encoding="utf-8",
            )

            loaded = AppConfig.load(paths)

            defaults = AppConfig.defaults(paths)
            self.assertEqual(defaults.ollama_base_url, loaded.ollama_base_url)
            self.assertTrue(loaded.voice_enabled)
            self.assertTrue(loaded.auto_speak_responses)
            self.assertFalse(loaded.web_search_enabled)
            self.assertFalse(loaded.microphone_muted)
            self.assertEqual(1.5, loaded.tts_rate)
            self.assertEqual(0.0, loaded.tts_volume)
            self.assertEqual("", loaded.preferred_input_name)

    def test_microphone_mute_setting_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.microphone_muted = True
            config.model_profile = "coding"

            config.save(paths)
            loaded = AppConfig.load(paths)

            self.assertTrue(loaded.microphone_muted)
            self.assertEqual("coding", loaded.model_profile)

    def test_continuous_voice_setting_round_trips_and_rejects_non_boolean_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.continuous_voice_enabled = True
            config.voice_enabled = False
            config.auto_speak_responses = False
            config.save(paths)

            loaded = AppConfig.load(paths)
            self.assertTrue(loaded.continuous_voice_enabled)
            self.assertTrue(loaded.voice_enabled)
            self.assertTrue(loaded.auto_speak_responses)

            paths.settings_file.write_text(
                '{"continuous_voice_enabled": "yes"}',
                encoding="utf-8",
            )
            self.assertFalse(AppConfig.load(paths).continuous_voice_enabled)

    def test_invalid_model_profile_falls_back_to_auto(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            paths.settings_file.write_text('{"model_profile": "oversized"}', encoding="utf-8")

            loaded = AppConfig.load(paths)

            self.assertEqual("auto", loaded.model_profile)

    def test_sidebar_preference_round_trips_and_rejects_non_boolean_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.sidebar_collapsed = True
            config.save(paths)

            self.assertTrue(AppConfig.load(paths).sidebar_collapsed)

            paths.settings_file.write_text('{"sidebar_collapsed": "yes"}', encoding="utf-8")
            self.assertFalse(AppConfig.load(paths).sidebar_collapsed)

    def test_theme_round_trips_and_invalid_values_fall_back_to_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_paths(Path(tmp))
            config = AppConfig.defaults(paths)
            config.theme = "violet"
            config.save(paths)

            self.assertEqual("violet", AppConfig.load(paths).theme)

            config.theme = "red"
            config.save(paths)
            self.assertEqual("red", AppConfig.load(paths).theme)

            paths.settings_file.write_text('{"theme": "unknown"}', encoding="utf-8")
            self.assertEqual("matrix", AppConfig.load(paths).theme)

    def test_load_and_save_preserves_preferred_input_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = AppPaths(
                root=root,
                data_dir=root / "data",
                models_dir=root / "models",
                stt_dir=root / "models" / "stt",
                tts_dir=root / "models" / "tts",
                cache_dir=root / "cache",
                chats_dir=root / "data" / "chats",
                history_file=root / "data" / "conversation_history.json",
                settings_file=root / "data" / "settings.json",
            )
            for directory in (paths.data_dir, paths.models_dir, paths.stt_dir, paths.tts_dir, paths.cache_dir, paths.chats_dir):
                directory.mkdir(parents=True, exist_ok=True)

            config = AppConfig.defaults(paths)
            config.preferred_input_name = "USB Microphone"
            config.playback_output_name = "Desk Speakers"
            config.working_folders = [str(root / "projects"), str(root / "notes")]
            config.active_working_folder = str(root / "notes")
            config.save(paths)

            loaded = AppConfig.load(paths)
            self.assertEqual("USB Microphone", loaded.preferred_input_name)
            self.assertEqual("Desk Speakers", loaded.playback_output_name)
            self.assertEqual([str(root / "notes")], loaded.working_folders)
            self.assertEqual(str(root / "notes"), loaded.active_working_folder)

    def test_load_repairs_invalid_working_folder_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = AppPaths(
                root=root,
                data_dir=root / "data",
                models_dir=root / "models",
                stt_dir=root / "models" / "stt",
                tts_dir=root / "models" / "tts",
                cache_dir=root / "cache",
                chats_dir=root / "data" / "chats",
                history_file=root / "data" / "conversation_history.json",
                settings_file=root / "data" / "settings.json",
            )
            paths.data_dir.mkdir(parents=True)
            first = str(root / "first")
            paths.settings_file.write_text(
                json.dumps(
                    {
                        "working_folders": [first, first, 42],
                        "active_working_folder": str(root / "missing"),
                    }
                ),
                encoding="utf-8",
            )

            loaded = AppConfig.load(paths)

            self.assertEqual([first], loaded.working_folders)
            self.assertEqual(first, loaded.active_working_folder)

    def test_load_repairs_invalid_voice_model_paths_to_project_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = AppPaths(
                root=root,
                data_dir=root / "data",
                models_dir=root / "models",
                stt_dir=root / "models" / "stt",
                tts_dir=root / "models" / "tts",
                cache_dir=root / "cache",
                chats_dir=root / "data" / "chats",
                history_file=root / "data" / "conversation_history.json",
                settings_file=root / "data" / "settings.json",
            )
            for directory in (paths.data_dir, paths.models_dir, paths.stt_dir, paths.tts_dir, paths.cache_dir, paths.chats_dir):
                directory.mkdir(parents=True, exist_ok=True)

            default_stt = paths.stt_dir / "vosk-model-small-en-us-0.15"
            default_tts = paths.tts_dir / "en_US-lessac-low.onnx"
            default_tts_config = paths.tts_dir / "en_US-lessac-low.onnx.json"
            default_stt.mkdir(parents=True, exist_ok=True)
            default_tts.write_text("model", encoding="utf-8")
            default_tts_config.write_text("config", encoding="utf-8")

            paths.settings_file.write_text(
                """
{
  "stt_model_dir": "D:\\\\missing\\\\stt",
  "tts_model_path": "D:\\\\missing\\\\voice.onnx",
  "tts_config_path": "D:\\\\missing\\\\voice.onnx.json"
}
""".strip(),
                encoding="utf-8",
            )

            loaded = AppConfig.load(paths)
            self.assertEqual(str(default_stt), loaded.stt_model_dir)
            self.assertEqual(str(default_tts), loaded.tts_model_path)
            self.assertEqual(str(default_tts_config), loaded.tts_config_path)


if __name__ == "__main__":
    unittest.main()
