from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from local_matrix_assistant.core.config import AppConfig, AppPaths
from local_matrix_assistant.core.constants import APP_NAME
from local_matrix_assistant.process_exit import exit_after_qt_shutdown
from local_matrix_assistant.ui.brand import (
    apply_windows_window_icon,
    configure_windows_app_identity,
    paco_icon,
)
from local_matrix_assistant.ui.compact_assistant import CompactAssistantWindow
from local_matrix_assistant.ui.inputs import install_clipboard_shortcut_filter
from local_matrix_assistant.ui.main_window import MainWindow
from local_matrix_assistant.ui.theme import stylesheet_for_theme


def main() -> int:
    paths = AppPaths.create()
    config = AppConfig.load(paths)

    configure_windows_app_identity()
    app = QApplication(sys.argv)
    clipboard_shortcut_filter = install_clipboard_shortcut_filter(app)
    app.setApplicationName(f"{APP_NAME} Compact")
    app.setWindowIcon(paco_icon())
    app.setStyleSheet(
        stylesheet_for_theme(
            config.theme,
            config.chat_font_family,
            config.chat_font_size,
        )
    )
    app.setFont(QFont("Consolas", 10))

    window = CompactAssistantWindow(config)
    window.exit_requested.connect(lambda: exit_after_qt_shutdown(0))
    compact_window: CompactAssistantWindow | None = window
    main_window: MainWindow | None = None

    def clear_compact_window(*_args) -> None:
        nonlocal compact_window
        compact_window = None

    def close_hidden_main_window() -> None:
        if main_window is not None and not main_window.isVisible():
            main_window.close()

    def show_main_mode() -> None:
        nonlocal main_window
        if main_window is None:
            main_window = MainWindow(paths, config)
            main_window.setWindowIcon(app.windowIcon())
            main_window.compact_mode_requested.connect(show_compact_mode)
            main_window.exit_requested.connect(lambda: exit_after_qt_shutdown(0))
        main_window.show()
        main_window.raise_()
        main_window.activateWindow()
        if compact_window is not None:
            compact_window.hide()

    def show_compact_mode() -> None:
        nonlocal compact_window
        if compact_window is None:
            current_config = main_window.config if main_window is not None else config
            compact_window = CompactAssistantWindow(current_config)
            compact_window.setWindowIcon(app.windowIcon())
            compact_window.main_mode_requested.connect(show_main_mode)
            compact_window.exit_requested.connect(lambda: exit_after_qt_shutdown(0))
            compact_window.closing.connect(close_hidden_main_window)
            compact_window.destroyed.connect(clear_compact_window)
        compact_window.show()
        compact_window.raise_()
        apply_windows_window_icon(compact_window)
        if main_window is not None:
            main_window.hide()

    window.main_mode_requested.connect(show_main_mode)
    window.closing.connect(close_hidden_main_window)
    window.destroyed.connect(clear_compact_window)
    window.setWindowIcon(app.windowIcon())
    window.show()
    window.raise_()
    QTimer.singleShot(0, lambda: apply_windows_window_icon(window))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
