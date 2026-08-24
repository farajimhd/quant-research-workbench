from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    sql_string,
)
from research.text_intelligence.news_synthesis_v1.engine import ENGINE_VERSION
from src.backend.discovery_projection import discovery_runtime_field
from src.backend.qmd_gateway_client import qmd_append_signal_stream_rows
from src.backend.signal_stream_runtime_service import signal_stream_session
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.watchlist_resolver import evaluate_rule_sets_frame


NEWS_SIGNAL_CHECKPOINT = "market-discovery:news-signal-cursor"
NEWS_SIGNAL_STREAM_ID = "bullish-news-v1"


def news_synthesis_events(
    *,
    start_at: datetime,
    as_of: datetime,
    client: ClickHouseHttpClient | None = None,
    limit: int = 2_000,
    after_updated_at: datetime | None = None,
    after_canonical_id: str = "",
) -> list[dict[str, Any]]:
    """Read bounded, production-version News Synthesis rows by availability."""

    if start_at.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("News Synthesis bounds must be timezone-aware")
    owned = client is None
    active = client or ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
        timeout_seconds=10,
    )
    cursor_clause = ""
    if after_updated_at is not None:
        cursor_clause = f"""
  AND (greatest(s.updated_at_utc,ifNull(f.created_at_utc,s.updated_at_utc)), s.canonical_news_id) > (
      parseDateTime64BestEffort({sql_string(after_updated_at.astimezone(UTC).isoformat())}),
      {sql_string(after_canonical_id)}
  )"""
    sql = f"""
SELECT s.canonical_news_id,
       toString(s.published_at_utc) AS published_at_text,
       s.engine_version,
       s.synthesis_json,
       f.stage AS funnel_stage,
       f.forecast_eligibility AS funnel_forecast_eligibility,
       f.eligible_probability AS funnel_eligible_probability,
       toString(greatest(s.updated_at_utc,ifNull(f.created_at_utc,s.updated_at_utc))) AS updated_at_text
FROM `q_live`.`news_synthesis_v1` AS s FINAL
LEFT JOIN `q_live`.`news_forecast_funnel_v1` AS f FINAL
  ON f.canonical_news_id=s.canonical_news_id
WHERE s.engine_version={sql_string(ENGINE_VERSION)}
  AND greatest(s.updated_at_utc,ifNull(f.created_at_utc,s.updated_at_utc))>=parseDateTime64BestEffort({sql_string(start_at.astimezone(UTC).isoformat())})
  AND greatest(s.updated_at_utc,ifNull(f.created_at_utc,s.updated_at_utc))<=parseDateTime64BestEffort({sql_string(as_of.astimezone(UTC).isoformat())})
{cursor_clause}
ORDER BY greatest(s.updated_at_utc,ifNull(f.created_at_utc,s.updated_at_utc)),s.canonical_news_id
LIMIT {max(1, min(int(limit), 10_000))}
FORMAT JSONEachRow
"""
    try:
        rows = list(active.iter_json_each_row(sql))
        return [
            {
                **{
                    key: value
                    for key, value in row.items()
                    if key not in {"published_at_text", "updated_at_text"}
                },
                "published_at_utc": row.get("published_at_text"),
                "updated_at_utc": row.get("updated_at_text"),
            }
            for row in rows
        ]
    finally:
        if owned:
            active.close()


def all_news_synthesis_events(
    *,
    start_at: datetime,
    as_of: datetime,
    page_size: int = 2_000,
    loader: Callable[..., list[dict[str, Any]]] = news_synthesis_events,
) -> list[dict[str, Any]]:
    """Read the complete bounded interval with stable keyset pagination."""

    result: list[dict[str, Any]] = []
    cursor_time: datetime | None = None
    cursor_id = ""
    while True:
        page = loader(
            start_at=start_at,
            as_of=as_of,
            limit=page_size,
            after_updated_at=cursor_time,
            after_canonical_id=cursor_id,
        )
        result.extend(page)
        if len(page) < page_size:
            return result
        tail = page[-1]
        next_time = _datetime(tail.get("updated_at_utc"))
        next_id = str(tail.get("canonical_news_id") or "")
        if next_time is None or (cursor_time, cursor_id) == (next_time, next_id):
            raise RuntimeError("News Synthesis pagination did not advance")
        cursor_time, cursor_id = next_time, next_id


def news_llm_review_events(*, start_at: datetime, as_of: datetime, client: ClickHouseHttpClient | None = None) -> list[dict[str, Any]]:
    owned = client is None
    active = client or ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password(), timeout_seconds=10)
    sql = f"""
SELECT canonical_news_id,toString(published_at_utc) published_at_utc,
       issuer_labels_json,toString(updated_at_utc) updated_at_utc
FROM q_live.news_llm_issuer_review_v1 FINAL
WHERE status='complete'
  AND updated_at_utc>=parseDateTime64BestEffort({sql_string(start_at.astimezone(UTC).isoformat())})
  AND updated_at_utc<=parseDateTime64BestEffort({sql_string(as_of.astimezone(UTC).isoformat())})
ORDER BY updated_at_utc,canonical_news_id LIMIT 10000 FORMAT JSONEachRow
"""
    try:
        return list(active.iter_json_each_row(sql))
    finally:
        if owned:
            active.close()


def all_news_intelligence_events(*, start_at: datetime, as_of: datetime) -> list[dict[str, Any]]:
    rows = [dict(row, event_authority="deterministic") for row in all_news_synthesis_events(start_at=start_at, as_of=as_of)]
    rows.extend(dict(row, event_authority="llm_review") for row in news_llm_review_events(start_at=start_at, as_of=as_of))
    return sorted(rows, key=lambda row: (str(row.get("updated_at_utc") or ""), str(row.get("canonical_news_id") or ""), str(row.get("event_authority") or "")))


class NewsSignalRuntime:
    """Materialize causal News Synthesis issuer events into Signal Streams."""

    def __init__(
        self,
        *,
        loader: Callable[..., list[dict[str, Any]]] = all_news_intelligence_events,
        publisher: Callable[[str, list[dict[str, Any]]], dict[str, Any]] = qmd_append_signal_stream_rows,
        live_activation_age: timedelta = timedelta(seconds=60),
    ) -> None:
        self._loader = loader
        self._publisher = publisher
        self._live_activation_age = live_activation_age

    def refresh(
        self,
        configuration: dict[str, Any],
        market_rows: list[dict[str, Any]],
        *,
        as_of: datetime,
        journal: TradingJournal,
    ) -> dict[str, Any]:
        cutoff = as_of.astimezone(UTC)
        session = signal_stream_session(cutoff)
        if not session["active"]:
            return {"status": "session_closed", "occurrences": [], "new_occurrences": []}
        checkpoint = journal.load_checkpoint(NEWS_SIGNAL_CHECKPOINT)
        state = dict(dict(checkpoint or {}).get("state") or {})
        cursor = _datetime(state.get("updated_at"))
        start_at = max(session["start_at"], cursor or session["start_at"])
        source_rows = self._loader(start_at=start_at, as_of=cutoff)
        by_ticker = {
            str(row.get("ticker") or row.get("symbol") or "").strip().upper(): row
            for row in market_rows
            if str(row.get("ticker") or row.get("symbol") or "").strip()
        }
        event_rows: list[dict[str, Any]] = []
        latest_cursor = cursor
        latest_id = str(state.get("canonical_news_id") or "")
        for source in source_rows:
            available_at = _datetime(source.get("updated_at_utc"))
            if available_at is None:
                continue
            canonical_id = str(source.get("canonical_news_id") or "")
            if cursor is not None and (available_at, canonical_id) <= (cursor, latest_id):
                continue
            latest_cursor, latest_id = available_at, canonical_id
            if source.get("event_authority") == "llm_review":
                event_rows.extend(llm_review_signal_rows(configuration, source=source, market_rows=by_ticker, available_at=available_at))
            else:
                event_rows.extend(news_synthesis_candidate_rows(configuration, _object(source.get("synthesis_json")), source=source, market_rows=by_ticker, available_at=available_at))
        discovery = dict(configuration.get("market_discovery") or {})
        rule_sets = {str(row.get("rule_set_id") or ""): row for row in discovery.get("rule_sets") or []}
        inserted: list[dict[str, Any]] = []
        for stream in discovery.get("signal_streams") or []:
            if not bool(stream.get("enabled", True)) or str(stream.get("source_type") or "") != "news_events":
                continue
            rule_ids = [str(value) for value in stream.get("inclusion_rule_sets") or [] if str(value)]
            masks = evaluate_rule_sets_frame((rule_sets[value] for value in rule_ids if value in rule_sets), event_rows)
            matched = []
            for index, row in enumerate(event_rows):
                results = [bool((masks.get(rule_id) or [False] * len(event_rows))[index]) for rule_id in rule_ids]
                if results and (any(results) if str(stream.get("inclusion_operator") or "all") == "any" else all(results)):
                    matched.append({**row, "matched_rule_set_ids": rule_ids})
            inserted.extend(self._publisher(str(stream.get("signal_stream_id") or NEWS_SIGNAL_STREAM_ID), matched).get("new_occurrences") or [])
        if latest_cursor is not None:
            journal.save_checkpoint(
                NEWS_SIGNAL_CHECKPOINT,
                f"{latest_cursor.isoformat()}|{latest_id}",
                {"updated_at": latest_cursor.isoformat(), "canonical_news_id": latest_id},
                cutoff,
            )
        dispatchable = [
            row
            for row in inserted
            if cutoff - (_datetime(row.get("available_at")) or cutoff)
            <= self._live_activation_age
        ]
        return {
            "status": "ready",
            "source_count": len(source_rows),
            "matching_count": len(event_rows),
            "occurrences": inserted,
            "new_occurrences": dispatchable,
        }


def news_synthesis_candidate_rows(
    configuration: dict[str, Any],
    document: dict[str, Any],
    *,
    source: dict[str, Any],
    market_rows: dict[str, dict[str, Any]] | None,
    available_at: datetime,
    require_market_row: bool = True,
) -> list[dict[str, Any]]:
    entities = {
        str(row.get("entity_id") or ""): dict(row)
        for row in document.get("entities") or []
    }
    eligible = {
        str(row.get("entity_id") or "")
        for row in document.get("eligibility") or []
        if str(row.get("product") or "") == "forecast_trigger"
        and bool(row.get("eligible"))
    }
    columns = list(dict(configuration.get("market_discovery") or {}).get("column_catalog") or [])
    result: list[dict[str, Any]] = []
    for view in document.get("issuer_views") or []:
        entity_id = str(view.get("entity_id") or "")
        sentiment = str(view.get("composite_sentiment") or "")
        ticker = str(entities.get(entity_id, {}).get("ticker") or "").strip().upper()
        if not ticker or (require_market_row and ticker not in (market_rows or {})):
            continue
        row = dict((market_rows or {}).get(ticker) or {"ticker": ticker, "symbol": ticker})
        values = {
            "identity.symbol": ticker,
            "news.composite_sentiment": sentiment,
            "news.positive_strength": int(view.get("positive_strength") or 0),
            "news.negative_strength": int(view.get("negative_strength") or 0),
            "news.forecast_trigger_eligible": entity_id in eligible,
            "news.funnel.forecast_eligible": str(source.get("funnel_forecast_eligibility") or "") == "eligible",
            "news.funnel.eligible_probability": float(source.get("funnel_eligible_probability") or 0),
            "news.funnel.stage": str(source.get("funnel_stage") or "missing"),
            "news.canonical_news_id": str(source.get("canonical_news_id") or ""),
            "news.published_at": str(source.get("published_at_utc") or ""),
        }
        row.update(values)
        for column in columns:
            source_id = str(column.get("source_id") or "")
            column_id = str(column.get("column_id") or "")
            if not column_id:
                continue
            runtime_field = discovery_runtime_field(source_id)
            value = values.get(source_id)
            if value is None:
                value = row.get(column_id, row.get(runtime_field, row.get(source_id)))
            if value is not None:
                row[column_id] = value
        row.update({
            "ticker": ticker,
            "symbol": ticker,
            "available_at": available_at.isoformat(),
            "source_event_id": str(source.get("canonical_news_id") or ""),
        })
        result.append(row)
    return result


def bullish_news_signal_rows(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Compatibility helper for focused tests and legacy callers."""
    return [row for row in news_synthesis_candidate_rows(*args, **kwargs) if row.get("news.forecast_trigger_eligible") and row.get("news.composite_sentiment") == "positive"]


def llm_review_signal_rows(
    configuration: dict[str, Any], *, source: dict[str, Any], market_rows: dict[str, dict[str, Any]] | None,
    available_at: datetime, require_market_row: bool = True,
) -> list[dict[str, Any]]:
    payload = _object(source.get("issuer_labels_json"))
    columns = list(dict(configuration.get("market_discovery") or {}).get("column_catalog") or [])
    result: list[dict[str, Any]] = []
    for issuer in payload.get("issuers") or []:
        ticker = str(issuer.get("ticker") or "").strip().upper()
        if not ticker or (require_market_row and ticker not in (market_rows or {})):
            continue
        positive = float(issuer.get("positive_implication_probability") or 0)
        negative = float(issuer.get("negative_implication_probability") or 0)
        sentiment = "mixed" if positive >= .5 and negative >= .5 else "positive" if positive >= .5 else "negative" if negative >= .5 else "neutral"
        eligible_probability = float(issuer.get("forecast_relevance_probability") or 0)
        row = dict((market_rows or {}).get(ticker) or {"ticker": ticker, "symbol": ticker})
        row.update({
            "ticker": ticker, "symbol": ticker,
            "news.llm.review_complete": True,
            "news.llm.forecast_relevance_probability": eligible_probability,
            "news.llm.forecast_eligible": eligible_probability >= .5,
            "news.llm.positive_implication_probability": positive,
            "news.llm.negative_implication_probability": negative,
            "news.llm.language_sentiment": sentiment,
            "news.canonical_news_id": str(source.get("canonical_news_id") or ""),
            "news.published_at": str(source.get("published_at_utc") or ""),
            "available_at": available_at.isoformat(),
            "source_event_id": f"{source.get('canonical_news_id')}:{ticker}:llm-review",
        })
        for column in columns:
            source_id = str(column.get("source_id") or "")
            column_id = str(column.get("column_id") or "")
            if column_id and source_id in row:
                row[column_id] = row[source_id]
        result.append(row)
    return result


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


NEWS_SIGNAL_RUNTIME = NewsSignalRuntime()
