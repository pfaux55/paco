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
    def test_weather_and_right_now_are_time_sensitive(self) -> None:
        self.assertTrue(WebSearchService.is_time_sensitive_query("weather in Muskoka right now"))

    def test_weather_location_is_extracted_without_temporal_words(self) -> None:
        cases = {
            "weather in muskoka right now": "muskoka",
            "what is the weather like in Toronto today?": "Toronto",
            "Muskoka temperature now": "Muskoka",
            "forecast for Bracebridge tomorrow": "Bracebridge",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(expected, WebSearchService.weather_location(query))
        self.assertEqual("", WebSearchService.weather_location("gas prices forecast"))

    def test_try_again_reuses_the_prior_weather_request(self) -> None:
        query = WebSearchService.resolve_query(
            "try again",
            ["weather in muskoka right now"],
        )

        self.assertEqual("weather in muskoka right now", query)

    def test_live_weather_provider_returns_location_specific_observation(self) -> None:
        class JsonResponse:
            def __init__(self, payload, url: str) -> None:
                self._payload = payload
                self.url = url
                self.content = b"{}"

            def raise_for_status(self) -> None:
                return

            def json(self):
                return self._payload

            def close(self) -> None:
                return

        geocoding = JsonResponse(
            {
                "results": [
                    {
                        "name": "Muskoka",
                        "admin1": "Ontario",
                        "country": "Canada",
                        "latitude": 44.91181,
                        "longitude": -79.37425,
                    }
                ]
            },
            WebSearchService.weather_geocoding_endpoint,
        )
        forecast_url = "https://api.open-meteo.com/v1/forecast?latitude=44.91181&longitude=-79.37425"
        forecast = JsonResponse(
            {
                "timezone": "America/Toronto",
                "current_units": {
                    "temperature_2m": "°C",
                    "apparent_temperature": "°C",
                    "relative_humidity_2m": "%",
                    "wind_speed_10m": "km/h",
                    "wind_gusts_10m": "km/h",
                    "precipitation": "mm",
                },
                "current": {
                    "time": "2026-08-27T15:30",
                    "temperature_2m": 19.7,
                    "apparent_temperature": 19.5,
                    "relative_humidity_2m": 83,
                    "wind_speed_10m": 17.9,
                    "wind_gusts_10m": 31.3,
                    "precipitation": 0.0,
                    "weather_code": 1,
                },
                "daily_units": {
                    "temperature_2m_max": "°C",
                    "precipitation_probability_max": "%",
                },
                "daily": {
                    "time": ["2026-08-27"],
                    "weather_code": [63],
                    "temperature_2m_max": [20.2],
                    "temperature_2m_min": [15.6],
                    "precipitation_probability_max": [77],
                },
            },
            forecast_url,
        )
        service = WebSearchService(max_pages_to_extract=0)
        with patch.object(
            service,
            "_request_response",
            side_effect=[geocoding, forecast],
        ):
            result = service._fetch_weather_result("Muskoka")

        self.assertIsNotNone(result)
        self.assertEqual("Open-Meteo", result.provider)
        self.assertIn("Muskoka, Ontario, Canada", result.title)
        self.assertIn("19.7°C", result.snippet)
        self.assertIn("2026-08-27: moderate rain", result.extracted_text)
        self.assertEqual(forecast_url, result.url)

    def test_weather_search_short_circuits_generic_search_engines(self) -> None:
        service = WebSearchService(max_pages_to_extract=0)
        weather = WebSearchResult(
            "Current weather for Muskoka, Ontario, Canada",
            "https://api.open-meteo.com/v1/forecast?latitude=44.9&longitude=-79.3",
            "19.7°C and mainly clear.",
            "api.open-meteo.com",
            source_type="weather",
            extracted_text="Live observation.",
            provider="Open-Meteo",
        )
        with (
            patch.object(service, "_fetch_weather_result", return_value=weather),
            patch.object(service, "_fetch_yahoo_results") as yahoo,
            patch.object(service, "_fetch_rss_results") as bing,
        ):
            response = service.search("weather in Muskoka right now")

        self.assertEqual([weather], response.results)
        self.assertEqual("Open-Meteo live weather", response.provider)
        yahoo.assert_not_called()
        bing.assert_not_called()

    def test_contextual_research_follow_up_uses_prior_user_topic(self) -> None:
        query = WebSearchService.resolve_query(
            "pull info necessary for analysis in the next chat you will do the actual analysis",
            ["do an analysis of gas prices over the next 6-18 months"],
        )

        self.assertEqual("do an analysis of gas prices over the next 6-18 months", query)

    def test_standalone_research_request_remains_the_query(self) -> None:
        request = "find information about gas prices in Ontario"

        self.assertEqual(request, WebSearchService.resolve_query(request, ["unrelated topic here"]))

    def test_contextual_research_without_prior_topic_remains_the_query(self) -> None:
        request = "gather the sources needed for the next analysis"

        self.assertEqual(request, WebSearchService.resolve_query(request, []))

    def test_conversational_analysis_request_becomes_a_focused_query(self) -> None:
        query = (
            "do an analysis of gas prices and everything affecting them and make predictions "
            "of where things will change and in what way in the next 6-18 months"
        )

        focused = WebSearchService.prepare_query(query)

        self.assertEqual('"gas prices" factors forecast', focused)

    def test_future_horizon_adds_forecast_to_focused_query(self) -> None:
        focused = WebSearchService.prepare_query(
            "do an analysis of gas prices over the next 6-18 months"
        )

        self.assertEqual('"gas prices" forecast', focused)

    def test_prediction_research_queries_collect_inputs_for_inference(self) -> None:
        queries = WebSearchService.prepare_research_queries(
            "do an analysis of gas prices over the next 6-18 months"
        )

        self.assertEqual('"gas prices" forecast', queries[0])
        self.assertTrue(any("historical data trends seasonality" in item for item in queries))
        self.assertTrue(any("key drivers supply demand risks" in item for item in queries))

    def test_documentation_query_adds_an_official_source_variant(self) -> None:
        queries = WebSearchService.prepare_research_queries(
            "OpenAI Responses API Python quickstart"
        )

        self.assertEqual("OpenAI Responses API Python quickstart", queries[0])
        self.assertIn("OpenAI Responses API Python official documentation", queries)

    def test_prediction_search_runs_each_complementary_web_query(self) -> None:
        service = WebSearchService(max_pages_to_extract=0)
        result = WebSearchResult(
            "Energy evidence",
            "https://example.com/evidence",
            "Historical and driver evidence.",
            "example.com",
            provider="Bing",
        )

        with (
            patch.object(service, "_fetch_yahoo_results", return_value=[]),
            patch.object(service, "_fetch_google_news_results", return_value=[]),
            patch.object(service, "_fetch_rss_results", return_value=[result]) as fetch,
        ):
            response = service.search("gas prices over the next 6-18 months")

        web_queries = [
            call.args[1]
            for call in fetch.call_args_list
            if call.args[0] == "https://www.bing.com/search"
        ]
        self.assertEqual(WebSearchService.prepare_research_queries(
            "gas prices over the next 6-18 months"
        ), web_queries)
        self.assertEqual([result], response.results)

    def test_search_uses_the_focused_query_for_providers_and_response(self) -> None:
        service = WebSearchService(max_pages_to_extract=0)
        result = WebSearchResult(
            "Gas price forecast",
            "https://example.com/forecast",
            "Gas price outlook.",
            "example.com",
            provider="Bing",
        )
        request = "do an analysis of gas prices and everything affecting them"

        with (
            patch.object(service, "_fetch_yahoo_results", return_value=[]),
            patch.object(service, "_fetch_rss_results", return_value=[result]) as fetch,
        ):
            response = service.search(request)

        self.assertEqual('"gas prices" factors', response.query)
        self.assertEqual('"gas prices" factors', fetch.call_args.args[1])

    def test_direct_url_query_is_not_rewritten(self) -> None:
        query = "analyze https://example.com/report and summarize its findings"

        self.assertEqual(query, WebSearchService.prepare_query(query))

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

    def test_brave_search_parses_results_without_exposing_its_key(self) -> None:
        class BraveResponse:
            def raise_for_status(self) -> None:
                return

            def json(self):
                return {
                    "web": {
                        "results": [
                            {
                                "title": "Responses API reference",
                                "url": "https://platform.openai.com/docs/api-reference/responses",
                                "description": "Official API documentation.",
                                "page_age": "2026-08-20T12:00:00Z",
                            }
                        ]
                    }
                }

            def close(self) -> None:
                return

        service = WebSearchService(brave_api_key="do-not-leak", max_pages_to_extract=0)
        with patch(
            "local_matrix_assistant.services.web_search.requests.get",
            return_value=BraveResponse(),
        ) as request:
            results = service._fetch_brave_results("Responses API")

        self.assertTrue(service.brave_enabled)
        self.assertEqual("Brave", results[0].provider)
        self.assertEqual("platform.openai.com", results[0].domain)
        self.assertEqual(
            "do-not-leak",
            request.call_args.kwargs["headers"]["X-Subscription-Token"],
        )

    def test_yahoo_fallback_parses_organic_results_and_unwraps_urls(self) -> None:
        html = """
        <div class="dd algo algo-sr">
          <div class="compTitle">
            <a href="https://r.search.yahoo.com/x/RU=https%3A%2F%2Fdocs.python.org%2F3.13%2Flibrary%2Fpathlib.html/RK=2/RS=x">
              <h3><span>pathlib — Python 3.13 documentation</span></h3>
            </a>
          </div>
          <div class="compText"><p>Object-oriented filesystem paths.</p></div>
        </div>
        """
        service = WebSearchService(max_pages_to_extract=0)
        with patch(
            "local_matrix_assistant.services.web_search.requests.get",
            return_value=_FakeResponse(html),
        ):
            results = service._fetch_yahoo_results("Python 3.13 pathlib")

        self.assertEqual(1, len(results))
        self.assertEqual("Yahoo", results[0].provider)
        self.assertEqual(
            "https://docs.python.org/3.13/library/pathlib.html",
            results[0].url,
        )
        self.assertIn("Object-oriented", results[0].snippet)

    def test_duplicate_keeps_first_provider_without_overriding_relevance(self) -> None:
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
        self.assertEqual("https://other.example/guide", ranked[0].url)
        self.assertIn("https://example.com/docs?utm_source=google", [item.url for item in ranked])

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

    def test_search_combines_providers_and_places_relevance_first(self) -> None:
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
            patch.object(service, "_fetch_yahoo_results", return_value=[]),
            patch.object(service, "_fetch_google_results", return_value=[google]),
            patch.object(service, "_fetch_rss_results", return_value=[bing]),
        ):
            response = service.search("exact research topic")

        self.assertEqual("Bing", response.results[0].provider)
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
            patch.object(service, "_fetch_yahoo_results", return_value=[]),
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

    def test_time_sensitive_search_prioritizes_relevance_over_provider(self) -> None:
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
            patch.object(service, "_fetch_yahoo_results", return_value=[]),
            patch.object(service, "_fetch_google_news_results", return_value=[google_news]),
            patch.object(service, "_fetch_rss_results", return_value=[bing]),
        ):
            response = service.search("current research update")

        self.assertEqual("Bing", response.results[0].provider)
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

    def test_google_news_match_uses_the_publisher_article_url(self) -> None:
        google_news = WebSearchResult(
            "Toronto forecast calls for rain Thursday - Publisher",
            "https://news.google.com/rss/articles/result-id",
            "Google News summary.",
            "publisher.example",
            source_type="news",
            provider="Google News",
        )
        bing_news = WebSearchResult(
            "Toronto forecast calls for rain Thursday | Publisher",
            "https://publisher.example/weather/toronto-rain",
            "Publisher summary.",
            "publisher.example",
            source_type="news",
            provider="Bing",
        )

        resolved = WebSearchService._resolve_google_news_results(
            [google_news],
            [bing_news],
        )

        self.assertEqual(bing_news.url, resolved[0].url)
        self.assertEqual("Google News/Bing", resolved[0].provider)

    def test_provider_mix_preserves_one_corroborating_provider(self) -> None:
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
        self.assertEqual(4, sum(result.provider == "Google News" for result in selected))
        self.assertEqual(1, sum(result.provider == "Bing" for result in selected))

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

    def test_bing_news_redirect_is_unwrapped_to_the_publisher(self) -> None:
        url = (
            "http://www.bing.com/news/apiclick.aspx?ref=FexRss&"
            "url=https%3A%2F%2Fpublisher.example%2Farticle%3Fid%3D7"
        )

        self.assertEqual(
            "https://publisher.example/article?id=7",
            WebSearchService._unwrap_search_result_url(url),
        )

    def test_bing_web_rss_timestamp_is_not_treated_as_publication_date(self) -> None:
        rss = """<?xml version="1.0"?>
        <rss><channel><item>
          <title>Ordinary documentation</title>
          <link>https://example.com/docs</link>
          <pubDate>Thu, 27 Aug 2026 13:00:00 GMT</pubDate>
        </item></channel></rss>
        """
        service = WebSearchService()

        with patch(
            "local_matrix_assistant.services.web_search.requests.get",
            return_value=_FakeResponse(rss),
        ):
            results = service._fetch_rss_results(
                "https://www.bing.com/search",
                "ordinary documentation",
                source_type="web",
            )

        self.assertEqual("", results[0].published_at)

    def test_weak_and_stale_results_are_removed_from_realistic_result_set(self) -> None:
        service = WebSearchService(max_pages_to_extract=0)
        results = [
            WebSearchResult(
                "Toronto forecast from last year",
                "https://old.example/weather",
                "Toronto weather forecast",
                "old.example",
                published_at="Wed, 01 Jan 2020 12:00:00 GMT",
                source_type="news",
            ),
            WebSearchResult(
                "Breaking News, Latest News and Videos",
                "https://cnn.example/",
                "World headlines",
                "cnn.example",
                source_type="web",
            ),
            WebSearchResult(
                "Current Toronto weather forecast",
                "https://weather.example/toronto",
                "Toronto conditions and seven day weather forecast.",
                "weather.example",
                source_type="web",
            ),
            WebSearchResult(
                "Unrelated sports result",
                "https://sports.example/hockey",
                "Toronto hockey news",
                "sports.example",
            ),
        ]

        filtered = service._filter_weak_results("latest Toronto weather forecast", results)

        self.assertEqual(["Current Toronto weather forecast"], [item.title for item in filtered])

    def test_requested_version_outranks_newer_wrong_documentation_version(self) -> None:
        service = WebSearchService(max_pages_to_extract=0)
        correct = WebSearchResult(
            "pathlib — Python 3.13.15 documentation",
            "https://docs.python.org/3.13/library/pathlib.html",
            "Object-oriented filesystem paths.",
            "docs.python.org",
        )
        wrong = WebSearchResult(
            "Python 3.14.7 documentation",
            "https://docs.python.org/3.14/",
            "Python language documentation.",
            "docs.python.org",
        )

        ranked = service._rank_and_filter_results(
            "Python 3.13 documentation pathlib",
            [wrong, correct],
        )

        self.assertEqual(correct.url, ranked[0].url)

    def test_python_documentation_url_preserves_requested_minor_version(self) -> None:
        result = WebSearchResult(
            "pathlib — Python documentation",
            "https://docs.python.org/3/library/pathlib.html",
            "Object-oriented filesystem paths.",
            "docs.python.org",
            provider="Yahoo",
        )

        normalized = WebSearchService._normalize_documentation_result(
            "Python 3.13 documentation pathlib",
            result,
        )

        self.assertEqual(
            "https://docs.python.org/3.13/library/pathlib.html",
            normalized.url,
        )

    def test_page_canonical_does_not_erase_requested_python_minor_version(self) -> None:
        class CanonicalScraper:
            def fetch(self, *_args, **_kwargs):
                return ExtractedWebPage(
                    url="https://docs.python.org/3/library/pathlib.html",
                    title="pathlib — Python 3.13.15 documentation",
                    text="Object-oriented filesystem paths.",
                )

        service = WebSearchService(scraper=CanonicalScraper())  # type: ignore[arg-type]
        result = WebSearchResult(
            "pathlib",
            "https://docs.python.org/3.13/library/pathlib.html",
            "",
            "docs.python.org",
        )

        extracted = service._extract_result(
            "Python 3.13 documentation pathlib",
            result,
            None,
        )

        self.assertEqual(
            "https://docs.python.org/3.13/library/pathlib.html",
            extracted.url,
        )

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

        self.assertIn("Live web search is enabled", context)
        self.assertIn("Do not claim that browsing", context)
        self.assertIn("untrusted reference data", context)
        self.assertIn("Extracted page text (untrusted):", context)
        self.assertLess(len(context), 2300)

    def test_prediction_prompt_requires_synthesis_and_uncertainty(self) -> None:
        response = WebSearchResponse(
            provider="test",
            query='"gas prices" forecast',
            results=[WebSearchResult("Source", "https://example.com", "Evidence")],
        )

        context = WebSearchService.build_prompt_context(response)

        self.assertIn("Do not stop merely because no source publishes", context)
        self.assertIn("base case", context)
        self.assertIn("state assumptions and confidence", context)

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
