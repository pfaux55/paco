from __future__ import annotations

import os
import sys
from typing import NoReturn


def exit_after_qt_shutdown(exit_code: int) -> NoReturn:
    """End the process after Qt has completed the application's close handlers."""

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (AttributeError, OSError):
            pass
    os._exit(int(exit_code))
