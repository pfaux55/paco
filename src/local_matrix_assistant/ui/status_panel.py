from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from local_matrix_assistant.core.constants import DEFAULT_ACTIVITY
from local_matrix_assistant.core.models import StatusSnapshot


class StatusPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.status_label = QLabel(DEFAULT_ACTIVITY)
        self.status_label.setObjectName("statusStrip")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self._snapshot: StatusSnapshot | None = None
        self._activity = DEFAULT_ACTIVITY

    def set_snapshot(self, snapshot: StatusSnapshot) -> None:
        self._snapshot = snapshot
        self._render()

    def set_activity(self, text: str) -> None:
        self._activity = text
        self._render()

    def _render(self) -> None:
        message = self._activity.strip() or DEFAULT_ACTIVITY
        if self._snapshot:
            problem = self._problem_text(self._snapshot)
            if problem and message == DEFAULT_ACTIVITY:
                message = problem
        self.status_label.setText(message)

    @staticmethod
    def _problem_text(snapshot: StatusSnapshot) -> str:
        if not snapshot.ollama_connected:
            return "Ollama offline"
        if not snapshot.model_ready:
            return "Model not installed"
        if not snapshot.mic_available:
            return "Mic missing"
        if not snapshot.output_available:
            return "Speaker missing"
        if not (snapshot.stt_ready and snapshot.tts_ready):
            return "Voice models missing"
        return ""
