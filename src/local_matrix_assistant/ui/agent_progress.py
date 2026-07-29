from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from local_matrix_assistant.ui.animated import fade_in_widget


class AgentProgressCard(QFrame):
    cancel_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("agentProgressCard")
        self.setProperty("taskState", "idle")
        self.setAccessibleName("Agent task progress")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 11, 14, 11)
        layout.setSpacing(7)

        header = QHBoxLayout()
        header.setSpacing(9)
        self.title_label = QLabel("Agent task")
        self.title_label.setObjectName("agentProgressTitle")
        header.addWidget(self.title_label)
        self.state_label = QLabel("RUNNING")
        self.state_label.setObjectName("agentProgressState")
        header.addWidget(self.state_label)
        header.addStretch(1)
        self.elapsed_label = QLabel("0:00 elapsed")
        self.elapsed_label.setObjectName("agentProgressElapsed")
        header.addWidget(self.elapsed_label)
        layout.addLayout(header)

        detail_row = QHBoxLayout()
        detail_row.setSpacing(10)
        self.phase_label = QLabel("Starting...")
        self.phase_label.setObjectName("agentProgressPhase")
        self.phase_label.setWordWrap(True)
        detail_row.addWidget(self.phase_label, stretch=1)
        self.cancel_button = QPushButton("Stop Agent")
        self.cancel_button.setObjectName("agentProgressCancel")
        self.cancel_button.setToolTip("Stop this task safely")
        self.cancel_button.clicked.connect(self._request_cancel)
        detail_row.addWidget(self.cancel_button)
        layout.addLayout(detail_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("agentProgressBar")
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(5)
        layout.addWidget(self.progress_bar)

        self._elapsed = QElapsedTimer()
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(500)
        self._tick_timer.timeout.connect(self._update_elapsed)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(2200)
        self._hide_timer.timeout.connect(self.reset)
        self._started = False
        self.cancel_pending = False
        self.setVisible(False)

    @property
    def task_state(self) -> str:
        return str(self.property("taskState") or "idle")

    def start(self, title: str, phase: str, cancel_label: str) -> None:
        self._hide_timer.stop()
        self._started = True
        self.cancel_pending = False
        self.title_label.setText(title.strip() or "Agent task")
        self.phase_label.setText(phase.strip() or "Starting...")
        self.cancel_button.setText(cancel_label)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self._set_state("running", "RUNNING")
        self._elapsed.start()
        self.elapsed_label.setText("0:00 elapsed")
        self._tick_timer.start()
        self.setAccessibleDescription(self.phase_label.text())
        self.setVisible(True)
        fade_in_widget(self, duration=180, start=0.35)

    def update_phase(self, phase: str) -> None:
        if not self._started or self.task_state != "running" or not phase.strip():
            return
        self.phase_label.setText(phase.strip())
        self.setAccessibleDescription(self.phase_label.text())

    def mark_cancel_requested(self) -> None:
        if not self._started or self.task_state != "running":
            return
        self.cancel_pending = True
        self.cancel_button.setEnabled(False)
        self.phase_label.setText("Stopping safely after the current operation...")
        self.setAccessibleDescription(self.phase_label.text())

    def prepare_finish(self) -> None:
        if not self._started:
            return
        self._tick_timer.stop()
        self._update_elapsed()
        self.cancel_button.setVisible(False)
        if self.task_state == "running" and not self.cancel_pending:
            self.phase_label.setText("Finalizing the result...")

    def finish(self, state: str, message: str) -> None:
        if not self._started:
            return
        normalized = state if state in {"success", "error", "canceled"} else "success"
        labels = {"success": "COMPLETED", "error": "FAILED", "canceled": "STOPPED"}
        self.prepare_finish()
        self.cancel_pending = normalized == "canceled"
        self.phase_label.setText(message.strip() or labels[normalized].title())
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100 if normalized == "success" else 0)
        self._set_state(normalized, labels[normalized])
        self.setAccessibleDescription(self.phase_label.text())
        self._hide_timer.start()

    def reset(self) -> None:
        self._tick_timer.stop()
        self._hide_timer.stop()
        self._started = False
        self.cancel_pending = False
        self.cancel_button.setVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self._set_state("idle", "IDLE")
        self.setVisible(False)

    def _request_cancel(self) -> None:
        self.mark_cancel_requested()
        self.cancel_requested.emit()

    def _update_elapsed(self) -> None:
        elapsed_ms = self._elapsed.elapsed() if self._elapsed.isValid() else 0
        total_seconds = max(0, elapsed_ms // 1000)
        minutes, seconds = divmod(total_seconds, 60)
        self.elapsed_label.setText(f"{minutes}:{seconds:02d} elapsed")

    def _set_state(self, state: str, label: str) -> None:
        self.setProperty("taskState", state)
        self.state_label.setText(label)
        for widget in (self, self.state_label, self.progress_bar):
            widget.setProperty("taskState", state)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
