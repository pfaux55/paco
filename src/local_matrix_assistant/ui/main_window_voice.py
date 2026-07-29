from __future__ import annotations

import re
import time

from local_matrix_assistant.core.models import VoiceOption
from local_matrix_assistant.ui.workers import FunctionWorker


class VoiceWindowMixin:
    voice_capture_stall_timeout_seconds = 4.0
    voice_transcription_timeout_seconds = 30.0
    voice_synthesis_timeout_seconds = 20.0

    def _toggle_voice_shortcut(self) -> None:
        page_stack = getattr(self, "page_stack", None)
        if page_stack is not None and page_stack.currentWidget() is not self.chat_panel:
            self._show_page(0, self.chat_nav_button)
        self._toggle_voice_mode()

    def _toggle_microphone_mute_shortcut(self) -> None:
        self._on_microphone_muted_toggled(not bool(self.config.microphone_muted))

    def _stop_voice_output_shortcut(self) -> None:
        self._stop_voice_output()

    def _start_voice_stage(
        self,
        stage: str,
        request_id: int,
        *,
        item_index: int = 0,
    ) -> None:
        self._voice_stage = stage
        self._voice_stage_request_id = request_id
        self._voice_stage_item_index = item_index
        self._voice_stage_started_at = time.monotonic()
        timer = getattr(self, "_voice_stage_timer", None)
        if timer is not None:
            timer.start()

    def _clear_voice_stage(
        self,
        stage: str | None = None,
        request_id: int | None = None,
        *,
        item_index: int | None = None,
    ) -> bool:
        if stage is not None and getattr(self, "_voice_stage", "") != stage:
            return False
        if request_id is not None and getattr(self, "_voice_stage_request_id", 0) != request_id:
            return False
        if item_index is not None and getattr(self, "_voice_stage_item_index", 0) != item_index:
            return False
        timer = getattr(self, "_voice_stage_timer", None)
        if timer is not None:
            timer.stop()
        self._voice_stage = ""
        self._voice_stage_request_id = 0
        self._voice_stage_item_index = 0
        self._voice_stage_started_at = None
        return True

    def _refresh_voice_stage_progress(self) -> None:
        stage = getattr(self, "_voice_stage", "")
        request_id = getattr(self, "_voice_stage_request_id", 0)
        started_at = getattr(self, "_voice_stage_started_at", None)
        if not stage or started_at is None:
            self._clear_voice_stage()
            return
        if stage == "transcribing":
            if request_id != getattr(self, "_voice_input_request_id", 0):
                self._clear_voice_stage()
                return
            timeout_seconds = self.voice_transcription_timeout_seconds
        elif stage == "synthesizing":
            if request_id != getattr(self, "_tts_request_id", 0):
                self._clear_voice_stage()
                return
            timeout_seconds = self.voice_synthesis_timeout_seconds
        else:
            self._clear_voice_stage()
            return

        elapsed_seconds = max(0, int(time.monotonic() - started_at))
        if elapsed_seconds >= timeout_seconds:
            self._on_voice_stage_timeout(stage, request_id)
            return

        if stage == "transcribing":
            message = f"Transcribing locally... {elapsed_seconds}s - audio stays on this device"
            self._set_activity(message)
            self.chat_panel.voice_only_panel.set_stage_message(message)
            return

        item_index = int(getattr(self, "_voice_stage_item_index", 0))
        total = len(getattr(self, "_tts_text_chunks", []))
        message = (
            f"Synthesizing spoken segment {item_index + 1}/{max(1, total)} locally... "
            f"{elapsed_seconds}s"
        )
        if not self.player.is_playing():
            self._set_activity(message)
            self.chat_panel.voice_only_panel.set_stage_message(message)

    def _on_voice_stage_timeout(self, stage: str, request_id: int) -> None:
        if stage == "transcribing":
            if request_id != getattr(self, "_voice_input_request_id", 0):
                return
            self._invalidate_voice_input()
            self._apply_audio_state("Idle")
            message = "Local transcription took too long and was detached. Record again to retry."
            self.chat_panel.voice_only_panel.set_recovery_message(message)
            self._set_activity(message)
            return

        if stage != "synthesizing" or request_id != getattr(self, "_tts_request_id", 0):
            return
        self._tts_request_id += 1
        self._clear_voice_stage()
        self._clear_voice_sequence()
        self._cancel_continuous_voice_resume(clear_arm=True)
        still_playing = self.player.is_playing()
        self._apply_audio_state("Speaking" if still_playing else "Idle")
        message = (
            "The next spoken segment took too long and was skipped. Current audio can finish; "
            "the full response remains in chat."
            if still_playing
            else "Local speech synthesis took too long and was detached. The response remains in chat."
        )
        self._voice_output_recovery_message = message if still_playing else ""
        self.chat_panel.voice_only_panel.set_recovery_message(message)
        self._set_activity(message)

    def _arm_voice_capture_health_check(self) -> None:
        now = time.monotonic()
        self._voice_capture_started_at = now
        self._voice_capture_last_audio_at = now
        timer = getattr(self, "_voice_capture_health_timer", None)
        if timer is not None:
            timer.start()

    def _clear_voice_capture_health_check(self) -> None:
        timer = getattr(self, "_voice_capture_health_timer", None)
        if timer is not None:
            timer.stop()
        self._voice_capture_started_at = None
        self._voice_capture_last_audio_at = None

    def _check_voice_capture_health(self) -> None:
        if not self.recorder.is_recording():
            self._clear_voice_capture_health_check()
            return
        last_audio_at = getattr(self, "_voice_capture_last_audio_at", None)
        if last_audio_at is None:
            self._arm_voice_capture_health_check()
            return
        if time.monotonic() - last_audio_at < self.voice_capture_stall_timeout_seconds:
            return

        self._invalidate_voice_input()
        self._voice_capture_pending = False
        self._cancel_continuous_voice_resume(clear_arm=True)
        cancel_error = ""
        try:
            self.recorder.cancel()
        except Exception as exc:  # noqa: BLE001
            cancel_error = f" Cleanup also failed: {exc}"
        finally:
            self._clear_voice_capture_health_check()
        self._apply_audio_state("Idle")
        activity_message = (
            "Microphone stopped delivering audio. Check the device connection, then try again."
            + cancel_error
        )
        self.chat_panel.voice_only_panel.set_recovery_message(
            "Microphone audio stopped. Reconnect or select another input, then tap to retry."
        )
        refresh_status = getattr(self, "refresh_status", None)
        if callable(refresh_status):
            refresh_status()
        self._set_activity(activity_message)

    def _toggle_voice_mode(self) -> None:
        if self._chat_write_is_blocked():
            return
        if not self.chat_panel.voice_only_mode_active():
            self._show_voice_only_screen()
        if self.recorder.is_recording():
            self._finish_voice_capture()
            return
        if self.config.microphone_muted:
            self._set_activity("Microphone is muted. Unmute it before starting voice capture.")
            self._apply_audio_state("Muted")
            return
        if self._awaiting_response:
            worker = getattr(self, "_active_stream_worker", None)
            if worker is None:
                self._set_activity("Wait for the current Agent or web-search task before starting voice capture.")
                return
            self._voice_capture_pending = True
            self._cancel_requested = True
            worker.cancel()
            self.chat_panel.cancel_button.setEnabled(False)
            self._set_activity("Interrupting the current reply to listen...")
            self._apply_audio_state("Interrupting")
            return
        self._start_voice_capture()

    def _start_voice_capture(self) -> None:
        self._invalidate_voice_input()
        self._arm_voice_capture_health_check()
        try:
            self._invalidate_voice_output()
            self.recorder.start()
            self._apply_audio_state("Recording")
        except Exception as exc:  # noqa: BLE001
            self._clear_voice_capture_health_check()
            self._set_activity(str(exc))
            self._apply_audio_state("Idle")
            self.chat_panel.voice_only_panel.set_recovery_message(
                "Microphone unavailable. Check the connection or choose another input in Voice."
            )

    def _resume_pending_voice_capture(self) -> None:
        if not self._voice_capture_pending:
            return
        self._voice_capture_pending = False
        if self.config.microphone_muted:
            self._apply_audio_state("Muted")
            return
        self._start_voice_capture()

    def _show_voice_only_screen(self) -> None:
        self.chat_panel.show_voice_only_mode(True)

    def _hide_voice_only_screen(self) -> None:
        self._cancel_continuous_voice_resume(clear_arm=True)
        self._voice_capture_pending = False
        if self.recorder.is_recording():
            self._invalidate_voice_input()
            cancel_error = ""
            try:
                self.recorder.cancel()
            except Exception as exc:  # noqa: BLE001
                cancel_error = f" Microphone cleanup failed: {exc}"
            finally:
                self._clear_voice_capture_health_check()
            self._apply_audio_state("Idle")
            self._set_activity("Voice capture canceled." + cancel_error)
        self.chat_panel.show_voice_only_mode(False)

    def _finish_voice_capture(self) -> None:
        try:
            wav_bytes = self.recorder.stop()
        except Exception as exc:  # noqa: BLE001
            self._set_activity(str(exc))
            self._apply_audio_state("Idle")
            return
        finally:
            self._clear_voice_capture_health_check()
        duration_seconds, average_level = self.recorder.inspect_wav(wav_bytes)
        if duration_seconds < 0.6:
            self._set_activity("Voice capture was too short. Hold voice mode a little longer before stopping.")
            self._apply_audio_state("Idle")
            return
        if average_level < 20:
            self._set_activity("No voice detected from the microphone. Speak closer to the mic and try again.")
            self._apply_audio_state("Idle")
            return
        self._set_activity("Transcribing local microphone audio...")
        self._apply_audio_state("Transcribing")
        request_id = getattr(self, "_voice_input_request_id", 0)
        self._start_voice_stage("transcribing", request_id)
        worker = FunctionWorker(lambda: (request_id, self.stt_service.transcribe(wav_bytes)))
        self.task_runner.start(
            worker,
            self._on_transcription_ready,
            lambda message: self._on_transcription_error(request_id, message),
        )

    def _on_voice_endpoint(self, reason: str) -> None:
        if not self.recorder.is_recording():
            return
        if reason == "maximum":
            self._set_activity("Maximum voice capture reached. Transcribing locally...")
        else:
            self._set_activity("Pause detected. Transcribing locally...")
        self._finish_voice_capture()

    def _on_microphone_level(self, level: int) -> None:
        if not self.recorder.is_recording():
            return
        self._voice_capture_last_audio_at = time.monotonic()
        self.chat_panel.voice_only_panel.set_input_level(level)

    def _on_transcription_ready(self, payload: object) -> None:
        if not isinstance(payload, tuple) or len(payload) != 2:
            self._on_transcription_error(
                getattr(self, "_voice_input_request_id", 0),
                "Speech recognition returned an invalid result.",
            )
            return
        request_id, transcription = payload
        if not isinstance(request_id, int):
            return
        if request_id != getattr(self, "_voice_input_request_id", 0):
            return
        if not isinstance(transcription, str):
            self._on_transcription_error(
                request_id,
                "Speech recognition returned an invalid result.",
            )
            return
        self._clear_voice_stage("transcribing", request_id)
        if self._try_run_agent_command(transcription, source="voice"):
            self._apply_audio_state("Idle")
            return
        if self._chat_write_is_blocked():
            self._apply_audio_state("Idle")
            return
        try:
            self._append_message("user", transcription, metadata={"source": "voice"})
        except Exception as exc:  # noqa: BLE001
            self.chat_panel.input_box.setPlainText(transcription)
            self._set_activity(f"Could not save voice message; it was restored to the composer: {exc}")
            self._apply_audio_state("Idle")
            return
        self._set_activity(f"Voice transcription ready: {transcription}")
        self._enable_web_search_if_requested(transcription)
        selection = self._select_model_for_prompt(transcription)
        if not selection.model:
            self._set_activity("No Ollama model selected.")
            self._apply_audio_state("Idle")
            return
        self._begin_assistant_response(selection.model, transcription)

    def _on_transcription_error(self, request_id: int, message: str) -> None:
        if request_id != getattr(self, "_voice_input_request_id", 0):
            return
        self._clear_voice_stage("transcribing", request_id)
        if "no text was recognized" in message.lower():
            message = "Microphone audio was captured, but no recognizable speech was detected."
        self._set_activity(f"Voice transcription failed: {message}")
        self._apply_audio_state("Idle")

    def _speak_response(self, text: str) -> None:
        chunks = self._prepare_voice_chunks(text)
        if not chunks:
            self._cancel_continuous_voice_resume(clear_arm=True)
            self._apply_audio_state("Idle")
            return
        request_id = self._begin_voice_sequence(chunks)
        self._continuous_voice_armed = self._continuous_voice_context_active()
        self._set_activity("Synthesizing speech locally...")
        self._apply_audio_state("Synthesizing")
        self._start_next_voice_chunk_synthesis(request_id)

    def _preview_voice(self) -> None:
        if self.recorder.is_recording() or getattr(self, "_voice_stage", "") == "transcribing":
            self._set_activity("Wait for microphone transcription to finish before previewing a voice.")
            return
        preview_text = self.voice_panel.preview_input.text().strip()
        if not preview_text:
            self._set_activity("Voice preview text is empty.")
            return
        chunks = self._prepare_voice_chunks(preview_text)
        if not chunks:
            self._set_activity("Voice preview has no speakable text.")
            self._apply_audio_state("Idle")
            return
        request_id = self._begin_voice_sequence(chunks)
        self._set_activity("Synthesizing preview voice locally...")
        self._apply_audio_state("Synthesizing")
        self._start_next_voice_chunk_synthesis(request_id)

    def _start_next_voice_chunk_synthesis(self, request_id: int) -> None:
        if request_id != getattr(self, "_tts_request_id", 0):
            return
        if getattr(self, "_tts_synthesis_active", False):
            return
        chunks = list(getattr(self, "_tts_text_chunks", []))
        index = int(getattr(self, "_tts_next_synthesis_index", 0))
        if index >= len(chunks):
            return
        self._tts_synthesis_active = True
        self._tts_next_synthesis_index = index + 1
        chunk = chunks[index]
        self._start_voice_stage("synthesizing", request_id, item_index=index)
        worker = FunctionWorker(
            lambda: (
                request_id,
                index,
                self.tts_service.synthesize(
                    chunk,
                    rate=self.config.tts_rate,
                    volume=self.config.tts_volume,
                ),
            )
        )
        self.task_runner.start(
            worker,
            self._on_tts_ready,
            lambda message: self._on_tts_error(request_id, message),
        )

    def _on_tts_ready(self, payload: object) -> None:
        if not isinstance(payload, tuple) or len(payload) != 3:
            return
        request_id, index, wav_bytes = payload
        if not isinstance(request_id, int) or not isinstance(index, int) or not isinstance(wav_bytes, bytes):
            return
        if request_id != self._tts_request_id or self.recorder.is_recording():
            return
        self._clear_voice_stage("synthesizing", request_id, item_index=index)
        self._tts_synthesis_active = False
        self._tts_audio_chunks[index] = wav_bytes
        self._start_next_voice_chunk_synthesis(request_id)
        if not self.player.is_playing():
            self._play_next_voice_chunk(request_id)

    def _play_next_voice_chunk(self, request_id: int) -> bool:
        if request_id != getattr(self, "_tts_request_id", 0) or self.recorder.is_recording():
            return False
        index = int(getattr(self, "_tts_next_play_index", 0))
        wav_bytes = getattr(self, "_tts_audio_chunks", {}).pop(index, None)
        if wav_bytes is None:
            return False
        try:
            self.player.play_wav(wav_bytes)
        except Exception as exc:  # noqa: BLE001
            self._clear_voice_sequence()
            self._cancel_continuous_voice_resume(clear_arm=True)
            self._apply_audio_state("Idle")
            self.chat_panel.voice_only_panel.set_recovery_message(
                "Speaker playback failed. Check the output connection or choose another speaker in Voice."
            )
            refresh_status = getattr(self, "refresh_status", None)
            if callable(refresh_status):
                refresh_status()
            self._set_activity(f"Playback error: {exc}")
            return False
        self._tts_next_play_index = index + 1
        total = len(getattr(self, "_tts_text_chunks", []))
        self._set_activity(f"Speaking response locally ({index + 1}/{total})...")
        self._apply_audio_state("Speaking")
        return True

    def _on_tts_error(self, request_id: int, message: str) -> None:
        if request_id != self._tts_request_id:
            return
        self._clear_voice_stage("synthesizing", request_id)
        self._clear_voice_sequence()
        self._cancel_continuous_voice_resume(clear_arm=True)
        self._set_activity(f"Voice output failed: {message}")
        self._apply_audio_state("Speaking" if self.player.is_playing() else "Idle")

    def _begin_voice_sequence(self, chunks: list[str]) -> int:
        self._invalidate_voice_output()
        self._tts_text_chunks = list(chunks)
        self._tts_audio_chunks = {}
        self._tts_next_synthesis_index = 0
        self._tts_next_play_index = 0
        self._tts_synthesis_active = False
        return self._tts_request_id

    def _clear_voice_sequence(self) -> None:
        self._tts_text_chunks = []
        self._tts_audio_chunks = {}
        self._tts_next_synthesis_index = 0
        self._tts_next_play_index = 0
        self._tts_synthesis_active = False

    def _invalidate_voice_output(self) -> None:
        self._cancel_continuous_voice_resume(clear_arm=True)
        self._clear_voice_stage("synthesizing")
        self._voice_output_recovery_message = ""
        self._tts_request_id = getattr(self, "_tts_request_id", 0) + 1
        self._clear_voice_sequence()
        self.player.stop()

    def _invalidate_voice_input(self) -> int:
        self._clear_voice_stage("transcribing")
        self._voice_input_request_id = getattr(self, "_voice_input_request_id", 0) + 1
        return self._voice_input_request_id

    @staticmethod
    def _prepare_voice_chunks(
        text: str,
        *,
        max_chunk_characters: int = 320,
        max_total_characters: int = 4000,
    ) -> list[str]:
        cleaned = re.sub(
            r"```.*?```",
            " Code block available in chat. ",
            text,
            flags=re.DOTALL,
        )
        cleaned = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", cleaned)
        cleaned = re.sub(r"https?://\S+", "link", cleaned)
        cleaned = re.sub(r"(?m)^\s{0,3}(?:#{1,6}|[-*+]|\d+[.)])\s+", "", cleaned)
        cleaned = cleaned.replace("`", "").replace("**", "").replace("__", "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            return []
        if len(cleaned) > max_total_characters:
            shortened = cleaned[:max_total_characters].rsplit(" ", 1)[0].rstrip(" ,;:")
            cleaned = f"{shortened}. The remaining response is available in chat."

        units = [unit.strip() for unit in re.split(r"(?<=[.!?])\s+", cleaned) if unit.strip()]
        chunks: list[str] = []
        current = ""
        for unit in units:
            pending = unit
            while len(pending) > max_chunk_characters:
                split_at = pending.rfind(" ", 0, max_chunk_characters + 1)
                if split_at <= 0:
                    split_at = max_chunk_characters
                piece = pending[:split_at].strip()
                if current:
                    chunks.append(current)
                    current = ""
                if piece:
                    chunks.append(piece)
                pending = pending[split_at:].strip()
            if not pending:
                continue
            candidate = f"{current} {pending}".strip() if current else pending
            if current and len(candidate) > max_chunk_characters:
                chunks.append(current)
                current = pending
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks

    def _stop_voice_output(self) -> None:
        self._invalidate_voice_output()
        self._apply_audio_state("Idle")
        self._set_activity("Voice output stopped.")

    def _apply_audio_state(self, text: str) -> None:
        if text == "Idle" and self.config.microphone_muted:
            text = "Muted"
        if self.recorder.is_recording():
            button_name = "micCircleButtonActive"
        elif self.config.microphone_muted:
            button_name = "micCircleButtonMuted"
        else:
            button_name = "micCircleButton"
        self.chat_panel.voice_button.setObjectName(button_name)
        self.chat_panel.voice_button.style().unpolish(self.chat_panel.voice_button)
        self.chat_panel.voice_button.style().polish(self.chat_panel.voice_button)
        voice_action = {
            "Recording": "Stop and transcribe voice capture",
            "Muted": "Microphone muted",
            "Interrupting": "Interrupting reply before voice capture",
            "Thinking": "Interrupt reply and start voice capture",
            "Synthesizing": "Stop spoken output and start voice capture",
            "Speaking": "Stop spoken output and start voice capture",
            "Transcribing": "Start a new voice capture",
        }.get(text, "Start voice capture")
        self.chat_panel.voice_button.setToolTip(
            f"{voice_action} (Ctrl+Shift+Space)"
        )
        self.chat_panel.voice_button.setAccessibleName(voice_action)
        self.chat_panel.voice_button.setAccessibleDescription(
            f"Current voice state: {text}. Shortcut Ctrl+Shift+Space."
        )
        self.chat_panel.set_audio_state(text)
        self.voice_panel.set_audio_state(text)
        if text != "Recording":
            self.chat_panel.voice_only_panel.set_input_level(0)
        voice_output_active = text in {"Synthesizing", "Speaking"}
        self.chat_panel.stop_audio_button.setEnabled(voice_output_active)
        self.voice_panel.stop_preview_button.setEnabled(voice_output_active)

    def _on_voice_enabled_toggled(self, checked: bool) -> None:
        changes: dict[str, object] = {"voice_enabled": checked}
        if not checked and self.config.continuous_voice_enabled:
            changes["continuous_voice_enabled"] = False
            self._cancel_continuous_voice_resume(clear_arm=True)
        persisted = self._update_config(**changes)
        self._sync_continuous_voice_controls()
        if persisted:
            self._set_activity("Voice responses enabled." if checked else "Voice responses disabled.")

    def _on_auto_speak_toggled(self, checked: bool) -> None:
        changes: dict[str, object] = {"auto_speak_responses": checked}
        if not checked and self.config.continuous_voice_enabled:
            changes["continuous_voice_enabled"] = False
            self._cancel_continuous_voice_resume(clear_arm=True)
        self._update_config(**changes)
        self._sync_continuous_voice_controls()

    def _on_continuous_voice_toggled(self, checked: bool) -> None:
        enabled = bool(checked)
        changes: dict[str, object] = {"continuous_voice_enabled": enabled}
        if enabled:
            changes.update(voice_enabled=True, auto_speak_responses=True)
        else:
            self._cancel_continuous_voice_resume(clear_arm=True)
        persisted = self._update_config(**changes)
        self._sync_continuous_voice_controls()
        if persisted:
            self._set_activity(
                "Hands-free conversation enabled. Tap the visualizer to begin."
                if enabled
                else "Hands-free conversation disabled."
            )

    def _sync_continuous_voice_controls(self) -> None:
        enabled = bool(self.config.continuous_voice_enabled)
        for checkbox, checked in (
            (self.voice_panel.voice_enabled_checkbox, self.config.voice_enabled),
            (self.voice_panel.auto_speak_checkbox, self.config.auto_speak_responses),
            (self.voice_panel.continuous_voice_checkbox, enabled),
        ):
            checkbox.blockSignals(True)
            checkbox.setChecked(checked)
            checkbox.blockSignals(False)
        self.chat_panel.voice_only_panel.set_continuous_enabled(enabled)

    def _continuous_voice_context_active(self) -> bool:
        if not (
            self.config.continuous_voice_enabled
            and self.config.voice_enabled
            and self.config.auto_speak_responses
            and self.chat_panel.voice_only_mode_active()
        ):
            return False
        page_stack = getattr(self, "page_stack", None)
        if page_stack is not None and page_stack.currentWidget() is not self.chat_panel:
            return False
        return True

    def _cancel_continuous_voice_resume(self, *, clear_arm: bool = False) -> None:
        timer = getattr(self, "_continuous_voice_timer", None)
        if timer is not None:
            timer.stop()
        if clear_arm:
            self._continuous_voice_armed = False

    def _schedule_continuous_voice_resume(self) -> None:
        if not getattr(self, "_continuous_voice_armed", False):
            return
        self._continuous_voice_armed = False
        if not self._continuous_voice_context_active() or self.config.microphone_muted:
            return
        timer = getattr(self, "_continuous_voice_timer", None)
        if timer is None:
            return
        timer.start()
        self._set_activity("Reply finished. Listening will resume...")

    def _resume_continuous_voice_capture(self) -> None:
        if not self._continuous_voice_context_active():
            return
        if (
            self.config.microphone_muted
            or self._awaiting_response
            or self._voice_capture_pending
            or self.recorder.is_recording()
            or self.player.is_playing()
            or getattr(self, "_tts_synthesis_active", False)
        ):
            return
        self._start_voice_capture()
        if self.recorder.is_recording():
            self._set_activity("Hands-free listening...")

    def _on_microphone_muted_toggled(self, checked: bool) -> None:
        muted = bool(checked)
        self._voice_capture_pending = False
        if muted:
            self._cancel_continuous_voice_resume(clear_arm=True)
            self._invalidate_voice_input()
            self._clear_voice_capture_health_check()
        if muted and self.recorder.is_recording():
            self.recorder.cancel()
        persisted = True
        if muted != self.config.microphone_muted:
            persisted = self._update_config(microphone_muted=muted)

        self.voice_panel.microphone_muted_checkbox.blockSignals(True)
        self.voice_panel.microphone_muted_checkbox.setChecked(muted)
        self.voice_panel.microphone_muted_checkbox.blockSignals(False)
        self.chat_panel.voice_only_panel.set_microphone_muted(muted)

        if self.player.is_playing():
            self._apply_audio_state("Speaking")
        else:
            self._apply_audio_state("Muted" if muted else "Idle")
        if persisted:
            self._set_activity("Microphone muted." if muted else "Microphone unmuted.")

    def _populate_voice_options(self) -> None:
        voices = self.tts_service.list_voices()
        current_voice = self.tts_service.current_voice()
        if not voices and current_voice:
            voices = [current_voice]

        self.voice_panel.voice_combo.blockSignals(True)
        self.voice_panel.voice_combo.clear()
        for voice in voices:
            self.voice_panel.voice_combo.addItem(f"{voice.label} ({voice.gender})", voice)
        for index in range(self.voice_panel.voice_combo.count()):
            voice = self.voice_panel.voice_combo.itemData(index)
            if isinstance(voice, VoiceOption) and str(voice.model_path) == self.config.tts_model_path:
                self.voice_panel.voice_combo.setCurrentIndex(index)
                break
        self.voice_panel.voice_combo.blockSignals(False)
        self._update_voice_details()

    def _update_voice_details(self) -> None:
        voice = self.voice_panel.voice_combo.currentData()
        if isinstance(voice, VoiceOption):
            self.voice_panel.voice_details.setText(f"{voice.label} | {voice.gender} | {voice.engine}\nModel: {voice.model_path.name}")
        else:
            self.voice_panel.voice_details.setText("No voice model detected.")
        self._update_model_hint()

    def _selected_voice_model_path(self) -> str:
        voice = self.voice_panel.voice_combo.currentData()
        if isinstance(voice, VoiceOption):
            return str(voice.model_path)
        return ""

    def _selected_voice_config_path(self) -> str:
        voice = self.voice_panel.voice_combo.currentData()
        if isinstance(voice, VoiceOption):
            return str(voice.config_path)
        return ""

    def _on_voice_selection_changed(self, index: int) -> None:
        voice = self.voice_panel.voice_combo.itemData(index)
        if not isinstance(voice, VoiceOption):
            self._update_voice_details()
            return
        persisted = self._update_config(
            tts_model_path=str(voice.model_path),
            tts_config_path=str(voice.config_path),
        )
        self._apply_runtime_config()
        self._update_voice_details()
        if persisted:
            self._set_activity(f"Selected voice: {voice.label}")
            self.refresh_status()

    def _on_input_device_changed(self, index: int) -> None:
        self._update_config(preferred_input_name=str(self.voice_panel.input_device_combo.itemData(index) or ""))
        self.recorder.set_input_device_name(self.config.preferred_input_name)

    def _on_output_device_changed(self, index: int) -> None:
        persisted = self._update_config(playback_output_name=str(self.voice_panel.output_device_combo.itemData(index) or ""))
        self.player.set_output_device_name(self.config.playback_output_name)
        self.startup_sequence.set_output_device_name(self.config.playback_output_name)
        if persisted:
            self.refresh_status()

    def _on_voice_tuning_changed(self) -> None:
        timer = getattr(self, "_voice_tuning_save_timer", None)
        if timer is not None:
            timer.start()
            return
        self._save_voice_tuning()

    def _save_voice_tuning(self) -> None:
        self._update_config(
            tts_rate=self.voice_panel.rate_slider.value() / 100.0,
            tts_volume=self.voice_panel.volume_slider.value() / 100.0,
        )

    def _refresh_input_device_options(self) -> None:
        available_inputs = self.recorder.list_inputs()
        desired_selection = self.config.preferred_input_name
        target_items = [("Automatic (system preferred)", "")]
        target_items.extend((input_name, input_name) for input_name in available_inputs)
        if desired_selection and desired_selection not in available_inputs:
            target_items.append(
                (f"Unavailable: {desired_selection} (automatic fallback)", desired_selection)
            )
        current_items = [
            (
                self.voice_panel.input_device_combo.itemText(index),
                self.voice_panel.input_device_combo.itemData(index) or "",
            )
            for index in range(self.voice_panel.input_device_combo.count())
        ]
        if current_items == target_items:
            self.voice_panel.input_device_combo.setCurrentIndex(max(0, self.voice_panel.input_device_combo.findData(desired_selection)))
            return

        self.voice_panel.input_device_combo.blockSignals(True)
        self.voice_panel.input_device_combo.clear()
        for label, value in target_items:
            self.voice_panel.input_device_combo.addItem(label, value)
        selected_index = self.voice_panel.input_device_combo.findData(desired_selection)
        self.voice_panel.input_device_combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        self.voice_panel.input_device_combo.blockSignals(False)

    def _refresh_output_device_options(self) -> None:
        available_outputs = self.player.list_outputs()
        desired_selection = self.config.playback_output_name
        target_items = [("Automatic (system preferred)", "")]
        target_items.extend((output_name, output_name) for output_name in available_outputs)
        if desired_selection and desired_selection not in available_outputs:
            target_items.append(
                (f"Unavailable: {desired_selection} (automatic fallback)", desired_selection)
            )
        current_items = [
            (
                self.voice_panel.output_device_combo.itemText(index),
                self.voice_panel.output_device_combo.itemData(index) or "",
            )
            for index in range(self.voice_panel.output_device_combo.count())
        ]
        if current_items == target_items:
            self.voice_panel.output_device_combo.setCurrentIndex(max(0, self.voice_panel.output_device_combo.findData(desired_selection)))
            return

        self.voice_panel.output_device_combo.blockSignals(True)
        self.voice_panel.output_device_combo.clear()
        for label, value in target_items:
            self.voice_panel.output_device_combo.addItem(label, value)
        selected_index = self.voice_panel.output_device_combo.findData(desired_selection)
        self.voice_panel.output_device_combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        self.voice_panel.output_device_combo.blockSignals(False)

    def _on_recording_changed(self, recording: bool, message: str) -> None:
        if recording:
            if getattr(self, "_voice_capture_started_at", None) is None:
                self._arm_voice_capture_health_check()
            self._apply_audio_state("Recording")
        else:
            self._clear_voice_capture_health_check()
            if not self.player.is_playing():
                self._apply_audio_state("Idle")
        self._set_activity(message)

    def _on_playback_changed(self, playing: bool, message: str) -> None:
        if playing and self.recorder.is_recording():
            self.recorder.cancel()
        playback_failed = not playing and (
            "device error" in message.casefold() or "cleanup error" in message.casefold()
        )
        if playback_failed:
            self._clear_voice_sequence()
            self._cancel_continuous_voice_resume(clear_arm=True)
            self._apply_audio_state("Idle")
            self.chat_panel.voice_only_panel.set_recovery_message(
                "Speaker playback stopped. Reconnect or select another output before continuing hands-free."
            )
            refresh_status = getattr(self, "refresh_status", None)
            if callable(refresh_status):
                refresh_status()
            self._set_activity(message)
            return
        pending_recovery = str(getattr(self, "_voice_output_recovery_message", "")).strip()
        if not playing and pending_recovery:
            self._voice_output_recovery_message = ""
            self._apply_audio_state("Idle")
            self.chat_panel.voice_only_panel.set_recovery_message(pending_recovery)
            self._set_activity(pending_recovery)
            return
        if not playing:
            request_id = getattr(self, "_tts_request_id", 0)
            if self._play_next_voice_chunk(request_id):
                return
            pending = (
                getattr(self, "_tts_synthesis_active", False)
                or int(getattr(self, "_tts_next_synthesis_index", 0))
                < len(getattr(self, "_tts_text_chunks", []))
            )
            if pending:
                self._apply_audio_state("Synthesizing")
                self._set_activity("Preparing the next spoken segment locally...")
                return
            self._apply_audio_state("Idle")
            self._schedule_continuous_voice_resume()
        else:
            self._apply_audio_state("Speaking")
        self._set_activity(message)
