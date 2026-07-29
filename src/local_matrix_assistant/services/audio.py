from __future__ import annotations

from array import array
from io import BytesIO
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import miniaudio
from PySide6.QtCore import QObject, Signal

from local_matrix_assistant.core.constants import APP_NAME
from local_matrix_assistant.services.audio_devices import (
    DEPRIORITIZED_INPUT_NAME_TOKENS,
    DEPRIORITIZED_OUTPUT_NAME_TOKENS,
    resolve_preferred_or_fallback,
    usable_devices,
)


_CAPTURE_BUFFER_MS = 100
_PLAYBACK_BUFFER_MS = 100


class AudioRecorder(QObject):
    recording_changed = Signal(bool, str)
    audio_level_changed = Signal(int)
    speech_ended = Signal(str)

    speech_start_ms = 180.0
    minimum_total_speech_ms = 350.0
    trailing_silence_ms = 900.0
    maximum_capture_ms = 30_000.0

    def __init__(self, preferred_input_name: str = "") -> None:
        super().__init__()
        self._capture_device: miniaudio.CaptureDevice | None = None
        self._capture_generator = None
        self._recorded = bytearray()
        self._preferred_input_name = preferred_input_name.strip()
        self._input_fallback_name: str | None = None
        self._last_average_level: int | None = None
        self._last_duration_seconds: float | None = None
        self._reset_voice_activity()

    @staticmethod
    def list_inputs() -> list[str]:
        try:
            captures = miniaudio.Devices([miniaudio.Backend.WASAPI]).get_captures()
        except Exception:  # noqa: BLE001
            return []
        return [device["name"] for device in usable_devices(captures)]

    def set_input_device_name(self, input_name: str) -> None:
        self._preferred_input_name = input_name.strip()

    def _resolve_input(self) -> dict | None:
        capture, fallback_name = resolve_preferred_or_fallback(
            miniaudio.Devices([miniaudio.Backend.WASAPI]).get_captures(),
            self._preferred_input_name,
            deprioritized_tokens=DEPRIORITIZED_INPUT_NAME_TOKENS,
        )
        self._input_fallback_name = fallback_name
        return capture

    def has_input(self) -> tuple[bool, str]:
        try:
            capture = self._resolve_input()
        except Exception as exc:  # noqa: BLE001
            return False, f"Microphone device check failed: {exc}"
        if not capture:
            return False, "No active microphone device detected."
        message = f"Using {capture['name']}"
        if self._input_fallback_name:
            message = f"Selected microphone '{self._input_fallback_name}' is unavailable. Using {capture['name']}"
        if self._last_average_level is not None and self._last_duration_seconds is not None:
            signal_text = (
                f"last signal level {self._last_average_level}"
                if self._last_average_level >= 20 and self._last_duration_seconds >= 0.6
                else f"last signal weak ({self._last_average_level})"
            )
            message = f"{message} | {signal_text}"
        return True, message

    def is_recording(self) -> bool:
        return self._capture_device is not None

    def start(self) -> None:
        if self.is_recording():
            return

        try:
            capture = self._resolve_input()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Could not query microphone devices: {exc}") from exc
        if not capture:
            raise RuntimeError("No active microphone device detected.")

        self._recorded = bytearray()
        self._reset_voice_activity()
        self._capture_generator = self._capture_stream()
        next(self._capture_generator)
        device: miniaudio.CaptureDevice | None = None
        try:
            device = miniaudio.CaptureDevice(
                input_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=1,
                sample_rate=16000,
                buffersize_msec=_CAPTURE_BUFFER_MS,
                device_id=capture["id"],
                backends=[miniaudio.Backend.WASAPI],
                app_name=APP_NAME,
            )
            device.start(self._capture_generator)
        except Exception as exc:
            if device is not None:
                try:
                    device.close()
                except Exception:
                    pass
            self._capture_generator = None
            self._recorded = bytearray()
            raise RuntimeError(f"Could not start microphone '{capture['name']}': {exc}") from exc
        self._capture_device = device
        fallback = (
            f" Selected microphone '{self._input_fallback_name}' was unavailable."
            if self._input_fallback_name
            else ""
        )
        self.recording_changed.emit(True, f"Listening on {capture['name']}...{fallback}")

    def stop(self) -> bytes:
        if not self._capture_device:
            raise RuntimeError("Recording is not active.")
        device = self._capture_device
        self._capture_device = None
        self._capture_generator = None
        try:
            device.stop()
        finally:
            try:
                device.close()
            except Exception:
                pass
        self.recording_changed.emit(False, "Mic idle")
        if not self._recorded:
            raise RuntimeError("No microphone audio was captured.")
        wav_bytes = self._wrap_wav(bytes(self._recorded))
        self._last_duration_seconds, self._last_average_level = self.inspect_wav(wav_bytes)
        self._reset_voice_activity()
        self.audio_level_changed.emit(0)
        return wav_bytes

    def cancel(self) -> None:
        device = self._capture_device
        self._capture_device = None
        if device:
            try:
                device.stop()
            except Exception:
                pass
            try:
                device.close()
            except Exception:
                pass
        self._capture_generator = None
        self._recorded = bytearray()
        self._reset_voice_activity()
        self.audio_level_changed.emit(0)
        self.recording_changed.emit(False, "Mic idle")

    def _capture_stream(self):
        while True:
            data = yield
            if data:
                self._process_capture_chunk(bytes(data))

    def _process_capture_chunk(self, raw_audio: bytes) -> None:
        if not raw_audio:
            return
        self._recorded.extend(raw_audio)
        samples = array("h")
        usable_length = len(raw_audio) - (len(raw_audio) % 2)
        samples.frombytes(raw_audio[:usable_length])
        if not samples:
            return
        level = int(sum(abs(sample) for sample in samples) / len(samples))
        self.audio_level_changed.emit(level)

        chunk_ms = (len(samples) / 16000.0) * 1000.0
        self._vad_capture_ms += chunk_ms
        threshold_floor = 600.0 if self._vad_capture_ms < 500.0 else 220.0
        threshold = max(threshold_floor, self._vad_noise_floor * 2.8)
        voiced = level >= threshold
        if voiced:
            self._vad_consecutive_speech_ms += chunk_ms
            self._vad_total_speech_ms += chunk_ms
            self._vad_silence_ms = 0.0
            if self._vad_consecutive_speech_ms >= self.speech_start_ms:
                self._vad_has_speech = True
        else:
            self._vad_noise_floor = (self._vad_noise_floor * 0.92) + (level * 0.08)
            self._vad_consecutive_speech_ms = max(
                0.0,
                self._vad_consecutive_speech_ms - (chunk_ms * 0.5),
            )
            if self._vad_has_speech:
                self._vad_silence_ms += chunk_ms

        if self._vad_endpoint_emitted:
            return
        if (
            self._vad_has_speech
            and self._vad_total_speech_ms >= self.minimum_total_speech_ms
            and self._vad_silence_ms >= self.trailing_silence_ms
        ):
            self._vad_endpoint_emitted = True
            self.speech_ended.emit("silence")
        elif self._vad_capture_ms >= self.maximum_capture_ms:
            self._vad_endpoint_emitted = True
            self.speech_ended.emit("maximum")

    def _reset_voice_activity(self) -> None:
        self._vad_noise_floor = 120.0
        self._vad_capture_ms = 0.0
        self._vad_consecutive_speech_ms = 0.0
        self._vad_total_speech_ms = 0.0
        self._vad_silence_ms = 0.0
        self._vad_has_speech = False
        self._vad_endpoint_emitted = False

    @staticmethod
    def _wrap_wav(raw_audio: bytes) -> bytes:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as handle:
            wav_path = Path(handle.name)
        try:
            with wave.open(str(wav_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(raw_audio)
            return wav_path.read_bytes()
        finally:
            wav_path.unlink(missing_ok=True)

    @staticmethod
    def inspect_wav(wav_bytes: bytes) -> tuple[float, int]:
        with wave.open(BytesIO(wav_bytes), "rb") as wav_file:
            frame_count = wav_file.getnframes()
            sample_rate = wav_file.getframerate()
            sample_width = wav_file.getsampwidth()
            frames = wav_file.readframes(frame_count)

        if sample_width != 2 or not frames:
            duration = frame_count / sample_rate if sample_rate else 0.0
            return duration, 0

        samples = array("h")
        samples.frombytes(frames)
        if not samples:
            duration = frame_count / sample_rate if sample_rate else 0.0
            return duration, 0

        mean_level = int(sum(abs(sample) for sample in samples) / len(samples))
        duration = frame_count / sample_rate if sample_rate else 0.0
        return duration, mean_level


class AudioPlayer(QObject):
    playback_changed = Signal(bool, str)

    def __init__(self, preferred_output_name: str = "") -> None:
        super().__init__()
        self._device: miniaudio.PlaybackDevice | None = None
        self._token = 0
        self._lock = threading.Lock()
        self._preferred_output_name = preferred_output_name.strip()
        self._output_fallback_name: str | None = None
        self._temp_wav_path: Path | None = None
        self._winsound = None
        if sys.platform.startswith("win"):
            import winsound

            self._winsound = winsound

    @staticmethod
    def list_outputs() -> list[str]:
        try:
            playbacks = miniaudio.Devices([miniaudio.Backend.WASAPI]).get_playbacks()
        except Exception:  # noqa: BLE001
            return []
        return [device["name"] for device in usable_devices(playbacks)]

    def set_output_device_name(self, output_name: str) -> None:
        self._preferred_output_name = output_name.strip()

    def _resolve_output(self) -> dict | None:
        output, fallback_name = resolve_preferred_or_fallback(
            miniaudio.Devices([miniaudio.Backend.WASAPI]).get_playbacks(),
            self._preferred_output_name,
            deprioritized_tokens=DEPRIORITIZED_OUTPUT_NAME_TOKENS,
        )
        self._output_fallback_name = fallback_name
        return output

    def has_output(self) -> tuple[bool, str]:
        try:
            output = self._resolve_output()
        except Exception as exc:  # noqa: BLE001
            if self._winsound:
                return True, f"Playback device check failed ({exc}). Using Windows default output"
            return False, f"Playback device check failed: {exc}"
        if output:
            if self._output_fallback_name:
                return True, f"Selected playback device '{self._output_fallback_name}' is unavailable. Using {output['name']}"
            return True, f"Using {output['name']}"
        if self._winsound:
            return True, "Using Windows default output"
        return False, "No active playback device detected."

    def is_playing(self) -> bool:
        with self._lock:
            return self._device is not None or self._temp_wav_path is not None

    def play_wav(self, wav_bytes: bytes) -> None:
        self.stop(emit_signal=False)
        if self._winsound and not self._preferred_output_name:
            self._play_on_windows_default(wav_bytes)
            return

        try:
            output = self._resolve_output()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Could not query playback devices: {exc}") from exc
        if not output:
            raise RuntimeError("No active playback device detected.")

        decoded = miniaudio.decode(
            wav_bytes,
            output_format=miniaudio.SampleFormat.SIGNED16,
            nchannels=1,
            sample_rate=16000,
        )
        stream = miniaudio.stream_raw_pcm_memory(
            decoded.samples,
            decoded.nchannels,
            decoded.sample_width,
        )
        next(stream)

        device: miniaudio.PlaybackDevice | None = None
        try:
            device = miniaudio.PlaybackDevice(
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=decoded.nchannels,
                sample_rate=decoded.sample_rate,
                buffersize_msec=_PLAYBACK_BUFFER_MS,
                device_id=output["id"],
                backends=[miniaudio.Backend.WASAPI],
                app_name=APP_NAME,
            )
            device.start(stream)
        except Exception as exc:
            if device is not None:
                try:
                    device.close()
                except Exception:
                    pass
            raise RuntimeError(f"Could not start playback on '{output['name']}': {exc}") from exc
        assert device is not None
        with self._lock:
            self._token += 1
            token = self._token
            self._device = device
        self.playback_changed.emit(True, f"Speaking through {output['name']}...")

        duration = decoded.num_frames / decoded.sample_rate if decoded.sample_rate else 0.0
        monitor = threading.Thread(
            target=self._await_miniaudio_finish,
            args=(token, duration),
            daemon=True,
        )
        monitor.start()

    def stop(self, *, emit_signal: bool = True) -> None:
        temp_wav_path: Path | None = None
        device: miniaudio.PlaybackDevice | None = None
        with self._lock:
            self._token += 1
            device = self._device
            self._device = None
            temp_wav_path = self._temp_wav_path
            self._temp_wav_path = None
        errors: list[str] = []
        if device:
            errors.extend(self._cleanup_device(device))
        if self._winsound:
            try:
                self._winsound.PlaySound(None, self._winsound.SND_PURGE)
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
        if temp_wav_path:
            try:
                temp_wav_path.unlink(missing_ok=True)
            except OSError as exc:
                errors.append(str(exc))
        message = (
            f"Playback stopped after a device error: {'; '.join(errors)}"
            if errors
            else "Speaker idle"
        )
        if emit_signal:
            self.playback_changed.emit(False, message)

    def _play_on_windows_default(self, wav_bytes: bytes) -> None:
        if not self._winsound:
            raise RuntimeError("Windows default playback is unavailable.")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as handle:
            temp_wav_path = Path(handle.name)
            handle.write(wav_bytes)

        duration = AudioRecorder.inspect_wav(wav_bytes)[0]
        with self._lock:
            self._token += 1
            token = self._token
            self._temp_wav_path = temp_wav_path

        try:
            self._winsound.PlaySound(
                str(temp_wav_path),
                self._winsound.SND_FILENAME | self._winsound.SND_ASYNC | self._winsound.SND_NODEFAULT,
            )
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                if token == self._token:
                    self._temp_wav_path = None
            try:
                temp_wav_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError(f"Could not start Windows default playback: {exc}") from exc
        self.playback_changed.emit(True, "Speaking through system default output...")

        monitor = threading.Thread(
            target=self._await_winsound_finish,
            args=(token, duration, temp_wav_path),
            daemon=True,
        )
        monitor.start()

    def _await_miniaudio_finish(self, token: int, duration: float) -> None:
        time.sleep(max(duration, 0.0) + 0.15)
        with self._lock:
            if token != self._token:
                return
            device = self._device
            self._device = None
        errors = self._cleanup_device(device) if device else []
        message = (
            f"Playback ended after a device error: {'; '.join(errors)}"
            if errors
            else "Speaker idle"
        )
        self.playback_changed.emit(False, message)

    def _await_winsound_finish(self, token: int, duration: float, temp_wav_path: Path) -> None:
        time.sleep(max(duration, 0.0) + 0.15)
        with self._lock:
            if token != self._token:
                try:
                    temp_wav_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return
            self._temp_wav_path = None
        try:
            temp_wav_path.unlink(missing_ok=True)
            message = "Speaker idle"
        except OSError as exc:
            message = f"Playback ended after a cleanup error: {exc}"
        self.playback_changed.emit(False, message)

    @staticmethod
    def _cleanup_device(device: miniaudio.PlaybackDevice) -> list[str]:
        errors: list[str] = []
        try:
            device.stop()
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        try:
            device.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
        return errors
