from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from local_matrix_assistant.core.config import AppConfig
from local_matrix_assistant.ui.inputs import NoWheelComboBox, NoWheelSlider
from local_matrix_assistant.ui.status_panel import StatusPanel


class VoicePanel(QWidget):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        voice_scroll = QScrollArea()
        voice_scroll.setWidgetResizable(True)
        voice_scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        voice_container = QWidget()
        voice_scroll.setWidget(voice_container)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)
        panel_layout.setSpacing(14)

        voice_header = QLabel("Voice Controls")
        voice_header.setObjectName("messageRole")
        panel_layout.addWidget(voice_header)

        self.voice_enabled_checkbox = QCheckBox("Enable spoken voice responses")
        self.voice_enabled_checkbox.setChecked(config.voice_enabled)
        self.voice_enabled_checkbox.setAccessibleName("Enable spoken voice responses")
        panel_layout.addWidget(self.voice_enabled_checkbox)

        self.auto_speak_checkbox = QCheckBox("Auto-play assistant replies aloud")
        self.auto_speak_checkbox.setChecked(config.auto_speak_responses)
        self.auto_speak_checkbox.setAccessibleName("Auto-play assistant replies aloud")
        panel_layout.addWidget(self.auto_speak_checkbox)

        self.continuous_voice_checkbox = QCheckBox("Continue listening after spoken replies")
        self.continuous_voice_checkbox.setChecked(config.continuous_voice_enabled)
        self.continuous_voice_checkbox.setToolTip(
            "Hands-free conversation in Voice Only mode. Listening resumes after playback."
        )
        self.continuous_voice_checkbox.setAccessibleName(
            "Continue listening after spoken replies"
        )
        panel_layout.addWidget(self.continuous_voice_checkbox)

        self.microphone_muted_checkbox = QCheckBox("Mute microphone")
        self.microphone_muted_checkbox.setChecked(config.microphone_muted)
        self.microphone_muted_checkbox.setToolTip("Blocks new microphone capture until unmuted")
        self.microphone_muted_checkbox.setAccessibleName("Mute microphone")
        panel_layout.addWidget(self.microphone_muted_checkbox)

        self.privacy_note = QLabel("Voice stays local: microphone audio is transcribed on this device.")
        self.privacy_note.setObjectName("privacyNote")
        self.privacy_note.setWordWrap(True)
        panel_layout.addWidget(self.privacy_note)

        panel_layout.addWidget(QLabel("Active TTS Engine"))
        self.engine_value = QLabel("Piper (local)")
        self.engine_value.setObjectName("statusStrip")
        self.engine_value.setAccessibleName("Active text to speech engine: Piper local")
        panel_layout.addWidget(self.engine_value)

        self.voice_combo = NoWheelComboBox()
        self.voice_combo.setAccessibleName("Text to speech voice")
        panel_layout.addWidget(QLabel("Voice"))
        panel_layout.addWidget(self.voice_combo)

        self.voice_details = QLabel("No voice model detected.")
        self.voice_details.setWordWrap(True)
        self.voice_details.setObjectName("statusLabel")
        self.voice_details.setAccessibleName("Selected voice details")
        panel_layout.addWidget(self.voice_details)

        self.input_device_combo = NoWheelComboBox()
        self.input_device_combo.setAccessibleName("Microphone input device")
        panel_layout.addWidget(QLabel("Microphone Input"))
        panel_layout.addWidget(self.input_device_combo)

        self.output_device_combo = NoWheelComboBox()
        self.output_device_combo.setAccessibleName("Speaker output device")
        panel_layout.addWidget(QLabel("Speaker Output"))
        panel_layout.addWidget(self.output_device_combo)

        panel_layout.addWidget(QLabel("Rate"))
        self.rate_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.rate_slider.setRange(50, 150)
        self.rate_slider.setValue(int(config.tts_rate * 100))
        self.rate_slider.setAccessibleName("Voice speaking rate")
        panel_layout.addWidget(self.rate_slider)
        self.rate_value = QLabel("")
        self.rate_value.setObjectName("statusLabel")
        panel_layout.addWidget(self.rate_value)

        panel_layout.addWidget(QLabel("Volume"))
        self.volume_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 150)
        self.volume_slider.setValue(int(config.tts_volume * 100))
        self.volume_slider.setAccessibleName("Voice output volume")
        panel_layout.addWidget(self.volume_slider)
        self.volume_value = QLabel("")
        self.volume_value.setObjectName("statusLabel")
        panel_layout.addWidget(self.volume_value)

        panel_layout.addWidget(QLabel("Preview Text"))
        self.preview_input = QLineEdit("System check. Local voice preview ready.")
        self.preview_input.setAccessibleName("Voice preview text")
        panel_layout.addWidget(self.preview_input)

        button_row = QHBoxLayout()
        self.preview_button = QPushButton("Preview Voice")
        self.stop_preview_button = QPushButton("Stop Voice")
        self.preview_button.setAccessibleName("Play voice preview")
        self.stop_preview_button.setAccessibleName("Stop spoken output")
        self.stop_preview_button.setToolTip("Stop spoken output (Ctrl+Shift+X)")
        button_row.addWidget(self.preview_button)
        button_row.addWidget(self.stop_preview_button)
        panel_layout.addLayout(button_row)

        self.audio_state_value = QLabel("Audio: Idle")
        self.audio_state_value.setObjectName("statusStrip")
        self.audio_state_value.setAccessibleName("Voice status: Idle")
        panel_layout.addWidget(self.audio_state_value)

        self.status_panel = StatusPanel()
        panel_layout.addWidget(self.status_panel)
        panel_layout.addStretch(1)

        voice_container_layout = QVBoxLayout(voice_container)
        voice_container_layout.setContentsMargins(0, 0, 0, 0)
        voice_container_layout.addWidget(panel)
        layout.addWidget(voice_scroll)

        self._update_slider_labels()
        self.rate_slider.valueChanged.connect(self._update_slider_labels)
        self.volume_slider.valueChanged.connect(self._update_slider_labels)

    def _update_slider_labels(self) -> None:
        self.rate_value.setText(f"{self.rate_slider.value()}% of default speed")
        self.volume_value.setText(f"{self.volume_slider.value()}% of default volume")
        self.rate_slider.setAccessibleDescription(
            f"{self.rate_slider.value()} percent of default speed"
        )
        self.volume_slider.setAccessibleDescription(
            f"{self.volume_slider.value()} percent of default volume"
        )

    def set_audio_state(self, text: str) -> None:
        self.audio_state_value.setText(f"Audio: {text}")
        self.audio_state_value.setAccessibleName(f"Voice status: {text}")
