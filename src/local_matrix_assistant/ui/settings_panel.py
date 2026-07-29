from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from local_matrix_assistant.core.config import AppConfig
from local_matrix_assistant.core.model_catalog import RECOMMENDED_MODELS, RecommendedModel
from local_matrix_assistant.core.models import ModelPullProgress
from local_matrix_assistant.ui.inputs import NoWheelComboBox
from local_matrix_assistant.ui.status_panel import StatusPanel


class SettingsPanel(QWidget):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        settings_container = QWidget()
        settings_scroll.setWidget(settings_container)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)
        panel_layout.setSpacing(14)

        settings_header = QLabel("System Controls")
        settings_header.setObjectName("messageRole")
        panel_layout.addWidget(settings_header)

        self.ollama_host_input = QLineEdit(config.ollama_base_url)
        self.ollama_host_input.setPlaceholderText("Ollama base URL")
        panel_layout.addWidget(QLabel("Ollama Host"))
        panel_layout.addWidget(self.ollama_host_input)

        self.model_combo = NoWheelComboBox()
        self.model_combo.setEditable(False)
        panel_layout.addWidget(QLabel("Ollama Model"))
        panel_layout.addWidget(self.model_combo)
        model_note = QLabel("Used by Manual mode and as a fallback. Choose task routing from the Chat composer.")
        model_note.setObjectName("statusLabel")
        model_note.setWordWrap(True)
        panel_layout.addWidget(model_note)

        self._installed_models: set[str] = set()
        self._model_install_busy = False
        self._installing_model = ""
        model_install_card = QFrame()
        model_install_card.setObjectName("modelInstallCard")
        model_install_layout = QVBoxLayout(model_install_card)
        model_install_layout.setContentsMargins(14, 14, 14, 14)
        model_install_layout.setSpacing(9)

        model_install_heading = QLabel("Install a Local Model")
        model_install_heading.setObjectName("messageRole")
        model_install_layout.addWidget(model_install_heading)
        model_install_note = QLabel(
            "Downloads are stored and managed by Ollama. Model sizes are approximate."
        )
        model_install_note.setObjectName("statusLabel")
        model_install_note.setWordWrap(True)
        model_install_layout.addWidget(model_install_note)

        self.model_install_combo = NoWheelComboBox()
        for model in RECOMMENDED_MODELS:
            self.model_install_combo.addItem(model.display_name, model.name)
        model_install_layout.addWidget(self.model_install_combo)

        self.model_install_details = QLabel()
        self.model_install_details.setObjectName("statusLabel")
        self.model_install_details.setWordWrap(True)
        model_install_layout.addWidget(self.model_install_details)

        model_install_controls = QHBoxLayout()
        self.model_install_button = QPushButton("Install")
        self.model_cancel_button = QPushButton("Cancel")
        self.model_cancel_button.setObjectName("secondaryButton")
        self.model_cancel_button.hide()
        model_install_controls.addWidget(self.model_install_button)
        model_install_controls.addWidget(self.model_cancel_button)
        model_install_controls.addStretch(1)
        model_install_layout.addLayout(model_install_controls)

        self.model_install_progress = QProgressBar()
        self.model_install_progress.setObjectName("modelInstallProgress")
        self.model_install_progress.setTextVisible(False)
        self.model_install_progress.hide()
        model_install_layout.addWidget(self.model_install_progress)

        self.model_install_status = QLabel("Choose a recommended model to install.")
        self.model_install_status.setObjectName("statusLabel")
        self.model_install_status.setWordWrap(True)
        model_install_layout.addWidget(self.model_install_status)
        panel_layout.addWidget(model_install_card)

        self.model_install_combo.currentIndexChanged.connect(
            self._on_model_install_selection_changed
        )
        self._update_model_install_details()

        self.stt_path_input = QLineEdit(config.stt_model_dir)
        panel_layout.addWidget(QLabel("STT Model Directory"))
        panel_layout.addWidget(self.stt_path_input)

        self.tts_model_input = QLineEdit(config.tts_model_path)
        panel_layout.addWidget(QLabel("TTS Model File"))
        panel_layout.addWidget(self.tts_model_input)

        self.tts_config_input = QLineEdit(config.tts_config_path)
        panel_layout.addWidget(QLabel("TTS Config File"))
        panel_layout.addWidget(self.tts_config_input)

        control_row = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh Status")
        self.save_button = QPushButton("Save Settings")
        control_row.addWidget(self.refresh_button)
        control_row.addWidget(self.save_button)
        panel_layout.addLayout(control_row)

        self.status_panel = StatusPanel()
        panel_layout.addWidget(self.status_panel)
        panel_layout.addStretch(1)

        settings_container_layout = QVBoxLayout(settings_container)
        settings_container_layout.setContentsMargins(0, 0, 0, 0)
        settings_container_layout.addWidget(panel)
        layout.addWidget(settings_scroll)

    def selected_recommended_model(self) -> RecommendedModel | None:
        selected_name = str(self.model_install_combo.currentData() or "")
        return next((model for model in RECOMMENDED_MODELS if model.name == selected_name), None)

    def set_installed_models(self, models: list[str]) -> None:
        self._installed_models = {model.casefold() for model in models}
        if not self._model_install_busy:
            self._update_model_install_details()

    def set_model_install_busy(self, model_name: str) -> None:
        self._model_install_busy = True
        self._installing_model = model_name
        self.model_install_combo.setEnabled(False)
        self.model_install_button.setEnabled(False)
        self.model_cancel_button.setEnabled(True)
        self.model_cancel_button.show()
        self.model_install_progress.setRange(0, 0)
        self.model_install_progress.show()
        self.model_install_status.setText(f"Starting {model_name} download...")

    def set_model_install_canceling(self) -> None:
        if not self._model_install_busy:
            return
        self.model_cancel_button.setEnabled(False)
        self.model_install_status.setText("Canceling model install...")

    def set_model_install_progress(self, progress: ModelPullProgress) -> None:
        if not self._model_install_busy or progress.model != self._installing_model:
            return
        percent = progress.percent
        status = progress.status.rstrip(".")
        if percent is None:
            self.model_install_progress.setRange(0, 0)
            self.model_install_status.setText(status.capitalize())
            return
        self.model_install_progress.setRange(0, 100)
        self.model_install_progress.setValue(percent)
        completed = self._format_bytes(progress.completed_bytes)
        total = self._format_bytes(progress.total_bytes)
        self.model_install_status.setText(
            f"{status.capitalize()} - {percent}% ({completed} of {total})"
        )

    def set_model_install_finished(self, model_name: str) -> None:
        self._installed_models.add(model_name.casefold())
        self._finish_model_install()
        self.model_install_progress.setRange(0, 100)
        self.model_install_progress.setValue(100)
        self.model_install_progress.show()
        self.model_install_status.setText(f"Installed {model_name}.")

    def set_model_install_canceled(self, model_name: str) -> None:
        self._finish_model_install()
        self.model_install_progress.hide()
        self.model_install_status.setText(
            f"Canceled {model_name}. Ollama may retain reusable partial download data."
        )

    def set_model_install_error(self, message: str) -> None:
        self._finish_model_install()
        self.model_install_progress.hide()
        self.model_install_status.setText(f"Install failed: {message}")

    def _finish_model_install(self) -> None:
        self._model_install_busy = False
        self._installing_model = ""
        self.model_install_combo.setEnabled(True)
        self.model_cancel_button.hide()
        self.model_cancel_button.setEnabled(True)
        self._update_model_install_details()

    def _on_model_install_selection_changed(self) -> None:
        if self._model_install_busy:
            return
        self.model_install_progress.hide()
        self._update_model_install_details()
        selected = self.selected_recommended_model()
        if selected is None:
            self.model_install_status.setText("Choose a recommended model to install.")
        elif selected.name.casefold() in self._installed_models:
            self.model_install_status.setText(f"{selected.name} is already installed.")
        else:
            self.model_install_status.setText("Ready to download from Ollama.")

    def _update_model_install_details(self) -> None:
        selected = self.selected_recommended_model()
        if selected is None:
            self.model_install_details.clear()
            self.model_install_button.setText("Install")
            self.model_install_button.setEnabled(False)
            return
        self.model_install_details.setText(selected.purpose)
        installed = selected.name.casefold() in self._installed_models
        self.model_install_button.setText("Installed" if installed else "Install")
        self.model_install_button.setEnabled(not installed and not self._model_install_busy)

    @staticmethod
    def _format_bytes(value: int) -> str:
        size = float(max(0, value))
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
            size /= 1024
        return f"{size:.1f} GB"
