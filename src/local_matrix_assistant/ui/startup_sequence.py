from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget

from local_matrix_assistant.ui.startup_overlay import StartupOverlay


class StartupSequence:
    startup_timeout_ms = 5000

    def __init__(
        self,
        *,
        app_name: str,
        root: QWidget,
    ) -> None:
        self._root = root
        self._started = False

        self._startup_timeout = QTimer(root)
        self._startup_timeout.setSingleShot(True)
        self._startup_timeout.setInterval(self.startup_timeout_ms)
        self._startup_timeout.timeout.connect(self._recover_from_timeout)

        self.overlay = StartupOverlay(app_name, root)
        self.overlay.finished.connect(self._finish)
        self.sync_geometry()

    def begin(self) -> None:
        if self._started:
            return
        self._started = True
        self._startup_timeout.start()
        self._start()

    def sync_geometry(self) -> None:
        self.overlay.setGeometry(self._root.rect())
        self.overlay.raise_()

    def stop(self) -> None:
        self._startup_timeout.stop()
        self.overlay.skip()

    def _start(self) -> None:
        if not self._startup_timeout.isActive():
            return
        self.sync_geometry()
        self.overlay.start()

    def _recover_from_timeout(self) -> None:
        self.overlay.skip()

    def _finish(self) -> None:
        self._startup_timeout.stop()
        self.overlay.setDisabled(True)
        self.overlay.hide()
