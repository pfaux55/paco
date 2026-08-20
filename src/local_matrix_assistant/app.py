from __future__ import annotations

import os
import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from local_matrix_assistant.core.config import AppConfig, AppPaths
from local_matrix_assistant.core.constants import APP_NAME
from local_matrix_assistant.process_exit import exit_after_qt_shutdown
from local_matrix_assistant.ui.brand import (
    apply_windows_window_icon,
    bring_windows_window_to_front,
    configure_windows_app_identity,
    paco_icon,
)
from local_matrix_assistant.ui.compact_assistant import CompactAssistantWindow
from local_matrix_assistant.ui.inputs import install_clipboard_shortcut_filter
from local_matrix_assistant.ui.main_window import MainWindow
from local_matrix_assistant.ui.theme import stylesheet_for_theme


def _signal_launcher_ready() -> None:
    event_name = os.environ.pop("PACO_STARTUP_EVENT", "")
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
    QTimer.singleShot(120, lambda: bring_windows_window_to_front(window))


def main() -> int:
    paths = AppPaths.create()
    config = AppConfig.load(paths)

    configure_windows_app_identity()
    app = QApplication(sys.argv)
    clipboard_shortcut_filter = install_clipboard_shortcut_filter(app)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(paco_icon())
    app.setStyleSheet(
        stylesheet_for_theme(
            config.theme,
            config.chat_font_family,
            config.chat_font_size,
        )
    )
    app.setFont(QFont("Consolas", 10))

    window = MainWindow(paths, config)
    window.exit_requested.connect(lambda: exit_after_qt_shutdown(0))
    compact_window: CompactAssistantWindow | None = None

    def clear_compact_window(*_args) -> None:
        nonlocal compact_window
        compact_window = None

    def close_hidden_main_window() -> None:
        if not window.isVisible():
            window.close()

    def show_main_mode() -> None:
        window.show()
        window.raise_()
        window.activateWindow()
        if compact_window is not None:
            compact_window.close()

    def show_compact_mode() -> None:
        nonlocal compact_window
        if compact_window is None:
            compact_window = CompactAssistantWindow(window.config)
            compact_window.setWindowIcon(app.windowIcon())
            compact_window.main_mode_requested.connect(show_main_mode)
            compact_window.exit_requested.connect(lambda: exit_after_qt_shutdown(0))
            compact_window.closing.connect(close_hidden_main_window)
            compact_window.destroyed.connect(clear_compact_window)
        compact_window.show()
        compact_window.raise_()
        compact_window.activateWindow()
        apply_windows_window_icon(compact_window)
        window.hide()

    window.compact_mode_requested.connect(show_compact_mode)
    window.startup_sequence.first_frame_ready.connect(_signal_launcher_ready)
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
