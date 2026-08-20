from __future__ import annotations

from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.process_exit import exit_after_qt_shutdown


class ProcessExitTests(unittest.TestCase):
    def test_exit_flushes_output_and_uses_immediate_process_exit(self) -> None:
        output = StringIO()
        error = StringIO()
        output.write("ready")
        error.write("done")

        with (
            patch("local_matrix_assistant.process_exit.sys.stdout", output),
            patch("local_matrix_assistant.process_exit.sys.stderr", error),
            patch("local_matrix_assistant.process_exit.os._exit", side_effect=SystemExit) as force_exit,
            self.assertRaises(SystemExit),
        ):
            exit_after_qt_shutdown(7)

        force_exit.assert_called_once_with(7)


if __name__ == "__main__":
    unittest.main()
