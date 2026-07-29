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

from local_matrix_assistant.services.web_search import WebSearchService


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return


class WebSearchSafetyTests(unittest.TestCase):
    def test_rss_results_reject_non_web_urls(self) -> None:
        rss = """<?xml version="1.0"?>
        <rss><channel>
          <item><title>Safe result</title><link>https://example.com/docs</link></item>
          <item><title>Local file</title><link>file:///C:/private.txt</link></item>
          <item><title>Script URL</title><link>javascript:alert(1)</link></item>
          <item><title>Missing host</title><link>https:///missing</link></item>
        </channel></rss>
        """
        service = WebSearchService()

        with patch(
            "local_matrix_assistant.services.web_search.requests.get",
            return_value=_FakeResponse(rss),
        ):
            results = service._fetch_rss_results(
                "https://www.bing.com/search",
                "safe links",
                source_type="web",
            )

        self.assertEqual(["https://example.com/docs"], [item.url for item in results])

    def test_url_validation_requires_http_host(self) -> None:
        self.assertTrue(WebSearchService._is_safe_web_url("http://localhost:8080/path"))
        self.assertTrue(WebSearchService._is_safe_web_url("https://example.com"))
        self.assertFalse(WebSearchService._is_safe_web_url("file:///tmp/private"))
        self.assertFalse(WebSearchService._is_safe_web_url("https:///missing-host"))

    def test_search_can_cancel_while_waiting_for_response_headers(self) -> None:
        release_request = threading.Event()

        class FakeResponse:
            def close(self) -> None:
                return

        class FakeSession:
            def __init__(self) -> None:
                self.closed = False

            def get(self, *_args, **_kwargs):
                release_request.wait(2)
                return FakeResponse()

            def close(self) -> None:
                self.closed = True

        session = FakeSession()
        service = WebSearchService(timeout_seconds=10)
        started = time.monotonic()
        try:
            with patch(
                "local_matrix_assistant.services.web_search.requests.Session",
                return_value=session,
            ):
                result = service.search(
                    "cancel this search",
                    should_cancel=lambda: time.monotonic() - started >= 0.08,
                )
        finally:
            release_request.set()

        self.assertTrue(result.canceled)
        self.assertEqual([], result.results)
        self.assertTrue(session.closed)
        self.assertLess(time.monotonic() - started, 0.5)


if __name__ == "__main__":
    unittest.main()
