from __future__ import annotations

import unittest
from unittest.mock import patch
from array import array
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.services.audio_devices import resolve_preferred_or_fallback
from local_matrix_assistant.services.audio import AudioPlayer, AudioRecorder


class AudioTests(unittest.TestCase):
    def test_resolve_preferred_or_fallback_returns_matching_device(self) -> None:
        devices = [{"name": "Desk Mic"}, {"name": "USB Mic"}]
        selected, fallback_name = resolve_preferred_or_fallback(devices, "USB Mic", deprioritized_tokens=("VIRTUAL",))
        self.assertEqual({"name": "USB Mic"}, selected)
        self.assertIsNone(fallback_name)

    def test_resolve_preferred_or_fallback_uses_real_device_when_selection_missing(self) -> None:
        devices = [{"name": "Virtual Mic"}, {"name": "Desk Mic"}]
        selected, fallback_name = resolve_preferred_or_fallback(devices, "Headset Mic", deprioritized_tokens=("VIRTUAL",))
        self.assertEqual({"name": "Desk Mic"}, selected)
        self.assertEqual("Headset Mic", fallback_name)

    def test_recorder_resolves_device_once_and_uses_low_latency_buffer(self) -> None:
        created: list[object] = []

        class FakeCaptureDevice:
            def __init__(self, **options) -> None:
                self.options = options
                self.closed = False
                created.append(self)

            def start(self, _stream) -> None:
                return

            def stop(self) -> None:
                return

            def close(self) -> None:
                self.closed = True

        recorder = AudioRecorder("Desk Mic")
        capture = {"name": "Desk Mic", "id": b"desk"}
        with (
            patch.object(recorder, "_resolve_input", return_value=capture) as resolve_input,
            patch("local_matrix_assistant.services.audio.miniaudio.CaptureDevice", FakeCaptureDevice),
        ):
            recorder.start()

        self.assertEqual(1, resolve_input.call_count)
        self.assertTrue(recorder.is_recording())
        self.assertEqual(100, created[0].options["buffersize_msec"])  # type: ignore[attr-defined]
        recorder.cancel()
        self.assertTrue(created[0].closed)  # type: ignore[attr-defined]

    def test_voice_activity_emits_one_endpoint_after_speech_and_trailing_silence(self) -> None:
        recorder = AudioRecorder()
        endpoints: list[str] = []
        levels: list[int] = []
        recorder.speech_ended.connect(endpoints.append)
        recorder.audio_level_changed.connect(levels.append)

        def pcm(level: int, milliseconds: int = 100) -> bytes:
            return array("h", [level] * (16 * milliseconds)).tobytes()

        for _ in range(3):
            recorder._process_capture_chunk(pcm(60))
        for _ in range(6):
            recorder._process_capture_chunk(pcm(1800))
        for _ in range(12):
            recorder._process_capture_chunk(pcm(70))

        self.assertEqual(["silence"], endpoints)
        self.assertIn(1800, levels)

    def test_background_noise_does_not_trigger_speech_but_maximum_duration_stops_capture(self) -> None:
        recorder = AudioRecorder()
        recorder.maximum_capture_ms = 1000.0
        endpoints: list[str] = []
        recorder.speech_ended.connect(endpoints.append)
        background = array("h", [300] * 1600).tobytes()

        for _ in range(12):
            recorder._process_capture_chunk(background)

        self.assertEqual(["maximum"], endpoints)

    def test_audio_device_enumeration_failure_returns_an_empty_list(self) -> None:
        with patch(
            "local_matrix_assistant.services.audio.miniaudio.Devices",
            side_effect=RuntimeError("audio service unavailable"),
        ):
            self.assertEqual([], AudioRecorder.list_inputs())
            self.assertEqual([], AudioPlayer.list_outputs())

    def test_playback_monitor_reports_cleanup_failure_and_clears_playing_state(self) -> None:
        class DisconnectedDevice:
            def stop(self) -> None:
                raise RuntimeError("device disconnected")

            def close(self) -> None:
                raise RuntimeError("device already gone")

        player = AudioPlayer()
        player._winsound = None
        player._token = 7
        player._device = DisconnectedDevice()  # type: ignore[assignment]
        events: list[tuple[bool, str]] = []
        player.playback_changed.connect(lambda playing, message: events.append((playing, message)))

        with patch("local_matrix_assistant.services.audio.time.sleep", lambda _seconds: None):
            player._await_miniaudio_finish(7, 0)

        self.assertFalse(player.is_playing())
        self.assertEqual(1, len(events))
        self.assertFalse(events[0][0])
        self.assertIn("device error", events[0][1])
        self.assertIn("device disconnected", events[0][1])


if __name__ == "__main__":
    unittest.main()
