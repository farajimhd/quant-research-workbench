from __future__ import annotations

import html
import json
import random
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import NewsArticleForClassification


@dataclass(frozen=True)
class HistoricalNewsArticle:
    article_id: str
    provider: str
    published_at: str
    title: str
    teaser: str
    body_text: str
    extracted_text: str
    article_url: str
    publisher_name: str
    tickers: list[str]
    channels: list[str]
    tags: list[str]
    source_path: str

    def to_classification_article(self) -> NewsArticleForClassification:
        return NewsArticleForClassification(
            source=self.provider,
            provider_article_id=self.article_id,
            canonical_article_id=f"{self.provider}:{self.article_id}",
            published_at=self.published_at,
            title=self.title,
            teaser=self.teaser,
            body_text=self.body_text,
            extracted_text=self.extracted_text,
            article_url=self.article_url,
            publisher_name=self.publisher_name,
            tickers=self.tickers,
            channels=self.channels,
            tags=self.tags,
            keywords=self.tags,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "article_id": self.article_id,
            "provider": self.provider,
            "published_at": self.published_at,
            "title": self.title,
            "teaser": self.teaser,
            "body_text": self.body_text,
            "extracted_text": self.extracted_text,
            "article_url": self.article_url,
            "publisher_name": self.publisher_name,
            "tickers": self.tickers,
            "channels": self.channels,
            "tags": self.tags,
            "source_path": self.source_path,
        }


def load_historical_articles(
    raw_root: Path,
    provider: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[HistoricalNewsArticle]:
    rows: list[HistoricalNewsArticle] = []
    for path in raw_root.rglob("*.json"):
        path_provider = infer_provider(path)
        if provider and path_provider != provider.lower():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in normalize_payload(payload):
            published = parse_dt(first_nonempty(item, "published", "published_utc", "created_at", "last_updated"))
            if not published:
                continue
            if start_date and published.date() < start_date:
                continue
            if end_date and published.date() > end_date:
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            pdf_text = "\n".join(str(pdf.get("text", "")) for pdf in item.get("pdfs", []) if isinstance(pdf, dict))
            rows.append(
                HistoricalNewsArticle(
                    article_id=str(first_nonempty(item, "benzinga_id", "id", "article_id") or path.stem),
                    provider=path_provider,
                    published_at=published.isoformat(),
                    title=title,
                    teaser=clean_html(str(item.get("teaser") or item.get("description") or "")),
                    body_text=clean_html(str(item.get("body") or item.get("content") or "")),
                    extracted_text=clean_html(pdf_text),
                    article_url=str(item.get("url") or item.get("article_url") or ""),
                    publisher_name=str(first_nonempty(item, "publisher", "publisher_name", "author") or ""),
                    tickers=normalize_tickers(item.get("tickers") or ticker_from_insights(item.get("insights"))),
                    channels=normalize_strings(item.get("channels")),
                    tags=normalize_strings(item.get("tags")),
                    source_path=str(path),
                )
            )
    rows.sort(key=lambda row: (row.published_at, row.provider, row.article_id, row.source_path))
    return rows


def sample_articles(articles: list[HistoricalNewsArticle], sample_size: int, seed: int) -> list[HistoricalNewsArticle]:
    if sample_size <= 0 or len(articles) <= sample_size:
        return list(articles)
    rng = random.Random(seed)
    selected = rng.sample(articles, sample_size)
    selected.sort(key=lambda row: (row.published_at, row.article_id))
    return selected


def load_articles_jsonl(path: Path) -> list[HistoricalNewsArticle]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        rows.append(
            HistoricalNewsArticle(
                article_id=str(item.get("article_id", "")),
                provider=str(item.get("provider", "")),
                published_at=str(item.get("published_at", "")),
                title=str(item.get("title", "")),
                teaser=str(item.get("teaser", "")),
                body_text=str(item.get("body_text", "")),
                extracted_text=str(item.get("extracted_text", "")),
                article_url=str(item.get("article_url", "")),
                publisher_name=str(item.get("publisher_name", "")),
                tickers=normalize_tickers(item.get("tickers")),
                channels=normalize_strings(item.get("channels")),
                tags=normalize_strings(item.get("tags")),
                source_path=str(item.get("source_path", "")),
            )
        )
    return rows


def article_key(article: HistoricalNewsArticle | dict[str, Any]) -> str:
    if isinstance(article, HistoricalNewsArticle):
        return f"{article.provider}:{article.article_id}:{article.source_path}"
    return f"{article.get('provider','')}:{article.get('article_id','')}:{article.get('source_path','')}"


def normalize_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return [item for item in payload["results"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def infer_provider(path: Path) -> str:
    parts = {part.lower() for part in path.parts}
    if "benzinga" in parts:
        return "benzinga"
    if "massive" in parts:
        return "massive"
    return "unknown"


def first_nonempty(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", []):
            return value
    return None


def clean_html(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def ticker_from_insights(insights: Any) -> list[str]:
    if not isinstance(insights, list):
        return []
    return [str(item.get("ticker", "")) for item in insights if isinstance(item, dict)]


def normalize_tickers(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    tickers = []
    for value in values:
        text = str(value).strip().upper()
        if text and text not in tickers:
            tickers.append(text)
    return tickers


def normalize_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    rows = []
    for value in values:
        text = str(value).strip()
        if text and text not in rows:
            rows.append(text)
    return rows


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
