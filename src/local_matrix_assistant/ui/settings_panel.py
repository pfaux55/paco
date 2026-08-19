from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from local_matrix_assistant.core.config import (
    AppConfig,
    MAX_CHAT_FONT_SIZE,
    MIN_CHAT_FONT_SIZE,
)
from local_matrix_assistant.core.model_catalog import RECOMMENDED_MODELS, RecommendedModel
from local_matrix_assistant.core.models import ModelPullProgress
from local_matrix_assistant.ui.inputs import NoWheelComboBox
from local_matrix_assistant.ui.theme import THEME_OPTIONS, THEME_PREVIEWS
from local_matrix_assistant.ui.status_panel import StatusPanel


class SettingsPanel(QWidget):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.setObjectName("settingsPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        settings_scroll = QScrollArea()
        settings_scroll.setObjectName("settingsScroll")
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        settings_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        settings_container = QWidget()
        settings_container.setObjectName("settingsSurface")
        settings_scroll.setWidget(settings_container)

        panel_layout = QVBoxLayout(settings_container)
        panel_layout.setContentsMargins(22, 18, 22, 22)
        panel_layout.setSpacing(14)

        appearance_card, appearance_layout = self._settings_card(
            "Appearance",
            "Choose how conversations and text entry look across every mode.",
        )
        appearance_grid = QGridLayout()
        appearance_grid.setContentsMargins(0, 2, 0, 0)
        appearance_grid.setHorizontalSpacing(16)
        appearance_grid.setVerticalSpacing(10)
        appearance_grid.setColumnStretch(0, 1)
        appearance_grid.setColumnStretch(1, 1)

        self.theme_combo = NoWheelComboBox()
        self.theme_combo.setObjectName("settingsControl")
        for theme_id, theme_name in THEME_OPTIONS:
            self.theme_combo.addItem(self._theme_preview_icon(theme_id), theme_name, theme_id)
        self.theme_combo.setIconSize(QSize(20, 20))
        self.theme_combo.setCurrentIndex(
            max(0, self.theme_combo.findData(config.theme))
        )
        appearance_grid.addWidget(
            self._field(
                "Color theme",
                self.theme_combo,
                "Applied instantly and remembered for future launches.",
            ),
            0,
            0,
        )

        font_controls = QWidget()
        font_controls.setObjectName("settingsControlGroup")
        font_row = QHBoxLayout()
        font_row.setContentsMargins(0, 0, 0, 0)
        font_row.setSpacing(8)
        font_controls.setLayout(font_row)
        self.font_family_combo = NoWheelComboBox()
        self.font_family_combo.setObjectName("settingsControl")
        self.font_family_combo.setEditable(False)
        font_families = QFontDatabase.families()
        if config.chat_font_family not in font_families:
            font_families.insert(0, config.chat_font_family)
        for family in font_families:
            self.font_family_combo.addItem(family)
            self.font_family_combo.setItemData(
                self.font_family_combo.count() - 1,
                QFont(family),
                Qt.ItemDataRole.FontRole,
            )
        family_index = self.font_family_combo.findText(config.chat_font_family)
        if family_index < 0:
            fallback_family = QFontDatabase.systemFont(
                QFontDatabase.SystemFont.GeneralFont
            ).family()
            family_index = self.font_family_combo.findText(fallback_family)
        self.font_family_combo.setCurrentIndex(max(0, family_index))
        self.font_family_combo.setAccessibleName("Chat font family")
        self.font_size_combo = NoWheelComboBox()
        self.font_size_combo.setObjectName("settingsControl")
        self.font_size_combo.setEditable(False)
        for size in range(MIN_CHAT_FONT_SIZE, MAX_CHAT_FONT_SIZE + 1):
            self.font_size_combo.addItem(f"{size} pt", size)
        self.font_size_combo.setCurrentIndex(
            max(0, self.font_size_combo.findData(config.chat_font_size))
        )
        self.font_size_combo.setAccessibleName("Chat font size")
        self.font_size_combo.setFixedWidth(92)
        font_row.addWidget(self.font_family_combo, 1)
        font_row.addWidget(self.font_size_combo)
        appearance_grid.addWidget(
            self._field(
                "Chat and input font",
                font_controls,
                "Used for messages, text boxes, and compact mode.",
            ),
            0,
            1,
        )
        appearance_layout.addLayout(appearance_grid)
        panel_layout.addWidget(appearance_card)

        model_card, model_layout = self._settings_card(
            "Local AI",
            "Connect to Ollama, select a fallback model, or install one locally.",
        )
        model_grid = QGridLayout()
        model_grid.setContentsMargins(0, 2, 0, 0)
        model_grid.setHorizontalSpacing(16)
        model_grid.setVerticalSpacing(10)
        model_grid.setColumnStretch(0, 1)
        model_grid.setColumnStretch(1, 1)

        self.ollama_host_input = QLineEdit(config.ollama_base_url)
        self.ollama_host_input.setObjectName("settingsControl")
        self.ollama_host_input.setPlaceholderText("Ollama base URL")
        model_grid.addWidget(
            self._field(
                "Ollama endpoint",
                self.ollama_host_input,
                "Local service address. The default uses this computer only.",
            ),
            0,
            0,
        )

        self.model_combo = NoWheelComboBox()
        self.model_combo.setObjectName("settingsControl")
        self.model_combo.setEditable(False)
        model_grid.addWidget(
            self._field(
                "Fallback model",
                self.model_combo,
                "Used by Manual routing and when no task-specific model matches.",
            ),
            0,
            1,
        )
        model_layout.addLayout(model_grid)

        self._installed_models: set[str] = set()
        self._model_install_busy = False
        self._installing_model = ""
        model_install_card = QFrame()
        model_install_card.setObjectName("modelInstallCard")
        model_install_layout = QVBoxLayout(model_install_card)
        model_install_layout.setContentsMargins(16, 15, 16, 15)
        model_install_layout.setSpacing(10)

        install_header = QHBoxLayout()
        install_header.setSpacing(8)
        model_install_heading = QLabel("Add a local model")
        model_install_heading.setObjectName("settingsInsetTitle")
        install_header.addWidget(model_install_heading)
        install_header.addStretch(1)
        local_badge = QLabel("LOCAL")
        local_badge.setObjectName("settingsLocalBadge")
        install_header.addWidget(local_badge)
        model_install_layout.addLayout(install_header)
        model_install_note = QLabel(
            "Downloads stay on this device and are managed by Ollama."
        )
        model_install_note.setObjectName("settingsFieldHelp")
        model_install_note.setWordWrap(True)
        model_install_layout.addWidget(model_install_note)

        self.model_install_combo = NoWheelComboBox()
        self.model_install_combo.setObjectName("settingsControl")
        for model in RECOMMENDED_MODELS:
            self.model_install_combo.addItem(model.display_name, model.name)
        model_install_layout.addWidget(self.model_install_combo)

        self.model_install_details = QLabel()
        self.model_install_details.setObjectName("settingsModelDetails")
        self.model_install_details.setWordWrap(True)
        model_install_layout.addWidget(self.model_install_details)

        model_install_controls = QHBoxLayout()
        self.model_install_button = QPushButton("Install")
        self.model_install_button.setObjectName("modelInstallButton")
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
        self.model_install_status.setObjectName("settingsInstallStatus")
        self.model_install_status.setWordWrap(True)
        model_install_layout.addWidget(self.model_install_status)
        model_layout.addWidget(model_install_card)
        panel_layout.addWidget(model_card)

        self.model_install_combo.currentIndexChanged.connect(
            self._on_model_install_selection_changed
        )
        self._update_model_install_details()

        voice_card, voice_layout = self._settings_card(
            "Voice runtime",
            "Point Paco to local speech recognition and speech synthesis files.",
        )

        self.stt_path_input = QLineEdit(config.stt_model_dir)
        self.stt_path_input.setObjectName("settingsControl")
        voice_layout.addWidget(
            self._field(
                "Speech recognition directory",
                self.stt_path_input,
                "Vosk model folder used for local transcription.",
            )
        )

        self.tts_model_input = QLineEdit(config.tts_model_path)
        self.tts_model_input.setObjectName("settingsControl")
        voice_layout.addWidget(
            self._field(
                "Speech synthesis model",
                self.tts_model_input,
                "Piper ONNX voice model used for local playback.",
            )
        )

        self.tts_config_input = QLineEdit(config.tts_config_path)
        self.tts_config_input.setObjectName("settingsControl")
        voice_layout.addWidget(
            self._field(
                "Speech synthesis configuration",
                self.tts_config_input,
                "JSON configuration paired with the Piper model.",
            )
        )
        panel_layout.addWidget(voice_card)
        panel_layout.addStretch(1)

        layout.addWidget(settings_scroll, 1)

        action_bar = QFrame()
        action_bar.setObjectName("settingsActionBar")
        action_bar.setAccessibleName("Settings actions")
        control_row = QHBoxLayout()
        control_row.setContentsMargins(16, 12, 16, 12)
        control_row.setSpacing(10)
        action_bar.setLayout(control_row)
        self.status_panel = StatusPanel()
        self.status_panel.setObjectName("settingsStatus")
        control_row.addWidget(self.status_panel, 1)
        self.refresh_button = QPushButton("Refresh Status")
        self.refresh_button.setObjectName("secondaryButton")
        self.save_button = QPushButton("Save Settings")
        self.save_button.setObjectName("primaryButton")
        control_row.addWidget(self.refresh_button, 0)
        control_row.addWidget(self.save_button)
        layout.addWidget(action_bar)

    @staticmethod
    def _settings_card(
        title: str,
        description: str,
    ) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("settingsCard")
        card.setAccessibleName(f"{title} settings")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 17, 18, 18)
        card_layout.setSpacing(13)

        copy = QVBoxLayout()
        copy.setSpacing(2)
        heading = QLabel(title)
        heading.setObjectName("settingsSectionTitle")
        copy.addWidget(heading)
        note = QLabel(description)
        note.setObjectName("settingsSectionDescription")
        note.setWordWrap(True)
        copy.addWidget(note)
        card_layout.addLayout(copy)
        return card, card_layout

    @staticmethod
    def _field(label_text: str, control: QWidget, help_text: str) -> QWidget:
        field = QWidget()
        field.setObjectName("settingsField")
        field_layout = QVBoxLayout(field)
        field_layout.setContentsMargins(0, 0, 0, 0)
        field_layout.setSpacing(6)
        label = QLabel(label_text)
        label.setObjectName("settingsFieldLabel")
        field_layout.addWidget(label)
        field_layout.addWidget(control)
        help_label = QLabel(help_text)
        help_label.setObjectName("settingsFieldHelp")
        help_label.setWordWrap(True)
        field_layout.addWidget(help_label)
        return field

    @staticmethod
    def _theme_preview_icon(theme_id: str) -> QIcon:
        background, accent = THEME_PREVIEWS[theme_id]
        preview = QPixmap(18, 18)
        preview.fill(QColor("transparent"))
        painter = QPainter(preview)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        circle = QPainterPath()
        circle.addEllipse(1, 1, 16, 16)
        painter.setClipPath(circle)
        painter.fillRect(0, 0, 9, 18, QColor(background))
        painter.fillRect(9, 0, 9, 18, QColor(accent))
        painter.setClipping(False)
        painter.setPen(QColor("#718078"))
        painter.drawEllipse(1, 1, 16, 16)
        painter.end()
        return QIcon(preview)

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
