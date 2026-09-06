"""Capture deterministic frontend UX review matrices with Playwright.

Use the current Python when Playwright is installed. Otherwise re-execute
through the Conda environment named by UI_REVIEW_CONDA_ENV (default: ml4t).
This launcher never installs packages or browser binaries.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable


THEMES = (
    "light", "slate", "parchment", "dawn", "harbor",
    "dark", "forest", "graphite", "ember", "amethyst",
)
SCALES = (0.8, 0.9, 1.0, 1.1, 1.25)


def registered_pages() -> tuple[str, ...]:
    registry_path = Path(__file__).resolve().parents[1] / "src" / "app" / "routes.ts"
    source = registry_path.read_text(encoding="utf-8")
    match = re.search(r"export const PAGE_KEYS = \[(.*?)\] as const;", source, re.DOTALL)
    if not match:
        raise RuntimeError(f"PAGE_KEYS could not be read from {registry_path}")
    pages = tuple(re.findall(r'"([a-z0-9-]+)"', match.group(1)))
    if not pages or len(pages) != len(set(pages)):
        raise RuntimeError(f"PAGE_KEYS is empty or contains duplicates in {registry_path}")
    return pages


PAGES = registered_pages()
VIEWPORTS = {
    "normal": {"width": 1600, "height": 1000},
    "compact": {"width": 1280, "height": 720},
}
REPRESENTATIVE_PAGES = ("real-live-trading", "replay-trading", "backtest-trading", "backtest-debug", "research-workspace", "canvas-configuration", "data-catalog-configuration", "rule-set-configuration", "market-discovery-configuration", "portfolio-configuration", "oms-configuration", "account-configuration", "revision-configuration", "canvas-focus", "services-dashboard")
REPRESENTATIVE_THEMES = ("light", "dark")
TARGETED_SCALES = (0.8, 1.0, 1.25)

SERVICE_REVIEW_LABELS = {
    "bar-gpt": ("BarGPT", "model gateway"),
    "ibkr": ("IBKR", "broker gateway"),
    "model-gateway": ("Model Gateway", "model gateway"),
    "news": ("News", "news gateway"),
    "news-hypothesis": ("News Hypothesis", "model gateway"),
    "qmd": ("QMD Live", "market data gateway"),
    "qmd-history": ("QMD History", "historical gateway"),
    "reference": ("Reference", "reference gateway"),
    "sec": ("SEC", "filing gateway"),
    "text-embed": ("Text Embed", "embedding gateway"),
    "text-intelligence": ("Text Intelligence", "semantic gateway"),
}


def service_status_fixture(service_id: str) -> dict[str, Any]:
    label, kind = SERVICE_REVIEW_LABELS[service_id]
    recent_rows = {
        "qmd": [
            {"ts_utc": "2026-08-21T21:58:00Z", "status": "active", "ticker": "AAPL", "primitive_key": "breakout", "rows": 128, "detail": "Live event accepted and persisted"},
            {"ts_utc": "2026-08-21T21:57:30Z", "status": "rejected", "ticker": "MSFT", "primitive_key": "liquidity", "reject_reason": "Spread outside configured threshold"},
        ],
        "reference": [
            {"ts_utc": "2026-08-21T21:58:00Z", "status": "completed", "provider": "SEC", "table": "issuer_identity", "rows": 42, "detail": "Canonical identities reconciled"},
            {"ts_utc": "2026-08-21T21:57:30Z", "status": "warning", "provider": "FIGI", "issue_type": "mapping", "detail": "One mapping deferred for review"},
        ],
        "text-embed": [
            {"ts_utc": "2026-08-21T21:58:00Z", "status": "completed", "source": "news", "stage": "embedding", "embedding_rows_written": 256, "detail": "Embedding batch committed"},
            {"ts_utc": "2026-08-21T21:57:30Z", "status": "running", "source": "sec", "stage": "tokenization", "tokens_written": 4096, "detail": "Token batch in progress"},
        ],
        "ibkr": [
            {"ts_utc": "2026-08-21T21:58:00Z", "status": "completed", "event": "account_check", "account_id": "DU123456", "detail": "Account routing readiness verified"},
            {"ts_utc": "2026-08-21T21:57:30Z", "status": "completed", "event": "keepalive", "endpoint": "tickle", "detail": "Gateway session refreshed"},
        ],
    }.get(service_id, [
        {"ts_utc": "2026-08-21T21:58:00Z", "status": "completed", "event": "review_activity", "rows": 24, "detail": "Deterministic service activity fixture"},
    ])
    log_rows = [
        {"ts_utc": "2026-08-21T21:56:00Z", "level": "info", "event": "review_log", "title": "Review log", "detail": "Structured runtime log fixture", "source": service_id},
    ]
    if service_id == "news":
        log_rows.extend([
            {
                "ts_utc": "2026-08-21T21:59:40Z",
                "level": "info",
                "event": "publish_completed",
                "fields": {
                    "coverage_mode": "live",
                    "poll_id": "review-poll-12",
                    "items": [{
                        "canonical_news_id": "review-news-001",
                        "inserted_rows": 1,
                        "provider_article_id": "review-provider-001",
                        "publish_status": "inserted",
                        "published_at_utc": "2026-08-21T21:58:00Z",
                        "quality_flags": [],
                        "skipped_rows": 0,
                        "tickers": ["AAPL"],
                        "title": "AAPL expands deterministic research platform",
                    }],
                },
            },
            {
                "ts_utc": "2026-08-21T21:59:30Z",
                "level": "info",
                "event": "background_batch_completed",
                "fields": {
                    "article_count": 1,
                    "coverage_mode": "live_background",
                    "enriched_urls": 1,
                    "fetch_task_count": 1,
                    "poll_id": "review-poll-12",
                    "queue_size": 0,
                    "wall_seconds": 0.8,
                    "items": [{
                        "canonical_news_id": "review-news-001",
                        "domain_sample": ["example.invalid"],
                        "external_fetch_status": "downloaded",
                        "provider_article_id": "review-provider-001",
                        "requires_enrichment": True,
                        "tickers": ["AAPL"],
                        "title": "AAPL expands deterministic research platform",
                        "url_count": 1,
                        "url_sample": ["https://example.invalid/review-article"],
                    }],
                },
            },
            {
                "ts_utc": "2026-08-21T21:59:20Z",
                "level": "info",
                "event": "gap_fill_finished",
                "fields": {
                    "chunks": 2,
                    "coverage_id": "review-coverage-001",
                    "end_utc": "2026-08-21T21:45:00Z",
                    "flushed": 2,
                    "start_utc": "2026-08-21T21:15:00Z",
                    "status": "completed",
                    "total_chunks": 2,
                    "written_rows": 24,
                },
            },
        ])
    return {
        "checked_at_utc": "2026-08-21T22:00:00Z",
        "current_operation": {"phase": "serving", "status": "running", "rows": 128, "detail": "Serving deterministic review workload"},
        "database_tables": {"rows": [{"database": "review", "table": f"{service_id.replace('-', '_')}_events", "role": "live events", "rows": "12,480", "rows_today": "128", "latest_update": "2026-08-21 21:58:00", "status": "ok"}]},
        "errors": {},
        "header": {"service": service_id},
        "health": {"running": True, "source": "review_fixture", "host_role": "live"},
        "logs": {"rows": log_rows},
        "metrics": {"activity_status": "running", "processed": 128, "queued": 2, "filtered": 4, "failed": 0, "ingest_events": 128, "trades_per_sec": 42, "quotes_per_sec": 96, "bar_events": 18, "gap_count": 0, "poll_runs": 12, "gateway_status": "ready", "auth_status": "ready", "keepalive_status": "ready"},
        "online": True,
        "operations": {},
        "readiness": {
            "schema_version": 1,
            "liveness": {"status": "ready", "evidence": "Fixture heartbeat", "source": "ui_review"},
            "dependencies": {"status": "ready", "evidence": "Fixture dependencies", "source": "ui_review"},
            "data": {"status": "ready", "evidence": "Fixture database", "source": "ui_review"},
            "execution": {"status": "ready", "evidence": "Fixture execution", "source": "ui_review"},
        },
        "recent": recent_rows,
        "registry": {"base_url": "http://ui-review.invalid", "description": "Deterministic browser-review service contract.", "id": service_id, "kind": kind, "label": label},
        "snapshot": {
            "tasks": [{"name": "live processing", "kind": "processing", "status": "running", "rows": 128, "last_at": "2026-08-21T21:58:00Z", "detail": "Bounded live processing"}],
            "dependencies": [{"name": "review dependency", "status": "ready", "detail": "Deterministic dependency fixture"}],
        },
        "status": "running",
    }


def fulfill_json(body: str):
    def handler(route: Any) -> None:
        route.fulfill(content_type="application/json", body=body)
    return handler


def news_today_fixture() -> dict[str, Any]:
    row = {
        "article_url": "https://example.invalid/review-article",
        "author": "Review Desk",
        "body_chars": 420,
        "canonical_news_id": "review-news-001",
        "channels": ["markets", "technology"],
        "content_quality_flags": [],
        "downloaded_at_utc": "2026-08-21T21:59:05Z",
        "external_chars": 0,
        "external_fetch_status": "not_required",
        "full_text_chars": 420,
        "has_body": 1,
        "has_external_text": 0,
        "has_pdf": 0,
        "is_title_only": 0,
        "normalized_title": "AAPL expands deterministic research platform",
        "pdf_chars": 0,
        "pdf_extract_status": "not_required",
        "provider_article_id": "review-provider-001",
        "provider_tags": ["earnings", "platform"],
        "published_at_utc": "2026-08-21T21:58:00Z",
        "text_preview": "The company expanded its research platform with deterministic controls.",
        "ticker_link_count": 1,
        "ticker_link_sample": ["AAPL"],
        "tickers": ["AAPL"],
        "title": "AAPL expands deterministic research platform",
        "url_domain": "example.invalid",
    }
    return {
        "database": "review",
        "normalized_table": "normalized_news",
        "rows": [row],
        "sort": "desc",
        "summary": {"external_text": 0, "latest": row["published_at_utc"], "loaded_rows": 1, "multi_ticker_rows": 0, "no_ticker_rows": 0, "one_ticker_rows": 1, "pdf_rows": 0, "total_rows": 1, "with_ticker": 1},
        "ticker_table": "news_tickers",
        "window_end_utc": "2026-08-21T22:00:00Z",
        "window_start_utc": "2026-08-21T13:30:00Z",
    }


def news_detail_fixture() -> dict[str, Any]:
    row = news_today_fixture()["rows"][0] | {
        "body_text": "<p>AAPL expands deterministic research platform</p><p>The company expanded its research platform with deterministic controls. The update keeps source authority explicit &amp; preserves audit evidence.</p><p>KEY POINTS:</p><ul><li>One canonical contract</li><li>Bounded live processing</li></ul>",
        "normalizer_version": "ui-review-v1",
        "raw_artifact_path": "runtime://ui-review/news/review-news-001.json",
        "teaser": "The company expanded its research platform with deterministic controls.",
    }
    return {
        "canonical_news_id": "review-news-001",
        "database": "review",
        "normalized_table": "normalized_news",
        "row": row,
        "ticker_rows": [{"canonical_news_id": "review-news-001", "ticker": "AAPL"}],
        "ticker_table": "news_tickers",
    }


def sec_today_fixture() -> dict[str, Any]:
    row = {
        "accepted_at_utc": "2026-08-21T21:57:00Z",
        "acceptance_datetime_raw": "20260821175700",
        "accession_number": "0000320193-26-000001",
        "accession_number_compact": "000032019326000001",
        "activity_status": "filing",
        "cik": "0000320193",
        "company_name": "Apple Inc.",
        "document_rows": 1,
        "document_text_ready_rows": 1,
        "feed_status": "completed",
        "filing_date": "2026-08-21",
        "filing_detail_url": "https://example.invalid/sec/filing",
        "filing_id": "review-filing-001",
        "form_type": "8-K",
        "identity_bridge_count": 1,
        "identity_tickers": ["AAPL"],
        "issuer_name": "Apple Inc.",
        "primary_currency_code": "USD",
        "primary_document": "review-8k.htm",
        "primary_document_rows": 1,
        "primary_document_url": "https://example.invalid/sec/review-8k.htm",
        "primary_exchange_code": "NASDAQ",
        "primary_ticker": "AAPL",
        "report_date": "2026-08-21",
        "text_chars": 260,
        "text_rows": 1,
        "text_status": "ready",
        "xbrl_fact_rows": 1,
        "xbrl_frame_rows": 0,
    }
    return {
        "database": "review",
        "filing_table": "sec_filings",
        "document_table": "sec_filing_document_v2",
        "text_table": "sec_filing_text_v2",
        "rows": [row],
        "sort": "desc",
        "summary": {"document_rows": 1, "latest": row["accepted_at_utc"], "loaded_rows": 1, "text_rows": 1, "total_filings": 1, "with_documents": 1, "with_text": 1, "with_xbrl": 1, "xbrl_fact_rows": 1, "xbrl_frame_rows": 0},
        "histogram": {"bin_seconds": 900, "rows": [{"bucket_utc": "2026-08-21T21:45:00Z", "document_rows": 1, "filing_only_rows": 0, "text_rows": 1, "total_rows": 1, "xbrl_rows": 1}], "window_end_utc": "2026-08-21T22:00:00Z", "window_start_utc": "2026-08-21T13:30:00Z"},
        "window_end_utc": "2026-08-21T22:00:00Z",
        "window_start_utc": "2026-08-21T13:30:00Z",
    }


def sec_detail_fixture() -> dict[str, Any]:
    filing_row = sec_today_fixture()["rows"][0] | {"accepted_at_source": "SEC submissions", "source_file_name": "review-submission.json"}
    return {
        "accession_number": filing_row["accession_number"],
        "cik": filing_row["cik"],
        "database": "review",
        "filing_row": filing_row,
        "document_rows": [{
            "byte_size": 4096, "content_format": "html", "description": "Current report", "document_id": "review-document-001",
            "document_name": "review-8k.htm", "document_role": "primary", "document_type": "8-K", "document_url": filing_row["primary_document_url"],
            "extraction_status": "completed", "file_extension": "htm", "filing_id": filing_row["filing_id"], "has_normalized_text": 1,
            "mime_type": "text/html", "normalizer_version": "ui-review-v1", "payload_char_count": 320, "sequence_number": 1,
        }],
        "text_rows": [{
            "document_id": "review-document-001", "source_archive_member": "review-8k.htm", "text": "ITEM 8.01 OTHER EVENTS\n\nThis deterministic filing text preserves canonical source evidence and document lineage.\n\nThe review fixture verifies readable SEC presentation.",
            "text_char_count": 156, "text_kind": "primary_document", "text_sha256": "review-text-sha256",
        }],
        "company_fact_rows": [{"tag": "EntityCommonStockSharesOutstanding", "value": 1000}],
        "frame_rows": [],
        "identity_rows": [{"cik": filing_row["cik"], "ticker": "AAPL", "exchange_code": "NASDAQ"}],
        "identity_summary": {"identity_bridge_count": 1, "issuer_name": "Apple Inc.", "primary_currency_code": "USD", "primary_exchange_code": "NASDAQ", "primary_ticker": "AAPL"},
    }


def chart_history_fixture(session_date: str, symbol: str = "AAPL") -> dict[str, Any]:
    start = datetime.fromisoformat(f"{session_date}T10:00:00+00:00")
    history: list[dict[str, Any]] = []
    indicators: list[dict[str, Any]] = []
    previous_close = 100.0
    for index in range(220):
        bar_start = start + timedelta(minutes=index)
        bar_end = bar_start + timedelta(minutes=1)
        direction = 1 if index % 7 not in {0, 1} else -1
        open_price = previous_close
        close_price = open_price + direction * (0.04 + (index % 5) * 0.01)
        high = max(open_price, close_price) + 0.05
        low = min(open_price, close_price) - 0.04
        history.append({
            "bar_start": bar_start.isoformat(),
            "bar_end": bar_end.isoformat(),
            "open": round(open_price, 4),
            "high": round(high, 4),
            "low": round(low, 4),
            "close": round(close_price, 4),
            "volume": 1000 + index * 13,
            "is_closed": True,
            "session_date": session_date,
        })
        indicators.append({
            "bar_start": bar_start.isoformat(),
            "session_date": session_date,
            "vwap": round(100 + index * 0.002, 4),
            "macd_line": round((index % 17 - 8) * 0.01, 4),
            "macd_signal": round((index % 13 - 6) * 0.008, 4),
            "macd_histogram": round((index % 9 - 4) * 0.006, 4),
        })
        previous_close = close_price
    structure_events: list[dict[str, Any]] = []
    structure_timeframes = ("100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h")
    for timeframe_index, timeframe in enumerate(structure_timeframes, start=1):
        direction = 1 if timeframe_index % 2 else -1
        level_id = 1_000 + timeframe_index
        price = round(101.0 + timeframe_index * 0.2, 4)
        pivot_at = start + timedelta(minutes=25 + timeframe_index)
        promoted_at = pivot_at + timedelta(minutes=2)
        break_pivot_at = start + timedelta(minutes=75 + timeframe_index)
        broken_at = break_pivot_at + timedelta(minutes=2)
        structure_events.extend((
            {
                "algorithm_version": 9,
                "event_id": 10_000 + timeframe_index * 2,
                "sym": symbol,
                "level_id": level_id,
                "timeframe": timeframe,
                "event_kind": "level_promoted",
                "direction": direction,
                "price": price,
                "lower": price,
                "upper": price,
                "strength": 0.72,
                "confidence": 0.81,
                "lifecycle": "active",
                "pivot_at": pivot_at.isoformat(),
                "confirmed_at": promoted_at.isoformat(),
            },
            {
                "algorithm_version": 9,
                "event_id": 10_001 + timeframe_index * 2,
                "sym": symbol,
                "level_id": level_id,
                "timeframe": timeframe,
                "event_kind": "bos",
                "direction": direction,
                "price": price,
                "lower": price,
                "upper": price,
                "strength": 0.76,
                "confidence": 0.84,
                "lifecycle": "broken",
                "pivot_at": break_pivot_at.isoformat(),
                "confirmed_at": broken_at.isoformat(),
            },
        ))
    footprint_as_of = int((start + timedelta(minutes=200)).timestamp() * 1_000)
    structure_level_history = [
        {
            "level_id": 2_001,
            "confidence": 0.86,
            "created_at_ms": int((start + timedelta(minutes=20)).timestamp() * 1_000),
            "distance": 0.12,
            "evidence_score": 0.79,
            "hold_count": 3,
            "last_test_at_ms": int((start + timedelta(minutes=180)).timestamp() * 1_000),
            "lower": 104.7,
            "lifecycle": "active",
            "price": 104.75,
            "promotions": [{"timeframe": "1m", "promoted_at_ms": int((start + timedelta(minutes=30)).timestamp() * 1_000), "score": 0.82}],
            "footprint_session_date": session_date,
            "footprint_as_of_ms": footprint_as_of,
            "footprint": [
                {"offset_ticks": -1, "price": 104.74, "total_volume": 1_200, "buy_volume": 720, "sell_volume": 430, "neutral_volume": 50, "trade_count": 38, "largest_trade": 180},
                {"offset_ticks": 0, "price": 104.75, "total_volume": 1_650, "buy_volume": 980, "sell_volume": 610, "neutral_volume": 60, "trade_count": 51, "largest_trade": 240},
                {"offset_ticks": 1, "price": 104.76, "total_volume": 1_080, "buy_volume": 600, "sell_volume": 430, "neutral_volume": 50, "trade_count": 34, "largest_trade": 160},
            ],
            "total_volume": 3_930,
            "buy_volume": 2_300,
            "sell_volume": 1_470,
            "neutral_volume": 160,
            "trade_count": 123,
            "side": 1,
            "strength": 0.83,
            "touch_count": 4,
            "upper": 104.8,
        },
    ]
    return {
        "as_of": history[-1]["bar_end"],
        "earliest_session_date": session_date,
        "has_more": False,
        "has_more_in_session": False,
        "history": history,
        "indicator_provenance": {
            "as_of": history[-1]["bar_end"],
            "complete": True,
            "engine_version": "ui-review-fixture-v1",
            "source": {"tiers": ["deterministic_ui_fixture"]},
        },
        "indicators": indicators,
        "indicators_available": True,
        "market_signal_events": [],
        "next_before": "",
        "previous_session_before": "",
        "stage": "full",
        "structure_events": structure_events,
        "structure_level_history": structure_level_history,
        "ticker": symbol,
        "timeframe": "1m",
    }


def daily_chart_history_fixture(session_date: str, symbol: str = "AAPL") -> dict[str, Any]:
    end = datetime.fromisoformat(f"{session_date}T20:00:00+00:00")
    history: list[dict[str, Any]] = []
    close = 94.0
    for index in range(120):
        bar_start = end - timedelta(days=119 - index)
        # Leave the deterministic split session without a candle. This proves
        # that the corporate-action date remains on the axis during a genuine
        # daily-history coverage gap.
        if bar_start.date() == (end - timedelta(days=5)).date():
            continue
        open_price = close
        close = open_price + (0.7 if index % 6 not in {0, 1} else -0.45)
        history.append({
            "bar_start": bar_start.isoformat(),
            "bar_end": (bar_start + timedelta(hours=6)).isoformat(),
            "open": round(open_price, 4),
            "high": round(max(open_price, close) + 0.35, 4),
            "low": round(min(open_price, close) - 0.3, 4),
            "close": round(close, 4),
            "volume": 1_000_000 + index * 12_500,
            "is_closed": True,
            "session_date": bar_start.date().isoformat(),
        })
    return {
        "history": history,
        "indicators": [],
        "indicators_available": False,
        "market_signal_events": [],
        "structure_events": [],
        "structure_level_history": [],
        "has_more": False,
        "split_adjusted": True,
    }


def ensure_playwright() -> None:
    try:
        import playwright.sync_api  # noqa: F401
        return
    except ImportError:
        pass

    if os.environ.get("UI_REVIEW_CONDA_REEXEC") == "1":
        raise SystemExit(
            "Playwright is unavailable in both the original Python and the "
            "configured Conda environment."
        )
    conda = shutil.which("conda")
    if not conda:
        raise SystemExit(
            "Playwright is not installed in this Python and 'conda' was not found. "
            "Set UI_REVIEW_CONDA_ENV or use a Playwright-enabled Python."
        )

    environment = os.environ.get("UI_REVIEW_CONDA_ENV", "ml4t")
    child_env = os.environ.copy()
    child_env["UI_REVIEW_CONDA_REEXEC"] = "1"
    command = [
        conda, "run", "-n", environment, "python",
        str(Path(__file__).resolve()), *sys.argv[1:],
    ]
    raise SystemExit(subprocess.run(command, env=child_env, check=False).returncode)


def parse_viewport(value: str) -> tuple[str, dict[str, int]]:
    try:
        name, dimensions = value.split(":", 1)
        width, height = dimensions.lower().split("x", 1)
        viewport = {"width": int(width), "height": int(height)}
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "viewport must use NAME:WIDTHxHEIGHT, for example compact:1280x720"
        ) from exc
    if not name or viewport["width"] < 320 or viewport["height"] < 240:
        raise argparse.ArgumentTypeError("viewport name and usable dimensions are required")
    return name, viewport


def cartesian(
    pages: Iterable[str],
    themes: Iterable[str],
    scales: Iterable[float],
    viewports: dict[str, dict[str, int]],
) -> Iterable[dict[str, Any]]:
    for page in pages:
        for theme in themes:
            for scale in scales:
                for viewport_name, viewport in viewports.items():
                    yield {
                        "page": page,
                        "theme": theme,
                        "scale": scale,
                        "viewport_name": viewport_name,
                        "viewport": viewport,
                    }


def unique_scenarios(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = (
            item["page"], item["theme"], item["scale"], item["viewport_name"],
            item["viewport"]["width"], item["viewport"]["height"],
        )
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def build_scenarios(args: argparse.Namespace) -> list[dict[str, Any]]:
    viewports = dict(args.viewport or VIEWPORTS.items())
    requested_pages = tuple(args.page or ())
    requested_themes = tuple(args.theme or ())
    requested_scales = tuple(args.scale or ())

    if args.matrix == "exhaustive":
        return list(cartesian(
            requested_pages or PAGES,
            requested_themes or THEMES,
            requested_scales or SCALES,
            viewports,
        ))

    if args.mode == "targeted":
        return list(cartesian(
            requested_pages or ("real-live-trading",),
            requested_themes or REPRESENTATIVE_THEMES,
            requested_scales or TARGETED_SCALES,
            viewports,
        ))

    pages = requested_pages or PAGES
    themes = requested_themes or THEMES
    scales = requested_scales or SCALES
    baseline_theme = "light" if "light" in themes else themes[0]
    baseline_scale = 1.0 if 1.0 in scales else scales[0]
    representative_pages = tuple(page for page in REPRESENTATIVE_PAGES if page in pages)
    if not representative_pages:
        representative_pages = (pages[0],)
    representative_themes = tuple(
        theme for theme in REPRESENTATIVE_THEMES if theme in themes
    ) or (themes[0],)

    scenarios: list[dict[str, Any]] = []
    scenarios.extend(cartesian(pages, (baseline_theme,), (baseline_scale,), viewports))
    scenarios.extend(cartesian(
        representative_pages, themes, (baseline_scale,), viewports,
    ))
    scenarios.extend(cartesian(
        representative_pages, representative_themes, scales, viewports,
    ))
    return unique_scenarios(scenarios)


def slug_scale(scale: float) -> str:
    return str(scale).replace(".", "p")


def validate_price_zone_legend(
    page: Any,
    chart: Any,
    issues: list[str],
    interaction_screenshot: Path | None = None,
) -> None:
    price_legend = chart.locator('[data-chart-pane="price"] .chart-legend')
    if price_legend.count() != 1:
        issues.append("price chart does not expose one overlay legend")
        return
    price_legend.locator(".chart-legend-header").click()
    legend_text = price_legend.inner_text()
    active_timeframe = chart.locator(
        ".chart-timeframe-row .timeframe-button.active"
    ).inner_text().strip()
    for expected_level_indicator in (
        f"{active_timeframe} · Swing levels",
        f"{active_timeframe} · Structure breaks",
        "Level volume footprint",
    ):
        if expected_level_indicator not in legend_text:
            issues.append(f"price legend omits {expected_level_indicator}")
    configure_levels = price_legend.get_by_role(
        "button", name="Configure Current support & resistance"
    )
    if configure_levels.count() == 1:
        configure_levels.click()
        levels_editor = page.get_by_role(
            "dialog", name="Current support & resistance indicator settings"
        )
        if levels_editor.get_by_text("Connector label size", exact=True).count():
            issues.append("price-axis-only decision zones expose irrelevant label-size settings")
        if levels_editor.get_by_label("Shape").count():
            issues.append("borderless current zones expose a nonfunctional edge-style setting")
        nearest_count = levels_editor.get_by_role(
            "slider", name="Current support & resistance nearest levels per side"
        )
        if nearest_count.count() != 1:
            issues.append("current structure settings omit nearest-level count")
        else:
            nearest_count.evaluate("""element => {
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                setter.call(element, '2');
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
            }""")
            page.wait_for_timeout(150)
            nearest_count = levels_editor.get_by_role(
                "slider", name="Current support & resistance nearest levels per side"
            )
            if nearest_count.input_value() != "2":
                issues.append(
                    f"current structure nearest-level count does not update (value={nearest_count.input_value()})"
                )
            nearest_count.evaluate("""element => {
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
                setter.call(element, '3');
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
            }""")
        if levels_editor.get_by_role("slider", name="Current support & resistance opacity").count() != 1:
            issues.append("current structure settings omit confidence-region opacity")
        levels_editor.get_by_role("button", name="Close indicator settings").click()
    structure_guides = price_legend.get_by_role(
        "button", name=re.compile(r"^Guide QMD generic structure$", re.IGNORECASE)
    )
    if structure_guides.count() < 1:
        issues.append("generic structure levels do not expose a guide")
    else:
        structure_guides.first.click()
        guide = page.get_by_role(
            "dialog", name=re.compile(r"^How to read: QMD generic structure$", re.IGNORECASE)
        )
        guide_text = guide.inner_text()
        for expected_explanation in (
            "matching its current timeframe by default",
            "eye controls remain available",
            "sh and sl lines are bounded",
            "volume footprint remain a separate immediate level-book view",
        ):
            if expected_explanation not in guide_text.lower():
                issues.append(
                    f"generic structure guide omits current contract: {expected_explanation}"
                )
        if interaction_screenshot:
            page.screenshot(
                path=str(interaction_screenshot.with_name(
                    interaction_screenshot.stem + "__structure-guide.png"
                )),
                full_page=True,
            )
        guide.get_by_role("button", name="Close").click()
    active_layers = {
        f"{active_timeframe} · Swing levels",
        f"{active_timeframe} · Structure breaks",
    }
    inactive_layer_count = 0
    for layer_kind in ("Swing levels", "Structure breaks"):
        visibility_controls = price_legend.locator(
            f'button[aria-label$="· {layer_kind}"]'
        )
        for index in range(visibility_controls.count()):
            visibility = visibility_controls.nth(index)
            aria_label = visibility.get_attribute("aria-label") or ""
            label = aria_label.removeprefix("Show ").removeprefix("Hide ")
            if label in active_layers and not aria_label.startswith("Hide "):
                issues.append(f"{label} is not enabled by default")
            if label not in active_layers:
                inactive_layer_count += 1
            if not visibility.is_enabled():
                issues.append(f"{label} visibility control is disabled")
    if inactive_layer_count < 2:
        issues.append("structure legend does not expose toggleable nonmatching timeframe layers")
    for layer_kind in ("Swing levels", "Structure breaks"):
        legend_label = f"{active_timeframe} · {layer_kind}"
        configure_layer = price_legend.get_by_role(
            "button", name=f"Configure {legend_label}"
        )
        if configure_layer.count() != 1:
            issues.append(f"{legend_label} does not expose configuration")
            continue
        configure_layer.click()
        layer_editor = page.get_by_role(
            "dialog", name=f"{legend_label} indicator settings"
        )
        history = layer_editor.get_by_role(
            "slider", name=f"{legend_label} history bars"
        )
        if history.count() != 1:
            issues.append(f"{legend_label} omits its history control")
        elif history.input_value() != "20" or history.get_attribute("max") != "1000":
            issues.append(
                f"{legend_label} history is not the 20-to-1000 authority "
                f"(value={history.input_value()}, max={history.get_attribute('max')})"
            )
        shape = layer_editor.get_by_label("Shape")
        width = layer_editor.get_by_role("slider", name="Width")
        opacity = layer_editor.get_by_role(
            "slider", name=f"{legend_label} opacity"
        )
        if layer_kind == "Swing levels":
            if (
                shape.input_value() != "solid"
                or width.input_value() != "4"
                or opacity.input_value() != "50"
            ):
                issues.append(
                    f"{legend_label} does not default to a solid four-pixel line at 50% opacity"
                )
        elif shape.input_value() != "dashed" or width.input_value() != "1":
            issues.append(f"{legend_label} does not default to a dashed one-pixel line")
        if layer_editor.get_by_text(
            re.compile(r"(break|historical) label limit", re.IGNORECASE)
        ).count():
            issues.append(f"{legend_label} exposes a competing label-history limit")
        overflow = layer_editor.evaluate(
            "element => element.scrollWidth > element.clientWidth + 1"
        )
        if overflow:
            issues.append(f"{legend_label} settings overflow horizontally")
        connector_control = layer_editor.get_by_text(
            "Swing-to-break connectors", exact=True
        )
        if layer_kind == "Structure breaks" and connector_control.count() != 1:
            issues.append(f"{legend_label} omits connector visibility")
        if layer_kind == "Swing levels" and connector_control.count():
            issues.append(f"{legend_label} exposes an irrelevant connector control")
        layer_editor.get_by_role("button", name="Close indicator settings").click()
    overlap_counts = page.evaluate("""() => {
        const labels = Array.from(document.querySelectorAll('.price-zone-label'));
        const boxes = labels.map(label => label.getBoundingClientRect());
        let overlaps = 0;
        for (let left = 0; left < boxes.length; left += 1) {
            for (let right = left + 1; right < boxes.length; right += 1) {
                const a = boxes[left];
                const b = boxes[right];
                if (a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top) overlaps += 1;
            }
        }
        const legend = document.querySelector('[data-chart-pane="price"] .chart-legend')?.getBoundingClientRect();
        const legendOverlaps = legend
            ? boxes.filter(box => box.left < legend.right && box.right > legend.left && box.top < legend.bottom && box.bottom > legend.top).length
            : 0;
        return { labels: overlaps, legend: legendOverlaps };
    }""")
    if overlap_counts["labels"]:
        issues.append(f"price-level labels still overlap ({overlap_counts['labels']} collisions)")
    if overlap_counts["legend"]:
        issues.append(f"price-level labels overlap the chart legend ({overlap_counts['legend']} collisions)")
    if interaction_screenshot:
        page.screenshot(path=str(interaction_screenshot.with_name(interaction_screenshot.stem + "__price-level-legend.png")), full_page=True)
    price_legend.locator(".chart-legend-header").click()


def validate_native_pane_resize(page: Any, chart: Any, issues: list[str]) -> None:
    if chart.locator(".chart-native-pane-overlay.chart-osc").count() < 2:
        return
    if chart.locator(".chart-pane-resize").count():
        issues.append("chart still renders duplicate custom pane resize handles")
    native_handles = chart.locator(
        '.chart-pane-canvas div[style*="position: absolute"][style*="cursor: row-resize"]'
    )
    if native_handles.count() < 2:
        issues.append("chart does not expose one native separator per pane boundary")
        return
    before = chart.locator(".chart-native-pane-overlay").evaluate_all(
        "elements => elements.map(element => { const box = element.getBoundingClientRect(); return { top: box.top, height: box.height }; })"
    )
    price_boundary_y = before[0]["top"] + before[0]["height"]
    handle_boxes = [
        native_handles.nth(index).bounding_box()
        for index in range(native_handles.count())
    ]
    handle_box = min(
        (box for box in handle_boxes if box and box["width"] > 0 and box["height"] > 0),
        key=lambda box: abs((box["y"] + box["height"] / 2) - price_boundary_y),
        default=None,
    )
    if not handle_box:
        issues.append("first native pane separator is not measurable")
        return
    page.mouse.move(handle_box["x"] + handle_box["width"] / 2, handle_box["y"] + handle_box["height"] / 2)
    page.mouse.down()
    page.mouse.move(handle_box["x"] + handle_box["width"] / 2, handle_box["y"] + handle_box["height"] / 2 + 70, steps=8)
    page.mouse.up()
    page.wait_for_timeout(250)
    after = chart.locator(".chart-native-pane-overlay").evaluate_all(
        "elements => elements.map(element => { const box = element.getBoundingClientRect(); const legend = element.querySelector('.chart-legend')?.getBoundingClientRect(); return { top: box.top, height: box.height, legendTop: legend?.top ?? null, legendBottom: legend?.bottom ?? null, bottom: box.bottom }; })"
    )
    if len(before) != len(after) or abs(after[0]["height"] - before[0]["height"]) < 6:
        issues.append(
            "native separator does not resize the top price pane "
            f"(before={before[0] if before else None}, after={after[0] if after else None}, "
            f"handle={handle_box}, boundary_y={round(price_boundary_y, 2)})"
        )
    for index, pane in enumerate(after):
        if pane["legendTop"] is not None and (pane["legendTop"] < pane["top"] - 1 or pane["legendBottom"] > pane["bottom"] + 1):
            issues.append(f"pane {index + 1} legend moved outside its owning pane after resize")
    persisted = page.evaluate("""() => Object.entries(localStorage)
        .filter(([key]) => key.endsWith('.pane-layout-v2'))
        .map(([, value]) => { try { return JSON.parse(value); } catch { return {}; } })
        .some(value => Number(value.price) > 0 && Object.keys(value).some(key => key.startsWith('oscillator:')))
    """)
    if not persisted:
        issues.append("native pane proportions are not persisted after resize")


def validate_loading_window_interactions(page: Any, window: Any, issues: list[str]) -> None:
    """Keep a loading container stable while pointer geometry is previewed and committed."""
    loading = window.locator(".canvas-preview-loading")
    if loading.count() and loading.is_visible():
        spinner = loading.evaluate(
            "element => ({ animation: getComputedStyle(element, '::before').animationName, content: getComputedStyle(element, '::before').content })"
        )
        if spinner["animation"] == "none" or spinner["content"] in ("none", "normal"):
            issues.append("Canvas loading message does not render an animated indicator on its left")

    resize_handle = window.locator(".workspace-window-resize")
    resize_box = resize_handle.bounding_box()
    before = window.bounding_box()
    if not resize_box or not before:
        issues.append("loading-container resize handle is not measurable")
        return

    origin_x = resize_box["x"] + resize_box["width"] / 2
    origin_y = resize_box["y"] + resize_box["height"] / 2
    page.mouse.move(origin_x, origin_y)
    page.mouse.down()
    samples: list[tuple[float, float]] = []
    for step in range(1, 9):
        page.mouse.move(origin_x + step * 6, origin_y + step * 5)
        sample = window.bounding_box()
        if sample:
            samples.append((sample["width"], sample["height"]))
    if window.get_attribute("data-resizing") != "true":
        issues.append("loading container does not expose its transient resize state")
    page.mouse.up()
    page.wait_for_timeout(100)
    after = window.bounding_box()
    if any(
        current[0] + 0.5 < previous[0] or current[1] + 0.5 < previous[1]
        for previous, current in zip(samples, samples[1:])
    ):
        issues.append("loading container oscillates while pointer resize advances monotonically")
    if not after or after["width"] < before["width"] + 42 or after["height"] < before["height"] + 34:
        issues.append("loading-container resize does not commit the final pointer geometry")
    if window.get_attribute("data-resizing") is not None:
        issues.append("loading container remains in transient resize state after pointer release")

    header = window.locator(":scope > .workspace-window-header")
    header_box = header.bounding_box()
    move_before = window.bounding_box()
    if not header_box or not move_before:
        issues.append("loading-container move handle is not measurable")
        return
    move_x = header_box["x"] + min(100, header_box["width"] * 0.3)
    move_y = header_box["y"] + header_box["height"] / 2
    page.mouse.move(move_x, move_y)
    page.mouse.down()
    move_samples: list[tuple[float, float]] = []
    for step in range(1, 9):
        page.mouse.move(move_x + step * 5, move_y + step * 3)
        sample = window.bounding_box()
        if sample:
            move_samples.append((sample["x"], sample["y"]))
    page.mouse.up()
    page.wait_for_timeout(100)
    move_after = window.bounding_box()
    if any(
        current[0] + 0.5 < previous[0] or current[1] + 0.5 < previous[1]
        for previous, current in zip(move_samples, move_samples[1:])
    ):
        issues.append("loading container oscillates while pointer movement advances monotonically")
    if not move_after or move_after["x"] < move_before["x"] + 34 or move_after["y"] < move_before["y"] + 18:
        issues.append("loading-container move does not commit the final pointer geometry")
    if window.get_attribute("data-dragging") is not None:
        issues.append("loading container remains in transient drag state after pointer release")

    reset = window.get_by_role("button", name=re.compile(r"^Reset .+ to its default layout$"))
    if reset.count() == 1:
        reset.click()


def validate_watch_universe_close_lifecycle(page: Any, issues: list[str]) -> None:
    """Closing tabs or the container must not synthesize replacement instances."""
    page.get_by_role("button", name="Canvas management", exact=True).click()
    management = page.get_by_role("complementary", name="Canvas management")
    library = management.get_by_role("region", name="Container library")
    watchlist_card = library.locator("article").filter(has_text="Watch Universe")
    try:
        watchlist_card.first.wait_for(state="visible", timeout=5000)
    except Exception:
        pass
    watchlist_definition_count = watchlist_card.count()
    if watchlist_definition_count != 1:
        issues.append(f"Container library exposes {watchlist_definition_count} Watch Universe definitions instead of one")
        management.get_by_role("button", name="Close canvas management").click()
        return
    watchlist_card.get_by_role("button", name="Add", exact=True).click()
    watchlists = page.locator('.workspace-window[data-window-kind="watchlist"]')
    watchlists.first.wait_for(state="visible", timeout=5000)
    if watchlists.count() != 1:
        issues.append("adding one Watch Universe created multiple container instances")
        return

    watchlist = watchlists.first
    remove_tabs = watchlist.locator(".watchlist-tab-remove")
    while remove_tabs.count():
        remove_tabs.first.click()
    page.wait_for_timeout(500)
    if watchlists.count() != 1 or watchlist.locator(".watchlist-tab-remove").count():
        issues.append("closing all Watchlist tabs recreated tabs or Watch Universe containers")
        return

    page.get_by_role("button", name="Canvas management", exact=True).click()
    management = page.get_by_role("complementary", name="Canvas management")
    library = management.get_by_role("region", name="Container library")
    library.locator("article").filter(has_text="Watch Universe").get_by_role("button", name="Add", exact=True).click()
    page.wait_for_timeout(100)
    if watchlists.count() != 2:
        issues.append("adding a second Watch Universe did not create exactly two instances")
        return

    # Reproduce stacked-window close clicks before React can render either state
    # update. Each close must subtract from the latest open-container set.
    page.evaluate("""() => {
        const buttons = Array.from(document.querySelectorAll(
            '.workspace-window[data-window-kind="watchlist"] button[aria-label^="Close Watch Universe"]'
        ));
        buttons.forEach((button) => button.click());
    }""")
    page.wait_for_timeout(500)
    if watchlists.count():
        issues.append("simultaneous Watch Universe closes reintroduced a previously closed container")
        return
    page.reload(wait_until="domcontentloaded")
    page.wait_for_timeout(500)
    if page.locator('.workspace-window[data-window-kind="watchlist"]').count():
        issues.append("closed Watch Universe did not remain closed after Canvas reload")


def validate_canvas_interactions(
    page: Any,
    scenario: dict[str, Any],
    interaction_screenshot: Path | None = None,
    chart_timeframe: str = "1m",
    chart_stress_cycles: int = 24,
    chart_stress_pattern: str = "mixed",
    chart_stress_only: bool = False,
    watchlist_close_only: bool = False,
) -> list[str]:
    """Exercise the Canvas behaviors that static screenshots cannot prove."""
    issues: list[str] = []
    if scenario["page"] == "canvas-focus":
        if page.locator(".sidebar").count():
            issues.append("focus canvas renders the application sidebar")
        positions = page.locator('.workspace-window[data-window-kind="positions"]')
        if positions.count():
            if positions.count() != 1:
                issues.append("focus canvas does not render exactly one Position Manager container")
                return issues
            bounds = positions.bounding_box()
            minimum_height = scenario["viewport"]["height"] - 92
            if not bounds or bounds["height"] < minimum_height:
                actual = round(bounds["height"]) if bounds else 0
                issues.append(f"focus container does not fill the working page ({actual} < {minimum_height})")
            headers = [value.strip().lower() for value in positions.locator("thead th").all_inner_texts()]
            if headers[:3] != ["symbol", "open unrealized", "peak unrealized"]:
                issues.append(f"Position Manager leading columns are incorrect: {headers[:3]}")
            if positions.locator("tbody tr").count() < 1:
                issues.append("Position Manager review has no populated position row")
            return issues
        charts_quotes = page.locator('.workspace-window[data-window-kind="charts_quotes"]')
        if charts_quotes.count():
            if charts_quotes.count() != 1:
                issues.append("focus canvas does not render exactly one Charts & Quotes container")
                return issues
            bounds = charts_quotes.bounding_box()
            minimum_height = scenario["viewport"]["height"] - 92
            if not bounds or bounds["height"] < minimum_height:
                actual = round(bounds["height"]) if bounds else 0
                issues.append(f"focus container does not fill the working page ({actual} < {minimum_height})")
            main = charts_quotes.locator(".charts-quotes-main-chart")
            tape = charts_quotes.locator(".charts-quotes-tape")
            context = charts_quotes.locator(".charts-quotes-context-row")
            expanded = main.bounding_box()
            if tape.is_visible() or context.is_visible():
                issues.append("Charts & Quotes does not open with the main chart maximized")
            restore = main.get_by_role("button", name="Restore chart panels", exact=True)
            restore.click()
            page.wait_for_timeout(350)
            restored = main.bounding_box()
            if not tape.is_visible() or not context.is_visible():
                issues.append("Restore chart panels does not reveal both supporting regions")
            if not expanded or not restored or expanded["width"] <= restored["width"] or expanded["height"] <= restored["height"]:
                issues.append("Maximizing the main chart does not increase both dimensions")
            if interaction_screenshot:
                charts_quotes.screenshot(path=str(interaction_screenshot.with_name(f"{interaction_screenshot.stem}__restored.png")))
            main.get_by_role("button", name="Maximize main chart", exact=True).focus()
            page.keyboard.press("Enter")
            page.wait_for_timeout(350)
            if tape.is_visible() or context.is_visible():
                issues.append("Keyboard maximize does not hide both supporting regions")
            restore.click()
            page.wait_for_timeout(350)
            daily = charts_quotes.locator(".charts-quotes-daily-chart")
            try:
                daily.get_by_text("Loading chart data...", exact=True).wait_for(state="hidden", timeout=120_000)
                marker = daily.locator('.chart-timeline-event[data-kind="split"]:visible')
                marker.first.wait_for(state="visible", timeout=30_000)
                if interaction_screenshot and marker.count() != 1:
                    issues.append("daily chart does not render exactly one deterministic split marker")
                if daily.get_by_text("Split-adjusted", exact=True).count() != 1:
                    issues.append("daily chart does not expose its split-adjusted price basis")
                marker_box = marker.first.bounding_box()
                daily_box = daily.bounding_box()
                marker_center_x = marker_box["x"] + marker_box["width"] / 2 if marker_box else None
                if not marker_box or not daily_box or marker_center_x is None or not (daily_box["x"] <= marker_center_x <= daily_box["x"] + daily_box["width"]):
                    issues.append("split marker is not anchored inside the daily chart")
                if interaction_screenshot:
                    daily.screenshot(path=str(interaction_screenshot))
            except Exception as exc:
                issues.append(f"daily split-marker interaction failed: {exc}")
            return issues
        chart = page.locator('.workspace-window[data-window-kind="chart"]')
        if chart.count() != 1:
            issues.append("focus canvas does not render exactly one Chart container")
        else:
            bounds = chart.bounding_box()
            minimum_height = scenario["viewport"]["height"] - 92
            if not bounds or bounds["height"] < minimum_height:
                actual = round(bounds["height"]) if bounds else 0
                issues.append(
                    f"focus container does not fill the working page ({actual} < {minimum_height})"
                )
            try:
                chart.get_by_text("Loading chart data...", exact=True).wait_for(state="hidden", timeout=120_000)
                zoom_button = chart.get_by_role("button", name="Box zoom", exact=True)
                zoom_button.wait_for(state="visible", timeout=30_000)
                if not zoom_button.is_disabled():
                    zoom_button.click()
                    page.keyboard.press("Escape")
                    if chart.locator(".chart-box-zoom").count():
                        issues.append("Box zoom does not cancel with Escape")
                    zoom_button.click()
                    zoom_surface = chart.locator(".chart-box-zoom")
                    zoom_bounds = zoom_surface.bounding_box()
                    if zoom_bounds:
                        x = zoom_bounds["x"]
                        y = zoom_bounds["y"]
                        w = zoom_bounds["width"]
                        h = zoom_bounds["height"]
                        page.mouse.move(x + w * .75, y + h * .75)
                        page.mouse.down()
                        page.mouse.move(x + w * .25, y + h * .25, steps=8)
                        if interaction_screenshot:
                            page.screenshot(path=str(interaction_screenshot.with_name(interaction_screenshot.stem + "__box-selection.png")))
                        page.mouse.up()
                        page.wait_for_timeout(350)
                        if zoom_button.get_attribute("aria-pressed") != "false" or zoom_surface.count():
                            issues.append("Box zoom does not apply and return to navigation after a reverse drag")
                        if interaction_screenshot:
                            page.screenshot(path=str(interaction_screenshot.with_name(interaction_screenshot.stem + "__box-result.png")))
                            page.screenshot(path=str(interaction_screenshot))
                        chart.get_by_role("button", name="Reset view", exact=True).click()
                else:
                    issues.append("Box zoom is unavailable on the populated review chart")
                price_pane = chart.locator(".chart-price").first
                price_pane.locator(".chart-pane-canvas canvas").first.wait_for(state="visible", timeout=30_000)
                if not chart_stress_only:
                    validate_price_zone_legend(page, chart, issues, interaction_screenshot)
                    validate_native_pane_resize(page, chart, issues)
                price_box = price_pane.bounding_box()
                if price_box:
                    center_x = price_box["x"] + price_box["width"] * 0.55
                    center_y = price_box["y"] + price_box["height"] * 0.5
                    right_axis_x = price_box["x"] + price_box["width"] - 18
                    time_axis_y = price_box["y"] + price_box["height"] - 8
                    page.mouse.move(8, 8)
                    future_space_baseline = price_pane.screenshot()
                    page.mouse.move(center_x, center_y)
                    page.mouse.down()
                    page.mouse.move(center_x - 160, center_y, steps=8)
                    page.mouse.up()
                    page.mouse.move(8, 8)
                    page.wait_for_timeout(250)
                    if future_space_baseline == price_pane.screenshot():
                        page.mouse.move(center_x + 80, center_y)
                        page.mouse.down()
                        page.mouse.move(center_x - 240, center_y, steps=12)
                        page.mouse.up()
                        page.mouse.move(8, 8)
                        page.wait_for_timeout(350)
                        if future_space_baseline == price_pane.screenshot():
                            issues.append("focus chart prevents panning the latest bar left to create future space")
                    for interaction_index in range(chart_stress_cycles):
                        page.mouse.move(center_x, center_y)
                        page.mouse.wheel(0, -180 if interaction_index % 2 == 0 else 150)
                        page.mouse.move(center_x, center_y)
                        page.mouse.down()
                        page.mouse.move(center_x + (160 if interaction_index % 2 == 0 else -125), center_y, steps=4)
                        page.mouse.up()
                        page.mouse.move(right_axis_x, center_y)
                        page.mouse.down()
                        page.mouse.move(right_axis_x, center_y + (42 if interaction_index % 2 == 0 else -36), steps=3)
                        page.mouse.up()
                        page.mouse.move(center_x, time_axis_y)
                        page.mouse.down()
                        page.mouse.move(center_x + (110 if interaction_index % 2 == 0 else -90), time_axis_y, steps=3)
                        page.mouse.up()
                        if chart_stress_cycles <= 20 or interaction_index % 20 == 19:
                            render_state = page.evaluate("""() => {
                                const shell = document.querySelector('.chart-shell');
                                const canvases = Array.from(shell?.querySelectorAll('canvas') || []);
                                return {
                                    appShell: Boolean(document.querySelector('.app-shell')),
                                    bodyTextLength: (document.body?.innerText || '').length,
                                    canvasCount: canvases.length,
                                    canvasPixels: canvases.reduce((total, canvas) => total + canvas.width * canvas.height, 0),
                                    scaleRecoveries: Number(shell?.getAttribute('data-chart-scale-recoveries') || 0),
                                };
                            }""")
                            print(f"focus chart render state {interaction_index + 1}: {json.dumps(render_state, sort_keys=True)}", flush=True)
                    page.wait_for_timeout(500)
                    if not chart.is_visible() or price_pane.locator("canvas").count() < 1 or not page.locator(".app-shell").is_visible():
                        issues.append("focus chart becomes blank after repeated pan, zoom, and axis-scale interactions")
                    if interaction_screenshot:
                        page.screenshot(path=str(interaction_screenshot.with_name(interaction_screenshot.stem + "__stress-final.png")), full_page=True)
            except Exception as exc:
                issues.append(f"Focus chart interaction check failed: {exc}")
        return issues

    if scenario["page"] == "market-discovery-configuration":
        if not (
            scenario["theme"] == "light"
            and scenario["scale"] == 1.0
            and scenario["viewport_name"] == "normal"
        ):
            return issues
        try:
            page.get_by_role("button", name=re.compile(r"^Top Penny Stock Gainers")).click()
            rule_lookup = page.get_by_role("button", name="Rule Set to add", exact=True)
            column_lookup = page.get_by_role("button", name="Column to add", exact=True)
            if rule_lookup.count() != 1 or rule_lookup.is_disabled():
                issues.append("Watchlist rules do not expose the registered Rule Set lookup")
            else:
                rule_lookup.click()
                if page.get_by_role("searchbox", name="Search Rule Set to add").count() != 1:
                    issues.append("Rule Set lookup does not expose its fixed search control")
                page.keyboard.press("Escape")
            if column_lookup.count() != 1 or column_lookup.is_disabled():
                issues.append("Watchlist columns do not expose the registered column lookup")
            else:
                column_lookup.click()
                if page.get_by_role("group", name="Rule Set Results").count() != 1:
                    issues.append("Column lookup does not expose Rule Set results as columns")
                if page.get_by_role("group", name="Data Field Outputs").count() != 1:
                    issues.append("Column lookup does not expose Data Field Outputs as columns")
                page.keyboard.press("Escape")
            if page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"):
                issues.append("Market Discovery page leaks horizontal scrolling to the document")
        except Exception as exc:
            issues.append(f"Market Discovery registry interaction check failed: {exc}")
        return issues

    if not (
        scenario["page"] == "canvas-configuration"
        and scenario["theme"] == "light"
        and scenario["scale"] == 1.0
        and scenario["viewport_name"] == "normal"
    ):
        return issues

    charts = page.locator('.workspace-window[data-window-kind="chart"]')
    if charts.count() < 1:
        return ["main canvas does not render a Chart container"]
    chart = charts.first
    try:
        scanner = page.locator('.workspace-window[data-window-kind="scanner"]')
        if scanner.count() == 1:
            validate_loading_window_interactions(page, scanner, issues)
            symbol_header = scanner.locator("th.market-list-symbol-column").first
            if symbol_header.count() and symbol_header.evaluate("element => getComputedStyle(element).boxShadow") != "none":
                issues.append("Scanner symbol column renders an unwanted right-edge shadow")
        validate_watch_universe_close_lifecycle(page, issues)
        if watchlist_close_only:
            return issues
        chart = page.locator('.workspace-window[data-window-kind="chart"]').first
        chart.get_by_text("Loading chart data...", exact=True).wait_for(state="hidden", timeout=120_000)
        chart.locator(".chart-pane-canvas canvas").first.wait_for(state="visible", timeout=30_000)
        timeframe_button = chart.get_by_role("button", name=chart_timeframe, exact=True)
        if timeframe_button.count() != 1:
            issues.append(f"chart does not expose the requested {chart_timeframe} stress timeframe")
        else:
            timeframe_button.click()
            chart.get_by_text("Loading chart data...", exact=True).wait_for(state="hidden", timeout=120_000)
            chart.locator(".chart-pane-canvas canvas").first.wait_for(state="visible", timeout=30_000)
            page.wait_for_timeout(350)
            if timeframe_button.get_attribute("aria-pressed") == "false":
                issues.append(f"chart did not activate the requested {chart_timeframe} stress timeframe")
        sidebar_toggle = page.get_by_role("button", name="Toggle sidebar")
        toggle_box = sidebar_toggle.bounding_box()
        if not toggle_box or not page.evaluate(
            "([x, y]) => Boolean(document.elementFromPoint(x, y)?.closest('.collapse-button'))",
            [toggle_box["x"] + toggle_box["width"] / 2, toggle_box["y"] + toggle_box["height"] / 2],
        ):
            issues.append("Sidebar collapse arrow renders below a canvas container")
        clock = page.get_by_label("Preview clock")
        zones = page.get_by_label("Preview time zones")
        if clock.count() != 1 or zones.count() != 1:
            issues.append("Canvas does not expose one three-zone preview clock")
        else:
            zone_text = zones.inner_text().lower()
            if not all(label in zone_text for label in ("et", "local", "utc")):
                issues.append("Preview clock does not identify ET, Local, and UTC")
            if "AAPL" in clock.inner_text():
                issues.append("Preview clock incorrectly contains ticker context")
            if clock.locator("input").count() or "Trading date" in clock.inner_text() or "New York" in clock.inner_text():
                issues.append("Preview clock exposes removed date/time editing controls")
            if any(len(value.inner_text().split(":")) < 3 for value in zones.locator("strong").all()):
                issues.append("Preview clocks do not render seconds")
            clock_colors = zones.locator("span").evaluate_all("elements => elements.map(element => getComputedStyle(element).color)")
            if len(set(clock_colors)) != 3:
                issues.append("ET, Local, and UTC clocks do not have distinct theme colors")
            if any(float(value.evaluate("element => getComputedStyle(element).fontSize").replace("px", "")) < 11 for value in zones.locator("strong").all()):
                issues.append("Preview datetime values are still undersized")
        set_default = page.get_by_role("button", name="Save shared default")
        manage_button = page.get_by_role("button", name="Canvas management", exact=True)
        set_default_box, manage_box = set_default.bounding_box(), manage_button.bounding_box()
        if not set_default_box or not manage_box or set_default_box["x"] >= manage_box["x"] or manage_box["x"] + manage_box["width"] < scenario["viewport"]["width"] - 18:
            issues.append("Save shared default and Canvas management are not grouped on the far right")
        if page.locator(".trading-workspace-command").count():
            issues.append("Canvas still renders the duplicate Main workspace context row")
        if page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth"):
            issues.append("Canvas page leaks horizontal scrolling to the document")
        if page.evaluate("document.documentElement.scrollHeight > document.documentElement.clientHeight + 1"):
            issues.append("Canvas page leaks vertical scrolling to the document")
        indicator_menu_trigger = chart.locator(".chart-column-select-button").filter(has_text=re.compile(r"^Indicators"))
        if indicator_menu_trigger.count():
            indicator_menu_trigger.first.click()
            indicator_menu = page.locator("body > .chart-column-menu-portal").first
            indicator_menu_box = indicator_menu.bounding_box()
            if not indicator_menu_box:
                issues.append("indicators popover is not rendered through the viewport portal")
            else:
                if (
                    indicator_menu_box["x"] < -1
                    or indicator_menu_box["y"] < -1
                    or indicator_menu_box["x"] + indicator_menu_box["width"] > scenario["viewport"]["width"] + 1
                    or indicator_menu_box["y"] + indicator_menu_box["height"] > scenario["viewport"]["height"] + 1
                ):
                    issues.append("indicators popover is clipped by a viewport edge")
                portal_is_top_layer = page.evaluate(
                    "([x, y]) => Boolean(document.elementFromPoint(x, y)?.closest('.chart-column-menu-portal'))",
                    [indicator_menu_box["x"] + indicator_menu_box["width"] / 2, indicator_menu_box["y"] + min(20, indicator_menu_box["height"] / 2)],
                )
                if not portal_is_top_layer:
                    issues.append("indicators popover renders behind a Canvas container")
                if interaction_screenshot:
                    page.screenshot(path=str(interaction_screenshot.with_name(interaction_screenshot.stem + "__indicators-popover.png")), full_page=True)
            indicator_menu_trigger.first.click()
        oscillator = chart.locator(".chart-osc").first
        if oscillator.count():
            oscillator.locator(".chart-legend-header").click()
            configure_indicator = oscillator.locator("button[aria-label^='Configure ']").first
            if configure_indicator.count():
                configure_indicator.click()
                editor = page.get_by_role("dialog", name=re.compile(r"indicator settings$"))
                editor_box = editor.bounding_box()
                if not editor_box:
                    issues.append("indicator settings portal is not measurable")
                elif editor_box["y"] < 0 or editor_box["y"] + editor_box["height"] > scenario["viewport"]["height"] + 1:
                    issues.append("indicator settings portal is clipped by the viewport edge")
                editor_style = editor.evaluate("element => ({ background: getComputedStyle(element).backgroundColor, backdrop: getComputedStyle(element).backdropFilter })")
                if editor_style["background"] in ("rgba(0, 0, 0, 0)", "transparent"):
                    issues.append("indicator settings portal is fully transparent")
                if editor_style["backdrop"] == "none":
                    issues.append("indicator settings portal lacks the intended light transparency treatment")
                alpha_match = re.search(r"/\s*([0-9.]+)\s*\)$|rgba?\([^)]*,\s*([0-9.]+)\s*\)$", editor_style["background"])
                if alpha_match:
                    alpha = float(next(value for value in alpha_match.groups() if value is not None))
                    if alpha >= 0.97:
                        issues.append("indicator settings portal is effectively opaque")
                    elif alpha < 0.82:
                        issues.append("indicator settings portal is too transparent for chart-legible controls")
                opacity_input = editor.get_by_role("slider", name=re.compile(r" opacity$"))
                if opacity_input.count() != 1:
                    issues.append("indicator settings does not expose a per-series opacity input")
                else:
                    opacity_input.fill("47")
                    if opacity_input.input_value() != "47":
                        issues.append("indicator opacity input does not update its persisted series setting")
                if interaction_screenshot:
                    page.screenshot(path=str(interaction_screenshot.with_name(interaction_screenshot.stem + "__indicator-config.png")), full_page=True)
                editor.get_by_role("button", name="Close indicator settings").click()
                configure_indicator.click()
                persisted_editor = page.get_by_role("dialog", name=re.compile(r"indicator settings$"))
                persisted_opacity = persisted_editor.get_by_role("slider", name=re.compile(r" opacity$"))
                if persisted_opacity.count() != 1 or persisted_opacity.input_value() != "47":
                    issues.append("indicator opacity does not persist after closing its settings")
                persisted_editor.get_by_role("button", name="Close indicator settings").click()
            else:
                issues.append("oscillator legend does not expose indicator configuration actions")
            resize_handle = chart.locator(".workspace-window-resize")
            if resize_handle.count() == 1:
                for _ in range(4):
                    resize_handle.press("Shift+ArrowUp")
                page.wait_for_timeout(180)
                native_surface = chart.locator(".chart-native-surface").first
                time_axis_is_contained = native_surface.evaluate(
                    """surface => {
                        const surfaceBounds = surface.getBoundingClientRect();
                        const stackBounds = surface.closest('.chart-canvas-stack')?.getBoundingClientRect();
                        const chartTable = surface.querySelector('table')?.getBoundingClientRect();
                        return Boolean(
                            stackBounds
                            && chartTable
                            && surfaceBounds.bottom <= stackBounds.bottom + 1
                            && chartTable.bottom <= surfaceBounds.bottom + 1
                        );
                    }"""
                )
                if not time_axis_is_contained:
                    issues.append("resizing a Chart container clips the bottom pane time axis")
                for _ in range(4):
                    resize_handle.press("Shift+ArrowDown")
                page.wait_for_timeout(180)
        price_pane = chart.locator(".chart-price").first
        latest_fit = chart.get_by_role("button", name="Fit session", exact=True)
        price_box = None
        if price_pane.count() and latest_fit.count():
            latest_fit.click()
            price_box = price_pane.bounding_box()
            if price_box:
                start_x = price_box["x"] + price_box["width"] * 0.55
                start_y = price_box["y"] + price_box["height"] * 0.55
                page.mouse.click(start_x, start_y)
                page.mouse.move(8, 8)
                page.wait_for_timeout(180)
                click_baseline = price_pane.screenshot()
                page.wait_for_timeout(350)
                if click_baseline != price_pane.screenshot():
                    issues.append("chart reverses a fit command after the first chart click")
                future_space_baseline = price_pane.screenshot()
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(start_x - 160, start_y, steps=8)
                page.mouse.up()
                page.mouse.move(8, 8)
                page.wait_for_timeout(250)
                if future_space_baseline == price_pane.screenshot():
                    page.mouse.move(start_x + 80, start_y)
                    page.mouse.down()
                    page.mouse.move(start_x - 240, start_y, steps=12)
                    page.mouse.up()
                    page.mouse.move(8, 8)
                    page.wait_for_timeout(350)
                    if future_space_baseline == price_pane.screenshot():
                        issues.append("chart prevents panning the latest bar left to create future space")
                latest_fit.click()
                page.wait_for_timeout(180)
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(start_x + 90, start_y, steps=6)
                page.mouse.up()
                page.mouse.move(8, 8)
                page.wait_for_timeout(250)
                manually_panned = price_pane.screenshot()
                page.wait_for_timeout(750)
                if manually_panned != price_pane.screenshot():
                    issues.append("chart reapplies an automatic fit after manual pan")
        center_latest = chart.get_by_role("button", name="Center latest", exact=True)
        reset_view = chart.get_by_role("button", name="Reset view", exact=True)
        if center_latest.count() != 1:
            issues.append("chart does not expose the concise center-latest action")
        if reset_view.count() != 1:
            issues.append("chart does not expose the concise reset-view action")
        elif price_pane.count() and price_box:
            for action, label in ((center_latest, "Center latest"), (reset_view, "Reset view")):
                if action.count() != 1:
                    continue
                action.click()
                page.wait_for_timeout(120)
                page.mouse.click(start_x, start_y)
                page.mouse.move(8, 8)
                page.wait_for_timeout(180)
                interaction_baseline = price_pane.screenshot()
                page.wait_for_timeout(350)
                if interaction_baseline != price_pane.screenshot():
                    issues.append(f"{label} reverses after the first chart click")

        if price_pane.count() and price_box:
            center_x = price_box["x"] + price_box["width"] * 0.55
            center_y = price_box["y"] + price_box["height"] * 0.5
            right_axis_x = price_box["x"] + price_box["width"] - 18
            time_axis_y = price_box["y"] + price_box["height"] - 8
            for interaction_index in range(chart_stress_cycles):
                pathological = chart_stress_pattern == "pathological"
                left_paging = chart_stress_pattern == "left-paging"
                page.mouse.move(center_x, center_y)
                if not left_paging:
                    page.mouse.wheel(0, -180 if pathological or interaction_index % 2 == 0 else 150)
                page.mouse.move(center_x, center_y)
                page.mouse.down()
                page.mouse.move(
                    price_box["x"] + price_box["width"] - 80
                    if left_paging
                    else center_x + (95 if pathological or interaction_index % 2 == 0 else -75),
                    center_y,
                    steps=8 if left_paging else 3,
                )
                page.mouse.up()
                if not left_paging or interaction_index % 2:
                    page.mouse.move(right_axis_x, center_y)
                    page.mouse.down()
                    page.mouse.move(right_axis_x, center_y + (35 if pathological or interaction_index % 2 == 0 else -30), steps=2)
                    page.mouse.up()
                if not left_paging or interaction_index % 3 == 2:
                    page.mouse.move(center_x, time_axis_y)
                    page.mouse.down()
                    page.mouse.move(center_x + (70 if pathological or interaction_index % 2 == 0 else -60), time_axis_y, steps=2)
                    page.mouse.up()
                if left_paging:
                    page.wait_for_timeout(120)
                if chart_stress_cycles <= 20 or interaction_index % 20 == 19:
                    render_state = page.evaluate("""() => {
                        const app = document.querySelector('.app-shell');
                        const chartShell = document.querySelector('.workspace-window[data-window-kind="chart"] .chart-shell');
                        const chartSurface = chartShell?.querySelector('.chart-native-surface');
                        const canvases = Array.from(chartShell?.querySelectorAll('canvas') || []);
                        const rect = chartSurface?.getBoundingClientRect();
                        return {
                            appShell: Boolean(app),
                            bodyTextLength: (document.body?.innerText || '').length,
                            canvasCount: canvases.length,
                            canvasPixels: canvases.reduce((total, canvas) => total + canvas.width * canvas.height, 0),
                            chartHeight: rect ? Math.round(rect.height) : null,
                            chartScaleRecoveries: Number(chartShell?.getAttribute('data-chart-scale-recoveries') || 0),
                            chartWidth: rect ? Math.round(rect.width) : null,
                            nativePaneCount: chartShell?.querySelectorAll('.chart-native-pane-overlay').length || 0,
                        };
                    }""")
                    print(f"chart render state {interaction_index + 1}: {json.dumps(render_state, sort_keys=True)}", flush=True)
                if interaction_index % 20 == 19:
                    print(f"chart stress: {interaction_index + 1}/{chart_stress_cycles} cycles", flush=True)
                    if not page.locator(".app-shell").is_visible():
                        issues.append(f"application became blank after {interaction_index + 1} chart interaction cycles")
                        break
            page.wait_for_timeout(500)
            if not chart.is_visible() or price_pane.locator("canvas").count() < 1 or not page.locator(".app-shell").is_visible():
                issues.append("chart becomes blank after repeated pan, zoom, and axis-scale interactions")
            if chart_stress_pattern == "pathological" and chart_stress_cycles >= 100:
                recovery_count = int(chart.locator(".chart-shell").get_attribute("data-chart-scale-recoveries") or "0")
                if recovery_count < 1:
                    issues.append("chart scale-safety boundary was not exercised by the pathological interaction stress")
            if chart_stress_only:
                if interaction_screenshot:
                    page.screenshot(path=str(interaction_screenshot.with_name(interaction_screenshot.stem + "__stress-final.png")), full_page=True)
                return issues

        # Verify grouping while the deterministic AAPL payload is still loaded.
        # Later link-management checks intentionally change shared ticker state.
        required_containers = ("All News", "Ticker News", "Quotes & Tape")
        missing_containers = [
            title for title in required_containers
            if page.get_by_role("region", name=title, exact=True).count() == 0
        ]
        for title in missing_containers:
            page.get_by_role("button", name="Canvas management", exact=True).click()
            library = page.get_by_role("region", name="Container library")
            article = library.locator("article").filter(has_text=title).first
            article.get_by_role("button", name="Add", exact=True).click()
            page.wait_for_timeout(250)
        chart_shell_before_group = chart.locator(".chart-shell").bounding_box()
        oscillator_count_before_group = chart.locator(".chart-osc").count()
        chart.locator(".workspace-window-header").get_by_role("button", name=re.compile(r"^Add .+ to group selection$")).click()
        grouping_tray = page.get_by_role("region", name="Container group selection")
        page.get_by_role("button", name="Add All News to group selection", exact=True).click()
        grouping_tray.get_by_role("button", name="Create group (2)", exact=True).click()
        chart_group = page.locator(".workspace-group-window")
        grouped_chart = chart_group.locator('[data-window-kind="chart"]')
        grouped_chart_shell = grouped_chart.locator(".chart-shell")
        for _ in range(20):
            if grouped_chart.locator(".chart-price").count() and grouped_chart.locator(".chart-osc").count() >= oscillator_count_before_group:
                break
            page.wait_for_timeout(250)
        chart_shell_after_group = grouped_chart_shell.bounding_box()
        if chart_shell_before_group and chart_shell_after_group:
            if chart_shell_after_group["width"] + 1 < chart_shell_before_group["width"] or chart_shell_after_group["height"] + 1 < chart_shell_before_group["height"]:
                issues.append("grouping clips the Chart member's existing content dimensions")
        else:
            issues.append("grouped Chart content is not measurable")
        grouped_price = grouped_chart.locator(".chart-price").first
        grouped_oscillator = grouped_chart.locator(".chart-osc").first
        if grouped_price.count() != 1 or grouped_chart.locator(".chart-osc").count() < oscillator_count_before_group:
            issues.append("grouping removes a Chart pane or its independent time-axis surface")
        elif oscillator_count_before_group:
            price_regions = grouped_chart.locator('[data-chart-pane="price"] .session-region')
            oscillator_regions = grouped_oscillator.locator(".session-region")
            if price_regions.count() != oscillator_regions.count() or price_regions.count() == 0:
                issues.append("grouped Chart does not retain matching extended-hours regions across panes")
            else:
                for region_index in range(price_regions.count()):
                    price_region_box = price_regions.nth(region_index).bounding_box()
                    oscillator_region_box = oscillator_regions.nth(region_index).bounding_box()
                    if price_region_box and oscillator_region_box and (
                        abs(price_region_box["x"] - oscillator_region_box["x"]) > 1
                        or abs(price_region_box["width"] - oscillator_region_box["width"]) > 1
                    ):
                        issues.append("grouped Chart session shading is not horizontally aligned across price and oscillator panes")
                        break
            grouped_fit = grouped_chart.get_by_role("button", name="Fit session", exact=True)
            grouped_fit.click()
            page.wait_for_timeout(180)
            grouped_price_box = grouped_price.bounding_box()
            if grouped_price_box:
                page.mouse.click(grouped_price_box["x"] + grouped_price_box["width"] * 0.55, grouped_price_box["y"] + grouped_price_box["height"] * 0.55)
                page.mouse.move(8, 8)
                page.wait_for_timeout(180)
                grouped_click_baseline = grouped_price.screenshot()
                page.wait_for_timeout(350)
                if grouped_click_baseline != grouped_price.screenshot():
                    issues.append("grouped Chart reverses a fit command after the first chart click")
        if interaction_screenshot:
            page.screenshot(path=str(interaction_screenshot.with_name(interaction_screenshot.stem + "__chart-grouped.png")), full_page=True)
        chart_group.get_by_role("button", name=re.compile(r"^Ungroup .+$")).click()
        page.wait_for_timeout(100)
        clear_group_selection = page.get_by_role("button", name="Clear group selection", exact=True)
        if clear_group_selection.count():
            clear_group_selection.click()

        canvas = page.locator("[data-workspace-canvas]")
        if canvas.evaluate("element => getComputedStyle(element).overflowX") not in ("auto", "scroll"):
            issues.append("Canvas is not its own horizontal scrolling surface")
        canvas_top = canvas.bounding_box()["y"]
        page.get_by_role("button", name="Canvas management", exact=True).click()
        management = page.get_by_role("complementary", name="Canvas management")
        if management.count() != 1:
            issues.append("Canvas management sidebar did not open")
        else:
            management_box = management.bounding_box()
            if management_box:
                top_layer_is_management = page.evaluate(
                    "([x, y]) => Boolean(document.elementFromPoint(x, y)?.closest('[aria-label=\"Canvas management\"]'))",
                    [management_box["x"] + management_box["width"] / 2, management_box["y"] + management_box["height"] / 2],
                )
                if not top_layer_is_management:
                    issues.append("Canvas containers render above the management sidebar")
        library = page.get_by_role("region", name="Container library")
        if library.count() != 1:
            issues.append("Container library did not open")
        else:
            articles = library.locator("article")
            available_text = library.locator("header small").inner_text()
            available_match = re.search(r"(\d+)\s+available", available_text)
            expected_articles = int(available_match.group(1)) if available_match else 0
            if expected_articles == 0 or articles.count() != expected_articles:
                issues.append("Container library does not show the complete compact container list")
            if articles.count() > 1:
                first_box, second_box = articles.nth(0).bounding_box(), articles.nth(1).bounding_box()
                if first_box and second_box and second_box["y"] <= first_box["y"]:
                    issues.append("Container library is not organized as a vertical list")
            if abs(canvas.bounding_box()["y"] - canvas_top) > 1:
                issues.append("Opening canvas management pushes the canvas down")
            if interaction_screenshot:
                page.screenshot(path=str(interaction_screenshot.with_name(interaction_screenshot.stem + "__management.png")), full_page=True)
        management.get_by_role("button", name="Close canvas management").click()
        title_bar = chart.locator(".workspace-window-header")
        link_button = title_bar.get_by_role("button", name="Link Chart")
        if link_button.count() != 1:
            issues.append("Chart link action is not in the container title bar")
        if "Blue" not in link_button.inner_text():
            issues.append("Chart does not expose its current link color at the point of use")
        scanner = page.locator('.workspace-window[data-window-kind="scanner"]')
        portfolio = page.get_by_role("region", name="Portfolio", exact=True)
        news = page.get_by_role("region", name="All News", exact=True)
        chart_tint = title_bar.evaluate("element => getComputedStyle(element).backgroundColor")
        scanner_tint = scanner.locator(".workspace-window-header").evaluate("element => getComputedStyle(element).backgroundColor")
        portfolio_tint = portfolio.locator(".workspace-window-header").evaluate("element => getComputedStyle(element).backgroundColor")
        if chart.get_attribute("data-linked") != "true":
            issues.append("single-symbol Chart does not expose its linked state")
        chart_link_marker = chart.get_by_label("Linked container color")
        if chart_link_marker.count() != 1:
            issues.append("linked Chart does not expose one link-color marker")
        elif chart_link_marker.evaluate("element => getComputedStyle(element).backgroundColor") != link_button.locator(".canvas-link-title-swatch").evaluate("element => getComputedStyle(element).backgroundColor"):
            issues.append("Chart title marker does not match its link color")
        if chart_tint != scanner_tint or chart_tint != portfolio_tint:
            issues.append("link color leaks from the link control into the whole title bar")
        if scanner.get_attribute("data-linked") != "false" or scanner.get_by_role("button", name="Link Scanner").count():
            issues.append("multi-symbol Scanner incorrectly exposes linking")
        if news.get_attribute("data-linked") != "false" or news.get_by_role("button", name="Link All News").count():
            issues.append("All News incorrectly exposes symbol linking")
        if scanner.get_by_label("Linked container color").count() or news.get_by_label("Linked container color").count() or portfolio.get_by_label("Linked container color").count():
            issues.append("non-linkable containers expose a title color marker")
        microstructure = page.get_by_role("region", name="Quotes & Tape", exact=True)
        ticker_news = page.get_by_role("region", name="Ticker News", exact=True)
        if chart.get_by_role("textbox", name="Ticker", exact=True).count() != 1:
            issues.append("the first Blue-linked Chart does not retain the group ticker input")
        microstructure_link = microstructure.get_by_role("button", name="Link Quotes & Tape", exact=True)
        microstructure_was_linked = microstructure.get_attribute("data-linked") == "true"
        if microstructure_was_linked:
            microstructure_link.evaluate("element => element.click()")
            page.get_by_role("button", name="Unlink Quotes & Tape", exact=True).click(force=True)
            page.wait_for_timeout(150)
        if microstructure.get_by_role("textbox", name="Quotes and tape ticker", exact=True).count() != 1:
            microstructure_state = microstructure.evaluate("element => ({ linked: element.getAttribute('data-linked'), inputs: Array.from(element.querySelectorAll('input')).map(input => input.getAttribute('aria-label')) })")
            issues.append(f"unlinked Quotes & Tape does not expose its own ticker input ({microstructure_state})")
        if ticker_news.get_by_role("textbox", name="Ticker news symbol", exact=True).count() != 1:
            issues.append("unlinked Ticker News does not expose its own ticker input")
        if not microstructure_was_linked:
            microstructure_link.evaluate("element => element.click()")
        page.get_by_role("button", name="Assign Quotes & Tape to Blue", exact=True).click(force=True)
        if microstructure.get_by_role("textbox", name="Quotes and tape ticker", exact=True).count():
            issues.append("a child linked Quotes & Tape retains a redundant ticker input")
        if chart.get_by_role("textbox", name="Ticker", exact=True).count() != 1:
            issues.append("linking a child removed the ticker input from the original group parent")
        parent_ticker = chart.get_by_role("textbox", name="Ticker", exact=True)
        parent_ticker.fill("MSFT")
        parent_ticker.press("Enter")
        page.wait_for_timeout(100)
        if "MSFT" not in microstructure.locator(".microstructure-header .ticker-identity").inner_text():
            issues.append("changing the parent ticker did not propagate to a linked child")
        parent_ticker.fill("AAPL")
        parent_ticker.press("Enter")
        chart.get_by_role("button", name="Link Chart", exact=True).click()
        chart.get_by_role("button", name="Unlink Chart", exact=True).click()
        if microstructure.get_by_role("textbox", name="Quotes and tape ticker", exact=True).count() != 1:
            issues.append("unlinking the group parent did not promote the next linked container")
        chart.get_by_role("button", name="Assign Chart to Blue", exact=True).click()
        if chart.get_by_role("textbox", name="Ticker", exact=True).count():
            issues.append("a Chart joining an established group incorrectly took ticker ownership")
        microstructure.get_by_role("button", name="Link Quotes & Tape", exact=True).click()
        microstructure.get_by_role("button", name="Unlink Quotes & Tape", exact=True).click()
        if chart.get_by_role("textbox", name="Ticker", exact=True).count() != 1:
            issues.append("the remaining linked Chart did not inherit ticker ownership")
        ticker_news_link = ticker_news.get_by_role("button", name="Link Ticker News", exact=True)
        ticker_news_link.evaluate("element => element.click()")
        page.get_by_role("button", name="Assign Ticker News to Blue", exact=True).click(force=True)
        if ticker_news.get_by_role("textbox", name="Ticker news symbol", exact=True).count():
            issues.append("a child linked Ticker News retains a redundant ticker input")
        ticker_news_link.evaluate("element => element.click()")
        ticker_news_link.evaluate("element => element.click()")
        page.get_by_role("button", name="Unlink Ticker News", exact=True).click(force=True)
        initial_link_border = link_button.evaluate("element => getComputedStyle(element).borderColor")
        link_button.click()
        if chart.get_by_label("Chart link configuration").count() != 1:
            issues.append("Chart link popover is not contained inside the Chart container")
        if page.locator(".canvas-config-drawer").count():
            issues.append("container configuration created a page-level drawer")
        if "Same color = linked" not in chart.get_by_label("Chart link configuration").inner_text():
            issues.append("Chart configuration does not explain the color-link model")
        color_picker = chart.get_by_label("Chart link color")
        if color_picker.locator(".canvas-link-color-choice").count() != 7:
            issues.append("Chart link picker does not expose exactly seven colors")
        link_configuration_text = chart.get_by_label("Chart link configuration").inner_text()
        if "Rows" in link_configuration_text:
            issues.append("Chart link popover contains unrelated row configuration")
        linked_list = chart.get_by_label("Chart linked containers")
        if "Chart" not in linked_list.inner_text() or "AAPL" not in linked_list.inner_text():
            issues.append("Chart link popover does not list the colored container and current ticker")
        if "Scanner" in linked_list.inner_text():
            issues.append("Chart link membership incorrectly includes multi-symbol Scanner")
        scanner.locator(".workspace-window-body").click(position={"x": 8, "y": 8})
        if chart.get_by_label("Chart link configuration").count():
            issues.append("Chart link popover remains open after clicking outside it")
        link_button.click()
        color_picker = chart.get_by_label("Chart link color")
        if interaction_screenshot:
            page.screenshot(path=str(interaction_screenshot), full_page=True)
        color_picker.get_by_role("button", name="Assign Chart to Violet").click()
        page.wait_for_timeout(100)
        violet_link_border = link_button.evaluate("element => getComputedStyle(element).borderColor")
        if violet_link_border == initial_link_border:
            issues.append("changing the Chart link color did not change its link-control accent")
        if title_bar.evaluate("element => getComputedStyle(element).backgroundColor") != chart_tint:
            issues.append("changing link color changed the whole Chart title bar")
        link_button.click()
        if "Violet" not in chart.get_by_role("button", name="Link Chart").inner_text():
            issues.append("changing a container link color did not update its title-bar state")
        link_button.click()
        chart.get_by_role("button", name="Unlink Chart").click()
        if chart.get_attribute("data-linked") != "false":
            issues.append("unlinking Chart did not remove its linked title-bar state")
        chart.get_by_role("button", name="Assign Chart to Violet").click()
        link_button.click()

        scanner.get_by_role("button", name="Configure Scanner").click()
        if scanner.get_by_label("Scanner settings").count() != 1 or "Maximum rows" not in scanner.get_by_label("Scanner settings").inner_text():
            issues.append("Scanner row configuration is not separated into its internal settings popover")
        scanner.get_by_role("button", name="Configure Scanner").click()

        if chart.get_by_role("button", name="1D", exact=True).count():
            chart.get_by_role("button", name="1D", exact=True).click()
            if chart.get_by_role("button", name="Fit range", exact=True).count() != 1:
                issues.append("daily chart does not expose the concise fit-range action")
            if chart.get_by_role("button", name="Reset view", exact=True).count() != 1:
                issues.append("daily chart does not expose Reset view")
            chart.get_by_role("button", name="1M", exact=True).click()
            if chart.get_by_role("button", name="Fit range", exact=True).count() != 1:
                issues.append("monthly chart does not expose the concise fit-range action")
            chart.get_by_role("button", name="1m", exact=True).click()

        resize_handle = chart.get_by_role("button", name=re.compile(r"^Resize .+\."))
        resize_box = resize_handle.bounding_box()
        chart_before_resize = chart.bounding_box()
        if not resize_box or not chart_before_resize:
            issues.append("Chart resize handle is not measurable")
        else:
            page.mouse.move(resize_box["x"] + resize_box["width"] / 2, resize_box["y"] + resize_box["height"] / 2)
            page.mouse.down()
            page.mouse.move(resize_box["x"] + resize_box["width"] / 2 + 36, resize_box["y"] + resize_box["height"] / 2 + 28, steps=4)
            page.mouse.up()
            page.wait_for_timeout(100)
            chart_after_resize = chart.bounding_box()
            if not chart_after_resize or chart_after_resize["width"] < chart_before_resize["width"] + 30 or chart_after_resize["height"] < chart_before_resize["height"] + 22:
                issues.append("Chart resize handle does not change both width and height")
            chart.get_by_role("button", name=re.compile(r"^Reset .+ to its default layout$")).click()

        minimize = chart.get_by_role("button", name=re.compile(r"^Minimize .+$"))
        if minimize.locator(".lucide-minus").count() != 1:
            issues.append("minimize action does not use the dedicated minus icon")
        minimize.click()
        restore = chart.get_by_role("button", name=re.compile(r"^Restore .+$"))
        if restore.count() != 1:
            issues.append("Chart did not enter the minimized state")
        elif restore.locator(".lucide-panel-top-open").count() != 1:
            issues.append("restore action does not use a distinct restore icon")
        restore.click()
        chart.get_by_role("button", name=re.compile(r"^Fullscreen .+$")).click()
        exit_fullscreen = chart.get_by_role("button", name=re.compile(r"^Exit fullscreen .+$"))
        if exit_fullscreen.count() != 1:
            issues.append("Chart did not enter the maximized state")
        elif exit_fullscreen.locator(".lucide-minimize-2").count() != 1:
            issues.append("fullscreen exit does not use the inward-arrow icon")
        if chart.get_by_role("button", name=re.compile(r"^Minimize .+$")).locator(".lucide-minus").count() != 1:
            issues.append("fullscreen and title-bar minimize actions are visually ambiguous")
        fullscreen_geometry = page.evaluate("""() => {
            const canvas = document.querySelector('[data-workspace-canvas]');
            const chart = document.querySelector('[data-window-kind="chart"]');
            if (!canvas || !chart) return null;
            const canvasRect = canvas.getBoundingClientRect();
            const chartRect = chart.getBoundingClientRect();
            return {
                canvasBottom: canvasRect.bottom,
                canvasOverflow: getComputedStyle(canvas).overflow,
                chartBottom: chartRect.bottom,
                documentHeight: document.documentElement.scrollHeight,
                viewportHeight: document.documentElement.clientHeight,
            };
        }""")
        if not fullscreen_geometry:
            issues.append("fullscreen geometry is unavailable")
        else:
            if fullscreen_geometry["chartBottom"] > fullscreen_geometry["canvasBottom"] + 1:
                issues.append("fullscreen Chart extends below the Canvas viewport")
            if fullscreen_geometry["canvasOverflow"] != "hidden":
                issues.append("fullscreen Canvas still exposes scrollbars")
            if fullscreen_geometry["documentHeight"] > fullscreen_geometry["viewportHeight"] + 1:
                issues.append("fullscreen Chart makes the document scroll")
        if interaction_screenshot:
            page.screenshot(path=str(interaction_screenshot.with_name(interaction_screenshot.stem + "__fullscreen.png")), full_page=True)
        page.get_by_role("button", name="Canvas management", exact=True).click()
        fullscreen_management = page.get_by_role("complementary", name="Canvas management")
        fullscreen_management.wait_for(state="visible", timeout=5000)
        sidebar_geometry = page.evaluate("""() => {
            const sidebar = document.querySelector('.workspace-management-sidebar');
            const chart = document.querySelector('[data-window-kind="chart"]');
            if (!sidebar || !chart) return null;
            const sidebarRect = sidebar.getBoundingClientRect();
            const chartRect = chart.getBoundingClientRect();
            return { chartRight: chartRect.right, sidebarLeft: sidebarRect.left };
        }""")
        if not sidebar_geometry or sidebar_geometry["chartRight"] > sidebar_geometry["sidebarLeft"] + 1:
            issues.append("fullscreen Chart does not reserve the right management sidebar")
        fullscreen_management.get_by_role("button", name="Close canvas management").click()
        exit_fullscreen.click(force=True)
        chart.get_by_role("button", name=re.compile(r"^Reset .+ to its default layout$")).click(force=True)

        page.get_by_role("button", name="Canvas management", exact=True).click()
        canvas_count_before_group_focus = page.evaluate("""() => {
            const raw = localStorage.getItem('quant-research-workbench.canvas.registry.v1');
            return raw ? JSON.parse(raw).canvases.length : 0;
        }""")
        reusable_group = page.get_by_role("button", name="Open Charts & Quotes group", exact=True)
        if reusable_group.count() != 1:
            issues.append("Canvas management does not expose one reusable Charts & Quotes group")
        else:
            with page.expect_popup(timeout=5000) as reusable_group_popup_info:
                reusable_group.click()
            reusable_group_popup = reusable_group_popup_info.value
            reusable_group_popup.locator('.workspace-window[data-window-kind="charts_quotes"]').wait_for(state="visible", timeout=10000)
            if reusable_group_popup.locator('.workspace-window[data-window-kind="charts_quotes"]').count() != 1:
                issues.append("reusable Charts & Quotes group did not open its fixed composition")
            reusable_group_popup.close()
            canvas_count_after_group_focus = page.evaluate("""() => {
                const raw = localStorage.getItem('quant-research-workbench.canvas.registry.v1');
                return raw ? JSON.parse(raw).canvases.length : 0;
            }""")
            if canvas_count_after_group_focus != canvas_count_before_group_focus:
                issues.append("opening the reusable Charts & Quotes group created a persistent Canvas")
        with page.expect_popup(timeout=5000) as blank_canvas_popup_info:
            page.get_by_role("button", name="New personal canvas", exact=True).click()
        blank_canvas_popup = blank_canvas_popup_info.value
        blank_canvas_popup.locator(".app-shell").wait_for(state="visible", timeout=5000)
        blank_canvas_popup.locator(".workspace-window").first.wait_for(state="visible", timeout=5000)
        if "#canvas-focus" not in blank_canvas_popup.url or blank_canvas_popup.locator(".sidebar").count():
            issues.append("new managed canvas did not open in a chromeless canvas page")
        if blank_canvas_popup.locator(".workspace-window").count() < 1:
            issues.append("new managed canvas opened without inheriting any containers")
        blank_canvas_popup.close()
        page.get_by_role("complementary", name="Canvas management").get_by_role("button", name="Close canvas management").click()

        with page.expect_popup(timeout=5000) as popup_info:
            chart.get_by_role("button", name=re.compile(r"^Open .+ in a new fullscreen canvas$")).click()
        popup = popup_info.value
        popup.locator(".app-shell").wait_for(state="visible", timeout=5000)
        popup_chart = popup.locator('.workspace-window[data-window-kind="chart"]')
        try:
            popup_chart.wait_for(state="visible", timeout=10000)
        except Exception:
            pass
        if "#canvas-focus" not in popup.url or popup.locator(".sidebar").count():
            issues.append("linked container did not open in a chromeless focus canvas")
        if popup_chart.count() != 1:
            issues.append("linked focus canvas does not contain the source Chart")
        popup.close()
        page.get_by_role("button", name="Canvas management", exact=True).click()
        if page.locator(".canvas-manager-items article").count() < 3:
            issues.append("main Canvas manager did not register managed and linked canvases")
        if page.locator(".canvas-manager-open").count() < 2:
            issues.append("registered canvases do not expose their names as open actions")
        page.get_by_role("complementary", name="Canvas management").get_by_role("button", name="Close canvas management").click()

        # Compound-container behavior: two groups can be grouped again. The
        # parent owns the only title bar; member chrome consumes no layout height.
        page.get_by_role("button", name="Add Scanner to group selection", exact=True).evaluate("element => element.click()")
        grouping_tray = page.get_by_role("region", name="Container group selection")
        if "Select another container or group, then confirm." not in grouping_tray.inner_text():
            issues.append("grouping selection does not explain the next step after the first container")
        if not grouping_tray.get_by_role("button", name="Select one more", exact=True).is_disabled():
            issues.append("grouping confirmation is active before a second selection exists")
        page.get_by_role("button", name="Add Portfolio to group selection", exact=True).evaluate("element => element.click()")
        if "Ready to merge under one title bar." not in grouping_tray.inner_text():
            issues.append("grouping selection does not explain the ready-to-merge result")
        grouping_tray.get_by_role("button", name="Create group (2)", exact=True).click()
        first_group = page.locator(".workspace-group-window")
        if first_group.count() != 1 or first_group.locator(".workspace-group-member").count() != 2:
            issues.append("grouping two containers did not create one two-member compound surface")
        if first_group.locator(".workspace-group-member .workspace-window-header").count():
            issues.append("grouped member title bars still consume layout space")
        grouped_fill_errors = first_group.locator(".workspace-group-member").evaluate_all("""(members) => members.flatMap((member) => {
            const body = member.querySelector(':scope > .workspace-window-body');
            const slot = body?.querySelector(':scope > .workspace-content-slot');
            const host = slot?.querySelector(':scope > .workspace-persistent-content-host');
            const content = host?.firstElementChild;
            if (!body || !slot || !host || !content) return ['missing persistent content sizing chain'];
            const bodyRect = body.getBoundingClientRect();
            return [slot, host, content].flatMap((element, index) => {
                const rect = element.getBoundingClientRect();
                return Math.abs(rect.width - bodyRect.width) > 2 || Math.abs(rect.height - bodyRect.height) > 2
                    ? [`content sizing layer ${index + 1} is ${Math.round(rect.width)}x${Math.round(rect.height)} inside ${Math.round(bodyRect.width)}x${Math.round(bodyRect.height)}`]
                    : [];
            });
        })""")
        if grouped_fill_errors:
            issues.append(f"grouped container content does not fill its member: {grouped_fill_errors[0]}")
        page.get_by_role("button", name="Clear group selection", exact=True).click()

        for title in ("Orders & Fills", "Position Manager"):
            page.get_by_role("button", name=f"Add {title} to group selection", exact=True).click(force=True)
        page.get_by_role("button", name="Create group (2)", exact=True).click()
        page.get_by_role("button", name="Clear group selection", exact=True).click()
        group_selectors = page.locator(".workspace-group-window > .workspace-group-header .workspace-group-select")
        if group_selectors.count() != 2:
            issues.append("two independent container groups are not exposed as selectable roots")
        else:
            group_selectors.nth(0).evaluate("element => element.click()")
            group_selectors.nth(1).evaluate("element => element.click()")
            page.get_by_role("button", name="Create parent group (2)", exact=True).click()
            page.wait_for_timeout(100)
            persisted = page.evaluate("""() => {
                const raw = localStorage.getItem('quant-research-workbench.trading-workspace.global.v1');
                return raw ? JSON.parse(raw) : null;
            }""")
            if not persisted or len(persisted.get("groups", {})) != 3:
                issues.append("grouping two groups did not persist the nested hierarchy")

        root_group = page.locator(".workspace-group-window")
        if root_group.count() != 1 or root_group.locator(".workspace-group-member").count() != 4:
            issues.append("nested group does not render all descendant containers under one title bar")
        else:
            root_header = root_group.locator(":scope > .workspace-group-header")
            root_title = root_header.locator(".workspace-window-heading strong").inner_text()
            if root_header.get_by_role("button", name=f"Close {root_title}", exact=True).count() != 1:
                issues.append("group title bar does not expose a close action")

            page.get_by_role("button", name="Canvas management", exact=True).click()
            group_manager = page.get_by_role("region", name="Workspace groups", exact=True)
            if group_manager.locator(".workspace-group-manager-row").count() != 3:
                issues.append("Manage does not list every persisted nested and root group")
            root_manager_row = group_manager.locator('.workspace-group-manager-row[data-root="true"]')
            root_manager_row.get_by_role("button", name=f"Rename {root_title}", exact=True).click()
            rename_input = root_manager_row.get_by_role("textbox", name=f"Rename {root_title}", exact=True)
            rename_input.fill("Research Workflow")
            root_manager_row.get_by_role("button", name="Save", exact=True).click()
            if root_header.locator(".workspace-window-heading strong").inner_text() != "Research Workflow":
                issues.append("renaming a group in Manage does not update its shared title bar")
            page.locator(".workspace-management-sidebar > header").get_by_role("button", name="Close canvas management", exact=True).click()

            root_header.get_by_role("button", name="Close Research Workflow", exact=True).click()
            if page.locator(".workspace-group-window").count():
                issues.append("closing a group does not remove its compound surface from the Canvas")
            page.get_by_role("button", name="Canvas management", exact=True).click()
            group_manager = page.get_by_role("region", name="Workspace groups", exact=True)
            root_manager_row = group_manager.locator('.workspace-group-manager-row[data-root="true"]')
            if root_manager_row.get_by_text("Research Workflow", exact=True).count() != 1:
                issues.append("closed group name is not retained in Manage")
            if "Closed" not in root_manager_row.inner_text():
                issues.append("Manage does not identify a closed group")
            persisted_closed_group = page.evaluate("""() => {
                const raw = localStorage.getItem('quant-research-workbench.trading-workspace.global.v1');
                if (!raw) return null;
                const groups = Object.values(JSON.parse(raw).groups || {});
                return groups.find((group) => group.title === 'Research Workflow') || null;
            }""")
            if not persisted_closed_group or not persisted_closed_group.get("closed"):
                issues.append("closed and renamed group lifecycle is not persisted")
            if interaction_screenshot:
                page.screenshot(path=str(interaction_screenshot.with_name(interaction_screenshot.stem + "__groups-management.png")), full_page=True)
            root_manager_row.get_by_role("button", name="Show Research Workflow on Canvas", exact=True).click()
            page.locator(".workspace-management-sidebar > header").get_by_role("button", name="Close canvas management", exact=True).click()
            root_group = page.locator(".workspace-group-window")
            if root_group.count() != 1 or root_group.locator(":scope > .workspace-group-header .workspace-window-heading strong").inner_text() != "Research Workflow":
                issues.append("Manage cannot restore a closed named group")
            minimize_group = root_group.get_by_role("button", name=re.compile(r"^Minimize .+$"))
            minimize_group.click()
            if root_group.locator(".workspace-group-body").count():
                issues.append("minimizing a group did not hide the complete compound body")
            root_group.get_by_role("button", name=re.compile(r"^Restore .+$")).click()
            root_group.get_by_role("button", name=re.compile(r"^Fullscreen .+$")).click(force=True)
            if root_group.get_by_role("button", name=re.compile(r"^Exit fullscreen .+$")).count() != 1:
                issues.append("fullscreen did not apply to the complete container group")
            root_group.get_by_role("button", name=re.compile(r"^Exit fullscreen .+$")).click(force=True)

        if interaction_screenshot:
            page.screenshot(path=str(interaction_screenshot.with_name(interaction_screenshot.stem + "__grouped.png")), full_page=True)

        page.reload(wait_until="domcontentloaded")
        page.locator(".workspace-group-window").wait_for(state="visible", timeout=5000)
        if page.locator(".workspace-group-window .workspace-group-member").count() != 4:
            issues.append("compound group hierarchy did not survive a page reload")
        if page.locator(".workspace-group-window > .workspace-group-header .workspace-window-heading strong").inner_text() != "Research Workflow":
            issues.append("renamed group title did not survive a page reload")
    except Exception as exc:
        issues.append(f"Canvas interaction check failed: {exc}")
    return issues


def validate_service_interactions(page: Any, scenario: dict[str, Any], interaction_screenshot: Path | None) -> list[str]:
    issues: list[str] = []
    if not scenario["page"].startswith("service-") or scenario["page"] == "services-dashboard":
        return issues
    authority_panel = page.locator(".service-operational-authority-panel")
    if authority_panel.count() != 1:
        issues.append("service detail does not expose exactly one operational authority panel")
    elif authority_panel.locator(".service-operational-authority-metric").count() != 6:
        issues.append("service operational authority does not expose all six contract metrics")
    if scenario["page"] in {"service-news", "service-sec"}:
        histogram_bins = page.locator(".service-histogram-bin.has-data")
        if not histogram_bins.count():
            issues.append("service histogram has no reviewable populated bin")
        else:
            histogram_bins.first.focus()
            if not page.locator(".service-histogram-hover").is_visible():
                issues.append("service histogram does not expose bin detail to keyboard focus")
    if scenario["page"] == "service-news":
        rows = page.locator(".news-today-table tbody tr")
        if not rows.count():
            return ["inserted News table has no reviewable rows"]
        first_row_box = rows.first.bounding_box()
        if not first_row_box or first_row_box["height"] < 1:
            issues.append("inserted News table rows collapse out of the visible layout")
        rows.first.focus()
        rows.first.press("Enter")
        modal = page.get_by_role("dialog", name="Inserted News Detail")
        try:
            modal.wait_for(state="visible", timeout=5000)
        except Exception:
            return ["inserted News row does not open its detail dialog"]
        readable_body = modal.locator(".news-full-readable-body")
        try:
            readable_body.get_by_text(re.compile(r"One canonical contract", re.IGNORECASE)).wait_for(state="visible", timeout=5000)
        except Exception:
            issues.append("News detail does not render the normalized readable article body")
        technical = modal.locator(".news-full-technical-section")
        technical.locator(":scope > summary").click()
        if modal.get_by_text("Actual Database Values", exact=True).count() != 1 or modal.locator(".news-full-metadata-table").count() != 1:
            issues.append("News technical detail omits the shared metadata table")
        if interaction_screenshot:
            page.screenshot(path=str(interaction_screenshot), full_page=True)
        page.keyboard.press("Escape")
        try:
            modal.wait_for(state="hidden", timeout=5000)
        except Exception:
            issues.append("Escape does not close the inserted News detail dialog")
        history_dialogs = (
            (
                ".news-publish-card:not(.news-enrichment-card):not(.news-coverage-card) .news-publish-history-table tbody tr",
                "News Publish Detail",
                "publish",
            ),
            (
                ".news-enrichment-card .news-enrichment-history-table tbody tr",
                "News Enrichment Detail",
                "enrichment",
            ),
            (
                ".news-coverage-card .news-coverage-history-table tbody tr",
                "Coverage / Gap Fill Detail",
                "coverage",
            ),
        )
        for selector, dialog_name, evidence_name in history_dialogs:
            history_rows = page.locator(selector)
            if not history_rows.count():
                issues.append(f"News {evidence_name} history has no reviewable rows")
                continue
            history_rows.first.focus()
            history_rows.first.press("Enter")
            history_modal = page.get_by_role("dialog", name=dialog_name)
            try:
                history_modal.wait_for(state="visible", timeout=5000)
            except Exception:
                issues.append(f"News {evidence_name} row does not open its detail dialog")
                continue
            if interaction_screenshot:
                page.screenshot(
                    path=str(interaction_screenshot.with_name(
                        interaction_screenshot.stem + f"__{evidence_name}.png"
                    )),
                    full_page=True,
                )
            page.keyboard.press("Escape")
            try:
                history_modal.wait_for(state="hidden", timeout=5000)
            except Exception:
                issues.append(f"Escape does not close the News {evidence_name} detail dialog")
        return issues
    if scenario["page"] == "service-sec":
        rows = page.locator(".news-today-table tbody tr")
        if not rows.count():
            return ["SEC filings table has no reviewable rows"]
        rows.first.focus()
        rows.first.press("Enter")
        modal = page.get_by_role("dialog", name="SEC Filing Detail")
        try:
            modal.wait_for(state="visible", timeout=5000)
        except Exception:
            return ["SEC filing row does not open its detail dialog"]
        readable_body = modal.locator(".sec-filing-readable-body")
        try:
            readable_body.get_by_text(re.compile(r"deterministic filing text", re.IGNORECASE)).wait_for(state="visible", timeout=5000)
        except Exception:
            issues.append("SEC detail does not render the normalized readable filing body")
        document_cards = modal.locator(".sec-filing-document-card")
        try:
            document_cards.first.wait_for(state="visible", timeout=5000)
        except Exception:
            pass
        if document_cards.count() != 1:
            issues.append("SEC detail does not reconcile its filing document with readable text")
        filing_parent = modal.locator(".sec-filing-data-sections details").filter(has_text="Filing Parent Row")
        if filing_parent.count() != 1:
            issues.append("SEC detail omits its filing parent source row")
        else:
            filing_parent.evaluate("element => { element.open = true; }")
            if filing_parent.locator(".news-full-metadata-table").count() != 1:
                issues.append("SEC filing parent detail omits the shared metadata table")
        if interaction_screenshot:
            page.screenshot(path=str(interaction_screenshot), full_page=True)
        page.keyboard.press("Escape")
        try:
            modal.wait_for(state="hidden", timeout=5000)
        except Exception:
            issues.append("Escape does not close the SEC filing detail dialog")
        return issues
    activity = page.locator(".service-activity-table")
    if activity.count() != 1:
        return ["service detail does not expose exactly one activity table"]
    rows = activity.locator("tbody tr")
    if not rows.count():
        return ["service activity table has no reviewable rows"]
    rows.first.click()
    modal = page.get_by_role("dialog", name=re.compile(r"Activity Detail$"))
    try:
        modal.wait_for(state="visible", timeout=5000)
    except Exception:
        return ["service activity row does not open its detail dialog"]
    if modal.get_by_text("Raw Service Activity Row", exact=True).count() != 1:
        issues.append("service activity detail omits the raw source evidence")
    if interaction_screenshot:
        page.screenshot(path=str(interaction_screenshot), full_page=True)
    page.keyboard.press("Escape")
    try:
        modal.wait_for(state="hidden", timeout=5000)
    except Exception:
        issues.append("Escape does not close the service activity detail dialog")
    return issues


def capture(args: argparse.Namespace) -> int:
    from playwright.sync_api import sync_playwright

    scenarios = build_scenarios(args)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    default_review_root = Path(
        os.environ.get(
            "QW_FRONTEND_REVIEW_ROOT",
            r"D:\TradingML\runtimes\quant-research-workbench\frontend-ui-review",
        )
    )
    default_output = default_review_root / timestamp
    output_dir = Path(args.output_dir or default_output)
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    capture_failures = 0
    objective_issues = 0
    base_url = args.url.rstrip("/")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        try:
            for index, scenario in enumerate(scenarios, start=1):
                context = browser.new_context(viewport=scenario["viewport"])
                scale_value = str(scenario["scale"]).rstrip("0").rstrip(".")
                context.add_init_script(
                    "localStorage.setItem('quant-research-workbench.theme', "
                    + json.dumps(scenario["theme"])
                    + "); localStorage.setItem('quant-research-workbench.ui-scale', "
                    + json.dumps(scale_value)
                    + ");"
                )
                if args.configuration_experience:
                    context.add_init_script(
                        "localStorage.setItem('trading-configuration-experience', "
                        + json.dumps(args.configuration_experience)
                        + "); sessionStorage.setItem('configuration-studio-started', 'true');"
                    )
                if args.canvas_visible_indicators:
                    canvas_settings = {
                        "version": 8,
                        "chart": {
                            "showVolume": True,
                            "symbol": "AAPL",
                            "timeframe": args.canvas_chart_timeframe,
                            "visibleIndicators": [
                                value.strip()
                                for value in args.canvas_visible_indicators.split(",")
                                if value.strip()
                            ],
                        },
                    }
                    context.add_init_script(
                        "localStorage.setItem('quant-research-workbench.canvas.container-settings.v1', "
                        + json.dumps(json.dumps(canvas_settings))
                        + ");"
                    )
                if args.canvas_session_date:
                    preview_context = {
                        "previewTime": args.canvas_preview_time,
                        "sessionDate": args.canvas_session_date,
                    }
                    context.add_init_script(
                        "localStorage.setItem('quant-research-workbench.canvas.preview-context.v1', "
                        + json.dumps(json.dumps(preview_context))
                        + ");"
                    )
                if scenario["page"] == "canvas-focus":
                    focus_id = args.canvas_id or "review-focus"
                    focus_container = "positions" if args.canvas_position_manager else "charts_quotes" if args.canvas_charts_quotes else "chart"
                    focus_layout = {
                        focus_container: {
                            "fullscreen": True,
                            "h": max(320, round(scenario["viewport"]["height"] / scenario["scale"]) - 62),
                            "minimized": False,
                            "w": max(680, round(scenario["viewport"]["width"] / scenario["scale"])),
                            "x": 0,
                            "y": 0,
                            "z": 1,
                        },
                    }
                    focus_state = {"layoutVersion": 3, "layouts": focus_layout, "openIds": [focus_container]}
                    focus_registry = {
                        "version": 1,
                        "canvases": [{"id": "main", "label": "Main"}, {"id": focus_id, "label": "Position focus" if args.canvas_position_manager else "Chart focus"}],
                        "linkAssignments": {focus_container: "A"},
                        "linkContexts": {
                            "A": {"symbol": args.canvas_symbol, "timeframe": args.canvas_chart_timeframe},
                            "B": {"symbol": "MSFT", "timeframe": "1m"},
                            "C": {"symbol": "NVDA", "timeframe": "5m"},
                        },
                    }
                    focus_chart_settings = {
                        "version": 8,
                        "chart": {
                            "showVolume": True,
                            "symbol": args.canvas_symbol,
                            "timeframe": args.canvas_chart_timeframe,
                            "visibleIndicators": [
                                value.strip()
                                for value in (args.canvas_visible_indicators or "indicator.vwap,indicator.macd,indicator.qmd_decision,indicator.qmd_generic_structure,indicator.qmd_level_footprint").split(",")
                                if value.strip()
                            ],
                        },
                        "charts_quotes": {
                            "main": {"showVolume": True, "symbol": args.canvas_symbol, "timeframe": args.canvas_chart_timeframe if args.historical_run_id else "10s", "visibleIndicators": []},
                            "month": {"showVolume": True, "symbol": args.canvas_symbol, "timeframe": "1mo", "visibleIndicators": []},
                            "daily": {"showVolume": True, "symbol": args.canvas_symbol, "timeframe": "1d", "visibleIndicators": []},
                        },
                    }
                    context.add_init_script(
                        "localStorage.setItem('quant-research-workbench.canvas.registry.v1', "
                        + json.dumps(json.dumps(focus_registry))
                        + "); localStorage.setItem("
                        + json.dumps(f"quant-research-workbench.trading-workspace.canvas.{focus_id}.v1")
                        + ", " + json.dumps(json.dumps(focus_state))
                        + "); localStorage.setItem('quant-research-workbench.canvas.container-settings.v1', "
                        + json.dumps(json.dumps(focus_chart_settings)) + ");"
                    )
                if args.seed_core_containers and args.canvas_id and scenario["page"] == "real-live-trading":
                    viewport_width = scenario["viewport"]["width"]
                    viewport_height = scenario["viewport"]["height"]
                    width = max(1180, viewport_width - 112)
                    height = max(780, viewport_height - 86)
                    content_top = 108
                    content_height = max(560, height - content_top - 12)
                    left_width = min(round(width * 0.44), max(480, round(width * 0.38)))
                    top_height = min(210, max(180, content_height - 290))
                    layouts = {
                        "portfolio": {"fullscreen": False, "h": top_height, "minimized": False, "w": left_width, "x": 12, "y": content_top, "z": 1},
                        "scanner": {"fullscreen": False, "h": max(280, content_height - top_height - 10), "minimized": False, "w": left_width, "x": 12, "y": content_top + top_height + 10, "z": 2},
                        "chart": {"fullscreen": False, "h": content_height, "minimized": False, "w": max(520, width - left_width - 34), "x": left_width + 22, "y": content_top, "z": 3},
                    }
                    storage_prefix = "quant-research-workbench.real-live-trading.layout"
                    storage_payload = {"chartWindows": [], "layoutVersion": 4, "layouts": layouts, "windows": ["portfolio", "scanner"]}
                    context.add_init_script(
                        "localStorage.setItem(" + json.dumps(f"{storage_prefix}.{args.canvas_id}") + ", " + json.dumps(json.dumps(storage_payload)) + ");"
                    )
                page = context.new_page()
                if args.stub_service_status and (scenario["page"] == "services-dashboard" or scenario["page"].startswith("service-")):
                    service_id = None if scenario["page"] == "services-dashboard" else scenario["page"].removeprefix("service-")
                    fleet = [service_status_fixture(item) for item in SERVICE_REVIEW_LABELS] if service_id is None else [service_status_fixture(service_id)]
                    service_fixture = fleet[0]
                    fleet_body = json.dumps({"checked_at_utc": service_fixture["checked_at_utc"], "services": fleet})
                    budget_body = json.dumps({"schema_version": 1, "wait_timeout_seconds": 5, "lanes": {}})
                    if service_id is not None:
                        page.route(
                            f"**/api/services/{service_id}/status?**",
                            fulfill_json(json.dumps(service_fixture)),
                        )
                    page.route(
                        "**/api/services/status?**",
                        fulfill_json(fleet_body),
                    )
                    page.route(
                        "**/api/system/workload-budgets",
                        fulfill_json(budget_body),
                    )
                    if service_id == "news":
                        page.route("**/api/services/news/today?**", fulfill_json(json.dumps(news_today_fixture())))
                        page.route("**/api/services/news/detail/**", fulfill_json(json.dumps(news_detail_fixture())))
                        page.route(
                            "**/api/services/news/histogram",
                            fulfill_json(json.dumps({
                                "bin_seconds": 900,
                                "rows": [{"bucket_utc": "2026-08-21T21:45:00Z", "broad_or_none_rows": 0, "single_ticker_rows": 1, "total_rows": 1}],
                                "window_end_utc": "2026-08-21T22:00:00Z",
                                "window_start_utc": "2026-08-21T13:30:00Z",
                            })),
                        )
                    if service_id == "sec":
                        page.route("**/api/services/sec/today?**", fulfill_json(json.dumps(sec_today_fixture())))
                        page.route("**/api/services/sec/detail/**", fulfill_json(json.dumps(sec_detail_fixture())))
                if args.canvas_session_date:
                    page.route(
                        "**/api/trading/canvas-context",
                        lambda route: route.fulfill(
                            content_type="application/json",
                            body=json.dumps({
                                "preview_time": args.canvas_preview_time,
                                "session_date": args.canvas_session_date,
                                "coverage": {"source": "ui-review-seed"},
                            }),
                        ),
                    )
                if scenario["page"] == "canvas-focus":
                    page.route(
                        "**/api/trading/canvas-profile",
                        lambda route: route.fulfill(
                            content_type="application/json",
                            body=json.dumps({"available": False, "profile": None}),
                        ),
                    )
                if args.stub_chart_history:
                    fixture_date = args.canvas_session_date or "2026-08-20"
                    page.route(
                        "**/api/trading/canvas-chart/history**",
                        lambda route: route.fulfill(
                            content_type="application/json",
                            body=json.dumps(
                                daily_chart_history_fixture(fixture_date)
                                if "timeframe=1d" in route.request.url
                                else chart_history_fixture(fixture_date)
                            ),
                        ),
                    )
                if args.stub_split_events:
                    split_date = (datetime.fromisoformat(args.canvas_session_date or "2026-08-20") - timedelta(days=5)).date().isoformat()
                    page.route(
                        "**/api/trading/ticker-facts/*/splits**",
                        fulfill_json(json.dumps({
                            "as_of": f"{args.canvas_session_date or '2026-08-20'}T20:00:00+00:00",
                            "events": [{
                                "available_at": f"{split_date}T12:00:00Z",
                                "direction": "forward",
                                "execution_date": split_date,
                                "id": f"stock-split:{split_date}:1:5",
                                "ratio": 5,
                                "source": "q_live.market_stock_split_v1",
                                "split_from": 1,
                                "split_to": 5,
                            }],
                            "row_count": 1,
                            "status": "ready",
                            "symbol": args.canvas_symbol,
                        })),
                    )
                console_errors: list[str] = []
                page_errors: list[str] = []
                page_crashes: list[str] = []
                failed_requests: list[str] = []
                page.on(
                    "console",
                    lambda message: console_errors.append(message.text)
                    if message.type == "error" else None,
                )
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.on("crash", lambda: page_crashes.append("Chromium renderer process crashed"))
                page.on(
                    "requestfailed",
                    lambda request: failed_requests.append(
                        f"{request.method} {request.url}: {request.failure}"
                    ),
                )
                page.on(
                    "response",
                    lambda response: failed_requests.append(
                        f"HTTP {response.status} {response.request.method} {response.url}"
                    )
                    if response.status >= 400
                    else None,
                )

                filename = (
                    f"{scenario['page']}__{scenario['theme']}"
                    f"__s{slug_scale(scenario['scale'])}"
                    f"__{scenario['viewport_name']}.png"
                )
                screenshot_path = output_dir / filename
                canvas_query = ""
                if args.canvas_id and scenario["page"] == "real-live-trading":
                    canvas_query = f"?liveCanvas={args.canvas_id}"
                elif scenario["page"] == "canvas-focus":
                    canvas_query = f"?canvas={args.canvas_id or 'review-focus'}&canvas_profile=draft"
                    if args.historical_run_id:
                        canvas_query = f"?replay_run={args.historical_run_id}&historical_mode=backtest"
                    if args.canvas_runtime_mode:
                        canvas_query += f"&runtime_mode={args.canvas_runtime_mode}"
                elif args.seed_core_containers and scenario["page"] == "replay-trading":
                    canvas_query = "?historicalWorkspace=replay"
                elif args.historical_run_id and scenario["page"] == "backtest-trading":
                    canvas_query = f"?backtest_run={args.historical_run_id}"
                elif args.seed_core_containers and scenario["page"] == "backtest-trading":
                    canvas_query = "?historicalWorkspace=backtest"
                result = {**scenario, "url": f"{base_url}/{canvas_query}#{scenario['page']}"}
                try:
                    page.goto(
                        result["url"], wait_until="domcontentloaded",
                        timeout=args.timeout_ms,
                    )
                    page.locator(".app-shell").wait_for(
                        state="visible", timeout=args.timeout_ms,
                    )
                    if args.historical_run_id and scenario["page"] == "canvas-focus":
                        page.get_by_role("button", name=args.canvas_chart_timeframe, exact=True).click(timeout=args.timeout_ms)
                    page.wait_for_timeout(args.settle_ms)
                    metrics = page.evaluate("""() => {
                        const root = document.documentElement;
                        const shell = document.querySelector('.app-shell');
                        const shellStyle = shell ? getComputedStyle(shell) : null;
                        return {
                            title: document.title,
                            bodyTextLength: (document.body.innerText || '').trim().length,
                            appShellPresent: Boolean(shell),
                            documentWidth: root.scrollWidth,
                            viewportWidth: root.clientWidth,
                            horizontalOverflow: root.scrollWidth > root.clientWidth + 1,
                            overflowingElements: Array.from(document.querySelectorAll('body *'))
                                .map((element) => {
                                    const rect = element.getBoundingClientRect();
                                    return { className: element.className || element.tagName, right: Math.round(rect.right), width: Math.round(rect.width) };
                                })
                                .filter((entry) => entry.right > root.clientWidth + 1)
                                .sort((a, b) => b.right - a.right)
                                .slice(0, 8),
                            scrollOverflowElements: Array.from(document.querySelectorAll('body *'))
                                .map((element) => ({ className: element.className || element.tagName, clientWidth: element.clientWidth, scrollWidth: element.scrollWidth }))
                                .filter((entry) => entry.scrollWidth > entry.clientWidth + 1)
                                .sort((a, b) => b.scrollWidth - a.scrollWidth)
                                .slice(0, 8),
                            resolvedTheme: [
                                'light', 'slate', 'parchment', 'dawn', 'harbor',
                                'dark', 'forest', 'graphite', 'ember', 'amethyst',
                            ].find((theme) => root.classList.contains(theme)) || null,
                            resolvedScale: shellStyle
                                ? shellStyle.getPropertyValue('--app-zoom').trim()
                                : null,
                            canvasGeometry: ['.focus-app-main', '.canvas-focus-page', '.trading-workspace-shell', '.trading-workspace-canvas', '.workspace-window[data-window-kind="chart"]']
                                .map((selector) => {
                                    const element = document.querySelector(selector);
                                    const rect = element?.getBoundingClientRect();
                                    const style = element ? getComputedStyle(element) : null;
                                    return {
                                        selector,
                                        height: rect ? Math.round(rect.height) : null,
                                        top: rect ? Math.round(rect.top) : null,
                                        computedHeight: style?.height || null,
                                        inlineStyle: element?.getAttribute('style') || null,
                                    };
                                }),
                        };
                    }""")
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    issues: list[str] = []
                    if not metrics["appShellPresent"]:
                        issues.append("app shell is missing")
                    if metrics["bodyTextLength"] < 20:
                        issues.append("rendered body is unexpectedly empty")
                    if metrics["resolvedTheme"] != scenario["theme"]:
                        issues.append(
                            f"theme resolved as {metrics['resolvedTheme']!r}, "
                            f"expected {scenario['theme']!r}"
                        )
                    expected_scale = float(scenario["scale"])
                    try:
                        resolved_scale = float(metrics["resolvedScale"])
                    except (TypeError, ValueError):
                        resolved_scale = None
                    if resolved_scale is None or abs(resolved_scale - expected_scale) > 0.001:
                        issues.append(
                            f"scale resolved as {metrics['resolvedScale']!r}, "
                            f"expected {expected_scale}"
                        )
                    if metrics["horizontalOverflow"]:
                        issues.append(
                            f"document overflows horizontally ({metrics['documentWidth']} > "
                            f"{metrics['viewportWidth']})"
                        )
                    if (
                        scenario["page"] == "services-dashboard" or scenario["page"].startswith("service-")
                    ) and page.locator(".services-page-loading-overlay").count():
                        issues.append("service page remained behind its blocking loading overlay")
                    interaction_screenshot = screenshot_path.with_name(
                        f"{screenshot_path.stem}__link-config.png"
                    ) if (
                        scenario["page"] == "canvas-configuration"
                        and scenario["theme"] == "light"
                        and scenario["scale"] == 1.0
                        and scenario["viewport_name"] == "normal"
                    ) else screenshot_path.with_name(
                        f"{screenshot_path.stem}__split-marker.png"
                    ) if (
                        args.stub_split_events
                        and scenario["page"] == "canvas-focus"
                        and scenario["theme"] == "light"
                        and scenario["scale"] == 1.0
                    ) else screenshot_path.with_name(
                        f"{screenshot_path.stem}__qmd-review.png"
                    ) if (
                        scenario["page"] == "canvas-focus"
                        and "indicator.qmd_generic_structure" in str(args.canvas_visible_indicators or "")
                    ) else screenshot_path.with_name(
                        f"{screenshot_path.stem}__service-detail.png"
                    ) if (
                        args.stub_service_status
                        and scenario["page"] in {"service-news", "service-qmd", "service-sec"}
                        and scenario["theme"] == "light"
                        and scenario["scale"] == 1.0
                        and scenario["viewport_name"] == "normal"
                    ) else screenshot_path.with_name(f"{screenshot_path.stem}__chart-interaction.png") if scenario["page"] == "canvas-focus" else None
                    issues.extend(validate_canvas_interactions(
                        page, scenario, interaction_screenshot,
                        args.canvas_chart_timeframe, args.chart_stress_cycles,
                        args.chart_stress_pattern, args.chart_stress_only,
                        args.watchlist_close_only,
                    ))
                    if args.stub_service_status:
                        issues.extend(validate_service_interactions(page, scenario, interaction_screenshot))
                    objective_issues += len(issues)
                    result.update({
                        "status": "captured",
                        "screenshot": str(screenshot_path),
                        "interaction_screenshot": str(interaction_screenshot) if interaction_screenshot else None,
                        "metrics": metrics,
                        "issues": issues,
                    })
                except Exception as exc:
                    capture_failures += 1
                    result.update({
                        "status": "capture_failed", "error": str(exc), "issues": [],
                    })
                finally:
                    result["console_errors"] = console_errors
                    result["page_errors"] = page_errors
                    result["page_crashes"] = page_crashes
                    result["failed_requests"] = failed_requests
                    results.append(result)
                    context.close()
                print(f"[{index}/{len(scenarios)}] {result['status']}: {filename}", flush=True)
        finally:
            browser.close()

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "matrix": args.matrix,
        "base_url": base_url,
        "scenario_count": len(scenarios),
        "capture_failures": capture_failures,
        "objective_issue_count": objective_issues,
        "results": results,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Review evidence: {output_dir}")
    print(f"Manifest: {manifest_path}")
    print(
        f"Captured {len(scenarios) - capture_failures}/{len(scenarios)} scenarios; "
        f"objective issues: {objective_issues}."
    )
    return 1 if capture_failures or (args.strict and objective_issues) else 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Capture route, theme, scale, and viewport evidence for UX review."
    )
    result.add_argument("--url", default="http://127.0.0.1:5173")
    result.add_argument("--canvas-id", help="open trading routes directly in the named child canvas")
    result.add_argument("--historical-run-id", help="review a portable Backtest Canvas or restore the main Backtest page by run ID")
    result.add_argument("--canvas-session-date", help="seed a deterministic Canvas preview session date (YYYY-MM-DD)")
    result.add_argument("--canvas-preview-time", default="09:45", help="preview time paired with --canvas-session-date (HH:MM)")
    result.add_argument("--canvas-runtime-mode", choices=("live", "paper"), help="open Canvas focus review under the selected real-time runtime authority")
    result.add_argument("--canvas-symbol", default="AAPL", help="symbol seeded into the Canvas focus review")
    result.add_argument("--canvas-chart-timeframe", default="1m", help="timeframe selected before Canvas interaction stress")
    result.add_argument("--canvas-visible-indicators", help="comma-separated Canvas chart indicator IDs to seed before capture")
    result.add_argument("--chart-stress-cycles", type=int, default=24, help="mixed pan, zoom, and axis-scale cycles in the Canvas interaction stress")
    result.add_argument("--chart-stress-pattern", choices=("mixed", "pathological", "left-paging"), default="mixed", help="alternate gestures, accumulate them in one direction, or force left-edge history paging")
    result.add_argument("--chart-stress-only", action="store_true", help="stop the Canvas interaction review after chart stress")
    result.add_argument("--stub-chart-history", action="store_true", help="use deterministic chart history for frontend-only renderer and interaction QA")
    result.add_argument("--canvas-charts-quotes", action="store_true", help="seed the Charts & Quotes container in Canvas focus review")
    result.add_argument("--canvas-position-manager", action="store_true", help="seed the Position Manager container in Canvas focus review")
    result.add_argument("--stub-split-events", action="store_true", help="use a deterministic stock-split event for daily chart QA")
    result.add_argument("--stub-service-status", action="store_true", help="use deterministic service contracts for frontend-only Services detail QA")
    result.add_argument("--watchlist-close-only", action="store_true", help="stop after the Watch Universe close and persistence regression")
    result.add_argument("--seed-core-containers", action="store_true", help="seed portfolio and scanner containers for child-canvas review")
    result.add_argument("--mode", choices=("targeted", "full"), default="targeted")
    result.add_argument("--matrix", choices=("bounded", "exhaustive"), default="bounded")
    result.add_argument("--page", action="append", choices=PAGES)
    result.add_argument("--theme", action="append", choices=THEMES)
    result.add_argument("--scale", action="append", type=float, choices=SCALES)
    result.add_argument(
        "--viewport", action="append", type=parse_viewport,
        metavar="NAME:WIDTHxHEIGHT",
    )
    result.add_argument("--output-dir")
    result.add_argument("--settle-ms", type=int, default=1500)
    result.add_argument("--timeout-ms", type=int, default=15000)
    result.add_argument("--headed", action="store_true")
    result.add_argument(
        "--configuration-experience",
        choices=("guided", "expert"),
        help="seed the non-Canvas configuration editor mode before capture",
    )
    result.add_argument(
        "--strict", action="store_true",
        help="return non-zero for objective layout, theme, scale, or blank-page issues",
    )
    return result


def main() -> int:
    ensure_playwright()
    return capture(parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
