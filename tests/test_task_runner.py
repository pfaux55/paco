from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.ui.task_runner import TaskRunner
from local_matrix_assistant.ui.workers import StreamWorker


class FakeThreadPool:
    def __init__(self) -> None:
        self.started: list[object] = []
        self.cleared = False

    def start(self, worker: object) -> None:
        self.started.append(worker)

    def clear(self) -> None:
        self.cleared = True

    def waitForDone(self, _timeout_ms: int) -> None:  # noqa: N802
        pass


class TaskRunnerTests(unittest.TestCase):
    def test_close_cancels_streams_and_clears_queued_work(self) -> None:
        pool = FakeThreadPool()
        runner = TaskRunner(pool)  # type: ignore[arg-type]
        worker = StreamWorker(lambda _on_chunk, _should_cancel: None)
        runner.start_stream(worker, lambda _chunk: None, lambda _result: None, lambda _error: None)

        runner.close()

        self.assertTrue(worker.is_cancelled())
        self.assertTrue(pool.cleared)

    def test_chunks_are_ignored_after_close(self) -> None:
        pool = FakeThreadPool()
        runner = TaskRunner(pool)  # type: ignore[arg-type]
        chunks: list[object] = []
        worker = StreamWorker(lambda _on_chunk, _should_cancel: None)
        runner.start_stream(worker, chunks.append, lambda _result: None, lambda _error: None)

        runner.close()
        worker.signals.chunk.emit("late")

        self.assertEqual([], chunks)


if __name__ == "__main__":
    unittest.main()
