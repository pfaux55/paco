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

from local_matrix_assistant.core.models import WebSearchResult
from local_matrix_assistant.services.web_scraper import WebPageScraper, WebScrapeError
from local_matrix_assistant.services.web_search import WebSearchError, WebSearchService


class _FeedResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.closed = False

    def raise_for_status(self) -> None:
        return

    def close(self) -> None:
        self.closed = True


class _PageResponse:
    def __init__(
        self,
        *,
        headers: dict[str, str],
        chunks: list[bytes] | None = None,
        redirect: bool = False,
    ) -> None:
        self.headers = headers
        self.encoding = "utf-8"
        self.is_redirect = redirect
        self.is_permanent_redirect = False
        self._chunks = chunks or []
        self.closed = False

    def raise_for_status(self) -> None:
        return

    def iter_content(self, *, chunk_size: int):
        del chunk_size
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


class AdvancedWebSearchProblemTests(unittest.TestCase):
    def test_google_news_outage_falls_back_to_bing_for_current_query(self) -> None:
        service = WebSearchService(
            google_api_key="",
            google_engine_id="",
            max_pages_to_extract=0,
        )
        bing = WebSearchResult(
            "Current verified report",
            "https://example.com/current-report",
            "Available Bing evidence.",
            "example.com",
            provider="Bing",
        )
        with (
            patch.object(service, "_fetch_yahoo_results", return_value=[]),
            patch.object(
                service,
                "_fetch_google_news_results",
                side_effect=WebSearchError("Google News unavailable"),
            ),
            patch.object(service, "_fetch_rss_results", return_value=[bing]),
        ):
            response = service.search("latest verified report")

        self.assertEqual([bing], response.results)
        self.assertEqual("Bing + page extraction", response.provider)

    def test_all_provider_failures_raise_a_controlled_search_error(self) -> None:
        service = WebSearchService(
            google_api_key="",
            google_engine_id="",
            max_pages_to_extract=0,
        )
        with (
            patch.object(
                service,
                "_fetch_yahoo_results",
                side_effect=WebSearchError("Yahoo unavailable"),
            ),
            patch.object(
                service,
                "_fetch_google_news_results",
                side_effect=WebSearchError("Google News unavailable"),
            ),
            patch.object(
                service,
                "_fetch_rss_results",
                side_effect=WebSearchError("Bing unavailable"),
            ),
        ):
            with self.assertRaisesRegex(WebSearchError, "Google News unavailable"):
                service.search("latest unavailable topic")

    def test_large_result_sets_are_bounded_and_domain_diverse(self) -> None:
        service = WebSearchService(
            google_api_key="",
            google_engine_id="",
            max_pages_to_extract=0,
        )
        results = [
            WebSearchResult(
                f"Research source {index}",
                f"https://source{index}.example/report",
                "Research evidence.",
                f"source{index}.example",
                provider="Bing",
            )
            for index in range(40)
        ]
        with (
            patch.object(service, "_fetch_yahoo_results", return_value=[]),
            patch.object(service, "_fetch_rss_results", return_value=results),
        ):
            response = service.search("research evidence", max_results=100)

        self.assertEqual(12, len(response.results))
        self.assertEqual(12, len({result.domain for result in response.results}))

    def test_cancellation_during_concurrent_page_extraction_returns_promptly(self) -> None:
        release = threading.Event()

        class BlockingScraper:
            def fetch(self, *_args, **_kwargs):
                release.wait(2)
                raise WebScrapeError("released")

        service = WebSearchService(
            max_pages_to_extract=2,
            scraper=BlockingScraper(),  # type: ignore[arg-type]
        )
        results = [
            WebSearchResult("One", "https://one.example/a", "", provider="Bing"),
            WebSearchResult("Two", "https://two.example/b", "", provider="Bing"),
        ]
        started = time.monotonic()
        try:
            returned = service._extract_result_pages(
                "topic",
                results,
                should_cancel=lambda: time.monotonic() - started > 0.08,
            )
        finally:
            release.set()

        self.assertEqual(results, returned)
        self.assertLess(time.monotonic() - started, 0.5)

    def test_rss_entity_declarations_are_rejected_and_response_is_closed(self) -> None:
        response = _FeedResponse(
            """<?xml version="1.0"?>
            <!DOCTYPE rss [<!ENTITY payload "expanded text">]>
            <rss><channel><item><title>&payload;</title>
            <link>https://example.com/</link></item></channel></rss>"""
        )
        service = WebSearchService(max_pages_to_extract=0)

        with self.assertRaisesRegex(WebSearchError, "prohibited declaration"):
            service._parse_rss_response(response, source_type="web", provider="Bing")  # type: ignore[arg-type]

        self.assertTrue(response.closed)

    def test_oversized_rss_feed_is_rejected_before_xml_parsing(self) -> None:
        service = WebSearchService(max_pages_to_extract=0)
        response = _FeedResponse("x" * (service.max_feed_characters + 1))

        with self.assertRaisesRegex(WebSearchError, "parsing limit"):
            service._parse_rss_response(response, source_type="web", provider="Bing")  # type: ignore[arg-type]

        self.assertTrue(response.closed)

    def test_tracking_and_query_order_variants_deduplicate(self) -> None:
        first = WebSearchService._normalize_url(
            "https://Example.com/report?b=2&utm_source=news&a=1#section"
        )
        second = WebSearchService._normalize_url(
            "https://example.com/report?a=1&b=2"
        )

        self.assertEqual(second, first)

    def test_login_filter_does_not_reject_innocent_domain_substrings(self) -> None:
        service = WebSearchService(max_pages_to_extract=0)
        valid = WebSearchResult(
            "Design institute research",
            "https://designinstitute.example/research",
            "Evidence",
            "designinstitute.example",
        )
        login = WebSearchResult(
            "Account login",
            "https://example.com/account/login",
            "Authentication",
            "example.com",
        )

        ranked = service._rank_and_filter_results("design research", [login, valid])

        self.assertEqual([valid], ranked)

    def test_iso_publication_dates_rank_newest_first(self) -> None:
        service = WebSearchService(max_pages_to_extract=0)
        older = WebSearchResult(
            "Release report",
            "https://older.example/report",
            "Release evidence",
            "older.example",
            published_at="2026-01-10T12:00:00Z",
            provider="Google News",
        )
        newer = WebSearchResult(
            "Release report",
            "https://newer.example/report",
            "Release evidence",
            "newer.example",
            published_at="2026-08-19T12:00:00Z",
            provider="Google News",
        )

        ranked = service._rank_and_filter_results("latest release", [older, newer])

        self.assertEqual(newer, ranked[0])

    def test_time_sensitive_detection_matches_words_not_substrings(self) -> None:
        self.assertTrue(WebSearchService.is_time_sensitive_query("latest release news"))
        self.assertTrue(WebSearchService.is_time_sensitive_query("energy price forecast"))
        self.assertFalse(WebSearchService.is_time_sensitive_query("newspaper updater design"))


class AdvancedWebScraperProblemTests(unittest.TestCase):
    def test_redirect_to_loopback_is_blocked_before_second_request(self) -> None:
        redirect = _PageResponse(
            headers={"Location": "http://127.0.0.1/admin"},
            redirect=True,
        )

        class Session:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return redirect

            def close(self) -> None:
                return

        session = Session()
        scraper = WebPageScraper()

        def validate(url: str) -> None:
            if "127.0.0.1" in url:
                raise WebScrapeError("Blocked a private redirect.")

        with (
            patch.object(scraper, "_validate_public_url", side_effect=validate),
            patch(
                "local_matrix_assistant.services.web_scraper.requests.Session",
                return_value=session,
            ),
        ):
            with self.assertRaisesRegex(WebScrapeError, "private redirect"):
                scraper.fetch("https://example.com/start")

        self.assertEqual(1, session.calls)
        self.assertTrue(redirect.closed)

    def test_excessive_redirect_chain_is_bounded(self) -> None:
        responses: list[_PageResponse] = []

        class Session:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                response = _PageResponse(headers={"Location": "/next"}, redirect=True)
                responses.append(response)
                return response

            def close(self) -> None:
                return

        session = Session()
        scraper = WebPageScraper()
        with (
            patch.object(scraper, "_validate_public_url"),
            patch(
                "local_matrix_assistant.services.web_scraper.requests.Session",
                return_value=session,
            ),
        ):
            with self.assertRaisesRegex(WebScrapeError, "redirect limit"):
                scraper.fetch("https://example.com/start")

        self.assertEqual(scraper.max_redirects + 1, session.calls)
        self.assertTrue(all(response.closed for response in responses))

    def test_chunked_body_cannot_bypass_download_limit(self) -> None:
        response = _PageResponse(
            headers={"Content-Type": "text/plain"},
            chunks=[b"123456", b"789012"],
        )

        class Session:
            def get(self, *_args, **_kwargs):
                return response

            def close(self) -> None:
                return

        scraper = WebPageScraper(max_download_bytes=10)
        with (
            patch.object(scraper, "_validate_public_url"),
            patch(
                "local_matrix_assistant.services.web_scraper.requests.Session",
                return_value=Session(),
            ),
        ):
            with self.assertRaisesRegex(WebScrapeError, "download limit"):
                scraper.fetch("https://example.com/stream")

        self.assertTrue(response.closed)

    def test_private_canonical_url_is_ignored(self) -> None:
        scraper = WebPageScraper()
        page = scraper.extract(
            "https://example.com/article",
            (
                b'<html><head><link rel="canonical" href="http://127.0.0.1/private">'
                b"</head><body><article><p>Public article evidence remains readable."
                b"</p></article></body></html>"
            ),
            "text/html",
            query="article evidence",
        )

        self.assertEqual("https://example.com/article", page.url)
        self.assertIn("Public article evidence", page.text)


if __name__ == "__main__":
    unittest.main()
