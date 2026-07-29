from __future__ import annotations

from io import BytesIO
import math
import struct
import wave

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    Property,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSequentialAnimationGroup,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget


def build_startup_chime() -> bytes:
    sample_rate = 16000
    duration_seconds = 1.55
    frame_count = int(sample_rate * duration_seconds)
    peak = 0.23
    samples = []

    for index in range(frame_count):
        t = index / sample_rate
        attack = min(1.0, t / 0.16)
        release = min(1.0, max(0.0, (duration_seconds - t) / 0.52))
        envelope = attack * release

        bloom = min(1.0, max(0.0, (t - 0.18) / 0.24))
        shimmer = min(1.0, max(0.0, (t - 0.32) / 0.2))

        root = math.sin(2.0 * math.pi * 196.0 * t)
        fifth = math.sin(2.0 * math.pi * 293.66 * t)
        upper = math.sin(2.0 * math.pi * 392.0 * t)
        air = math.sin(2.0 * math.pi * 587.33 * t)
        warmth = math.sin(2.0 * math.pi * 98.0 * t)
        sway = math.sin(2.0 * math.pi * 1.65 * t) * 0.5 + 0.5

        value = peak * envelope * (
            (0.46 * root)
            + (0.25 * fifth * (0.72 + (0.28 * bloom)))
            + (0.14 * upper * bloom)
            + (0.06 * air * shimmer)
            + (0.09 * warmth)
        ) * (0.92 + (0.08 * sway))
        samples.append(max(-1.0, min(1.0, value)))

    pcm = b"".join(struct.pack("<h", int(sample * 32767)) for sample in samples)
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


class StartupOverlay(QWidget):
    finished = Signal()

    def __init__(self, app_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._intro_progress = 0.0
        self._reveal_progress = 0.0
        self._overlay_opacity = 0.0
        self._started = False
        self._finished = False
        self._sound_callback = None

        self.setObjectName("startupOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._title = QLabel(app_name, self)
        self._title.setObjectName("title")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setFont(QFont("Consolas", 28, QFont.Weight.Bold))

        self._eyebrow = QLabel("BOOTING LOCAL SYSTEM", self)
        self._eyebrow.setObjectName("startupEyebrow")
        self._eyebrow.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._status = QLabel("Audio, model, and interface layers syncing", self)
        self._status.setObjectName("startupStatus")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._title_opacity = QGraphicsOpacityEffect(self._title)
        self._eyebrow_opacity = QGraphicsOpacityEffect(self._eyebrow)
        self._status_opacity = QGraphicsOpacityEffect(self._status)
        self._title.setGraphicsEffect(self._title_opacity)
        self._eyebrow.setGraphicsEffect(self._eyebrow_opacity)
        self._status.setGraphicsEffect(self._status_opacity)
        self._title_opacity.setOpacity(0.0)
        self._eyebrow_opacity.setOpacity(0.0)
        self._status_opacity.setOpacity(0.0)

        self.hide()

    def get_intro_progress(self) -> float:
        return self._intro_progress

    def set_intro_progress(self, value: float) -> None:
        self._intro_progress = max(0.0, min(1.0, value))
        self.update()

    intro_progress = Property(float, get_intro_progress, set_intro_progress)

    def get_reveal_progress(self) -> float:
        return self._reveal_progress

    def set_reveal_progress(self, value: float) -> None:
        self._reveal_progress = max(0.0, min(1.0, value))
        self._update_label_geometry()
        self.update()

    reveal_progress = Property(float, get_reveal_progress, set_reveal_progress)

    def get_overlay_opacity(self) -> float:
        return self._overlay_opacity

    def set_overlay_opacity(self, value: float) -> None:
        self._overlay_opacity = max(0.0, min(1.0, value))
        self.update()

    overlay_opacity = Property(float, get_overlay_opacity, set_overlay_opacity)

    def start(self, sound_callback=None) -> None:
        if self._started:
            return
        self._started = True
        self._sound_callback = sound_callback
        self.set_intro_progress(0.0)
        self.set_reveal_progress(0.0)
        self.set_overlay_opacity(0.0)
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._update_label_geometry()
        self._build_animation().start()

    def skip(self) -> None:
        if self._finished:
            return
        if hasattr(self, "_animation_group"):
            self._animation_group.stop()
        self._complete()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_label_geometry()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        event.accept()
        self.skip()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            event.accept()
            self.skip()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setOpacity(self._overlay_opacity)

        rect = self.rect()
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor(2, 9, 7, 248))
        gradient.setColorAt(0.5, QColor(3, 13, 10, 235))
        gradient.setColorAt(1.0, QColor(0, 3, 2, 250))
        painter.fillRect(rect, gradient)

        painter.setOpacity(0.16 * (1.0 - self._reveal_progress))
        scanline_pen = QPen(QColor(90, 255, 160, 28))
        painter.setPen(scanline_pen)
        for y in range(0, rect.height(), 6):
            painter.drawLine(0, y, rect.width(), y)
        painter.setOpacity(1.0)

        center = QPoint(rect.center().x(), max(120, rect.center().y() - 40))
        base_radius = min(rect.width(), rect.height()) * 0.18
        energy = min(1.0, self._intro_progress * 1.2)
        pulse = (math.sin(self._intro_progress * math.pi * 6.0) * 0.5 + 0.5) * energy

        outer_radius = base_radius * (0.9 + (0.38 * energy))
        inner_radius = base_radius * (0.48 + (0.16 * pulse))

        for scale, alpha in ((1.28, 0.15), (1.0, 0.32), (0.72, 0.58)):
            pen = QPen(QColor(96, 255, 170, int(255 * alpha * (1.0 - self._reveal_progress))), 2)
            painter.setPen(pen)
            painter.drawEllipse(center, int(outer_radius * scale), int(outer_radius * scale))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(80, 255, 152, int(120 * energy)))
        painter.drawEllipse(center, int(inner_radius), int(inner_radius))

        bar_count = 28
        bar_width = 6
        bar_spacing = 5
        total_width = (bar_count * bar_width) + ((bar_count - 1) * bar_spacing)
        baseline = center.y() + int(base_radius * 1.5)
        left = center.x() - (total_width // 2)

        for index in range(bar_count):
            distance = abs(index - ((bar_count - 1) / 2.0)) / (bar_count / 2.0)
            wave = math.sin((self._intro_progress * 8.0) - (index * 0.48)) * 0.5 + 0.5
            height = 16 + int((1.0 - distance) * 42 * energy) + int(wave * 26 * energy)
            alpha = int((120 + (100 * wave)) * (1.0 - self._reveal_progress))
            painter.setBrush(QColor(104, 255, 178, alpha))
            painter.drawRoundedRect(left + (index * (bar_width + bar_spacing)), baseline - height, bar_width, height, 3, 3)

        glow_pen = QPen(QColor(196, 255, 226, int(180 * (1.0 - self._reveal_progress))), 1)
        painter.setPen(glow_pen)
        painter.drawLine(center.x() - 170, baseline + 24, center.x() + 170, baseline + 24)

    def _build_animation(self) -> QSequentialAnimationGroup:
        self._animation_group = QSequentialAnimationGroup(self)

        overlay_fade_in = QPropertyAnimation(self, b"overlay_opacity", self)
        overlay_fade_in.setDuration(260)
        overlay_fade_in.setStartValue(0.0)
        overlay_fade_in.setEndValue(1.0)
        overlay_fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        intro_progress = QPropertyAnimation(self, b"intro_progress", self)
        intro_progress.setDuration(1350)
        intro_progress.setStartValue(0.0)
        intro_progress.setEndValue(1.0)
        intro_progress.setEasingCurve(QEasingCurve.Type.InOutCubic)

        eyebrow_fade = QPropertyAnimation(self._eyebrow_opacity, b"opacity", self)
        eyebrow_fade.setDuration(320)
        eyebrow_fade.setStartValue(0.0)
        eyebrow_fade.setEndValue(1.0)
        eyebrow_fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        title_fade = QPropertyAnimation(self._title_opacity, b"opacity", self)
        title_fade.setDuration(420)
        title_fade.setStartValue(0.0)
        title_fade.setEndValue(1.0)
        title_fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        status_fade = QPropertyAnimation(self._status_opacity, b"opacity", self)
        status_fade.setDuration(380)
        status_fade.setStartValue(0.0)
        status_fade.setEndValue(1.0)
        status_fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        reveal_progress = QPropertyAnimation(self, b"reveal_progress", self)
        reveal_progress.setDuration(520)
        reveal_progress.setStartValue(0.0)
        reveal_progress.setEndValue(1.0)
        reveal_progress.setEasingCurve(QEasingCurve.Type.InOutCubic)

        eyebrow_fade_out = QPropertyAnimation(self._eyebrow_opacity, b"opacity", self)
        eyebrow_fade_out.setDuration(360)
        eyebrow_fade_out.setStartValue(1.0)
        eyebrow_fade_out.setEndValue(0.0)
        eyebrow_fade_out.setEasingCurve(QEasingCurve.Type.InCubic)

        title_fade_out = QPropertyAnimation(self._title_opacity, b"opacity", self)
        title_fade_out.setDuration(360)
        title_fade_out.setStartValue(1.0)
        title_fade_out.setEndValue(0.0)
        title_fade_out.setEasingCurve(QEasingCurve.Type.InCubic)

        status_fade_out = QPropertyAnimation(self._status_opacity, b"opacity", self)
        status_fade_out.setDuration(360)
        status_fade_out.setStartValue(1.0)
        status_fade_out.setEndValue(0.0)
        status_fade_out.setEasingCurve(QEasingCurve.Type.InCubic)

        overlay_fade_out = QPropertyAnimation(self, b"overlay_opacity", self)
        overlay_fade_out.setDuration(520)
        overlay_fade_out.setStartValue(1.0)
        overlay_fade_out.setEndValue(0.0)
        overlay_fade_out.setEasingCurve(QEasingCurve.Type.InCubic)

        intro_group = QParallelAnimationGroup(self)
        intro_group.addAnimation(overlay_fade_in)
        intro_group.addAnimation(intro_progress)

        title_group = QSequentialAnimationGroup(self)
        title_group.addPause(120)
        title_group.addAnimation(eyebrow_fade)
        title_group.addPause(70)
        title_group.addAnimation(title_fade)
        title_group.addPause(90)
        title_group.addAnimation(status_fade)
        intro_group.addAnimation(title_group)

        outro_group = QParallelAnimationGroup(self)
        outro_group.addAnimation(reveal_progress)
        outro_group.addAnimation(overlay_fade_out)
        outro_group.addAnimation(eyebrow_fade_out)
        outro_group.addAnimation(title_fade_out)
        outro_group.addAnimation(status_fade_out)

        self._animation_group.addAnimation(intro_group)
        self._animation_group.addPause(120)
        self._animation_group.addAnimation(outro_group)
        self._animation_group.finished.connect(self._complete)
        self._animation_group.stateChanged.connect(self._on_animation_state_changed)
        return self._animation_group

    def _complete(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.hide()
        self.finished.emit()

    def _on_animation_state_changed(self, new_state, old_state) -> None:
        del old_state
        if new_state == QAbstractAnimation.State.Running and self._sound_callback:
            self._sound_callback()
            self._sound_callback = None

    def _update_label_geometry(self) -> None:
        rect = self.rect()
        slide_y = int(self._reveal_progress * 26)
        title_top = int(rect.height() * 0.25) + slide_y

        self._eyebrow.setGeometry(QRect(0, title_top, rect.width(), 28))
        self._title.setGeometry(QRect(0, title_top + 26, rect.width(), 48))
        self._status.setGeometry(QRect(0, title_top + 82, rect.width(), 28))
