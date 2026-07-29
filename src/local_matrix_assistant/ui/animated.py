from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QTabWidget, QWidget


def fade_in_widget(widget: QWidget, *, duration: int = 260, start: float = 0.18) -> None:
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    effect.setOpacity(start)

    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(start)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)
    widget._fade_animation = animation  # type: ignore[attr-defined]
    animation.start()


class AnimatedTabWidget(QTabWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.currentChanged.connect(self._animate_current_tab)

    def _animate_current_tab(self, index: int) -> None:
        widget = self.widget(index)
        if widget is not None:
            fade_in_widget(widget, duration=280, start=0.12)
