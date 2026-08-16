from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from local_matrix_assistant.services.audio import AudioPlayer
from local_matrix_assistant.ui.startup_overlay import StartupOverlay, build_startup_chime


class StartupSequence:
    startup_timeout_ms = 5000

    def __init__(
        self,
        *,
        app_name: str,
        root: QWidget,
        content_root: QWidget,
        startup_player: AudioPlayer,
    ) -> None:
        self._root = root
        self._content_root = content_root
        self._startup_player = startup_player
        self._startup_audio = build_startup_chime()
        self._started = False

        self._startup_timeout = QTimer(root)
        self._startup_timeout.setSingleShot(True)
        self._startup_timeout.setInterval(self.startup_timeout_ms)
        self._startup_timeout.timeout.connect(self._recover_from_timeout)

        self.content_opacity = QGraphicsOpacityEffect(content_root)
        self.content_opacity.setOpacity(0.3)
        content_root.setGraphicsEffect(self.content_opacity)

        self.overlay = StartupOverlay(app_name, root)
        self.overlay.finished.connect(self._finish)
        self.sync_geometry()

    def begin(self) -> None:
        if self._started:
            return
        self._started = True
        self._startup_timeout.start()
        QTimer.singleShot(80, self._start)

    def sync_geometry(self) -> None:
        self.overlay.setGeometry(self._root.rect())
        self.overlay.raise_()

    def stop(self) -> None:
        self._startup_timeout.stop()
        self._startup_player.stop()
        self.overlay.skip()

    def set_output_device_name(self, output_name: str) -> None:
        self._startup_player.set_output_device_name(output_name)

    def _start(self) -> None:
        if not self._startup_timeout.isActive():
            return
        self.sync_geometry()
        self.overlay.start(self._play_sound)
        self._animate_content_opacity(0.3, 1.0, 1900)

    def _recover_from_timeout(self) -> None:
        self.overlay.skip()

    def _finish(self) -> None:
        self._startup_timeout.stop()
        self._startup_player.stop()
        self.content_opacity.setOpacity(1.0)
        self.overlay.setDisabled(True)
        self.overlay.hide()

    def _play_sound(self) -> None:
        try:
            self._startup_player.play_wav(self._startup_audio)
        except Exception:
            return

    def _animate_content_opacity(self, start: float, end: float, duration_ms: int) -> None:
        self._content_fade = QPropertyAnimation(self.content_opacity, b"opacity", self._root)
        self._content_fade.setDuration(duration_ms)
        self._content_fade.setStartValue(start)
        self._content_fade.setEndValue(end)
        self._content_fade.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._content_fade.start()
