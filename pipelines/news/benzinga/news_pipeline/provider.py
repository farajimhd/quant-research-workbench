from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib import error, parse, request

from pipelines.news.benzinga.news_benzinga_normalize import parse_provider_datetime
from pipelines.news.benzinga.news_pipeline.config import BenzingaProviderRuntimeConfig


RETRY_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class BenzingaProviderConfig:
    endpoint_url: str
    api_key: str
    page_limit: int = 1_000
    max_pages: int = 1_000

    @classmethod
    def from_env(cls) -> "BenzingaProviderConfig":
        cfg = BenzingaProviderRuntimeConfig.from_env()
        return cls(endpoint_url=cfg.endpoint_url, api_key=cfg.api_key, page_limit=cfg.page_limit, max_pages=cfg.max_pages)


@dataclass(frozen=True, slots=True)
class BenzingaFetchResult:
    items: list[dict[str, Any]]
    pages: int
    saturated: bool
    next_url: str


@dataclass(frozen=True, slots=True)
class BenzingaProbeResult:
    has_news: bool
    rows_seen: int
    pages: int


@dataclass(frozen=True, slots=True)
class MarketStatusResult:
    raw: dict[str, Any]
    market: str
    early_hours: bool
    after_hours: bool
    server_time: str
    fetched_at_utc: datetime


class BenzingaProviderClient:
    def __init__(self, config: BenzingaProviderConfig | None = None) -> None:
        self.config = config or BenzingaProviderConfig.from_env()
        if not self.config.api_key:
            raise RuntimeError("MASSIVE_API_KEY is required for Benzinga provider fetches")

    def fetch_window(self, start_utc: datetime, end_utc: datetime) -> BenzingaFetchResult:
        next_url: str | None = build_benzinga_url(self.config, start_utc, end_utc)
        pages = 0
        items: list[dict[str, Any]] = []
        while next_url and pages < self.config.max_pages:
            pages += 1
            response = fetch_json(next_url)
            for item in response.get("results") or []:
                if isinstance(item, dict):
                    items.append(item)
            next_url = response.get("next_url")
            if next_url:
                next_url = append_api_key(str(next_url), self.config.api_key)
        return BenzingaFetchResult(items=items, pages=pages, saturated=bool(next_url), next_url=str(next_url or ""))

    def probe_window(self, start_utc: datetime, end_utc: datetime) -> BenzingaProbeResult:
        """Cheap existence probe for coverage validation.

        This intentionally asks for only one provider row. It answers "does the
        provider currently have any news in this interval?" without downloading
        or logging article data.
        """

        probe_config = BenzingaProviderConfig(
            endpoint_url=self.config.endpoint_url,
            api_key=self.config.api_key,
            page_limit=1,
            max_pages=1,
        )
        response = fetch_json(build_benzinga_url(probe_config, start_utc, end_utc))
        rows_seen = sum(1 for item in response.get("results") or [] if isinstance(item, dict))
        return BenzingaProbeResult(has_news=rows_seen > 0, rows_seen=rows_seen, pages=1)


class MassiveMarketStatusClient:
    def __init__(self, *, endpoint_url: str, api_key: str) -> None:
        self.endpoint_url = endpoint_url
        self.api_key = api_key
        if not self.api_key:
            raise RuntimeError("MASSIVE_API_KEY is required for market status fetches")

    def fetch_now(self) -> MarketStatusResult:
        response = fetch_json(append_api_key(self.endpoint_url, self.api_key))
        return MarketStatusResult(
            raw=response,
            market=str(response.get("market") or "").strip().lower(),
            early_hours=parse_bool(response.get("earlyHours")),
            after_hours=parse_bool(response.get("afterHours")),
            server_time=str(response.get("serverTime") or ""),
            fetched_at_utc=datetime.now(UTC),
        )


def build_benzinga_url(config: BenzingaProviderConfig, start_utc: datetime, end_utc: datetime) -> str:
    params = {
        "published.gte": to_provider_time(start_utc),
        "published.lt": to_provider_time(end_utc),
        "limit": str(config.page_limit),
        "sort": "published.asc",
        "apiKey": config.api_key,
    }
    separator = "&" if "?" in config.endpoint_url else "?"
    return config.endpoint_url.rstrip("?&") + separator + parse.urlencode(params)


def append_api_key(url: str, api_key: str) -> str:
    if "apiKey=" in url:
        return url
    return url + ("&" if "?" in url else "?") + parse.urlencode({"apiKey": api_key})


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def fetch_json(url: str) -> dict[str, Any]:
    req = request.Request(url, headers={"User-Agent": "quant-research-workbench-benzinga-pipeline/1.0"})
    body = ""
    for attempt in range(1, 5):
        try:
            with request.urlopen(req, timeout=60) as response:  # noqa: S310
                body = response.read().decode("utf-8", errors="replace")
                break
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code not in RETRY_HTTP_CODES or attempt >= 4:
                raise RuntimeError(f"Massive Benzinga HTTP {exc.code}: {body}") from exc
            time.sleep(provider_retry_sleep_seconds(exc, attempt))
        except (TimeoutError, error.URLError):
            if attempt >= 4:
                raise
            time.sleep(provider_retry_sleep_seconds(None, attempt))
    value = json.loads(body)
    if not isinstance(value, dict):
        raise RuntimeError("Massive Benzinga response was not a JSON object")
    return value


def provider_retry_sleep_seconds(exc: error.HTTPError | None, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After", "") if exc is not None else ""
    parsed = parse_retry_after_seconds(retry_after)
    if parsed is not None:
        return min(300.0, parsed)
    return min(60.0, 1.0 * (2 ** (attempt - 1)))


def parse_retry_after_seconds(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text).astimezone(UTC)
        except Exception:
            try:
                parsed = parse_provider_datetime(text)
            except Exception:
                return None
        return max(0.0, (parsed - datetime.now(UTC)).total_seconds())


def to_provider_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
