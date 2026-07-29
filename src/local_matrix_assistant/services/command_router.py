from __future__ import annotations

import re


_POLITE_PREFIX = r"(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
_WEB_SEARCH_COMMAND = re.compile(
    rf"^\s*{_POLITE_PREFIX}(?:search\s+(?:the\s+)?web|search\s+online|web\s+search)"
    r"(?:\s+for)?(?:\s*[:,-]\s*|\s+)?(?P<query>.*?)\s*[?.!]*\s*$",
    re.IGNORECASE,
)


def explicit_web_search_query(text: str) -> str | None:
    """Return the requested query when text explicitly asks for a web search."""

    match = _WEB_SEARCH_COMMAND.match(text)
    if not match:
        return None
    query = match.group("query").strip()
    return query or text.strip()
