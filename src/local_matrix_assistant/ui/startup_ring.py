from __future__ import annotations

import os
import time

from PySide6.QtCore import QPoint, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


RING_SIZE = 82
RING_STROKE_WIDTH = 8.0
RING_ROTATION_DEGREES_PER_MILLISECOND = 5.5 / 16.0
RING_GRADIENT = (
    (112, 255, 184),
    (54, 226, 136),
    (22, 145, 82),
    (44, 235, 139),
    (112, 255, 184),
)


def launcher_ring_center() -> QPoint | None:
    try:
        return QPoint(
            int(os.environ["PACO_STARTUP_RING_CENTER_X"]),
            int(os.environ["PACO_STARTUP_RING_CENTER_Y"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


class StartupRing(QWidget):
    """Paint the launcher's fixed ring from its shared wall-clock phase."""

    segment_count = 64

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        try:
            self._started_at_ms = int(os.environ["PACO_STARTUP_RING_STARTED_MS"])
        except (KeyError, TypeError, ValueError):
            self._started_at_ms = int(time.time() * 1000)

        self.setFixedSize(RING_SIZE, RING_SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAccessibleName("Paco startup in progress")
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(16)
        self._animation_timer.timeout.connect(self.update)

    def angle_at(self, now_ms: int) -> float:
        elapsed_ms = max(0, now_ms - self._started_at_ms)
        return (elapsed_ms * RING_ROTATION_DEGREES_PER_MILLISECOND) % 360.0

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        ring = QRectF(23.0, 23.0, 36.0, 36.0)
        segment_sweep = 360.0 / self.segment_count
        angle = self.angle_at(int(time.time() * 1000))
        for index in range(self.segment_count):
            position = index / self.segment_count
            pen = QPen(self._gradient_color(position), RING_STROKE_WIDTH)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            painter.drawArc(
                ring,
                -round((angle + (index * segment_sweep)) * 16),
                -round((segment_sweep + 1.2) * 16),
            )

    def showEvent(self, event) -> None:  # type: ignore[override]
        self._animation_timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._animation_timer.stop()
        super().hideEvent(event)

    @staticmethod
    def _gradient_color(position: float) -> QColor:
        scaled = position * (len(RING_GRADIENT) - 1)
        start_index = min(int(scaled), len(RING_GRADIENT) - 2)
        blend = scaled - start_index
        start = RING_GRADIENT[start_index]
        end = RING_GRADIENT[start_index + 1]
        return QColor(
            start[0] + int((end[0] - start[0]) * blend),
            start[1] + int((end[1] - start[1]) * blend),
            start[2] + int((end[2] - start[2]) * blend),
        )
