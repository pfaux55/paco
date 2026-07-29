from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.core.models import ChatMessage, ModelPullProgress
from local_matrix_assistant.services.ollama import OllamaClient, OllamaError


class OllamaPayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = [ChatMessage(role="user", content="Hello", timestamp="now")]

    def test_payload_includes_context_and_output_options(self) -> None:
        payload = OllamaClient._build_payload(
            "llama3.2:3b",
            self.messages,
            stream=True,
            options={"num_ctx": 4096, "num_predict": 768},
        )

        self.assertEqual({"num_ctx": 4096, "num_predict": 768}, payload["options"])
        self.assertTrue(payload["stream"])
        self.assertFalse(payload["think"])

    def test_payload_omits_options_when_not_provided(self) -> None:
        payload = OllamaClient._build_payload("llama3.2:3b", self.messages, stream=False)

        self.assertNotIn("options", payload)

    def test_payload_sends_bounded_base64_images_only_on_the_attached_message(self) -> None:
        import base64

        image_data = base64.b64encode(b"jpeg-bytes").decode("ascii")
        messages = [
            ChatMessage("system", "system", "now"),
            ChatMessage(
                "user",
                "Describe this",
                "now",
                metadata={
                    "attachments": [
                        {
                            "name": "screen.jpg",
                            "kind": "image",
                            "content": "Local image snapshot.",
                            "size_bytes": 10,
                            "image_data": image_data,
                            "media_type": "image/jpeg",
                        }
                    ]
                },
            ),
        ]

        payload = OllamaClient._build_payload("vision-model", messages, stream=True)

        self.assertNotIn("images", payload["messages"][0])
        self.assertEqual([image_data], payload["messages"][1]["images"])

    def test_stream_can_cancel_while_waiting_for_response_headers(self) -> None:
        release_request = threading.Event()

        class FakeResponse:
            def close(self) -> None:
                return

        class FakeSession:
            def __init__(self) -> None:
                self.closed = False

            def post(self, *_args, **_kwargs):
                release_request.wait(2)
                return FakeResponse()

            def close(self) -> None:
                self.closed = True

        session = FakeSession()
        client = OllamaClient("http://127.0.0.1:11434")
        started = time.monotonic()
        try:
            with patch("local_matrix_assistant.services.ollama.requests.Session", return_value=session):
                result = client.chat_stream(
                    "test-model",
                    self.messages,
                    lambda _chunk: None,
                    lambda: time.monotonic() - started >= 0.08,
                )
        finally:
            release_request.set()

        self.assertTrue(result.canceled)
        self.assertTrue(session.closed)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_stream_captures_ollama_timing_and_token_metrics(self) -> None:
        response = FakePullResponse(
            [
                '{"message":{"content":"Hello "},"done":false}',
                (
                    '{"message":{"content":"locally"},"done":true,'
                    '"total_duration":4200000000,"load_duration":500000000,'
                    '"prompt_eval_count":80,"prompt_eval_duration":400000000,'
                    '"eval_count":120,"eval_duration":3000000000}'
                ),
            ]
        )
        session = FakePullSession()
        client = OllamaClient("http://127.0.0.1:11434")
        chunks: list[str] = []

        with patch.object(client, "_open_cancellable_stream", return_value=(response, session)):
            result = client.chat_stream(
                "llama3.2:3b",
                self.messages,
                chunks.append,
                lambda: False,
            )

        self.assertEqual("Hello locally", result.content)
        self.assertEqual(["Hello ", "locally"], chunks)
        self.assertEqual(4_200_000_000, result.total_duration_ns)
        self.assertEqual(500_000_000, result.load_duration_ns)
        self.assertEqual(80, result.prompt_eval_count)
        self.assertEqual(120, result.eval_count)
        self.assertAlmostEqual(40.0, result.generation_tokens_per_second)
        self.assertTrue(response.closed)
        self.assertTrue(session.closed)

    def test_stream_surfaces_in_band_ollama_error(self) -> None:
        response = FakePullResponse(['{"error":"model runner crashed","done":true}'])
        session = FakePullSession()
        client = OllamaClient("http://127.0.0.1:11434")

        with (
            patch.object(client, "_open_cancellable_stream", return_value=(response, session)),
            self.assertRaisesRegex(OllamaError, "model runner crashed"),
        ):
            client.chat_stream(
                "llama3.2:3b",
                self.messages,
                lambda _chunk: None,
                lambda: False,
            )

        self.assertTrue(response.closed)
        self.assertTrue(session.closed)

    def test_pull_model_streams_progress_and_requires_success(self) -> None:
        response = FakePullResponse(
            [
                '{"status":"pulling manifest"}',
                '{"status":"downloading layer","completed":50,"total":100}',
                '{"status":"success"}',
            ]
        )
        session = FakePullSession()
        client = OllamaClient("http://127.0.0.1:11434")
        progress: list[ModelPullProgress] = []

        with patch.object(client, "_open_cancellable_stream", return_value=(response, session)) as opener:
            result = client.pull_model("llama3.2:3b", progress.append, lambda: False)

        self.assertEqual("llama3.2:3b", result.model)
        self.assertFalse(result.canceled)
        self.assertEqual([None, 50, None], [item.percent for item in progress])
        self.assertTrue(response.closed)
        self.assertTrue(session.closed)
        self.assertEqual("/api/pull", opener.call_args.kwargs["endpoint"])
        self.assertEqual((5, None), opener.call_args.kwargs["timeout"])

    def test_pull_model_surfaces_streamed_ollama_error(self) -> None:
        response = FakePullResponse(['{"error":"model not found"}'])
        session = FakePullSession()
        client = OllamaClient("http://127.0.0.1:11434")

        with (
            patch.object(client, "_open_cancellable_stream", return_value=(response, session)),
            self.assertRaisesRegex(OllamaError, "model not found"),
        ):
            client.pull_model("missing:model", lambda _progress: None, lambda: False)

        self.assertTrue(response.closed)
        self.assertTrue(session.closed)

    def test_pull_model_cancels_during_stream(self) -> None:
        response = FakePullResponse(
            [
                '{"status":"pulling manifest"}',
                '{"status":"downloading","completed":1,"total":10}',
            ]
        )
        session = FakePullSession()
        client = OllamaClient("http://127.0.0.1:11434")
        canceled = False

        def on_progress(_progress: ModelPullProgress) -> None:
            nonlocal canceled
            canceled = True

        with patch.object(client, "_open_cancellable_stream", return_value=(response, session)):
            result = client.pull_model("llama3.2:3b", on_progress, lambda: canceled)

        self.assertTrue(result.canceled)
        self.assertTrue(response.closed)
        self.assertTrue(session.closed)


class FakePullResponse:
    def __init__(self, lines: list[str], status_code: int = 200, text: str = "") -> None:
        self.lines = lines
        self.status_code = status_code
        self.text = text
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def iter_lines(self, *, decode_unicode: bool):
        self.decode_unicode = decode_unicode
        yield from self.lines

    def close(self) -> None:
        self.closed = True


class FakePullSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


if __name__ == "__main__":
    unittest.main()
