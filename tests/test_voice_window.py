from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from local_matrix_assistant.core.config import AppConfig
from local_matrix_assistant.services.model_router import ModelSelection
from local_matrix_assistant.ui.chat_panel import ChatPanel
from local_matrix_assistant.ui.main_window_chat import ChatWindowMixin
from local_matrix_assistant.ui.main_window_voice import VoiceWindowMixin
from local_matrix_assistant.ui.voice_panel import VoicePanel


def build_config() -> AppConfig:
    return AppConfig(
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="test-model",
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
        web_search_enabled=False,
        working_folders=[],
        active_working_folder="",
        microphone_muted=False,
    )


class FakeRecorder:
    def __init__(self) -> None:
        self.recording = False
        self.start_calls = 0
        self.cancel_calls = 0
        self.available_inputs = ["Desk Mic"]
        self.input_device_name = ""

    def is_recording(self) -> bool:
        return self.recording

    def start(self) -> None:
        self.start_calls += 1
        self.recording = True

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.recording = False

    def stop(self) -> bytes:
        self.recording = False
        return b"captured-wave"

    @staticmethod
    def inspect_wav(_wav_bytes: bytes) -> tuple[float, int]:
        return 1.2, 900

    def list_inputs(self) -> list[str]:
        return list(self.available_inputs)

    def set_input_device_name(self, name: str) -> None:
        self.input_device_name = name


class FakePlayer:
    def __init__(self) -> None:
        self.playing = False
        self.stop_calls = 0
        self.played: list[bytes] = []
        self.available_outputs = ["Desk Speakers"]
        self.output_device_name = ""

    def is_playing(self) -> bool:
        return self.playing

    def stop(self) -> None:
        self.stop_calls += 1
        self.playing = False

    def play_wav(self, payload: bytes) -> None:
        self.played.append(payload)
        self.playing = True

    def list_outputs(self) -> list[str]:
        return list(self.available_outputs)

    def set_output_device_name(self, name: str) -> None:
        self.output_device_name = name


class FakeTtsService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def synthesize(self, text: str, *, rate: float, volume: float) -> bytes:
        del rate, volume
        self.calls.append(text)
        return f"wave:{text}".encode()


class FakeSttService:
    @staticmethod
    def transcribe(_wav_bytes: bytes) -> str:
        return "automatic endpoint"


class DeferredRunner:
    def __init__(self) -> None:
        self.pending: list[tuple[object, object, object]] = []

    def start(self, worker, on_result, on_error) -> None:
        self.pending.append((worker, on_result, on_error))

    def complete_next(self) -> None:
        worker, on_result, on_error = self.pending.pop(0)
        try:
            on_result(worker.fn())
        except Exception as exc:  # noqa: BLE001
            on_error(str(exc))


class FakeStreamWorker:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeTimer:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.active = False
        self.start_calls = 0

    def start(self) -> None:
        self.active = True
        self.start_calls += 1

    def stop(self) -> None:
        self.active = False

    def isActive(self) -> bool:
        return self.active

    def fire(self) -> None:
        if not self.active:
            return
        self.active = False
        self.callback()


class FakeAgentPanel:
    def __init__(self) -> None:
        self.busy = False

    def set_busy(self, busy: bool) -> None:
        self.busy = busy


class VoiceHarness(ChatWindowMixin, VoiceWindowMixin):
    def __init__(self) -> None:
        self.config = build_config()
        self.chat_panel = ChatPanel()
        self.voice_panel = VoicePanel(self.config)
        self.recorder = FakeRecorder()
        self.player = FakePlayer()
        self.tts_service = FakeTtsService()
        self.stt_service = FakeSttService()
        self.task_runner = DeferredRunner()
        self.agent_panel = FakeAgentPanel()
        self._awaiting_response = False
        self._active_stream_worker = None
        self._voice_capture_pending = False
        self._cancel_requested = False
        self._voice_input_request_id = 0
        self._tts_request_id = 0
        self._continuous_voice_armed = False
        self._continuous_voice_timer = FakeTimer(self._resume_continuous_voice_capture)
        self.activities: list[str] = []
        self.appended_messages: list[tuple[str, str, dict | None]] = []
        self.reply_requests: list[tuple[str, str]] = []

    def _set_activity(self, text: str) -> None:
        self.activities.append(text)

    def _update_config(self, **changes: object) -> None:
        self.config = replace(self.config, **changes)

    def _try_run_agent_command(self, _text: str, *, source: str) -> bool:
        self.assert_voice_source = source
        return False

    def _append_message(self, role: str, content: str, metadata=None):
        self.appended_messages.append((role, content, metadata))

    @staticmethod
    def _enable_web_search_if_requested(_text: str):
        return None

    @staticmethod
    def _select_model_for_prompt(_text: str) -> ModelSelection:
        return ModelSelection("test-model", "fast", "voice test", automatic=True)

    def _begin_assistant_response(self, model: str, text: str) -> None:
        self.reply_requests.append((model, text))

    def close(self) -> None:
        self.chat_panel.close()
        self.voice_panel.close()


class VoiceWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_microphone_mute_blocks_capture_and_syncs_both_controls(self) -> None:
        harness = VoiceHarness()

        harness._on_microphone_muted_toggled(True)
        harness._toggle_voice_mode()

        self.assertTrue(harness.config.microphone_muted)
        self.assertTrue(harness.voice_panel.microphone_muted_checkbox.isChecked())
        self.assertTrue(harness.chat_panel.voice_only_panel.mute_button.isChecked())
        self.assertEqual("Unmute Mic", harness.chat_panel.voice_only_panel.mute_button.text())
        self.assertEqual("Audio: Muted", harness.voice_panel.audio_state_value.text())
        self.assertEqual(0, harness.recorder.start_calls)
        harness.close()

    def test_hands_free_toggle_syncs_controls_and_required_voice_settings(self) -> None:
        harness = VoiceHarness()
        harness.config = replace(
            harness.config,
            voice_enabled=False,
            auto_speak_responses=False,
        )

        harness._on_continuous_voice_toggled(True)

        self.assertTrue(harness.config.continuous_voice_enabled)
        self.assertTrue(harness.config.voice_enabled)
        self.assertTrue(harness.config.auto_speak_responses)
        self.assertTrue(harness.voice_panel.continuous_voice_checkbox.isChecked())
        self.assertTrue(harness.voice_panel.voice_enabled_checkbox.isChecked())
        self.assertTrue(harness.voice_panel.auto_speak_checkbox.isChecked())
        self.assertTrue(harness.chat_panel.voice_only_panel.continuous_button.isChecked())
        self.assertEqual("Hands-Free On", harness.chat_panel.voice_only_panel.continuous_button.text())
        harness.close()

    def test_voice_only_header_fits_the_minimum_window_content_width(self) -> None:
        harness = VoiceHarness()
        panel = harness.chat_panel.voice_only_panel
        panel.resize(796, 640)
        panel.set_continuous_enabled(True)
        panel.show()
        self.app.processEvents()

        self.assertLessEqual(panel.minimumSizeHint().width(), 796)
        for button in (panel.close_button, panel.continuous_button, panel.mute_button):
            self.assertLessEqual(button.geometry().right(), panel.width())
        harness.close()

    def test_voice_visualizer_is_keyboard_operable_and_exposes_state(self) -> None:
        harness = VoiceHarness()
        panel = harness.chat_panel.voice_only_panel
        activations: list[str] = []
        panel.toggle_requested.connect(lambda: activations.append("toggle"))
        panel.show()
        panel.visualizer.setFocus()
        self.app.processEvents()

        self.assertEqual(Qt.FocusPolicy.StrongFocus, panel.visualizer.focusPolicy())
        self.assertEqual("Voice capture control", panel.visualizer.accessibleName())
        QTest.keyClick(panel.visualizer, Qt.Key.Key_Space)
        QTest.keyClick(panel.visualizer, Qt.Key.Key_Return)
        self.assertEqual(["toggle", "toggle"], activations)

        harness._apply_audio_state("Recording")
        self.assertEqual(
            "Stop and transcribe voice capture",
            harness.chat_panel.voice_button.accessibleName(),
        )
        self.assertEqual(
            "Voice status: Recording",
            panel.state_label.accessibleName(),
        )
        self.assertIn("Recording", panel.visualizer.accessibleDescription())

        panel.set_recovery_message("Reconnect the microphone.")
        self.assertIn("recovery needed", panel.hint_label.accessibleDescription())
        harness.close()

    def test_voice_shortcut_wrappers_sync_mute_and_stop_output(self) -> None:
        harness = VoiceHarness()
        harness.player.playing = True

        harness._toggle_microphone_mute_shortcut()
        self.assertTrue(harness.config.microphone_muted)
        self.assertEqual(
            "Unmute microphone",
            harness.chat_panel.voice_only_panel.mute_button.accessibleName(),
        )

        harness._stop_voice_output_shortcut()
        self.assertEqual(1, harness.player.stop_calls)
        self.assertFalse(harness.player.is_playing())
        harness.close()

    def test_hands_free_resumes_capture_only_after_full_playback(self) -> None:
        harness = VoiceHarness()
        harness._show_voice_only_screen()
        harness._on_continuous_voice_toggled(True)

        harness._speak_response("A complete local spoken reply.")
        harness.task_runner.complete_next()

        self.assertFalse(harness._continuous_voice_timer.isActive())
        harness.player.playing = False
        harness._on_playback_changed(False, "Speaker idle")

        self.assertTrue(harness._continuous_voice_timer.isActive())
        self.assertEqual(0, harness.recorder.start_calls)
        harness._continuous_voice_timer.fire()

        self.assertEqual(1, harness.recorder.start_calls)
        self.assertTrue(harness.recorder.is_recording())
        self.assertIn("Hands-free listening...", harness.activities)
        harness.close()

    def test_hands_free_resume_is_canceled_by_mute_stop_and_leaving_voice_only(self) -> None:
        for cancel_action in (
            lambda harness: harness._on_microphone_muted_toggled(True),
            lambda harness: harness._stop_voice_output(),
            lambda harness: harness._hide_voice_only_screen(),
        ):
            with self.subTest(cancel_action=cancel_action):
                harness = VoiceHarness()
                harness._show_voice_only_screen()
                harness._on_continuous_voice_toggled(True)
                harness._continuous_voice_armed = True
                harness._schedule_continuous_voice_resume()

                cancel_action(harness)
                harness._continuous_voice_timer.fire()

                self.assertFalse(harness._continuous_voice_timer.isActive())
                self.assertEqual(0, harness.recorder.start_calls)
                harness.close()

    def test_preview_and_tts_error_never_trigger_hands_free_listening(self) -> None:
        harness = VoiceHarness()
        harness._show_voice_only_screen()
        harness._on_continuous_voice_toggled(True)

        harness._preview_voice()
        harness.task_runner.complete_next()
        harness.player.playing = False
        harness._on_playback_changed(False, "Speaker idle")
        self.assertFalse(harness._continuous_voice_timer.isActive())

        harness._speak_response("Reply that fails to synthesize.")
        request_id = harness._tts_request_id
        harness._on_tts_error(request_id, "model unavailable")
        harness._on_playback_changed(False, "Speaker idle")

        self.assertFalse(harness._continuous_voice_timer.isActive())
        self.assertEqual(0, harness.recorder.start_calls)
        harness.close()

    def test_voice_preview_cannot_replace_active_transcription_stage(self) -> None:
        harness = VoiceHarness()
        harness._voice_stage_timer = FakeTimer(harness._refresh_voice_stage_progress)
        harness._start_voice_capture()
        harness._finish_voice_capture()
        transcription_request_id = harness._voice_input_request_id

        harness._preview_voice()

        self.assertEqual("transcribing", harness._voice_stage)
        self.assertEqual(transcription_request_id, harness._voice_stage_request_id)
        self.assertEqual(1, len(harness.task_runner.pending))
        self.assertEqual([], harness.tts_service.calls)
        self.assertIn("transcription to finish", harness.activities[-1])
        harness.close()

    def test_muting_active_capture_cancels_the_microphone(self) -> None:
        harness = VoiceHarness()
        harness.recorder.recording = True

        harness._on_microphone_muted_toggled(True)

        self.assertEqual(1, harness.recorder.cancel_calls)
        self.assertFalse(harness.recorder.is_recording())
        harness.close()

    def test_silent_disconnected_microphone_watchdog_recovers_voice_ui(self) -> None:
        harness = VoiceHarness()
        harness._voice_capture_health_timer = FakeTimer(harness._check_voice_capture_health)
        harness._continuous_voice_armed = True
        with patch(
            "local_matrix_assistant.ui.main_window_voice.time.monotonic",
            return_value=100.0,
        ):
            harness._start_voice_capture()

        self.assertTrue(harness._voice_capture_health_timer.isActive())
        self.assertTrue(harness.recorder.is_recording())

        with patch(
            "local_matrix_assistant.ui.main_window_voice.time.monotonic",
            return_value=102.0,
        ):
            harness._on_microphone_level(0)
        with patch(
            "local_matrix_assistant.ui.main_window_voice.time.monotonic",
            return_value=105.5,
        ):
            harness._check_voice_capture_health()
        self.assertTrue(harness.recorder.is_recording())

        with patch(
            "local_matrix_assistant.ui.main_window_voice.time.monotonic",
            return_value=106.1,
        ):
            harness._check_voice_capture_health()

        self.assertFalse(harness.recorder.is_recording())
        self.assertEqual(1, harness.recorder.cancel_calls)
        self.assertFalse(harness._voice_capture_health_timer.isActive())
        self.assertFalse(harness._continuous_voice_armed)
        self.assertEqual("Audio: Idle", harness.voice_panel.audio_state_value.text())
        self.assertIn("stopped delivering audio", harness.activities[-1])
        self.assertEqual(
            "error",
            harness.chat_panel.voice_only_panel.hint_label.property("voiceRecovery"),
        )
        self.assertIn("Reconnect", harness.chat_panel.voice_only_panel.hint_label.text())
        harness.close()

    def test_leaving_voice_only_cancels_active_microphone_capture(self) -> None:
        harness = VoiceHarness()
        harness._voice_capture_health_timer = FakeTimer(harness._check_voice_capture_health)
        harness._show_voice_only_screen()
        harness._start_voice_capture()

        harness._hide_voice_only_screen()

        self.assertFalse(harness.chat_panel.voice_only_mode_active())
        self.assertFalse(harness.recorder.is_recording())
        self.assertEqual(1, harness.recorder.cancel_calls)
        self.assertFalse(harness._voice_capture_health_timer.isActive())
        self.assertIn("Voice capture canceled", harness.activities[-1])
        harness.close()

    def test_voice_barge_in_cancels_stream_before_starting_microphone(self) -> None:
        harness = VoiceHarness()
        stream = FakeStreamWorker()
        harness._awaiting_response = True
        harness._active_stream_worker = stream

        harness._toggle_voice_mode()

        self.assertTrue(stream.cancelled)
        self.assertTrue(harness._voice_capture_pending)
        self.assertEqual(0, harness.recorder.start_calls)
        self.assertEqual("Audio: Interrupting", harness.voice_panel.audio_state_value.text())

        harness._awaiting_response = False
        harness._resume_pending_voice_capture()

        self.assertEqual(1, harness.recorder.start_calls)
        self.assertTrue(harness.recorder.is_recording())
        harness.close()

    def test_stale_synthesis_cannot_play_after_voice_capture_starts(self) -> None:
        harness = VoiceHarness()
        harness._speak_response("First response")

        harness._toggle_voice_mode()
        harness.task_runner.complete_next()

        self.assertTrue(harness.recorder.is_recording())
        self.assertEqual([], harness.player.played)
        harness.close()

    def test_long_reply_starts_playing_after_first_chunk_and_prefetches_the_rest(self) -> None:
        harness = VoiceHarness()
        response = " ".join(
            f"Sentence {index} explains a useful local voice behavior in enough detail."
            for index in range(18)
        )

        harness._speak_response(response)

        self.assertEqual("Audio: Synthesizing", harness.voice_panel.audio_state_value.text())
        self.assertTrue(harness.voice_panel.stop_preview_button.isEnabled())
        self.assertEqual(1, len(harness.task_runner.pending))

        harness.task_runner.complete_next()

        self.assertEqual(1, len(harness.player.played))
        self.assertGreater(len(harness._tts_text_chunks), 1)
        self.assertEqual("Audio: Speaking", harness.voice_panel.audio_state_value.text())
        self.assertEqual(1, len(harness.task_runner.pending))

        while harness.task_runner.pending:
            harness.task_runner.complete_next()
        chunk_count = len(harness._tts_text_chunks)
        self.assertEqual(chunk_count, len(harness.tts_service.calls))
        self.assertEqual(1, len(harness.player.played))

        for expected_count in range(2, chunk_count + 1):
            harness.player.playing = False
            harness._on_playback_changed(False, "Speaker idle")
            self.assertEqual(expected_count, len(harness.player.played))

        harness.player.playing = False
        harness._on_playback_changed(False, "Speaker idle")
        self.assertEqual("Audio: Idle", harness.voice_panel.audio_state_value.text())
        harness.close()

    def test_playback_device_failure_clears_queue_and_disables_hands_free_resume(self) -> None:
        harness = VoiceHarness()
        harness._tts_text_chunks = ["first", "second"]
        harness._tts_audio_chunks = {1: b"second"}
        harness._continuous_voice_armed = True

        harness._on_playback_changed(
            False,
            "Playback ended after a device error: device disconnected",
        )

        self.assertEqual([], harness._tts_text_chunks)
        self.assertEqual({}, harness._tts_audio_chunks)
        self.assertFalse(harness._continuous_voice_armed)
        self.assertFalse(harness._continuous_voice_timer.isActive())
        self.assertEqual("Audio: Idle", harness.voice_panel.audio_state_value.text())
        self.assertIn("device disconnected", harness.activities[-1])
        self.assertEqual(
            "error",
            harness.chat_panel.voice_only_panel.hint_label.property("voiceRecovery"),
        )
        self.assertIn("select another output", harness.chat_panel.voice_only_panel.hint_label.text())

        harness._apply_audio_state("Recording")
        self.assertEqual("", harness.chat_panel.voice_only_panel.hint_label.property("voiceRecovery"))
        harness.close()

    def test_voice_chunking_omits_code_and_bounds_spoken_output(self) -> None:
        chunks = VoiceHarness._prepare_voice_chunks(
            "## Result\n```python\nprint('secret')\n```\n"
            "Read [the guide](https://example.com/guide). "
            + "A detailed sentence continues here. " * 30,
            max_chunk_characters=90,
            max_total_characters=260,
        )

        spoken = " ".join(chunks)
        self.assertTrue(chunks)
        self.assertTrue(all(len(chunk) <= 90 for chunk in chunks))
        self.assertIn("Code block available in chat", spoken)
        self.assertNotIn("print", spoken)
        self.assertNotIn("https://", spoken)
        self.assertIn("remaining response is available in chat", spoken)

    def test_new_capture_invalidates_an_older_transcription_callback(self) -> None:
        harness = VoiceHarness()
        harness._voice_input_request_id = 4

        harness._start_voice_capture()
        activities_before = list(harness.activities)
        harness._on_transcription_ready((4, "stale request"))
        harness._on_transcription_error(4, "stale failure")

        self.assertEqual(5, harness._voice_input_request_id)
        self.assertEqual(activities_before, harness.activities)
        self.assertTrue(harness.recorder.is_recording())
        harness.close()

    def test_current_transcription_routes_to_chat_with_voice_metadata(self) -> None:
        harness = VoiceHarness()
        harness._voice_input_request_id = 7

        harness._on_transcription_ready((7, "Explain local inference"))

        self.assertEqual(
            [("user", "Explain local inference", {"source": "voice"})],
            harness.appended_messages,
        )
        self.assertEqual([("test-model", "Explain local inference")], harness.reply_requests)
        self.assertEqual("voice", harness.assert_voice_source)
        harness.close()

    def test_voice_message_save_failure_restores_transcript_to_composer(self) -> None:
        harness = VoiceHarness()
        harness._voice_input_request_id = 8

        def fail_save(_role: str, _content: str, metadata=None):
            del metadata
            raise OSError("disk full")

        harness._append_message = fail_save  # type: ignore[method-assign]

        harness._on_transcription_ready((8, "Preserve this transcription"))

        self.assertEqual("Preserve this transcription", harness.chat_panel.input_box.toPlainText())
        self.assertEqual([], harness.reply_requests)
        self.assertIn("restored to the composer", harness.activities[-1])
        self.assertEqual("Audio: Idle", harness.chat_panel.audio_state_label.text())
        harness.close()

    def test_silence_endpoint_stops_once_and_starts_local_transcription(self) -> None:
        harness = VoiceHarness()
        harness._start_voice_capture()

        harness._on_voice_endpoint("silence")
        harness._on_voice_endpoint("silence")

        self.assertFalse(harness.recorder.is_recording())
        self.assertEqual("Audio: Transcribing", harness.voice_panel.audio_state_value.text())
        self.assertEqual(1, len(harness.task_runner.pending))

        harness.task_runner.complete_next()

        self.assertEqual(
            [("test-model", "automatic endpoint")],
            harness.reply_requests,
        )
        harness.close()

    def test_transcription_progress_times_out_and_ignores_late_result(self) -> None:
        harness = VoiceHarness()
        harness._voice_stage_timer = FakeTimer(harness._refresh_voice_stage_progress)
        harness._start_voice_capture()
        harness._finish_voice_capture()

        self.assertEqual("transcribing", harness._voice_stage)
        self.assertTrue(harness._voice_stage_timer.isActive())
        harness._voice_stage_started_at = 100.0
        with patch(
            "local_matrix_assistant.ui.main_window_voice.time.monotonic",
            return_value=105.0,
        ):
            harness._refresh_voice_stage_progress()

        self.assertIn("Transcribing locally... 5s", harness.activities[-1])
        self.assertEqual(
            "progress",
            harness.chat_panel.voice_only_panel.hint_label.property("voiceRecovery"),
        )

        original_request_id = harness._voice_input_request_id
        with patch(
            "local_matrix_assistant.ui.main_window_voice.time.monotonic",
            return_value=131.0,
        ):
            harness._refresh_voice_stage_progress()

        self.assertEqual(original_request_id + 1, harness._voice_input_request_id)
        self.assertEqual("", harness._voice_stage)
        self.assertFalse(harness._voice_stage_timer.isActive())
        self.assertEqual("Audio: Idle", harness.voice_panel.audio_state_value.text())
        self.assertIn("took too long", harness.activities[-1])
        self.assertEqual(
            "error",
            harness.chat_panel.voice_only_panel.hint_label.property("voiceRecovery"),
        )

        harness.task_runner.complete_next()
        self.assertEqual([], harness.appended_messages)
        self.assertEqual([], harness.reply_requests)
        harness.close()

    def test_background_synthesis_timeout_preserves_current_audio_and_discards_late_chunk(self) -> None:
        harness = VoiceHarness()
        harness._voice_stage_timer = FakeTimer(harness._refresh_voice_stage_progress)
        response = " ".join(
            f"Sentence {index} provides enough spoken detail for local synthesis."
            for index in range(14)
        )
        harness._speak_response(response)
        harness.task_runner.complete_next()

        self.assertTrue(harness.player.is_playing())
        self.assertTrue(harness._tts_synthesis_active)
        self.assertEqual("synthesizing", harness._voice_stage)
        self.assertEqual(1, len(harness.task_runner.pending))
        harness._continuous_voice_armed = True
        harness._voice_stage_started_at = 100.0
        original_request_id = harness._tts_request_id
        with patch(
            "local_matrix_assistant.ui.main_window_voice.time.monotonic",
            return_value=121.0,
        ):
            harness._refresh_voice_stage_progress()

        self.assertEqual(original_request_id + 1, harness._tts_request_id)
        self.assertTrue(harness.player.is_playing())
        self.assertEqual("Audio: Speaking", harness.voice_panel.audio_state_value.text())
        self.assertEqual([], harness._tts_text_chunks)
        self.assertFalse(harness._continuous_voice_armed)
        self.assertIn("Current audio can finish", harness.activities[-1])

        harness.task_runner.complete_next()
        self.assertEqual(1, len(harness.player.played))

        harness.player.playing = False
        harness._on_playback_changed(False, "Speaker idle")
        self.assertEqual("Audio: Idle", harness.voice_panel.audio_state_value.text())
        self.assertIn("full response remains in chat", harness.activities[-1])
        self.assertEqual(
            "error",
            harness.chat_panel.voice_only_panel.hint_label.property("voiceRecovery"),
        )
        harness.close()

    def test_missing_selected_devices_remain_visible_with_automatic_fallback(self) -> None:
        harness = VoiceHarness()
        harness.config = replace(
            harness.config,
            preferred_input_name="Travel Mic",
            playback_output_name="Travel Speakers",
        )

        harness._refresh_input_device_options()
        harness._refresh_output_device_options()

        self.assertEqual("Travel Mic", harness.voice_panel.input_device_combo.currentData())
        self.assertIn("Unavailable: Travel Mic", harness.voice_panel.input_device_combo.currentText())
        self.assertEqual("Travel Speakers", harness.voice_panel.output_device_combo.currentData())
        self.assertIn("Unavailable: Travel Speakers", harness.voice_panel.output_device_combo.currentText())

        harness.recorder.available_inputs.append("Travel Mic")
        harness.player.available_outputs.append("Travel Speakers")
        harness._refresh_input_device_options()
        harness._refresh_output_device_options()

        self.assertEqual("Travel Mic", harness.voice_panel.input_device_combo.currentText())
        self.assertEqual("Travel Speakers", harness.voice_panel.output_device_combo.currentText())
        harness.close()

    def test_stop_voice_invalidates_pending_synthesis(self) -> None:
        harness = VoiceHarness()
        harness._speak_response("First response")

        harness._stop_voice_output()
        harness.task_runner.complete_next()

        self.assertEqual([], harness.player.played)
        self.assertIn("Voice output stopped.", harness.activities)
        harness.close()

    def test_voice_button_remains_available_to_interrupt_streaming(self) -> None:
        harness = VoiceHarness()

        harness._set_interaction_busy(True, allow_cancel=True)

        self.assertTrue(harness.chat_panel.voice_button.isEnabled())
        self.assertTrue(harness.chat_panel.cancel_button.isEnabled())
        harness.close()


if __name__ == "__main__":
    unittest.main()
