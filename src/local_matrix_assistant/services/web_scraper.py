from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
import ipaddress
import json
import re
import socket
from typing import Callable
from urllib.parse import urljoin, urlparse

import requests


class WebScrapeError(RuntimeError):
    """Raised when a remote page cannot be fetched or safely extracted."""


@dataclass(slots=True)
class ExtractedWebPage:
    url: str
    title: str = ""
    description: str = ""
    published_at: str = ""
    text: str = ""
    content_type: str = ""
    word_count: int = 0


class _ReadableHTMLParser(HTMLParser):
    _blocked_tags = {
        "button",
        "canvas",
        "footer",
        "form",
        "iframe",
        "nav",
        "noscript",
        "script",
        "style",
        "svg",
    }
    _content_tags = {
        "article",
        "blockquote",
        "dd",
        "div",
        "dt",
        "figcaption",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "main",
        "p",
        "pre",
        "td",
        "th",
    }
    _published_keys = {
        "article:published_time",
        "date",
        "datepublished",
        "dc.date",
        "pubdate",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.published_at = ""
        self.canonical_url = ""
        self.chunks: list[str] = []
        self._blocked_depth = 0
        self._capture_depth = 0
        self._title_depth = 0
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attributes = {key.casefold(): (value or "") for key, value in attrs}
        if tag in self._blocked_tags:
            self._blocked_depth += 1
        if self._blocked_depth:
            return
        if tag == "title":
            self._title_depth += 1
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or attributes.get("itemprop") or "").casefold()
            value = attributes.get("content", "").strip()
            if key in {"description", "og:description", "twitter:description"} and not self.description:
                self.description = value
            elif key in self._published_keys and not self.published_at:
                self.published_at = value
        if tag == "link" and "canonical" in attributes.get("rel", "").casefold().split():
            self.canonical_url = attributes.get("href", "").strip()
        if tag in self._content_tags:
            if self._capture_depth == 0:
                self._buffer = []
            self._capture_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self._blocked_tags and self._blocked_depth:
            self._blocked_depth -= 1
            return
        if self._blocked_depth:
            return
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in self._content_tags and self._capture_depth:
            self._capture_depth -= 1
            if self._capture_depth == 0:
                chunk = _clean_text(" ".join(self._buffer))
                if len(chunk) >= 24:
                    self.chunks.append(chunk)
                self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._blocked_depth:
            return
        if self._title_depth:
            self.title = _clean_text(f"{self.title} {data}")
        if self._capture_depth:
            self._buffer.append(data)


class WebPageScraper:
    """Bounded public-web fetcher with redirect and content validation."""

    allowed_content_types = {
        "application/json",
        "application/pdf",
        "application/xhtml+xml",
        "text/html",
        "text/plain",
        "text/xml",
    }
    max_redirects = 4

    def __init__(
        self,
        *,
        timeout_seconds: int = 8,
        max_download_bytes: int = 2 * 1024 * 1024,
        max_extracted_characters: int = 16_000,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_download_bytes = max_download_bytes
        self.max_extracted_characters = max_extracted_characters
        self.headers = dict(headers or {})

    def fetch(
        self,
        url: str,
        *,
        query: str = "",
        should_cancel: Callable[[], bool] | None = None,
    ) -> ExtractedWebPage:
        current_url = url
        session = requests.Session()
        try:
            for _redirect in range(self.max_redirects + 1):
                if should_cancel and should_cancel():
                    raise WebScrapeError("Web page fetch canceled.")
                self._validate_public_url(current_url)
                response = session.get(
                    current_url,
                    headers=self.headers,
                    timeout=self.timeout_seconds,
                    stream=True,
                    allow_redirects=False,
                )
                try:
                    if response.is_redirect or response.is_permanent_redirect:
                        target = response.headers.get("Location", "").strip()
                        if not target:
                            raise WebScrapeError("Web page returned an empty redirect.")
                        current_url = urljoin(current_url, target)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
                    if content_type not in self.allowed_content_types:
                        raise WebScrapeError(f"Unsupported web content type: {content_type or 'unknown'}.")
                    declared_size = _safe_int(response.headers.get("Content-Length", ""))
                    if declared_size > self.max_download_bytes:
                        raise WebScrapeError("Web page exceeds the download limit.")
                    payload = self._read_bounded(response, should_cancel)
                    page = self.extract(current_url, payload, content_type, response.encoding, query=query)
                    if not page.text:
                        raise WebScrapeError("Web page contained no readable text.")
                    return page
                finally:
                    response.close()
            raise WebScrapeError("Web page exceeded the redirect limit.")
        except requests.RequestException as exc:
            raise WebScrapeError(f"Web page is unavailable: {exc}") from exc
        finally:
            session.close()

    def extract(
        self,
        url: str,
        payload: bytes,
        content_type: str,
        encoding: str | None = None,
        *,
        query: str = "",
    ) -> ExtractedWebPage:
        if content_type == "application/pdf":
            return self._extract_pdf(url, payload, query)
        decoded = payload.decode(encoding or "utf-8", errors="replace")
        if content_type in {"text/html", "application/xhtml+xml"}:
            return self._extract_html(url, decoded, query)
        if content_type == "application/json":
            try:
                decoded = json.dumps(json.loads(decoded), ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                pass
        passages = [
            _clean_text(part)
            for part in re.split(r"(?:\r?\n){2,}|(?<=[.!?])\s+(?=[A-Z0-9])", decoded)
            if _clean_text(part)
        ]
        text = self._select_relevant_passages(passages, query)
        return ExtractedWebPage(
            url=url,
            text=text,
            content_type=content_type,
            word_count=len(text.split()),
        )

    def _extract_html(self, url: str, html: str, query: str) -> ExtractedWebPage:
        parser = _ReadableHTMLParser()
        try:
            parser.feed(html)
            parser.close()
        except (AssertionError, ValueError):
            pass
        canonical = urljoin(url, parser.canonical_url) if parser.canonical_url else url
        if not self._is_public_url(canonical, resolve=False):
            canonical = url
        chunks = _deduplicate_chunks(parser.chunks)
        if parser.description:
            chunks.insert(0, _clean_text(parser.description))
        text = self._select_relevant_passages(chunks, query)
        return ExtractedWebPage(
            url=canonical,
            title=_clean_text(parser.title),
            description=_clean_text(parser.description),
            published_at=_clean_text(parser.published_at),
            text=text,
            content_type="text/html",
            word_count=sum(len(chunk.split()) for chunk in chunks),
        )

    def _extract_pdf(self, url: str, payload: bytes, query: str) -> ExtractedWebPage:
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(payload))
            chunks = [
                _clean_text(page.extract_text() or "")
                for page in reader.pages[:12]
            ]
            metadata = reader.metadata
            title = _clean_text(str(metadata.title or "")) if metadata else ""
        except Exception as exc:  # noqa: BLE001
            raise WebScrapeError(f"PDF text could not be extracted: {exc}") from exc
        chunks = [chunk for chunk in chunks if chunk]
        text = self._select_relevant_passages(chunks, query)
        return ExtractedWebPage(
            url=url,
            title=title,
            text=text,
            content_type="application/pdf",
            word_count=sum(len(chunk.split()) for chunk in chunks),
        )

    def _read_bounded(
        self,
        response: requests.Response,
        should_cancel: Callable[[], bool] | None,
    ) -> bytes:
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=32 * 1024):
            if should_cancel and should_cancel():
                raise WebScrapeError("Web page fetch canceled.")
            if not chunk:
                continue
            size += len(chunk)
            if size > self.max_download_bytes:
                raise WebScrapeError("Web page exceeds the download limit.")
            chunks.append(chunk)
        return b"".join(chunks)

    def _select_relevant_passages(self, chunks: list[str], query: str) -> str:
        tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", query.casefold())
            if len(token) >= 3
        }
        ranked: list[tuple[int, int, str]] = []
        for position, chunk in enumerate(chunks):
            clean = _clean_text(chunk)
            if len(clean) < 24:
                continue
            lowered = clean.casefold()
            hits = sum(1 for token in tokens if token in lowered)
            ranked.append((hits, -position, clean))
        ranked.sort(reverse=True)
        selected: list[tuple[int, str]] = []
        length = 0
        for _hits, negative_position, chunk in ranked:
            remaining = self.max_extracted_characters - length
            if remaining < 24:
                break
            selected.append((-negative_position, chunk[:remaining]))
            length += min(len(chunk), remaining) + 2
        return "\n\n".join(chunk for _position, chunk in selected).strip()

    @classmethod
    def _validate_public_url(cls, url: str) -> None:
        if not cls._is_public_url(url, resolve=True):
            raise WebScrapeError("Blocked a non-public or invalid web address.")

    @staticmethod
    def _is_public_url(url: str, *, resolve: bool) -> bool:
        try:
            parsed = urlparse(url)
            if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
                return False
            if parsed.username or parsed.password or any(ord(char) < 32 for char in url):
                return False
            hostname = parsed.hostname.rstrip(".").casefold()
            if hostname == "localhost" or hostname.endswith(".localhost"):
                return False
            addresses: set[str]
            try:
                addresses = {str(ipaddress.ip_address(hostname))}
            except ValueError:
                if not resolve:
                    return True
                addresses = {
                    item[4][0]
                    for item in socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
                }
            return bool(addresses) and all(_is_public_ip(address) for address in addresses)
        except (OSError, UnicodeError, ValueError):
            return False


def _is_public_ip(address: str) -> bool:
    value = ipaddress.ip_address(address)
    return bool(value.is_global and not value.is_multicast)


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _deduplicate_chunks(chunks: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        normalized = re.sub(r"\W+", " ", chunk).casefold().strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        output.append(chunk)
    return output


def _safe_int(value: str) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
