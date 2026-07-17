from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class NewsSummary:
    provider: str
    provider_article_id: str
    canonical_news_id: str
    published_at_utc: str
    title: str
    teaser: str
    article_url: str
    tickers: list[str]
    channels: list[str]
    provider_tags: list[str]
    content_quality_flags: list[str]
    is_title_only: int
    has_body: int
    has_external_text: int
    has_pdf: int
    external_fetch_status: str
    pdf_extract_status: str
    normalizer_version: str
    text_hash: str


class NewsMemoryState:
    def __init__(self, history_limit: int, *, metadata_retention_hours: float = 24.0) -> None:
        self._history_limit = max(100, history_limit)
        self._retention = timedelta(hours=max(0.0, float(metadata_retention_hours)))
        self._lock = asyncio.Lock()
        self._recent: deque[NewsSummary] = deque(maxlen=self._history_limit)
        self._by_ticker: dict[str, deque[NewsSummary]] = defaultdict(lambda: deque(maxlen=self._history_limit))
        self._seen: dict[str, datetime] = {}
        self._revision = 0

    async def add_rows(self, rows: list[dict[str, Any]]) -> int:
        return await self.upsert_rows(rows)

    async def upsert_rows(self, rows: list[dict[str, Any]]) -> int:
        added = 0
        updated = False
        async with self._lock:
            self._prune_locked(datetime.now(UTC))
            for row in rows:
                key = str(row.get("canonical_news_id") or "")
                if not key:
                    continue
                summary = row_to_summary(row)
                updated = True
                summary_time = parse_dt(summary.published_at_utc) or datetime.now(UTC)
                is_new = key not in self._seen
                self._seen[key] = summary_time
                self._recent.appendleft(summary)
                for ticker in summary.tickers:
                    self._by_ticker[ticker.upper()].appendleft(summary)
                if is_new:
                    added += 1
            if updated:
                self._revision += 1
        return added

    async def recent_snapshot(self, limit: int = 250) -> dict[str, Any]:
        async with self._lock:
            self._prune_locked(datetime.now(UTC))
            rows = latest_unique(list(self._recent), max(1, min(limit, self._history_limit)))
            return {
                "as_of": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "revision": self._revision,
                "row_count": len(rows),
                "total_articles": len(self._seen),
                "rows": [asdict(row) for row in rows],
            }

    async def ticker_snapshot(self, ticker: str, limit: int = 100) -> dict[str, Any]:
        key = ticker.upper()
        now = datetime.now(UTC)
        async with self._lock:
            self._prune_locked(now)
            rows = latest_unique(list(self._by_ticker.get(key, [])), max(1, min(limit, self._history_limit)))
            revision = self._revision
        parsed_times = [parse_dt(row.published_at_utc) for row in rows]
        return {
            "as_of": now.isoformat().replace("+00:00", "Z"),
            "revision": revision,
            "ticker": key,
            "news_count_5m": sum(1 for value in parsed_times if value and value >= now - timedelta(minutes=5)),
            "news_count_30m": sum(1 for value in parsed_times if value and value >= now - timedelta(minutes=30)),
            "news_count_session": len(rows),
            "rows": [asdict(row) for row in rows],
        }

    async def stats(self) -> dict[str, Any]:
        async with self._lock:
            self._prune_locked(datetime.now(UTC))
            return {
                "recent_rows": len(self._recent),
                "ticker_keys": len(self._by_ticker),
                "seen_ids": len(self._seen),
                "metadata_retention_hours": self._retention.total_seconds() / 3600.0 if self._retention.total_seconds() > 0 else 0.0,
            }

    def _prune_locked(self, now: datetime) -> None:
        if self._retention.total_seconds() <= 0:
            return
        cutoff = now - self._retention
        self._recent = deque((row for row in self._recent if keep_summary(row, cutoff)), maxlen=self._history_limit)
        empty_tickers: list[str] = []
        for ticker, rows in self._by_ticker.items():
            self._by_ticker[ticker] = deque((row for row in rows if keep_summary(row, cutoff)), maxlen=self._history_limit)
            if not self._by_ticker[ticker]:
                empty_tickers.append(ticker)
        for ticker in empty_tickers:
            self._by_ticker.pop(ticker, None)
        self._seen = {key: value for key, value in self._seen.items() if value >= cutoff}


def row_to_summary(row: dict[str, Any]) -> NewsSummary:
    return NewsSummary(
        provider=str(row.get("provider") or "benzinga"),
        provider_article_id=str(row.get("provider_article_id") or ""),
        canonical_news_id=str(row.get("canonical_news_id") or ""),
        published_at_utc=str(row.get("published_at_utc") or ""),
        title=str(row.get("title") or ""),
        teaser=str(row.get("teaser") or ""),
        article_url=str(row.get("article_url") or ""),
        tickers=[str(item).upper() for item in row.get("tickers") or [] if str(item)],
        channels=[str(item) for item in row.get("channels") or [] if str(item)],
        provider_tags=[str(item) for item in row.get("provider_tags") or [] if str(item)],
        content_quality_flags=[str(item) for item in row.get("content_quality_flags") or [] if str(item)],
        is_title_only=int(row.get("is_title_only") or 0),
        has_body=int(row.get("has_body") or 0),
        has_external_text=int(row.get("has_external_text") or 0),
        has_pdf=int(row.get("has_pdf") or 0),
        external_fetch_status=str(row.get("external_fetch_status") or ""),
        pdf_extract_status=str(row.get("pdf_extract_status") or ""),
        normalizer_version=str(row.get("normalizer_version") or ""),
        text_hash=str(row.get("text_hash") or ""),
    )


def latest_unique(rows: list[NewsSummary], limit: int) -> list[NewsSummary]:
    output: list[NewsSummary] = []
    seen: set[str] = set()
    for row in rows:
        key = row.canonical_news_id
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(row)
        if len(output) >= limit:
            break
    return output


def keep_summary(row: NewsSummary, cutoff: datetime) -> bool:
    published = parse_dt(row.published_at_utc)
    return published is None or published >= cutoff


def parse_dt(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        if "T" in text:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
        return datetime.fromisoformat(text.replace(" ", "T") + "+00:00").astimezone(UTC)
    except ValueError:
        return None
