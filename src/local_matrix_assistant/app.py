from __future__ import annotations

import os
import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from local_matrix_assistant.core.config import AppConfig, AppPaths
from local_matrix_assistant.core.constants import APP_NAME
from local_matrix_assistant.ui.brand import (
    apply_windows_window_icon,
    bring_windows_window_to_front,
    configure_windows_app_identity,
    jarvis_icon,
)
from local_matrix_assistant.ui.main_window import MainWindow
from local_matrix_assistant.ui.theme import stylesheet_for_theme


def _signal_launcher_ready() -> None:
    event_name = os.environ.pop("JARVIS_STARTUP_EVENT", "")
    if sys.platform != "win32" or not event_name:
        return

    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_event = kernel32.OpenEventW
    open_event.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    open_event.restype = ctypes.c_void_p
    set_event = kernel32.SetEvent
    set_event.argtypes = [ctypes.c_void_p]
    set_event.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    handle = open_event(0x0002, 0, event_name)
    if handle:
        set_event(handle)
        close_handle(handle)


def _present_window(window: MainWindow) -> None:
    apply_windows_window_icon(window)
    _signal_launcher_ready()
    QTimer.singleShot(120, lambda: bring_windows_window_to_front(window))


def main() -> int:
    paths = AppPaths.create()
    config = AppConfig.load(paths)

    configure_windows_app_identity()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(jarvis_icon())
    app.setStyleSheet(stylesheet_for_theme(config.theme))
    app.setFont(QFont("Consolas", 10))

    window = MainWindow(paths, config)
    window.setWindowIcon(app.windowIcon())
    window.show()
    if window.windowState() & Qt.WindowState.WindowMinimized:
        window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
    window.raise_()
    window.activateWindow()
    QTimer.singleShot(0, lambda: _present_window(window))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
