from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)


class FunctionWorker(QRunnable):
    def __init__(self, fn: Callable[[], object]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(str(exc))
            return
        self.signals.result.emit(result)


class StreamWorkerSignals(QObject):
    chunk = Signal(object)
    result = Signal(object)
    error = Signal(str)


class StreamWorker(QRunnable):
    def __init__(self, fn: Callable[[Callable[[object], None], Callable[[], bool]], object]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = StreamWorkerSignals()
        self._cancelled = False
        self.setAutoDelete(False)

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:
        try:
            result = self.fn(self.signals.chunk.emit, self.is_cancelled)
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(str(exc))
            return
        self.signals.result.emit(result)
