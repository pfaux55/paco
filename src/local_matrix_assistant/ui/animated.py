from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QGraphicsOpacityEffect, QTabWidget, QWidget


class AnimatedSvgWidget(QWidget):
    """Render a bundled, script-free animated SVG without network access."""

    def __init__(
        self,
        source: str | Path,
        *,
        size: int = 24,
        frames_per_second: int = 12,
        accessible_name: str = "Animated status indicator",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._source = str(source)
        self._frames_per_second = frames_per_second
        self._loaded = True
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAccessibleName(accessible_name)
        self._renderer = QSvgRenderer(self._source, self)
        self._renderer.setFramesPerSecond(self._frames_per_second)
        self._renderer.setAnimationEnabled(False)
        self._renderer.repaintNeeded.connect(self.update)

    @property
    def is_valid(self) -> bool:
        return self._renderer.isValid()

    @property
    def is_animated(self) -> bool:
        return self._renderer.animated()

    def sizeHint(self) -> QSize:  # type: ignore[override]
        return self.minimumSizeHint()

    def minimumSizeHint(self) -> QSize:  # type: ignore[override]
        return self.minimumSize()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        if not self._renderer.isValid():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._renderer.render(painter, QRectF(self.rect()))

    def showEvent(self, event) -> None:  # type: ignore[override]
        if not self._loaded:
            self._renderer.load(self._source)
            self._renderer.setFramesPerSecond(self._frames_per_second)
            self._loaded = True
        self._renderer.setAnimationEnabled(True)
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._renderer.setAnimationEnabled(False)
        if self._loaded and self._renderer.animated():
            self._renderer.load(QByteArray())
            self._loaded = False
        super().hideEvent(event)


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
