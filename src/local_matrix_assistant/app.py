from __future__ import annotations

import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from local_matrix_assistant.core.config import AppConfig, AppPaths
from local_matrix_assistant.core.constants import APP_NAME
from local_matrix_assistant.ui.main_window import MainWindow
from local_matrix_assistant.ui.theme import MATRIX_STYLESHEET


def main() -> int:
    paths = AppPaths.create()
    config = AppConfig.load(paths)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(MATRIX_STYLESHEET)
    app.setFont(QFont("Consolas", 10))

    window = MainWindow(paths, config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
