from __future__ import annotations

import html
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl
from dotenv import load_dotenv

from src.data_provider.manifest import ArtifactRecord, upsert_artifact
from src.data_provider.store import partition_path, write_frame


NEWS_PROVIDER = "benzinga"
NEWS_GROUP = "news_benzinga"
NEWS_TIMEFRAME = "event"
NEWS_LOOKBACK_DAYS = 3
NEWS_API_URL = "https://api.massive.com/benzinga/v2/news"
NEW_YORK = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parents[2]

NEWS_COLUMNS: dict[str, pl.DataType] = {
    "provider": pl.Utf8,
    "news_id": pl.Utf8,
    "ticker": pl.Utf8,
    "published_utc": pl.Utf8,
    "published_et": pl.Utf8,
    "session_date": pl.Utf8,
    "bar_time_market": pl.Utf8,
    "minute_of_day": pl.Int64,
    "title": pl.Utf8,
    "teaser": pl.Utf8,
    "body_text": pl.Utf8,
    "article_ticker_count": pl.Int64,
    "article_tickers": pl.List(pl.Utf8),
    "channels": pl.List(pl.Utf8),
    "tags": pl.List(pl.Utf8),
    "url": pl.Utf8,
    "pdf_text": pl.Utf8,
    "raw_json": pl.Utf8,
}


def ensure_benzinga_news_cache(processed_root: Path, session_date: date) -> dict[str, Any]:
    required_dates = news_required_dates(session_date)
    existing = [day for day in required_dates if news_partition_path(processed_root, day).exists()]
    if len(existing) != len(required_dates):
        api_key = massive_api_key()
        if not api_key:
            return news_status(processed_root, session_date, status="missing_auth", message="Set MASSIVE_API_KEY or POLYGON_API_KEY to download Benzinga news.")
        try:
            rows = fetch_benzinga_news(required_dates[0], required_dates[-1] + timedelta(days=1), api_key)
            write_news_partitions(processed_root, required_dates, rows)
        except Exception as exc:
            return news_status(processed_root, session_date, status="error", message=str(exc))
    clear_news_cache()
    return news_status(processed_root, session_date)


def news_status(processed_root: Path, session_date: date, *, status: str | None = None, message: str = "") -> dict[str, Any]:
    required_dates = news_required_dates(session_date)
    ready = []
    missing = []
    rows = 0
    for day in required_dates:
        path = news_partition_path(processed_root, day)
        if path.exists():
            ready.append(day.isoformat())
            try:
                rows += pl.scan_parquet(str(path)).select(pl.len()).collect().item()
            except Exception:
                pass
        else:
            missing.append(day.isoformat())
    resolved_status = status or ("ready" if not missing else "missing")
    if status is None and missing:
        resolved_status = "missing"
    return {
        "label": "Benzinga news",
        "group": "news",
        "timeframe": NEWS_TIMEFRAME,
        "expected_sessions": len(required_dates),
        "ready_sessions": len(ready),
        "rows": rows,
        "status": resolved_status,
        "missing_sessions": missing[:10],
        "message": message,
    }


def news_at_payload(processed_root: Path, session_date: date, bar_time: str, tickers: list[str] | None = None) -> dict[str, Any]:
    cutoff = parse_market_bar_time(session_date, bar_time)
    cutoff_epoch = int(cutoff.timestamp())
    frame = load_news_window(processed_root, session_date)
    if frame.is_empty():
        return {"articles": [], "by_ticker": {}, "session_date": session_date.isoformat(), "bar_time": bar_time}
    if tickers:
        wanted = {ticker.strip().upper() for ticker in tickers if ticker.strip()}
        frame = frame.filter(pl.col("ticker").is_in(sorted(wanted)))
    if frame.is_empty():
        return {"articles": [], "by_ticker": {}, "session_date": session_date.isoformat(), "bar_time": bar_time}
    frame = (
        frame.with_columns(pl.col("published_utc").str.to_datetime(time_zone="UTC").dt.epoch(time_unit="s").alias("_published_epoch"))
        .filter(pl.col("_published_epoch") <= cutoff_epoch)
        .with_columns((pl.lit(cutoff_epoch) - pl.col("_published_epoch")).truediv(60).alias("news_age_minutes"))
        .with_columns(news_heat_expr("news_age_minutes").alias("news_recency"))
        .sort(["ticker", "published_utc"], descending=[False, True])
        .drop("_published_epoch")
    )
    rows = frame.to_dicts()
    by_ticker: dict[str, dict[str, Any]] = {}
    articles: list[dict[str, Any]] = []
    for row in rows:
        article = news_article_payload(row)
        articles.append(article)
        ticker = str(row.get("ticker") or "").upper()
        bucket = by_ticker.setdefault(
            ticker,
            {
                "live_news_recent": False,
                "live_news_recency": "none",
                "live_news_count": 0,
                "live_news_latest_title": "",
                "live_news_latest_time": "",
                "live_news_items": [],
            },
        )
        if len(bucket["live_news_items"]) < 8:
            bucket["live_news_items"].append(article)
        bucket["live_news_count"] += 1
        if not bucket["live_news_latest_title"]:
            bucket["live_news_latest_title"] = article["title"]
            bucket["live_news_latest_time"] = article["published_et"]
            bucket["live_news_recency"] = article["recency"]
            bucket["live_news_recent"] = article["recency"] in {"hot", "cold"}
    return {"articles": articles[:250], "by_ticker": by_ticker, "session_date": session_date.isoformat(), "bar_time": bar_time}


def news_required_dates(session_date: date) -> list[date]:
    return [session_date - timedelta(days=offset) for offset in range(NEWS_LOOKBACK_DAYS, -1, -1)]


def news_partition_path(processed_root: Path, session_date: date) -> Path:
    return partition_path(processed_root, NEWS_GROUP, NEWS_TIMEFRAME, session_date)


def massive_api_key() -> str:
    load_news_env()
    for name in ("MASSIVE_API_KEY", "MASSIVE_STOCK_API_KEY", "POLYGON_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


@lru_cache(maxsize=1)
def load_news_env() -> None:
    for env_path in (Path.cwd() / ".env", REPO_ROOT / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)
    load_dotenv(override=False)


def fetch_benzinga_news(start_date: date, end_date_exclusive: date, api_key: str) -> list[dict[str, Any]]:
    params = {
        "published.gte": start_date.isoformat(),
        "published.lt": end_date_exclusive.isoformat(),
        "limit": "50000",
        "sort": "published.asc",
        "apiKey": api_key,
    }
    url = f"{NEWS_API_URL}?{urllib.parse.urlencode(params)}"
    results: list[dict[str, Any]] = []
    page_count = 0
    while url and page_count < 20:
        page_count += 1
        payload = request_json(url)
        results.extend(payload.get("results") or [])
        next_url = str(payload.get("next_url") or "")
        url = with_api_key(next_url, api_key) if next_url else ""
    return normalize_benzinga_articles(results)


def request_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "quant-research-workbench/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Benzinga news request failed: HTTP {exc.code} {detail[:240]}") from exc


def with_api_key(url: str, api_key: str) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if not any(key.lower() == "apikey" for key, _ in params):
        params.append(("apiKey", api_key))
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(params)))


def normalize_benzinga_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for article in articles:
        published_utc = parse_news_timestamp(article.get("published"))
        if not published_utc:
            continue
        published_et = published_utc.astimezone(NEW_YORK)
        tickers = normalize_tickers(article.get("tickers") or article.get("stocks"))
        if not tickers:
            continue
        raw_body = str(article.get("body") or "")
        base = {
            "provider": NEWS_PROVIDER,
            "news_id": str(article.get("benzinga_id") or article.get("id") or stable_news_id(article)),
            "published_utc": published_utc.isoformat().replace("+00:00", "Z"),
            "published_et": published_et.isoformat(),
            "session_date": published_et.date().isoformat(),
            "bar_time_market": f"{published_et.hour:02d}:{published_et.minute:02d}",
            "minute_of_day": published_et.hour * 60 + published_et.minute,
            "title": clean_text(article.get("title")),
            "teaser": clean_text(article.get("teaser")),
            "body_text": html_to_text(raw_body),
            "article_ticker_count": len(tickers),
            "article_tickers": tickers,
            "channels": normalize_text_list(article.get("channels")),
            "tags": normalize_text_list(article.get("tags")),
            "url": str(article.get("url") or ""),
            "pdf_text": extract_pdf_text_if_needed(str(article.get("url") or ""), raw_body),
            "raw_json": json.dumps(article, ensure_ascii=True, sort_keys=True),
        }
        for ticker in tickers:
            key = (base["news_id"], ticker)
            if key in seen:
                continue
            seen.add(key)
            rows.append({**base, "ticker": ticker})
    return rows


def write_news_partitions(processed_root: Path, required_dates: list[date], rows: list[dict[str, Any]]) -> None:
    rows_by_date: dict[str, list[dict[str, Any]]] = {day.isoformat(): [] for day in required_dates}
    for row in rows:
        session = str(row.get("session_date") or "")
        if session in rows_by_date:
            rows_by_date[session].append(row)
    for session_text, session_rows in rows_by_date.items():
        frame = news_frame(session_rows)
        path = news_partition_path(processed_root, date.fromisoformat(session_text))
        write_frame(path, frame)
        upsert_artifact(
            processed_root,
            ArtifactRecord(
                group=NEWS_GROUP,
                timeframe=NEWS_TIMEFRAME,
                session_date=session_text,
                path=str(path),
                rows=frame.height,
                columns=frame.columns,
                built_at=datetime.now().isoformat(timespec="seconds"),
            ),
        )


def news_frame(rows: list[dict[str, Any]]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(schema=NEWS_COLUMNS)
    return pl.DataFrame(rows, schema=NEWS_COLUMNS).sort(["published_utc", "ticker"])


@lru_cache(maxsize=16)
def cached_news_window(processed_root_text: str, session_date_text: str, signature: tuple[tuple[str, int], ...]) -> pl.DataFrame:
    del signature
    processed_root = Path(processed_root_text)
    session_date = date.fromisoformat(session_date_text)
    frames = []
    for day in news_required_dates(session_date):
        path = news_partition_path(processed_root, day)
        if path.exists():
            try:
                frames.append(pl.read_parquet(path))
            except Exception:
                continue
    return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame(schema=NEWS_COLUMNS)


def load_news_window(processed_root: Path, session_date: date) -> pl.DataFrame:
    signature = []
    for day in news_required_dates(session_date):
        path = news_partition_path(processed_root, day)
        try:
            signature.append((str(path), path.stat().st_mtime_ns if path.exists() else 0))
        except OSError:
            signature.append((str(path), 0))
    return cached_news_window(str(processed_root), session_date.isoformat(), tuple(signature))


def clear_news_cache() -> None:
    cached_news_window.cache_clear()


def parse_market_bar_time(session_date: date, bar_time: str) -> datetime:
    match = re.match(r"^(\d{1,2}):(\d{2})", str(bar_time or ""))
    if not match:
        return datetime.combine(session_date, time(4, 0), tzinfo=NEW_YORK)
    hour = min(23, max(0, int(match.group(1))))
    minute = min(59, max(0, int(match.group(2))))
    return datetime.combine(session_date, time(hour, minute), tzinfo=NEW_YORK)


def parse_news_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def news_heat_expr(column: str) -> pl.Expr:
    # Product-wide news-temperature contract: hot is neon red (<= 4h), cold is
    # neon blue (> 4h and <= 24h), and old is neutral gray (> 24h).
    return (
        pl.when(pl.col(column) <= 240).then(pl.lit("hot"))
        .when(pl.col(column) <= 1440).then(pl.lit("cold"))
        .otherwise(pl.lit("old"))
    )


def news_article_payload(row: dict[str, Any]) -> dict[str, Any]:
    article_tickers = row.get("article_tickers") or raw_article_tickers(row.get("raw_json"))
    return {
        "age_minutes": float(row.get("news_age_minutes") or 0),
        "body_text": row.get("body_text") or "",
        "channels": row.get("channels") or [],
        "pdf_text": row.get("pdf_text") or "",
        "published_et": row.get("published_et") or "",
        "recency": row.get("news_recency") or "old",
        "tags": row.get("tags") or [],
        "teaser": row.get("teaser") or "",
        "ticker": row.get("ticker") or "",
        "ticker_count": int(row.get("article_ticker_count") or len(article_tickers) or 1),
        "tickers": article_tickers or [str(row.get("ticker") or "")],
        "title": row.get("title") or "",
        "url": row.get("url") or "",
    }


def normalize_tickers(value: Any) -> list[str]:
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, list):
        items = value
    else:
        items = []
    return sorted({str(item).strip().upper() for item in items if str(item).strip()})


def raw_article_tickers(raw_json: Any) -> list[str]:
    try:
        article = json.loads(str(raw_json or "{}"))
    except json.JSONDecodeError:
        return []
    return normalize_tickers(article.get("tickers") or article.get("stocks"))


def normalize_text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [clean_text(value)] if value.strip() else []
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    return []


def clean_text(value: Any) -> str:
    return html.unescape(str(value or "")).replace("\u00a0", " ").strip()


def html_to_text(value: str) -> str:
    text = re.sub(r"<\s*br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"</\s*p\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def stable_news_id(article: dict[str, Any]) -> str:
    source = f"{article.get('published')}|{article.get('title')}|{article.get('url')}"
    return hashlib.sha1(source.encode("utf-8", errors="ignore")).hexdigest()


def extract_pdf_text_if_needed(url: str, body_html: str = "") -> str:
    pdf_url = first_pdf_url(url, body_html)
    if not pdf_url:
        return ""
    try:
        request = urllib.request.Request(pdf_url, headers={"User-Agent": "quant-research-workbench/1.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            data = response.read(12_000_000)
        if "pdf" not in content_type and not data.startswith(b"%PDF"):
            return ""
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    except Exception:
        return ""


def first_pdf_url(url: str, body_html: str) -> str:
    if url and ".pdf" in url.lower():
        return url
    match = re.search(r"https?://[^\"'\s<>]+\.pdf(?:\?[^\"'\s<>]+)?", body_html or "", flags=re.IGNORECASE)
    return html.unescape(match.group(0)) if match else ""
