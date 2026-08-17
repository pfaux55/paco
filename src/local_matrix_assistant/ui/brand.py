from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QLabel, QWidget


ASSET_DIR = Path(__file__).resolve().parents[1] / "assets"
PACO_ICON_PATH = ASSET_DIR / "paco_icon.png"
PACO_WINDOWS_ICON_PATH = ASSET_DIR / "paco_icon.ico"
WINDOWS_APP_ID = "Paco.LocalMatrixAssistant"
_native_icon_handles: list[int] = []


def paco_icon() -> QIcon:
    return QIcon(str(PACO_WINDOWS_ICON_PATH))


def configure_windows_app_identity() -> None:
    if sys.platform != "win32":
        return

    import ctypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    set_app_id = shell32.SetCurrentProcessExplicitAppUserModelID
    set_app_id.argtypes = [ctypes.c_wchar_p]
    set_app_id.restype = ctypes.c_long
    set_app_id(WINDOWS_APP_ID)


def apply_windows_window_icon(window: QWidget) -> None:
    if sys.platform != "win32":
        return

    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    load_image = user32.LoadImageW
    load_image.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    load_image.restype = ctypes.c_void_p
    send_message = user32.SendMessageW
    send_message.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
    send_message.restype = ctypes.c_void_p

    window_handle = ctypes.c_void_p(int(window.winId()))
    for icon_kind, size in ((1, 256), (0, 32)):
        icon_handle = load_image(None, str(PACO_WINDOWS_ICON_PATH), 1, size, size, 0x0010)
        if icon_handle:
            _native_icon_handles.append(int(icon_handle))
            send_message(window_handle, 0x0080, ctypes.c_void_p(icon_kind), icon_handle)


def bring_windows_window_to_front(window: QWidget) -> None:
    if sys.platform != "win32":
        window.raise_()
        window.activateWindow()
        return

    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    window_handle = ctypes.c_void_p(int(window.winId()))
    show_window = user32.ShowWindowAsync
    show_window.argtypes = [ctypes.c_void_p, ctypes.c_int]
    bring_to_top = user32.BringWindowToTop
    bring_to_top.argtypes = [ctypes.c_void_p]
    set_foreground = user32.SetForegroundWindow
    set_foreground.argtypes = [ctypes.c_void_p]
    set_window_position = user32.SetWindowPos
    set_window_position.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    show_window(window_handle, 5)
    bring_to_top(window_handle)
    set_foreground(window_handle)
    set_window_position(window_handle, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0003)
    set_window_position(window_handle, ctypes.c_void_p(-2), 0, 0, 0, 0, 0x0003)
    window.raise_()
    window.activateWindow()


def paco_mark(
    size: int,
    *,
    parent: QWidget | None = None,
    accessible_name: str = "Paco",
) -> QLabel:
    label = QLabel(parent)
    label.setObjectName("pacoMark")
    label.setAccessibleName(accessible_name)
    label.setFixedSize(size, size)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    pixmap = QPixmap(str(PACO_ICON_PATH)).scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    label.setPixmap(pixmap)
    return label
