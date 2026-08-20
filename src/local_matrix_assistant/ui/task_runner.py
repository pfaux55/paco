from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThreadPool

from local_matrix_assistant.ui.workers import FunctionWorker, StreamWorker


class TaskRunner:
    def __init__(self, thread_pool: QThreadPool) -> None:
        self._thread_pool = thread_pool
        self._active_workers: set[object] = set()
        self._closing = False

    def close(self) -> None:
        self._closing = True
        for worker in tuple(self._active_workers):
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                cancel()
        self._thread_pool.clear()

    def wait_for_done(self, timeout_ms: int) -> None:
        self._thread_pool.waitForDone(timeout_ms)

    def start(
        self,
        worker: FunctionWorker,
        on_result: Callable[[object], None],
        on_error: Callable[[str], None],
    ) -> None:
        self._active_workers.add(worker)

        def _finish_result(payload: object) -> None:
            self._active_workers.discard(worker)
            if not self._closing:
                on_result(payload)

        def _finish_error(message: str) -> None:
            self._active_workers.discard(worker)
            if not self._closing:
                on_error(message)

        worker.signals.result.connect(_finish_result)
        worker.signals.error.connect(_finish_error)
        self._thread_pool.start(worker)

    def start_stream(
        self,
        worker: StreamWorker,
        on_chunk: Callable[[object], None],
        on_result: Callable[[object], None],
        on_error: Callable[[str], None],
    ) -> None:
        self._active_workers.add(worker)

        def _finish_result(payload: object) -> None:
            self._active_workers.discard(worker)
            if not self._closing:
                on_result(payload)

        def _finish_error(message: str) -> None:
            self._active_workers.discard(worker)
            if not self._closing:
                on_error(message)

        def _emit_chunk(payload: object) -> None:
            if not self._closing:
                on_chunk(payload)

        worker.signals.chunk.connect(_emit_chunk)
        worker.signals.result.connect(_finish_result)
        worker.signals.error.connect(_finish_error)
        self._thread_pool.start(worker)
