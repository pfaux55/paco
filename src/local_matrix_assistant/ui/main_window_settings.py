from __future__ import annotations

from dataclasses import replace
from functools import partial

from PySide6.QtWidgets import QApplication

from local_matrix_assistant.core.constants import DEFAULT_ACTIVITY
from local_matrix_assistant.core.model_catalog import RECOMMENDED_MODEL_NAMES
from local_matrix_assistant.core.models import ModelPullProgress, ModelPullResult, StatusSnapshot
from local_matrix_assistant.ui.workers import FunctionWorker, StreamWorker
from local_matrix_assistant.ui.theme import normalize_theme, stylesheet_for_theme


class SettingsStatusWindowMixin:
    def refresh_status(self) -> None:
        if self._status_poll_inflight:
            return
        self._status_poll_inflight = True
        self._set_activity("Refreshing local service status...")
        model_name = self.settings_panel.model_combo.currentText().strip() or self.config.ollama_model
        worker = FunctionWorker(partial(self.status_service.build_snapshot, model_name))
        self.task_runner.start(worker, self._apply_status_snapshot, self._on_status_error)

    def _apply_status_snapshot(self, payload: object) -> None:
        assert isinstance(payload, StatusSnapshot)
        self._status_poll_inflight = False
        self._refresh_input_device_options()
        self._refresh_output_device_options()
        self._populate_voice_options()
        self._sync_available_models(payload.available_models)

        active_model = self.settings_panel.model_combo.currentText().strip() or self.config.ollama_model
        model_ready = payload.ollama_connected and active_model in payload.available_models
        payload = replace(
            payload,
            model_ready=model_ready,
            model_name=active_model,
            model_message=(f"Using {active_model}" if model_ready else payload.model_message),
        )
        self._last_status_snapshot = payload

        self.chat_panel.status_panel.set_snapshot(payload)
        self.voice_panel.status_panel.set_snapshot(payload)
        self.settings_panel.status_panel.set_snapshot(payload)
        self._sync_system_notice(payload)
        self._update_model_hint()
        if self.chat_panel.status_panel.status_label.text() == "Refreshing local service status...":
            self._set_activity(DEFAULT_ACTIVITY)

    def _on_status_error(self, message: str) -> None:
        self._status_poll_inflight = False
        self._set_activity(f"Status refresh failed: {message}")
        if getattr(self, "_settings_save_pending", False):
            return
        self._show_system_notice(
            key="status_refresh_failed",
            message=f"Local service status could not be refreshed: {message}",
            severity="error",
            action_id="retry_status",
            action_label="Retry",
        )

    def _save_settings(self) -> None:
        persisted = self._update_config(
            theme=normalize_theme(str(self.settings_panel.theme_combo.currentData() or "")),
            ollama_base_url=self.settings_panel.ollama_host_input.text().strip() or self.config.ollama_base_url,
            stt_model_dir=self.settings_panel.stt_path_input.text().strip(),
            tts_model_path=self._selected_voice_model_path() or self.settings_panel.tts_model_input.text().strip(),
            tts_config_path=self._selected_voice_config_path() or self.settings_panel.tts_config_input.text().strip(),
            preferred_input_name=self.voice_panel.input_device_combo.currentData() or "",
            playback_output_name=self.voice_panel.output_device_combo.currentData() or "",
            ollama_model=self.settings_panel.model_combo.currentText().strip() or self.config.ollama_model,
        )
        self._apply_runtime_config()
        if not persisted:
            return
        self._set_activity("Settings saved.")
        self.refresh_status()

    def _apply_runtime_config(self) -> None:
        self.ollama_client.update_base_url(self.config.ollama_base_url)
        self.stt_service.update_model_dir(self.config.stt_model_dir)
        self.tts_service.update_paths(self.config.tts_model_path, self.config.tts_config_path)
        self.recorder.set_input_device_name(self.config.preferred_input_name)
        self.player.set_output_device_name(self.config.playback_output_name)
        self.startup_sequence.set_output_device_name(self.config.playback_output_name)
        self.desktop_action_service.update_working_folders(
            self.config.working_folders,
            self.config.active_working_folder,
        )
        self.agent_panel.set_active_folder(str(self.desktop_action_service.active_working_folder or ""))
        self.settings_panel.tts_model_input.setText(self.config.tts_model_path)
        self.settings_panel.tts_config_input.setText(self.config.tts_config_path)

    def _set_activity(self, text: str) -> None:
        self.chat_panel.status_panel.set_activity(text)
        self.agent_panel.status_panel.set_activity(text)
        self.voice_panel.status_panel.set_activity(text)
        self.settings_panel.status_panel.set_activity(text)
        if hasattr(self, "sidebar_activity_label"):
            self.sidebar_activity_label.setText(text)

    def _apply_initial_ui_state(self) -> None:
        self.chat_panel.set_web_search_enabled(self.config.web_search_enabled)
        self.chat_panel.set_model_profile(self.config.model_profile)
        self.voice_panel.engine_value.setText(self.tts_service.engine_name)
        self.chat_panel.voice_only_panel.set_microphone_muted(self.config.microphone_muted)
        self.chat_panel.voice_only_panel.set_continuous_enabled(self.config.continuous_voice_enabled)
        self.voice_panel.continuous_voice_checkbox.setChecked(self.config.continuous_voice_enabled)

    def _sync_available_models(self, available_models: list[str]) -> None:
        self.available_ollama_models = list(available_models)
        self.settings_panel.set_installed_models(available_models)
        current_items = [self.settings_panel.model_combo.itemText(index) for index in range(self.settings_panel.model_combo.count())]
        desired_selection = self.config.ollama_model.strip()
        if current_items != available_models:
            self.settings_panel.model_combo.blockSignals(True)
            self.settings_panel.model_combo.clear()
            self.settings_panel.model_combo.addItems(available_models)
            self.settings_panel.model_combo.blockSignals(False)

        if not available_models:
            return
        if desired_selection in available_models:
            self.settings_panel.model_combo.setCurrentText(desired_selection)
            return
        self.settings_panel.model_combo.setCurrentIndex(0)
        selected_model = self.settings_panel.model_combo.currentText().strip()
        if selected_model and selected_model != self.config.ollama_model:
            self._update_config(ollama_model=selected_model)

    def _update_config(self, **changes: object) -> bool:
        self.config = replace(self.config, **changes)
        try:
            self.config.save(self.paths)
        except OSError as exc:
            self._settings_save_pending = True
            self._set_activity(f"Setting applied for this session but could not be saved: {exc}")
            self._show_system_notice(
                key="settings_unsaved",
                message=f"Settings are active for this session but were not saved: {exc}",
                severity="error",
                action_id="retry_settings",
                action_label="Retry Save",
                dismissible=False,
            )
            return False
        if getattr(self, "_settings_save_pending", False):
            self._settings_save_pending = False
            self._sync_system_notice(getattr(self, "_last_status_snapshot", None))
        return True

    def _sync_system_notice(self, snapshot: StatusSnapshot | None) -> None:
        if getattr(self, "_settings_save_pending", False):
            return
        if snapshot is None:
            notice = getattr(self, "system_notice", None)
            if notice is not None:
                notice.clear_notice()
            return
        if not snapshot.ollama_connected:
            self._show_system_notice(
                key="ollama_offline",
                message="Ollama is offline. Start Ollama locally, then retry the connection check.",
                action_id="retry_status",
                action_label="Retry",
            )
            return
        if not snapshot.available_models:
            self._show_system_notice(
                key="ollama_models_missing",
                message="No local Ollama model is installed. Install one from Settings.",
                action_id="open_settings",
                action_label="Install Model",
            )
            return
        if not snapshot.model_ready:
            self._show_system_notice(
                key="selected_model_missing",
                message=snapshot.model_message,
                action_id="open_settings",
                action_label="Open Settings",
            )
            return
        if not (snapshot.stt_ready and snapshot.tts_ready):
            self._show_system_notice(
                key="voice_models_missing",
                message="Local speech models are incomplete. Review the configured STT and TTS files.",
                action_id="open_settings",
                action_label="Open Settings",
            )
            return
        if not snapshot.mic_available:
            self._show_system_notice(
                key="microphone_missing",
                message=f"Microphone unavailable: {snapshot.mic_message}",
                action_id="open_voice",
                action_label="Open Voice",
            )
            return
        if not snapshot.output_available:
            self._show_system_notice(
                key="speaker_missing",
                message=f"Speaker output unavailable: {snapshot.output_message}",
                action_id="open_voice",
                action_label="Open Voice",
            )
            return
        self._dismissed_system_notice_key = ""
        notice = getattr(self, "system_notice", None)
        if notice is not None:
            notice.clear_notice()

    def _show_system_notice(
        self,
        *,
        key: str,
        message: str,
        severity: str = "warning",
        action_id: str = "",
        action_label: str = "",
        dismissible: bool = True,
    ) -> None:
        notice = getattr(self, "system_notice", None)
        if notice is None:
            return
        if dismissible and key == getattr(self, "_dismissed_system_notice_key", ""):
            notice.clear_notice()
            return
        notice.show_notice(
            key=key,
            message=message,
            severity=severity,
            action_id=action_id,
            action_label=action_label,
            dismissible=dismissible,
        )

    def _dismiss_system_notice(self) -> None:
        notice = getattr(self, "system_notice", None)
        if notice is None:
            return
        self._dismissed_system_notice_key = notice.notice_key
        notice.clear_notice()

    def _on_system_notice_action(self, action_id: str) -> None:
        if action_id == "retry_status":
            self._dismissed_system_notice_key = ""
            self.refresh_status()
        elif action_id == "open_settings":
            self._show_page(3, self.settings_nav_button)
        elif action_id == "open_voice":
            self._show_page(2, self.voice_nav_button)
        elif action_id == "retry_settings":
            self._retry_settings_save()

    def _retry_settings_save(self) -> None:
        try:
            self.config.save(self.paths)
        except OSError as exc:
            self._settings_save_pending = True
            self._set_activity(f"Settings still could not be saved: {exc}")
            self._show_system_notice(
                key="settings_unsaved",
                message=f"Settings still could not be saved: {exc}",
                severity="error",
                action_id="retry_settings",
                action_label="Retry Save",
                dismissible=False,
            )
            return
        self._settings_save_pending = False
        self._set_activity("Settings saved.")
        self._sync_system_notice(getattr(self, "_last_status_snapshot", None))

    def _on_model_changed(self, model_name: str) -> None:
        if not model_name:
            return
        persisted = self._update_config(ollama_model=model_name)
        self._active_model_selection = None
        self._update_model_hint()
        if persisted:
            self._set_activity(f"Selected local model: {model_name}")

    def _start_model_install(self) -> None:
        if self._active_model_pull_worker is not None:
            return
        selected = self.settings_panel.selected_recommended_model()
        if selected is None or selected.name not in RECOMMENDED_MODEL_NAMES:
            self.settings_panel.set_model_install_error("Choose a recommended model.")
            return
        if selected.name.casefold() in {
            model.casefold() for model in self.available_ollama_models
        }:
            self.settings_panel.set_installed_models(self.available_ollama_models)
            return

        model_name = selected.name
        worker = StreamWorker(
            lambda on_progress, should_cancel: self.ollama_client.pull_model(
                model_name,
                on_progress,
                should_cancel,
            )
        )
        self._active_model_pull_worker = worker
        self._active_model_pull_name = model_name
        self.settings_panel.set_model_install_busy(model_name)
        self._set_activity(f"Installing local model: {model_name}")
        self.task_runner.start_stream(
            worker,
            self._on_model_pull_progress,
            self._on_model_pull_finished,
            self._on_model_pull_error,
        )

    def _cancel_model_install(self) -> None:
        worker = self._active_model_pull_worker
        if worker is None:
            return
        worker.cancel()
        self.settings_panel.set_model_install_canceling()
        self._set_activity(f"Canceling local model install: {self._active_model_pull_name}")

    def _on_model_pull_progress(self, payload: object) -> None:
        if not isinstance(payload, ModelPullProgress):
            return
        if payload.model != self._active_model_pull_name:
            return
        self.settings_panel.set_model_install_progress(payload)

    def _on_model_pull_finished(self, payload: object) -> None:
        if not isinstance(payload, ModelPullResult):
            self._on_model_pull_error("Ollama returned an invalid install result.")
            return
        if payload.model != self._active_model_pull_name:
            return
        self._active_model_pull_worker = None
        self._active_model_pull_name = ""
        if payload.canceled:
            self.settings_panel.set_model_install_canceled(payload.model)
            self._set_activity(f"Canceled local model install: {payload.model}")
            return

        self.settings_panel.set_model_install_finished(payload.model)
        self._update_config(ollama_model=payload.model)
        self._dismissed_system_notice_key = ""
        self._set_activity(f"Installed local model: {payload.model}")
        self.refresh_status()

    def _on_theme_changed(self) -> None:
        theme = normalize_theme(str(self.settings_panel.theme_combo.currentData() or ""))
        if theme == self.config.theme:
            return
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet_for_theme(theme))
        persisted = self._update_config(theme=theme)
        self._set_activity(
            "Theme changed." if persisted else "Theme changed for this session."
        )

    def _on_model_pull_error(self, message: str) -> None:
        model_name = self._active_model_pull_name
        self._active_model_pull_worker = None
        self._active_model_pull_name = ""
        self.settings_panel.set_model_install_error(message)
        self._set_activity(
            f"Could not install {model_name or 'the local model'}: {message}"
        )
