from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from local_matrix_assistant.core.models import WebSearchResponse, WebSearchResult
from local_matrix_assistant.services.web_scraper import ExtractedWebPage, WebScrapeError
from local_matrix_assistant.services.web_search import WebSearchError, WebSearchService


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return

    def close(self) -> None:
        return


class WebSearchSafetyTests(unittest.TestCase):
    def test_google_search_parses_results_and_metadata(self) -> None:
        class GoogleResponse:
            def raise_for_status(self) -> None:
                return

            def json(self):
                return {
                    "items": [
                        {
                            "title": "Official result",
                            "link": "https://example.com/research",
                            "snippet": "Detailed search evidence.",
                            "pagemap": {
                                "metatags": [
                                    {"article:published_time": "2026-08-18T12:00:00Z"}
                                ]
                            },
                        },
                        {"title": "Unsafe", "link": "file:///C:/private.txt"},
                    ]
                }

            def close(self) -> None:
                return

        service = WebSearchService(
            google_api_key="secret-key",
            google_engine_id="engine-id",
            max_pages_to_extract=0,
        )
        with patch(
            "local_matrix_assistant.services.web_search.requests.get",
            return_value=GoogleResponse(),
        ) as request:
            results = service._fetch_google_results("research topic")

        self.assertEqual(1, len(results))
        self.assertEqual("Google", results[0].provider)
        self.assertEqual("2026-08-18T12:00:00Z", results[0].published_at)
        params = request.call_args.kwargs["params"]
        self.assertEqual("secret-key", params["key"])
        self.assertEqual("engine-id", params["cx"])
        self.assertEqual("active", params["safe"])

    def test_google_results_are_preferred_and_bing_duplicates_are_removed(self) -> None:
        service = WebSearchService(
            google_api_key="key",
            google_engine_id="engine",
            max_pages_to_extract=0,
        )
        google = WebSearchResult(
            "Topic documentation",
            "https://example.com/docs?utm_source=google",
            "Google result",
            "example.com",
            provider="Google",
        )
        bing_duplicate = WebSearchResult(
            "Topic documentation",
            "https://example.com/docs",
            "Bing result",
            "example.com",
            provider="Bing",
        )
        bing_other = WebSearchResult(
            "Topic documentation exact reference",
            "https://other.example/guide",
            "Bing result",
            "other.example",
            provider="Bing",
        )

        ranked = service._rank_and_filter_results(
            "topic documentation",
            [google, bing_duplicate, bing_other],
        )

        self.assertEqual(2, len(ranked))
        self.assertEqual("Google", ranked[0].provider)
        self.assertEqual("https://example.com/docs?utm_source=google", ranked[0].url)

    def test_google_failures_do_not_expose_api_keys(self) -> None:
        service = WebSearchService(
            google_api_key="do-not-leak",
            google_engine_id="engine",
        )
        with patch(
            "local_matrix_assistant.services.web_search.requests.get",
            side_effect=requests.RequestException("request URL contained do-not-leak"),
        ):
            with self.assertRaises(WebSearchError) as raised:
                service._fetch_google_results("topic")
        self.assertNotIn("do-not-leak", str(raised.exception))

    def test_search_combines_providers_and_places_google_first(self) -> None:
        service = WebSearchService(
            google_api_key="key",
            google_engine_id="engine",
            max_pages_to_extract=0,
        )
        google = WebSearchResult(
            "General source",
            "https://google-result.example/page",
            "A broad result.",
            "google-result.example",
            provider="Google",
        )
        bing = WebSearchResult(
            "Exact research topic",
            "https://bing-result.example/page",
            "An exact result.",
            "bing-result.example",
            provider="Bing",
        )
        with (
            patch.object(service, "_fetch_google_results", return_value=[google]),
            patch.object(service, "_fetch_rss_results", return_value=[bing]),
        ):
            response = service.search("exact research topic")

        self.assertEqual("Google", response.results[0].provider)
        self.assertIn("Google + Bing", response.provider)

    def test_search_falls_back_to_bing_when_google_fails(self) -> None:
        service = WebSearchService(
            google_api_key="key",
            google_engine_id="engine",
            max_pages_to_extract=0,
        )
        bing = WebSearchResult(
            "Bing source",
            "https://example.com/page",
            "Available evidence.",
            "example.com",
            provider="Bing",
        )
        with (
            patch.object(service, "_fetch_google_results", side_effect=WebSearchError("unavailable")),
            patch.object(service, "_fetch_rss_results", return_value=[bing]),
        ):
            response = service.search("research topic")

        self.assertEqual([bing], response.results)
        self.assertIn("Bing", response.provider)
        self.assertIn("Google API fallback", response.provider)

    def test_google_news_requires_no_account_and_uses_publisher_domain(self) -> None:
        rss = """<?xml version="1.0"?>
        <rss><channel><item>
          <title>Current research update - Publisher</title>
          <link>https://news.google.com/rss/articles/result-id</link>
          <pubDate>Wed, 19 Aug 2026 12:00:00 GMT</pubDate>
          <description>New evidence was published today.</description>
          <source url="https://publisher.example">Publisher</source>
        </item></channel></rss>
        """
        service = WebSearchService(
            google_api_key="",
            google_engine_id="",
            max_pages_to_extract=0,
        )

        with patch(
            "local_matrix_assistant.services.web_search.requests.get",
            return_value=_FakeResponse(rss),
        ) as request:
            results = service._fetch_google_news_results("current research")

        self.assertFalse(service.google_enabled)
        self.assertEqual("Google News", results[0].provider)
        self.assertEqual("publisher.example", results[0].domain)
        self.assertNotIn("key", request.call_args.kwargs["params"])
        self.assertNotIn("cx", request.call_args.kwargs["params"])

    def test_time_sensitive_search_prioritizes_account_free_google_news(self) -> None:
        service = WebSearchService(
            google_api_key="",
            google_engine_id="",
            max_pages_to_extract=0,
        )
        google_news = WebSearchResult(
            "General update",
            "https://news.google.com/rss/articles/result-id",
            "Current coverage.",
            "publisher.example",
            source_type="news",
            provider="Google News",
        )
        bing = WebSearchResult(
            "Exact current research update",
            "https://example.com/update",
            "Current coverage.",
            "example.com",
            source_type="news",
            provider="Bing",
        )
        with (
            patch.object(service, "_fetch_google_news_results", return_value=[google_news]),
            patch.object(service, "_fetch_rss_results", return_value=[bing]),
        ):
            response = service.search("current research update")

        self.assertEqual("Google News", response.results[0].provider)
        self.assertIn("Google News + Bing", response.provider)

    def test_google_news_feed_pages_are_not_scraped_as_articles(self) -> None:
        class RecordingScraper:
            def __init__(self) -> None:
                self.urls: list[str] = []

            def fetch(self, url: str, *, query: str, should_cancel):
                self.urls.append(url)
                return ExtractedWebPage(url=url, text="Extracted article evidence.")

        scraper = RecordingScraper()
        service = WebSearchService(max_pages_to_extract=1, scraper=scraper)  # type: ignore[arg-type]
        google_news = WebSearchResult(
            "News",
            "https://news.google.com/rss/articles/result-id",
            "Snippet",
            provider="Google News",
        )
        bing = WebSearchResult(
            "Article",
            "https://example.com/article",
            "Snippet",
            provider="Bing",
        )

        enriched = service._extract_result_pages("topic", [google_news, bing], should_cancel=None)

        self.assertEqual(["https://example.com/article"], scraper.urls)
        self.assertFalse(enriched[0].extracted_text)
        self.assertTrue(enriched[1].extracted_text)

    def test_google_priority_preserves_bing_corroboration(self) -> None:
        results = [
            *[
                WebSearchResult(
                    f"Google {index}",
                    f"https://news.google.com/{index}",
                    "News",
                    provider="Google News",
                )
                for index in range(8)
            ],
            *[
                WebSearchResult(
                    f"Bing {index}",
                    f"https://example.com/{index}",
                    "Web",
                    provider="Bing",
                )
                for index in range(4)
            ],
        ]

        selected = WebSearchService._select_provider_mix(results, 5)

        self.assertEqual("Google News", selected[0].provider)
        self.assertEqual(3, sum(result.provider == "Google News" for result in selected))
        self.assertEqual(2, sum(result.provider == "Bing" for result in selected))

    def test_rss_results_reject_non_web_urls(self) -> None:
        rss = """<?xml version="1.0"?>
        <rss><channel>
          <item><title>Safe result</title><link>https://example.com/docs</link></item>
          <item><title>Local file</title><link>file:///C:/private.txt</link></item>
          <item><title>Script URL</title><link>javascript:alert(1)</link></item>
          <item><title>Missing host</title><link>https:///missing</link></item>
          <item><title>Private service</title><link>http://127.0.0.1/admin</link></item>
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
        self.assertFalse(WebSearchService._is_safe_web_url("https://user:secret@example.com"))
        self.assertFalse(WebSearchService._is_safe_web_url("https://example.com:invalid"))

    def test_ranker_normalizes_tracking_urls_and_diversifies_domains(self) -> None:
        service = WebSearchService(max_pages_to_extract=0)
        results = [
            WebSearchResult("Alpha primary", "https://alpha.test/a?utm_source=x", "", "alpha.test"),
            WebSearchResult("Alpha duplicate", "https://alpha.test/a", "", "alpha.test"),
            WebSearchResult("Alpha second", "https://alpha.test/b", "", "alpha.test"),
            WebSearchResult("Alpha third", "https://alpha.test/c", "", "alpha.test"),
            WebSearchResult("Beta source", "https://beta.test/report", "", "beta.test"),
        ]

        ranked = service._rank_and_filter_results("source report", results)

        self.assertEqual(3, len(ranked))
        self.assertEqual(2, sum(result.domain == "alpha.test" for result in ranked))
        self.assertEqual(1, sum(result.domain == "beta.test" for result in ranked))

    def test_page_extraction_enriches_results_and_failure_keeps_snippet(self) -> None:
        class FakeScraper:
            def fetch(self, url: str, *, query: str, should_cancel):
                if "broken" in url:
                    raise WebScrapeError("blocked")
                return ExtractedWebPage(
                    url=url,
                    title="Extracted title",
                    description="Extracted description",
                    text=f"Detailed page evidence for {query}.",
                    content_type="text/html",
                    word_count=120,
                )

        service = WebSearchService(max_pages_to_extract=2, scraper=FakeScraper())  # type: ignore[arg-type]
        original = [
            WebSearchResult("First", "https://good.test/article", "Search snippet", "good.test"),
            WebSearchResult("Second", "https://broken.test/article", "Fallback snippet", "broken.test"),
        ]

        enriched = service._extract_result_pages("research topic", original, should_cancel=None)

        self.assertEqual("Extracted title", enriched[0].title)
        self.assertIn("Detailed page evidence", enriched[0].extracted_text)
        self.assertEqual("text/html", enriched[0].content_type)
        self.assertEqual("Second", enriched[1].title)
        self.assertEqual("Fallback snippet", enriched[1].snippet)

    def test_direct_public_urls_are_detected_without_private_network_targets(self) -> None:
        urls = WebSearchService._extract_urls(
            "Compare https://example.com/report?utm_source=chat, "
            "https://example.com/report and http://127.0.0.1/admin."
        )

        self.assertEqual(["https://example.com/report?utm_source=chat"], urls)

    def test_direct_url_is_omitted_when_its_page_cannot_be_safely_fetched(self) -> None:
        class RejectingScraper:
            def fetch(self, *_args, **_kwargs):
                raise WebScrapeError("private redirect")

        service = WebSearchService(max_pages_to_extract=1, scraper=RejectingScraper())  # type: ignore[arg-type]
        direct = WebSearchResult(
            "Requested page",
            "https://example.com/private-redirect",
            "Direct page requested by the user.",
            "example.com",
            source_type="direct",
        )

        self.assertEqual([], service._extract_result_pages("inspect it", [direct], should_cancel=None))

    def test_prompt_context_marks_extracted_text_untrusted_and_bounds_each_source(self) -> None:
        response = WebSearchResponse(
            provider="test",
            query="test",
            results=[
                WebSearchResult(
                    "Source",
                    "https://example.com",
                    "Summary",
                    extracted_text="evidence " * 400,
                )
            ],
        )

        context = WebSearchService.build_prompt_context(response)

        self.assertIn("untrusted reference data", context)
        self.assertIn("Extracted page text (untrusted):", context)
        self.assertLess(len(context), 2300)

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
