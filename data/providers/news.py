"""Berita RSS/Atom dari sumber yang dikonfigurasi.

Artikel adalah DATA TIDAK TEPERCAYA: hanya metadata (sumber, judul, URL, waktu publikasi)
yang disimpan; tag HTML dibuang; panjang dibatasi; XML diparse dengan defusedxml.
Isi artikel tidak pernah dijadikan instruksi bagi LLM.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from defusedxml import ElementTree as SafeET
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from config.common import ConfigError, VerificationMeta, load_yaml
from core.redaction import redact_exception
from core.timeutil import to_utc, utc_now

MAX_TITLE_CHARS = 300
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}
_ATOM_NS = "{http://www.w3.org/2005/Atom}"


class NewsSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    url: HttpUrl
    kind: Literal["rss", "atom", "auto"] = "auto"
    enabled: bool = True


class NewsSourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    meta: VerificationMeta
    sources: list[NewsSource] = []

    @property
    def enabled_sources(self) -> list[NewsSource]:
        return [s for s in self.sources if s.enabled]


def load_news_sources(path: Path) -> NewsSourcesConfig:
    try:
        return NewsSourcesConfig.model_validate(load_yaml(path))
    except ValueError as exc:
        raise ConfigError(f"news_sources tidak valid ({path}): {exc}") from exc


@dataclass(frozen=True, slots=True)
class NewsItem:
    source: str
    title: str
    url: str
    published_at: datetime | None
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class NewsFetchResult:
    items: tuple[NewsItem, ...]
    errors: dict[str, str]  # nama sumber -> pesan error teredaksi
    sources_tried: tuple[str, ...]

    @property
    def all_failed(self) -> bool:
        return bool(self.sources_tried) and len(self.errors) == len(self.sources_tried)


class NewsFetchError(RuntimeError):
    pass


class TextFetcher(Protocol):
    async def __call__(self, url: str) -> str: ...


def make_httpx_fetcher(
    *, timeout_seconds: float = 15.0, max_bytes: int = DEFAULT_MAX_BYTES
) -> TextFetcher:
    async def fetch(url: str) -> str:
        headers = {"User-Agent": "stock-signal-bot/0.1 (+rss reader)"}
        async with httpx.AsyncClient(
            timeout=timeout_seconds, follow_redirects=True, headers=headers
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise NewsFetchError(f"respons melebihi batas {max_bytes} byte")
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
        return b"".join(chunks).decode(encoding, errors="replace")

    return fetch


# ------------------------------------------------------------------------------ parsing


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", value))
    text = _WS_RE.sub(" ", text).strip()
    return text[:MAX_TITLE_CHARS]


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise ValueError("URL harus http(s) absolut")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        return to_utc(parsedate_to_datetime(text))
    except (TypeError, ValueError):
        pass
    try:
        return to_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _child_text(node, *names: str) -> str | None:
    for name in names:
        child = node.find(name)
        if child is not None:
            # itertext menyertakan teks elemen anak (mis. markup HTML yang tidak di-escape).
            text = "".join(child.itertext()).strip()
            if text:
                return text
    return None


def parse_feed(
    xml_text: str, *, source_name: str, fetched_at: datetime, max_items: int = 50
) -> list[NewsItem]:
    """Parse RSS 2.0 atau Atom. Elemen tidak dikenal diabaikan; item tanpa URL sah dibuang."""
    try:
        root = SafeET.fromstring(
            xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text
        )
    except Exception as exc:  # noqa: BLE001 - defusedxml melempar beragam exception
        raise NewsFetchError(f"XML tidak valid: {redact_exception(exc)}") from None

    items: list[NewsItem] = []
    tag = root.tag.lower()
    if tag == "rss" or root.find("channel") is not None:
        channel = root.find("channel")
        entries = channel.findall("item") if channel is not None else []
        for entry in entries:
            link = _child_text(entry, "link", "guid")
            title = clean_text(_child_text(entry, "title"))
            published = parse_datetime(
                _child_text(entry, "pubDate", "{http://purl.org/dc/elements/1.1/}date")
            )
            _append(items, source_name, title, link, published, fetched_at)
    elif tag == f"{_ATOM_NS}feed" or tag.endswith("feed"):
        for entry in root.findall(f"{_ATOM_NS}entry") or root.findall("entry"):
            link = None
            for link_el in entry.findall(f"{_ATOM_NS}link") or entry.findall("link"):
                rel = link_el.get("rel", "alternate")
                if rel == "alternate" and link_el.get("href"):
                    link = link_el.get("href")
                    break
            title = clean_text(_child_text(entry, f"{_ATOM_NS}title", "title"))
            published = parse_datetime(
                _child_text(
                    entry, f"{_ATOM_NS}published", f"{_ATOM_NS}updated", "published", "updated"
                )
            )
            _append(items, source_name, title, link, published, fetched_at)
    else:
        raise NewsFetchError(f"format feed tidak dikenal (root <{root.tag}>)")
    return items[:max_items]


def _append(
    items: list[NewsItem],
    source: str,
    title: str,
    link: str | None,
    published: datetime | None,
    fetched_at: datetime,
) -> None:
    if not title or not link:
        return
    try:
        url = canonical_url(link)
    except ValueError:
        return
    items.append(
        NewsItem(source=source, title=title, url=url, published_at=published, fetched_at=fetched_at)
    )


def dedupe(items: Iterable[NewsItem]) -> list[NewsItem]:
    seen_urls: set[str] = set()
    seen_titles: set[tuple[str, str]] = set()
    out: list[NewsItem] = []
    for item in items:
        title_key = (item.source, item.title.casefold())
        if item.url in seen_urls or title_key in seen_titles:
            continue
        seen_urls.add(item.url)
        seen_titles.add(title_key)
        out.append(item)
    return out


class NewsProvider:
    def __init__(
        self,
        sources: Iterable[NewsSource],
        *,
        fetcher: TextFetcher | None = None,
        clock: Callable[[], datetime] = utc_now,
        max_items_per_source: int = 30,
    ) -> None:
        self._sources = [s for s in sources if s.enabled]
        self._fetch = fetcher or make_httpx_fetcher()
        self._clock = clock
        self._max_items = max_items_per_source

    @property
    def source_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self._sources)

    async def fetch_all(self) -> NewsFetchResult:
        items: list[NewsItem] = []
        errors: dict[str, str] = {}
        for source in self._sources:
            fetched_at = self._clock()
            try:
                text = await self._fetch(str(source.url))
                items.extend(
                    parse_feed(
                        text,
                        source_name=source.name,
                        fetched_at=fetched_at,
                        max_items=self._max_items,
                    )
                )
            except Exception as exc:  # noqa: BLE001 - kegagalan satu sumber tidak menggagalkan yang lain
                errors[source.name] = redact_exception(exc)
        deduped = dedupe(items)
        deduped.sort(
            key=lambda i: (
                i.published_at is None,
                -(i.published_at.timestamp() if i.published_at else 0),
            )
        )
        return NewsFetchResult(items=tuple(deduped), errors=errors, sources_tried=self.source_names)
