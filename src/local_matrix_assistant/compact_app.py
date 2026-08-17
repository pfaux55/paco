from __future__ import annotations

import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from local_matrix_assistant.core.config import AppConfig, AppPaths
from local_matrix_assistant.core.constants import APP_NAME
from local_matrix_assistant.ui.brand import (
    apply_windows_window_icon,
    configure_windows_app_identity,
    paco_icon,
)
from local_matrix_assistant.ui.compact_assistant import CompactAssistantWindow
from local_matrix_assistant.ui.theme import stylesheet_for_theme


def main() -> int:
    paths = AppPaths.create()
    config = AppConfig.load(paths)

    configure_windows_app_identity()
    app = QApplication(sys.argv)
    app.setApplicationName(f"{APP_NAME} Compact")
    app.setWindowIcon(paco_icon())
    app.setStyleSheet(stylesheet_for_theme(config.theme))
    app.setFont(QFont("Consolas", 10))

    window = CompactAssistantWindow(config)
    window.setWindowIcon(app.windowIcon())
    window.show()
    window.raise_()
    QTimer.singleShot(0, lambda: apply_windows_window_icon(window))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
