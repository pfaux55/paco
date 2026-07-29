from __future__ import annotations

from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QComboBox, QSlider


class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        event.ignore()


class NoWheelSlider(QSlider):
    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        event.ignore()
