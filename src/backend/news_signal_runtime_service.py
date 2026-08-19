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
from src.backend.signal_stream_runtime_service import SIGNAL_STREAM_RUNTIME, signal_stream_session
from src.trading_runtime.journal import TradingJournal


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
  AND (updated_at_utc, canonical_news_id) > (
      parseDateTime64BestEffort({sql_string(after_updated_at.astimezone(UTC).isoformat())}),
      {sql_string(after_canonical_id)}
  )"""
    sql = f"""
SELECT canonical_news_id,
       toString(published_at_utc) AS published_at_text,
       engine_version,
       synthesis_json,
       toString(updated_at_utc) AS updated_at_text
FROM `q_live`.`news_synthesis_v1` FINAL
WHERE engine_version={sql_string(ENGINE_VERSION)}
  AND updated_at_utc>=parseDateTime64BestEffort({sql_string(start_at.astimezone(UTC).isoformat())})
  AND updated_at_utc<=parseDateTime64BestEffort({sql_string(as_of.astimezone(UTC).isoformat())})
{cursor_clause}
ORDER BY updated_at_utc,canonical_news_id
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


class NewsSignalRuntime:
    """Materialize causal News Synthesis issuer events into Signal Streams."""

    def __init__(
        self,
        *,
        loader: Callable[..., list[dict[str, Any]]] = all_news_synthesis_events,
        live_activation_age: timedelta = timedelta(seconds=60),
    ) -> None:
        self._loader = loader
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
            document = _object(source.get("synthesis_json"))
            event_rows.extend(
                bullish_news_signal_rows(
                    configuration,
                    document,
                    source=source,
                    market_rows=by_ticker,
                    available_at=available_at,
                )
            )
        inserted = SIGNAL_STREAM_RUNTIME.append_external_event_rows(
            configuration,
            signal_stream_id=NEWS_SIGNAL_STREAM_ID,
            rows=event_rows,
            journal=journal,
        )
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


def bullish_news_signal_rows(
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
        if entity_id not in eligible or sentiment != "positive":
            continue
        ticker = str(entities.get(entity_id, {}).get("ticker") or "").strip().upper()
        if not ticker or (require_market_row and ticker not in (market_rows or {})):
            continue
        row = dict((market_rows or {}).get(ticker) or {"ticker": ticker, "symbol": ticker})
        values = {
            "identity.symbol": ticker,
            "news.composite_sentiment": sentiment,
            "news.positive_strength": int(view.get("positive_strength") or 0),
            "news.negative_strength": int(view.get("negative_strength") or 0),
            "news.forecast_trigger_eligible": True,
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
