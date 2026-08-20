from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.compact_app import main
from local_matrix_assistant.process_exit import exit_after_qt_shutdown


if __name__ == "__main__":
    exit_after_qt_shutdown(main())
