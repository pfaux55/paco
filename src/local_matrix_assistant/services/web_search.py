from __future__ import annotations

from datetime import datetime
from html import unescape
from email.utils import parsedate_to_datetime
import re
import queue
import threading
from typing import Callable
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import requests

from local_matrix_assistant.core.models import WebSearchResponse, WebSearchResult


class WebSearchError(RuntimeError):
    """Raised when the web search provider cannot be reached or parsed."""


TIME_SENSITIVE_TOKENS = {
    "latest",
    "current",
    "today",
    "recent",
    "newest",
    "news",
    "update",
    "updated",
    "release",
    "released",
}
LOW_VALUE_URL_TOKENS = {"login", "signin", "auth"}


class WebSearchService:
    provider_name = "Bing RSS"

    def __init__(self, timeout_seconds: int = 10) -> None:
        self._timeout_seconds = timeout_seconds
        self._headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/122.0 Safari/537.36"
            )
        }

    def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        should_cancel: Callable[[], bool] | None = None,
    ) -> WebSearchResponse:
        normalized_query = query.strip()
        if not normalized_query:
            return WebSearchResponse(provider=self.provider_name, query="", results=[])
        if self._is_cancelled(should_cancel):
            return self._canceled_response(normalized_query)

        time_sensitive = self.is_time_sensitive_query(normalized_query)
        web_results = self._fetch_rss_results(
            "https://www.bing.com/search",
            normalized_query,
            source_type="web",
            should_cancel=should_cancel,
        )
        if self._is_cancelled(should_cancel):
            return self._canceled_response(normalized_query, time_sensitive=time_sensitive)
        news_results = (
            self._fetch_rss_results(
                "https://www.bing.com/news/search",
                normalized_query,
                source_type="news",
                should_cancel=should_cancel,
            )
            if time_sensitive
            else []
        )
        if self._is_cancelled(should_cancel):
            return self._canceled_response(normalized_query, time_sensitive=time_sensitive)

        merged = self._rank_and_filter_results(normalized_query, [*news_results, *web_results])
        if not merged:
            raise WebSearchError("Web search returned no usable results.")
        return WebSearchResponse(
            provider=f"{self.provider_name} ({'news + web' if time_sensitive else 'web'})",
            query=normalized_query,
            results=merged[:max_results],
            time_sensitive=time_sensitive,
        )

    @staticmethod
    def is_time_sensitive_query(query: str) -> bool:
        lowered = query.lower()
        return any(token in lowered for token in TIME_SENSITIVE_TOKENS)

    def _fetch_rss_results(
        self,
        endpoint: str,
        query: str,
        *,
        source_type: str,
        should_cancel: Callable[[], bool] | None = None,
    ) -> list[WebSearchResult]:
        try:
            response = self._get_response(endpoint, query, should_cancel)
            if response is None:
                return []
            response.raise_for_status()
        except requests.RequestException as exc:
            raise WebSearchError(f"Web search is unavailable: {exc}") from exc

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise WebSearchError(f"Web search response could not be parsed: {exc}") from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

        results: list[WebSearchResult] = []
        for item in root.findall(".//item"):
            title = self._clean_text(item.findtext("title") or "")
            url = (item.findtext("link") or "").strip()
            if not title or not self._is_safe_web_url(url):
                continue
            domain = self._domain_for_url(url)
            published_at = (item.findtext("pubDate") or "").strip()
            description = self._clean_text(item.findtext("description") or "")
            snippet_parts = [part for part in (published_at, domain, description) if part]
            results.append(
                WebSearchResult(
                    title=title,
                    url=url,
                    snippet=" | ".join(snippet_parts),
                    domain=domain,
                    published_at=published_at,
                    source_type=source_type,
                )
            )
        return results

    def _get_response(
        self,
        endpoint: str,
        query: str,
        should_cancel: Callable[[], bool] | None,
    ) -> requests.Response | None:
        request_kwargs = {
            "params": {"q": query, "format": "rss", "mkt": "en-US", "setlang": "en-US"},
            "headers": self._headers,
            "timeout": self._timeout_seconds,
        }
        if should_cancel is None:
            return requests.get(endpoint, **request_kwargs)

        session = requests.Session()
        pending: queue.Queue[requests.Response | BaseException] = queue.Queue(maxsize=1)

        def open_request() -> None:
            try:
                response = session.get(endpoint, **request_kwargs)
                if should_cancel():
                    response.close()
                pending.put(response)
            except BaseException as exc:  # noqa: BLE001
                pending.put(exc)

        opener = threading.Thread(
            target=open_request,
            name="jarvis-web-search-connect",
            daemon=True,
        )
        opener.start()
        while True:
            if should_cancel():
                session.close()
                return None
            try:
                outcome = pending.get(timeout=0.05)
            except queue.Empty:
                continue
            session.close()
            if isinstance(outcome, requests.RequestException):
                raise outcome
            if isinstance(outcome, BaseException):
                raise WebSearchError(f"Web search could not start: {outcome}") from outcome
            return outcome

    @staticmethod
    def _is_cancelled(should_cancel: Callable[[], bool] | None) -> bool:
        return bool(should_cancel and should_cancel())

    def _canceled_response(
        self,
        query: str,
        *,
        time_sensitive: bool = False,
    ) -> WebSearchResponse:
        return WebSearchResponse(
            provider=self.provider_name,
            query=query,
            results=[],
            time_sensitive=time_sensitive,
            canceled=True,
        )

    def _rank_and_filter_results(self, query: str, results: list[WebSearchResult]) -> list[WebSearchResult]:
        query_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", query.lower())
            if len(token) >= 4 and token not in TIME_SENSITIVE_TOKENS
        }
        deduped: list[WebSearchResult] = []
        seen_urls: set[str] = set()
        for result in results:
            if result.url in seen_urls:
                continue
            seen_urls.add(result.url)
            if any(token in result.url.lower() for token in LOW_VALUE_URL_TOKENS):
                continue
            deduped.append(result)

        def score(result: WebSearchResult) -> tuple[int, int, int, str]:
            domain_lower = result.domain.lower()
            title_lower = result.title.lower()
            token_hits = sum(token in domain_lower or token in title_lower for token in query_tokens)
            is_news = 1 if result.source_type == "news" else 0
            has_date = 1 if result.published_at else 0
            timestamp = int(self._published_timestamp(result.published_at))
            return (token_hits, is_news, has_date, timestamp, result.title)

        deduped.sort(key=score, reverse=True)
        return deduped

    @staticmethod
    def build_prompt_context(search_response: WebSearchResponse) -> str:
        today = datetime.now().strftime("%Y-%m-%d")
        lines = [
            f"Today's local date is {today}.",
            "Use these search results only when they help answer the user's most recent question.",
            "Answer from the supplied sources only. If the sources do not establish the answer, say that clearly instead of guessing.",
        ]
        if search_response.time_sensitive:
            lines.append(
                "This query appears time-sensitive. Prefer the newest explicit corroborating sources, compare publication dates carefully, and mention uncertainty if the sources conflict."
            )
            lines.append("Do not invent a different current date. Use only the dates shown in the sources or the local date above.")
        lines.append("Cite source-backed claims using bracketed numbers like [1] or [2].")

        for index, result in enumerate(search_response.results, start=1):
            lines.append(f"[{index}] {result.title}")
            lines.append(f"URL: {result.url}")
            if result.domain:
                lines.append(f"Domain: {result.domain}")
            if result.published_at:
                lines.append(f"Published: {result.published_at}")
            if result.source_type:
                lines.append(f"Source type: {result.source_type}")
            if result.snippet:
                lines.append(f"Snippet: {result.snippet}")
        return "\n".join(lines)

    @staticmethod
    def _clean_text(text: str) -> str:
        stripped = re.sub(r"<[^>]+>", " ", text)
        collapsed = re.sub(r"\s+", " ", unescape(stripped)).strip()
        return collapsed

    @staticmethod
    def _domain_for_url(url: str) -> str:
        return urlparse(url).netloc.replace("www.", "")

    @staticmethod
    def _is_safe_web_url(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme.casefold() in {"http", "https"} and bool(parsed.hostname)

    @staticmethod
    def _published_timestamp(published_at: str) -> float:
        if not published_at:
            return 0.0
        try:
            return parsedate_to_datetime(published_at).timestamp()
        except (TypeError, ValueError, OverflowError):
            return 0.0
