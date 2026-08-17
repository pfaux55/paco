from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _launch_with_loading_view() -> bool:
    if sys.platform != "win32" or os.environ.get("PACO_STARTUP_EVENT"):
        return False

    launcher = ROOT / "data" / "PacoLauncher.exe"
    if not launcher.is_file():
        return False

    subprocess.Popen([str(launcher)], cwd=launcher.parent)
    return True


if __name__ == "__main__":
    if _launch_with_loading_view():
        raise SystemExit(0)

    from local_matrix_assistant.app import main

    raise SystemExit(main())
