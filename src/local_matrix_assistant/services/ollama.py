from __future__ import annotations

from dataclasses import dataclass
import json
import queue
import threading
from typing import Callable

import requests

from local_matrix_assistant.core.models import (
    ChatMessage,
    ChatStreamResult,
    ModelPullProgress,
    ModelPullResult,
)
from local_matrix_assistant.services.attachments import AttachmentService


class OllamaError(RuntimeError):
    """Raised when the local Ollama service is unavailable or returns an error."""


@dataclass(slots=True)
class OllamaStatus:
    connected: bool
    message: str
    models: list[str]


class OllamaClient:
    _think_option_key = "_paco_think"
    max_thinking_characters = 30_000

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = 180

    def update_base_url(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def status(self) -> OllamaStatus:
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=3)
            response.raise_for_status()
        except requests.RequestException as exc:
            return OllamaStatus(
                connected=False,
                message=f"Offline: {exc}",
                models=[],
            )

        payload = response.json()
        models = [
            (item.get("name", ""), int(item.get("size", 0) or 0))
            for item in payload.get("models", [])
            if item.get("name")
        ]
        models.sort(key=lambda item: (item[1] <= 0, item[1], item[0]))
        ordered_models = [name for name, _size in models]
        if not ordered_models:
            return OllamaStatus(
                connected=True,
                message="Connected, but no local models are installed.",
                models=[],
            )
        return OllamaStatus(
            connected=True,
            message=f"Connected with {len(models)} local model(s).",
            models=ordered_models,
        )

    def chat(
        self,
        model: str,
        messages: list[ChatMessage],
        *,
        options: dict | None = None,
    ) -> str:
        payload = self._build_payload(model, messages, stream=False, options=options)
        try:
            response = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=(5, self.timeout),
            )
        except requests.Timeout as exc:
            raise OllamaError(
                "Timed out waiting for a local Ollama reply. The selected model may still be loading."
            ) from exc
        except requests.RequestException as exc:
            raise OllamaError(f"Could not reach Ollama at {self.base_url}: {exc}") from exc

        self._raise_for_error_response(response, model)

        payload = response.json()
        message = payload.get("message", {})
        content = message.get("content", "").strip()
        if not content:
            raise OllamaError("Ollama returned an empty response.")
        return content

    def chat_stream(
        self,
        model: str,
        messages: list[ChatMessage],
        on_chunk: Callable[[str], None],
        should_cancel: Callable[[], bool],
        *,
        options: dict | None = None,
    ) -> ChatStreamResult:
        payload = self._build_payload(model, messages, stream=True, options=options)
        response, session = self._open_cancellable_stream(payload, should_cancel)
        if response is None:
            return ChatStreamResult(content="", canceled=True)

        try:
            self._raise_for_error_response(response, model)
        except Exception:
            response.close()
            session.close()
            raise

        chunks: list[str] = []
        thinking_chunks: list[str] = []
        thinking_characters = 0
        stream_metrics: dict[str, int] = {}
        watcher_finished = threading.Event()

        def close_on_cancel() -> None:
            while not watcher_finished.wait(0.05):
                if should_cancel():
                    response.close()
                    return

        watcher = threading.Thread(target=close_on_cancel, name="paco-ollama-cancel", daemon=True)
        watcher.start()
        try:
            with response:
                for raw_line in response.iter_lines(decode_unicode=True):
                    if should_cancel():
                        return ChatStreamResult(content="".join(chunks), canceled=True)
                    if not raw_line:
                        continue
                    try:
                        payload = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    if error := str(payload.get("error", "")).strip():
                        raise OllamaError(f"Ollama streaming failed: {error}")
                    message = payload.get("message", {})
                    content = message.get("content", "") if isinstance(message, dict) else ""
                    thinking = message.get("thinking", "") if isinstance(message, dict) else ""
                    if thinking and thinking_characters < self.max_thinking_characters:
                        bounded = thinking[: self.max_thinking_characters - thinking_characters]
                        thinking_chunks.append(bounded)
                        thinking_characters += len(bounded)
                    if content:
                        chunks.append(content)
                        on_chunk(content)
                    if payload.get("done"):
                        stream_metrics = {
                            key: self._nonnegative_int(payload.get(key))
                            for key in (
                                "total_duration",
                                "load_duration",
                                "prompt_eval_count",
                                "prompt_eval_duration",
                                "eval_count",
                                "eval_duration",
                            )
                        }
                        break
        except (OSError, requests.RequestException) as exc:
            if should_cancel():
                return ChatStreamResult(content="".join(chunks), canceled=True)
            raise OllamaError(f"Ollama streaming failed: {exc}") from exc
        finally:
            watcher_finished.set()
            session.close()

        content = "".join(chunks).strip()
        if not content and not should_cancel():
            raise OllamaError("Ollama returned an empty response.")
        return ChatStreamResult(
            content=content,
            thinking="".join(thinking_chunks).strip(),
            canceled=should_cancel(),
            total_duration_ns=stream_metrics.get("total_duration", 0),
            load_duration_ns=stream_metrics.get("load_duration", 0),
            prompt_eval_count=stream_metrics.get("prompt_eval_count", 0),
            prompt_eval_duration_ns=stream_metrics.get("prompt_eval_duration", 0),
            eval_count=stream_metrics.get("eval_count", 0),
            eval_duration_ns=stream_metrics.get("eval_duration", 0),
        )

    def pull_model(
        self,
        model: str,
        on_progress: Callable[[ModelPullProgress], None],
        should_cancel: Callable[[], bool],
    ) -> ModelPullResult:
        model_name = model.strip()
        if not model_name or len(model_name) > 128:
            raise OllamaError("Choose a valid Ollama model name.")
        response, session = self._open_cancellable_stream(
            {"model": model_name, "stream": True},
            should_cancel,
            endpoint="/api/pull",
            timeout=(5, None),
        )
        if response is None:
            return ModelPullResult(model_name, canceled=True)
        if response.status_code >= 400:
            detail = response.text.strip()
            response.close()
            session.close()
            raise OllamaError(
                f"Ollama could not install '{model_name}' (HTTP {response.status_code}): {detail}"
            )

        watcher_finished = threading.Event()

        def close_on_cancel() -> None:
            while not watcher_finished.wait(0.05):
                if should_cancel():
                    response.close()
                    return

        watcher = threading.Thread(
            target=close_on_cancel,
            name="paco-ollama-pull-cancel",
            daemon=True,
        )
        watcher.start()
        succeeded = False
        try:
            with response:
                for raw_line in response.iter_lines(decode_unicode=True):
                    if should_cancel():
                        return ModelPullResult(model_name, canceled=True)
                    if not raw_line:
                        continue
                    try:
                        payload = json.loads(raw_line)
                    except json.JSONDecodeError:
                        continue
                    if error := str(payload.get("error", "")).strip():
                        raise OllamaError(f"Ollama model install failed: {error}")
                    status = str(payload.get("status", "")).strip()
                    completed = self._nonnegative_int(payload.get("completed"))
                    total = self._nonnegative_int(payload.get("total"))
                    if status:
                        on_progress(
                            ModelPullProgress(
                                model_name,
                                status,
                                completed_bytes=completed,
                                total_bytes=total,
                            )
                        )
                    if status.casefold() == "success":
                        succeeded = True
                        break
        except (OSError, requests.RequestException) as exc:
            if should_cancel():
                return ModelPullResult(model_name, canceled=True)
            raise OllamaError(f"Ollama model install stream failed: {exc}") from exc
        finally:
            watcher_finished.set()
            session.close()

        if should_cancel():
            return ModelPullResult(model_name, canceled=True)
        if not succeeded:
            raise OllamaError("Ollama ended the model install before reporting success.")
        return ModelPullResult(model_name)

    def _open_cancellable_stream(
        self,
        payload: dict,
        should_cancel: Callable[[], bool],
        *,
        endpoint: str = "/api/chat",
        timeout: tuple[float, float | None] | None = None,
    ) -> tuple[requests.Response | None, requests.Session]:
        session = requests.Session()
        pending: queue.Queue[requests.Response | BaseException] = queue.Queue(maxsize=1)

        def open_stream() -> None:
            try:
                response = session.post(
                    f"{self.base_url}{endpoint}",
                    json=payload,
                    timeout=timeout or (5, self.timeout),
                    stream=True,
                )
                if should_cancel():
                    response.close()
                pending.put(response)
            except BaseException as exc:  # noqa: BLE001
                pending.put(exc)

        opener = threading.Thread(target=open_stream, name="paco-ollama-connect", daemon=True)
        opener.start()
        while True:
            if should_cancel():
                session.close()
                return None, session
            try:
                outcome = pending.get(timeout=0.05)
            except queue.Empty:
                continue
            if isinstance(outcome, requests.Timeout):
                session.close()
                raise OllamaError(
                    "Timed out waiting for a local Ollama reply. The selected model may still be loading."
                ) from outcome
            if isinstance(outcome, requests.RequestException):
                session.close()
                raise OllamaError(f"Could not reach Ollama at {self.base_url}: {outcome}") from outcome
            if isinstance(outcome, BaseException):
                session.close()
                raise OllamaError(f"Could not start the Ollama request: {outcome}") from outcome
            return outcome, session

    @staticmethod
    def _nonnegative_int(value: object) -> int:
        if isinstance(value, bool):
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _build_payload(
        model: str,
        messages: list[ChatMessage],
        stream: bool,
        options: dict | None = None,
    ) -> dict:
        api_messages: list[dict] = []
        for message in messages:
            api_message = {"role": message.role, "content": message.content}
            images = [
                str(attachment["image_data"])
                for attachment in AttachmentService.metadata_attachments(message.metadata)
                if attachment.get("image_data")
            ]
            if images:
                api_message["images"] = images
            api_messages.append(api_message)
        request_options = dict(options or {})
        think = request_options.pop(OllamaClient._think_option_key, False) is True
        payload = {
            "model": model,
            "stream": stream,
            "think": think,
            "messages": api_messages,
        }
        if request_options:
            payload["options"] = request_options
        return payload

    @staticmethod
    def _raise_for_error_response(response: requests.Response, model: str) -> None:
        if response.status_code == 404:
            raise OllamaError(f"Model '{model}' is not installed in Ollama.")
        if response.status_code >= 400:
            if "bad_alloc" in response.text.lower():
                raise OllamaError(
                    f"Ollama could not load '{model}' in local memory. Choose a smaller installed model."
                )
            raise OllamaError(f"Ollama returned HTTP {response.status_code}: {response.text}")
