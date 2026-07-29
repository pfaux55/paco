from __future__ import annotations

import math

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class VoiceVisualizer(QWidget):
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._phase = 0.0
        self._state = "Idle"
        self._input_level = 0.0
        self._target_input_level = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self.setMinimumHeight(340)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Voice capture control")
        self.setAccessibleDescription(
            "Current voice state: Idle. Press Enter or Space to start voice capture."
        )

    def set_state(self, state: str) -> None:
        self._state = state or "Idle"
        if self._state != "Recording":
            self._target_input_level = 0.0
        self.setAccessibleDescription(
            f"Current voice state: {self._state}. Press Enter or Space to activate voice capture."
        )
        self.update()

    def set_input_level(self, level: int) -> None:
        normalized = min(1.0, max(0.0, level / 2600.0))
        self._target_input_level = normalized ** 0.55

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        event.accept()
        self.clicked.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in {
            Qt.Key.Key_Enter,
            Qt.Key.Key_Return,
            Qt.Key.Key_Space,
        }:
            event.accept()
            if not event.isAutoRepeat():
                self.clicked.emit()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = self.rect().adjusted(8, 8, -8, -8)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor(7, 18, 12))
        gradient.setColorAt(1.0, QColor(3, 9, 7))
        border_color = QColor(111, 255, 169) if self.hasFocus() else QColor(29, 111, 64)
        painter.setPen(QPen(border_color, 2 if self.hasFocus() else 1))
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, 24, 24)

        center_x = rect.center().x()
        center_y = rect.center().y() - 10
        state_energy = {
            "Idle": 0.18,
            "Muted": 0.04,
            "Recording": 1.0,
            "Interrupting": 0.52,
            "Transcribing": 0.48,
            "Thinking": 0.35,
            "Synthesizing": 0.58,
            "Speaking": 0.82,
        }.get(self._state, 0.26)
        if self._state == "Recording":
            state_energy = max(0.24, self._input_level)
        muted = self._state == "Muted"

        radius = min(rect.width(), rect.height()) * 0.17
        pulse = math.sin(self._phase * 1.8) * 0.5 + 0.5
        for scale, alpha in ((1.55, 36), (1.18, 72), (0.82, 128)):
            ring_color = (178, 84, 84) if muted else (88, 255, 137)
            pen = QPen(QColor(*ring_color, int(alpha * (0.45 + state_energy * 0.55))), 2)
            painter.setPen(pen)
            painter.drawEllipse(
                rect.center(),
                int(radius * scale * (1.0 + pulse * 0.08 * state_energy)),
                int(radius * scale * (1.0 + pulse * 0.08 * state_energy)),
            )

        painter.setPen(Qt.PenStyle.NoPen)
        center_color = (126, 54, 54) if muted else (98, 255, 165)
        painter.setBrush(QColor(*center_color, int(90 + state_energy * 80)))
        painter.drawEllipse(rect.center(), int(radius * 0.5), int(radius * 0.5))

        bar_count = 40
        bar_width = max(4, rect.width() // 120)
        spacing = 4
        total = (bar_count * bar_width) + ((bar_count - 1) * spacing)
        start_x = center_x - total // 2
        baseline = center_y + int(radius * 1.9)

        for index in range(bar_count):
            distance = abs(index - ((bar_count - 1) / 2.0)) / (bar_count / 2.0)
            wave = math.sin((self._phase * 2.4) - (index * 0.34)) * 0.5 + 0.5
            height = 18 + int((1.0 - distance) * 56 * state_energy) + int(wave * 34 * state_energy)
            if muted:
                color = QColor(146, 104, 104, 62)
            else:
                color = QColor(110, 255, 186, int(80 + (140 * wave)))
            painter.setBrush(color)
            painter.drawRoundedRect(start_x + (index * (bar_width + spacing)), baseline - height, bar_width, height, 3, 3)

        baseline_color = QColor(147, 103, 103, 100) if muted else QColor(196, 255, 226, 130)
        painter.setPen(QPen(baseline_color, 1))
        painter.drawLine(start_x - 8, baseline + 18, start_x + total + 8, baseline + 18)

    def _tick(self) -> None:
        speed = {
            "Idle": 0.05,
            "Muted": 0.015,
            "Recording": 0.18,
            "Interrupting": 0.12,
            "Transcribing": 0.1,
            "Thinking": 0.08,
            "Synthesizing": 0.12,
            "Speaking": 0.16,
        }.get(self._state, 0.06)
        smoothing = 0.36 if self._target_input_level > self._input_level else 0.16
        self._input_level += (self._target_input_level - self._input_level) * smoothing
        if self._state == "Recording":
            self._target_input_level *= 0.84
        else:
            self._target_input_level = 0.0
        self._phase += speed
        self.update()


class VoiceOnlyPanel(QWidget):
    toggle_requested = Signal()
    close_requested = Signal()
    mute_requested = Signal(bool)
    continuous_requested = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self.header = QFrame()
        self.header.setObjectName("panel")
        header_layout = QVBoxLayout(self.header)
        header_layout.setContentsMargins(18, 16, 18, 16)
        header_layout.setSpacing(10)

        title_col = QVBoxLayout()
        title = QLabel("Voice Only")
        title.setObjectName("title")
        title_col.addWidget(title)

        self.subtitle = QLabel("Tap to listen. A pause sends automatically; tap again to send now.")
        self.subtitle.setObjectName("statusLabel")
        self.subtitle.setWordWrap(True)
        title_col.addWidget(self.subtitle)
        header_layout.addLayout(title_col)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        action_row.addStretch(1)

        self.close_button = QPushButton("Back to Chat")
        self.close_button.setAccessibleName("Return to chat")
        action_row.addWidget(self.close_button)

        self.continuous_button = QPushButton("Hands-Free Off")
        self.continuous_button.setObjectName("voiceContinuousButton")
        self.continuous_button.setCheckable(True)
        self.continuous_button.setToolTip("Resume listening after each spoken reply")
        self.continuous_button.setAccessibleName("Enable hands-free voice")
        action_row.addWidget(self.continuous_button)

        self.mute_button = QPushButton("Mute Mic")
        self.mute_button.setObjectName("voiceMuteButton")
        self.mute_button.setCheckable(True)
        self.mute_button.setToolTip("Mute microphone capture (Ctrl+Shift+M)")
        self.mute_button.setAccessibleName("Mute microphone")
        action_row.addWidget(self.mute_button)
        header_layout.addLayout(action_row)
        layout.addWidget(self.header)

        self.visualizer_frame = QFrame()
        self.visualizer_frame.setObjectName("voiceOnlyPanel")
        visualizer_layout = QVBoxLayout(self.visualizer_frame)
        visualizer_layout.setContentsMargins(24, 24, 24, 24)
        visualizer_layout.setSpacing(14)

        self.visualizer = VoiceVisualizer()
        visualizer_layout.addWidget(self.visualizer, stretch=1)

        self.state_label = QLabel("Audio: Idle")
        self.state_label.setObjectName("voiceOnlyState")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setAccessibleName("Voice status: Idle")
        visualizer_layout.addWidget(self.state_label)

        self.hint_label = QLabel("Voice-only view active")
        self.hint_label.setObjectName("statusLabel")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint_label.setWordWrap(True)
        self.hint_label.setAccessibleName("Voice guidance")
        self.hint_label.setAccessibleDescription("Voice-only view active")
        visualizer_layout.addWidget(self.hint_label)
        layout.addWidget(self.visualizer_frame, stretch=1)

        self.visualizer.clicked.connect(self.toggle_requested.emit)
        self.close_button.clicked.connect(self.close_requested.emit)
        self.continuous_button.toggled.connect(self.continuous_requested.emit)
        self.mute_button.toggled.connect(self.mute_requested.emit)

    def set_audio_state(self, text: str) -> None:
        self.state_label.setText(f"Audio: {text}")
        self.state_label.setAccessibleName(f"Voice status: {text}")
        self.visualizer.set_state(text)
        self.hint_label.setProperty("voiceRecovery", "")
        self.hint_label.style().unpolish(self.hint_label)
        self.hint_label.style().polish(self.hint_label)

        idle_hint = (
            "Hands-free is ready. Tap the visualizer to begin."
            if getattr(self, "_continuous_enabled", False)
            else "Tap the visualizer to start listening."
        )
        hint = {
            "Idle": idle_hint,
            "Muted": "Microphone muted. Unmute to start listening.",
            "Recording": "Listening now. Pause to send automatically, or tap to send now.",
            "Interrupting": "Stopping the current reply, then listening.",
            "Transcribing": "Converting your voice into text.",
            "Thinking": "Generating a reply from the local model.",
            "Synthesizing": (
                "Preparing speech. Listening resumes after playback."
                if getattr(self, "_continuous_enabled", False)
                else "Preparing the next spoken segment locally."
            ),
            "Speaking": (
                "Playing the response. Listening resumes when playback ends."
                if getattr(self, "_continuous_enabled", False)
                else "Playing the response aloud."
            ),
        }.get(text, "Voice-only view active")
        self.hint_label.setText(hint)
        self.hint_label.setAccessibleDescription(hint)

    def set_recovery_message(self, message: str) -> None:
        recovery_message = str(message).strip()
        self.hint_label.setText(recovery_message)
        self.hint_label.setAccessibleDescription(f"Voice recovery needed: {recovery_message}")
        self.hint_label.setProperty("voiceRecovery", "error")
        self.hint_label.style().unpolish(self.hint_label)
        self.hint_label.style().polish(self.hint_label)

    def set_stage_message(self, message: str) -> None:
        stage_message = str(message).strip()
        self.hint_label.setText(stage_message)
        self.hint_label.setAccessibleDescription(stage_message)
        self.hint_label.setProperty("voiceRecovery", "progress")
        self.hint_label.style().unpolish(self.hint_label)
        self.hint_label.style().polish(self.hint_label)

    def set_continuous_enabled(self, enabled: bool) -> None:
        self._continuous_enabled = bool(enabled)
        self.continuous_button.blockSignals(True)
        self.continuous_button.setChecked(enabled)
        self.continuous_button.setText("Hands-Free On" if enabled else "Hands-Free Off")
        self.continuous_button.setAccessibleName(
            "Disable hands-free voice" if enabled else "Enable hands-free voice"
        )
        self.continuous_button.setObjectName(
            "voiceContinuousButtonOn" if enabled else "voiceContinuousButton"
        )
        self.continuous_button.style().unpolish(self.continuous_button)
        self.continuous_button.style().polish(self.continuous_button)
        self.continuous_button.blockSignals(False)
        current_state = self.state_label.text().removeprefix("Audio: ")
        self.set_audio_state(current_state)

    def set_microphone_muted(self, muted: bool) -> None:
        self.mute_button.blockSignals(True)
        self.mute_button.setChecked(muted)
        self.mute_button.setText("Unmute Mic" if muted else "Mute Mic")
        self.mute_button.setAccessibleName("Unmute microphone" if muted else "Mute microphone")
        self.mute_button.setObjectName("voiceMuteButtonOn" if muted else "voiceMuteButton")
        self.mute_button.style().unpolish(self.mute_button)
        self.mute_button.style().polish(self.mute_button)
        self.mute_button.blockSignals(False)

    def set_input_level(self, level: int) -> None:
        self.visualizer.set_input_level(level)
