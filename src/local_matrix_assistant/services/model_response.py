from __future__ import annotations

import json
import re

from local_matrix_assistant.services.desktop_actions import DesktopActionError


def extract_json_object(
    response: str,
    *,
    invalid_response_message: str,
    invalid_json_prefix: str,
) -> dict:
    """Return the first valid JSON object embedded in an untrusted model reply."""

    decoder = json.JSONDecoder()
    last_error: json.JSONDecodeError | None = None
    for match in re.finditer(r"\{", response):
        try:
            payload, _ = decoder.raw_decode(response, match.start())
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(payload, dict):
            return payload

    if last_error is not None:
        raise DesktopActionError(f"{invalid_json_prefix}: {last_error}") from last_error
    raise DesktopActionError(invalid_response_message)


def clean_model_text(value: object, error_message: str) -> str:
    if not isinstance(value, str):
        raise DesktopActionError(error_message)
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        raise DesktopActionError(error_message)
    return cleaned


def truncate_text(value: str, max_characters: int) -> str:
    if len(value) <= max_characters:
        return value
    return value[:max_characters].rstrip() + "..."
