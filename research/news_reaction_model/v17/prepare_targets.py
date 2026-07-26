from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import math
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v16.prepared import close_arrays, open_arrays
from research.news_reaction_model.v17 import RESPONSE_WINDOWS
from research.news_reaction_model.v17.config import LoaderConfig
from research.news_reaction_model.v17.prepared import (
    ARRAY_FILES,
    BUILD_STATE_FILE,
    MANIFEST_FILE,
    THRESHOLDS_FILE,
    audit_target_arrays,
    create_target_arrays,
    open_target_arrays_for_resume,
    row_key_hash,
    v16_identity_sha256,
    write_json_atomic,
)
from research.news_reaction_model.v17.targets import (
    RAW_METRIC_NAMES,
    TARGET_VERSION,
    TargetThresholds,
    classify_persistence,
    classify_window,
    fit_thresholds,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
UTC = dt.timezone.utc
EASTERN = ZoneInfo("America/New_York")
PHASE_TO_HORIZON = {
    "premarket": ("event_premarket", "premarket_close"),
    "regular": ("event_regular", "regular_close"),
    "afterhours": ("event_afterhours", "extended_close"),
}


def _q(value: Any) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _qi(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def _decode(value: Any) -> str:
    return bytes(value).rstrip(b"\x00").decode("utf-8")


def _parse_utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _clickhouse_utc(value: dt.datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


class BuildCancelled(RuntimeError):
    """Raised inside workers after the operator requests a graceful stop."""


class CancellationController:
    """Coordinate worker cancellation and terminate only this build's queries."""

    def __init__(self) -> None:
        self._requested = threading.Event()
        self._lock = threading.Lock()
        self._active_query_ids: set[str] = set()

    @property
    def requested(self) -> bool:
        return self._requested.is_set()

    def request_stop(self) -> None:
        self._requested.set()

    def raise_if_requested(self) -> None:
        if self.requested:
            raise BuildCancelled("V17 target preparation was cancelled.")

    def register_query(self) -> str:
        with self._lock:
            if self._requested.is_set():
                raise BuildCancelled("V17 target preparation was cancelled.")
            query_id = f"news-v17-targets-{uuid.uuid4()}"
            self._active_query_ids.add(query_id)
            return query_id

    def unregister_query(self, query_id: str) -> None:
        with self._lock:
            self._active_query_ids.discard(query_id)

    def active_query_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._active_query_ids))

    def cancel_active_queries(
        self,
        client: ClickHouseHttpClient,
    ) -> int:
        query_ids = self.active_query_ids()
        if not query_ids:
            return 0
        client.execute(
            "KILL QUERY WHERE query_id IN ("
            + ", ".join(_q(query_id) for query_id in query_ids)
            + ") SYNC"
        )
        return len(query_ids)


def _event_table(base: str, day: dt.date) -> str:
    return base if base.rsplit("_", 1)[-1].isdigit() else f"{base}_{day.year}"


def calendar_sessions(client: ClickHouseHttpClient, config: LoaderConfig) -> list[dt.date]:
    text = client.query_tsv(
        f"""
SELECT current_session_date
FROM {_qi(config.news_database)}.{_qi(config.reaction_calendar_table)} FINAL
WHERE calendar_version = {_q(config.reaction_calendar_version)}
  AND is_session = 1
  AND current_session_date >= toDate({_q(config.train_start)}) - 7
  AND current_session_date < toDate({_q(config.validation_end_exclusive)}) + 14
ORDER BY current_session_date
"""
    )
    values = [dt.date.fromisoformat(line.strip()) for line in text.splitlines() if line.strip()]
    if not values:
        raise RuntimeError("Reaction calendar returned no exchange sessions.")
    return values


def _session_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime, dt.datetime, dt.datetime]:
    def at(hour: int, minute: int = 0) -> dt.datetime:
        return dt.datetime.combine(day, dt.time(hour, minute), EASTERN).astimezone(UTC)

    return at(4), at(9, 30), at(16), at(20)


def source_label_rows(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    start: dt.datetime,
    end: dt.datetime,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    phase_codes = tuple(value[1] for value in PHASE_TO_HORIZON.values())
    text = client.execute(
        f"""
SELECT
    canonical_news_id,
    ticker,
    toString(published_at_utc),
    publication_session,
    toString(reaction_session_date),
    horizon_code,
    ifNull(anchor_price, nan),
    ifNull(high_return, nan),
    ifNull(low_return, nan),
    ifNull(target_return, nan),
    ifNull(toFloat64(toUnixTimestamp64Micro(window_high_timestamp_utc)), nan),
    ifNull(toFloat64(toUnixTimestamp64Micro(window_low_timestamp_utc)), nan),
    ifNull(toFloat64(toUnixTimestamp64Micro(target_at_utc)), nan),
    observation_count,
    quality_status
FROM {_qi(config.news_database)}.{_qi(config.reaction_table)} FINAL
WHERE label_version = {_q(config.label_version)}
  AND published_at_utc >= toDateTime64({_q(_clickhouse_utc(start))}, 6, 'UTC')
  AND published_at_utc < toDateTime64({_q(_clickhouse_utc(end))}, 6, 'UTC')
  AND applicable = 1
  AND quality_status = 'clean'
  AND corporate_action_overlap = 0
  AND horizon_code IN ({", ".join(_q(value) for value in phase_codes)})
ORDER BY published_at_utc, canonical_news_id, ticker, horizon_code
FORMAT TabSeparatedRaw
"""
    )
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for line in text.splitlines():
        if not line:
            continue
        fields = line.split("\t")
        key = (fields[0], fields[1], fields[2])
        publication_session = fields[3]
        expected = PHASE_TO_HORIZON.get(publication_session, ("", ""))[1]
        base = rows.setdefault(
            key,
            {
                "publication_session": publication_session,
                "reaction_session_date": dt.date.fromisoformat(fields[4]),
                "phase": None,
            },
        )
        if fields[5] == expected:
            base["phase"] = {
                "anchor_price": float(fields[6]),
                "high_return": float(fields[7]),
                "low_return": float(fields[8]),
                "terminal_return": float(fields[9]),
                "high_timestamp": float(fields[10]),
                "low_timestamp": float(fields[11]),
                "end_timestamp": float(fields[12]),
                "observation_count": int(fields[13]),
                "quality_status": fields[14],
            }
        if math.isfinite(float(fields[6])):
            base["anchor_price"] = float(fields[6])
    return rows


def load_split_dates(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
) -> dict[str, frozenset[dt.date]]:
    text = client.execute(f"""
SELECT upperUTF8(provider_ticker), execution_date
FROM {_qi(config.news_database)}.{_qi(config.split_table)} FINAL
WHERE provider_ticker != ''
  AND split_from > 0
  AND split_to > 0
  AND split_from != split_to
  AND execution_date >= toDate({_q(config.train_start)})
  AND execution_date < toDate({_q(config.validation_end_exclusive)})
GROUP BY provider_ticker, execution_date
ORDER BY provider_ticker, execution_date
FORMAT TabSeparatedRaw
""")
    grouped: dict[str, set[dt.date]] = {}
    for line in text.splitlines():
        if not line:
            continue
        ticker, date_text = line.split("\t", 1)
        grouped.setdefault(ticker, set()).add(dt.date.fromisoformat(date_text))
    return {ticker: frozenset(values) for ticker, values in grouped.items()}


EMPTY_EVENT_ROWS = np.empty((0, 8), dtype=np.float64)


def event_rows_for_tickers(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    tickers: list[str] | tuple[str, ...],
    session_day: dt.date,
    cancellation: CancellationController | None = None,
) -> dict[str, np.ndarray]:
    if cancellation is not None:
        cancellation.raise_if_requested()
    requested = tuple(dict.fromkeys(str(value) for value in tickers if str(value)))
    if not requested:
        return {}
    table_names = tuple(
        dict.fromkeys(
            _event_table(config.events_table_base, value)
            for value in (session_day, session_day + dt.timedelta(days=1))
        )
    )
    tables = [f"{_qi(config.market_database)}.{_qi(value)}" for value in table_names]
    date_predicate = (
        f"event_date >= toDate({_q(session_day)}) "
        f"AND event_date <= toDate({_q(session_day)}) + 1"
    )
    # The source is annual and partitioned by event month. Keep the date
    # predicate inside every branch even when only one annual table is used;
    # otherwise a one-session request scans the ticker's entire year.
    source = (
        "("
        + " UNION ALL ".join(
            f"SELECT * FROM {table} WHERE {date_predicate}" for table in tables
        )
        + ")"
    )
    condition_reference = (
        f"{_qi(config.market_database)}.{_qi(config.condition_reference_table)}"
    )
    start_utc, _regular, _close, end_utc = _session_bounds(session_day)
    sql = f"""
WITH
  (SELECT groupArray(toUInt8(token_id)) FROM {condition_reference}
   WHERE source_family='trade_conditions' AND is_join_canonical=1 AND update_last=1)
      AS update_last_tokens,
  (SELECT groupArray(toUInt8(token_id)) FROM {condition_reference}
   WHERE source_family='trade_conditions' AND is_join_canonical=1 AND update_high_low=1)
      AS update_high_low_tokens,
  (SELECT groupArray(toUInt8(token_id)) FROM {condition_reference}
   WHERE source_family='trade_conditions' AND is_join_canonical=1
     AND update_last=1 AND update_high_low=1) AS full_tokens,
  (SELECT any(toUInt8(token_id)) FROM {condition_reference}
   WHERE source_family='trade_conditions' AND is_join_canonical=1 AND modifier_int=12)
      AS form_t_token,
raw AS
(
  SELECT
    ticker,
    sip_timestamp_us,
    ordinal,
    bitOr(
      bitShiftLeft(toUInt128(sip_timestamp_us), 64),
      toUInt128(ordinal)
    ) AS event_order_key,
    bitAnd(event_meta,1)=1 AS is_trade,
    toFloat64(price_primary_int)/if(bitAnd(event_meta,2)=2,10000.0,100.0)
      AS primary_price,
    toFloat64(price_secondary_int)/if(bitAnd(event_meta,4)=4,10000.0,100.0)
      AS secondary_price,
    toFloat64(size_primary) AS trade_size,
    arrayFilter(x -> x != 0, [condition_token_1,condition_token_2,
      condition_token_3,condition_token_4,condition_token_5]) AS condition_tokens,
    toTimeZone(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us),'UTC'),
      'America/New_York') AS local_timestamp,
    toUInt8(
      toHour(local_timestamp)<9
      OR (toHour(local_timestamp)=9 AND toMinute(local_timestamp)<30)
      OR toHour(local_timestamp)>=16
    ) AS is_extended_hours
  FROM {source}
  WHERE ticker IN ({", ".join(_q(value) for value in requested)})
    AND sip_timestamp_us >= toUInt64(toUnixTimestamp64Micro(
      toDateTime64({_q(_clickhouse_utc(start_utc))},6,'UTC')))
    AND sip_timestamp_us < toUInt64(toUnixTimestamp64Micro(
      toDateTime64({_q(_clickhouse_utc(end_utc))},6,'UTC')))
    AND sip_timestamp_us > 0 AND ordinal > 0
),
trades AS
(
  SELECT
    ticker,
    sip_timestamp_us,
    ordinal,
    event_order_key,
    primary_price AS trade_price,
    trade_size,
    toUInt8(
      empty(condition_tokens)
      OR if(
        is_extended_hours=1 AND has(condition_tokens,form_t_token)
          AND arrayAll(x -> x=form_t_token OR has(full_tokens,x),condition_tokens),
        1,
        arrayAll(x -> has(update_last_tokens,x),condition_tokens)
      )
    ) AS update_last,
    toUInt8(
      empty(condition_tokens)
      OR if(
        is_extended_hours=1 AND has(condition_tokens,form_t_token)
          AND arrayAll(x -> x=form_t_token OR has(full_tokens,x),condition_tokens),
        1,
        arrayAll(x -> has(update_high_low_tokens,x),condition_tokens)
      )
    ) AS update_high_low
  FROM raw
  WHERE is_trade AND primary_price>0 AND trade_size>0
),
eligible_trades AS
(
  SELECT *
  FROM trades
  WHERE update_last=1 OR update_high_low=1
),
quotes AS
(
  SELECT
    ticker,
    sip_timestamp_us,
    ordinal,
    event_order_key,
    secondary_price AS bid_price,
    primary_price AS ask_price
  FROM raw
  WHERE NOT is_trade AND secondary_price>0 AND primary_price>=secondary_price
)
SELECT
  t.ticker,
  t.sip_timestamp_us,
  t.ordinal,
  t.trade_price,
  t.trade_size,
  ifNull(q.bid_price,0),
  ifNull(q.ask_price,0),
  t.update_last,
  t.update_high_low
FROM eligible_trades AS t
ASOF LEFT JOIN quotes AS q
  ON t.ticker=q.ticker AND t.event_order_key>=q.event_order_key
ORDER BY t.ticker,t.sip_timestamp_us,t.ordinal
SETTINGS max_threads=2,max_memory_usage='4G',join_algorithm='full_sorting_merge'
FORMAT TabSeparatedRaw
"""
    query_id = cancellation.register_query() if cancellation is not None else None
    try:
        text = (
            client.execute(sql, query_id=query_id)
            if query_id is not None
            else client.execute(sql)
        )
    except RuntimeError as exc:
        if cancellation is not None and cancellation.requested:
            raise BuildCancelled("Active V17 ClickHouse query was cancelled.") from exc
        raise
    finally:
        if cancellation is not None and query_id is not None:
            cancellation.unregister_query(query_id)
    if cancellation is not None:
        cancellation.raise_if_requested()
    if not text.strip():
        return {ticker: EMPTY_EVENT_ROWS for ticker in requested}
    payloads: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line:
            continue
        ticker, separator, payload = line.partition("\t")
        if not separator:
            raise RuntimeError(
                f"Malformed compact event result for ticker batch on {session_day}."
            )
        payloads.setdefault(ticker, []).append(payload)
    result: dict[str, np.ndarray] = {}
    for ticker in requested:
        rows = payloads.get(ticker)
        if not rows:
            result[ticker] = EMPTY_EVENT_ROWS
            continue
        values = np.fromstring("\t".join(rows), sep="\t", dtype=np.float64)
        if values.size % 8:
            raise RuntimeError(f"Malformed compact event result for {ticker} {session_day}.")
        result[ticker] = values.reshape(-1, 8)
    return result


def event_rows(
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    ticker: str,
    session_day: dt.date,
    cancellation: CancellationController | None = None,
) -> np.ndarray:
    """Compatibility wrapper for focused validation and single-ticker callers."""
    return event_rows_for_tickers(
        client,
        config,
        [ticker],
        session_day,
        cancellation=cancellation,
    )[ticker]


def summarize_events(
    event_days: list[np.ndarray],
    *,
    start: dt.datetime,
    end: dt.datetime,
    anchor_price: float,
    exact_phase: dict[str, Any] | None = None,
    minimum_observations: int = 3,
    absolute_cache: dict[tuple[int, int], dict[str, float]] | None = None,
) -> tuple[np.ndarray, bool]:
    start_us = int(start.timestamp() * 1_000_000)
    end_us = int(end.timestamp() * 1_000_000)
    cache_key = (start_us, end_us)
    absolute = absolute_cache.get(cache_key) if absolute_cache is not None else None
    if absolute is None:
        slices: list[np.ndarray] = []
        for events in event_days:
            if not events.size:
                continue
            lower = int(np.searchsorted(events[:, 0], start_us, side="left"))
            upper = int(np.searchsorted(events[:, 0], end_us, side="left"))
            if upper > lower:
                slices.append(events[lower:upper])
        selected = (
            slices[0]
            if len(slices) == 1
            else np.concatenate(slices, axis=0)
            if slices
            else np.empty((0, 8), dtype=np.float64)
        )
        update_last = selected[:, 6].astype(np.bool_) if selected.size else np.zeros(0, dtype=np.bool_)
        update_high_low = (
            selected[:, 7].astype(np.bool_) if selected.size else np.zeros(0, dtype=np.bool_)
        )
        last = selected[update_last]
        extrema = selected[update_high_low]
        if last.size and extrema.size:
            prices = last[:, 2]
            sizes = last[:, 3]
            notionals = prices * sizes
            bids = last[:, 4]
            asks = last[:, 5]
            valid_quote = (bids > 0) & (asks >= bids)
            midpoint = np.where(valid_quote, (bids + asks) / 2.0, 0.0)
            buys = valid_quote & ((prices >= asks) | ((prices > bids) & (prices >= midpoint)))
            sells = valid_quote & ~buys
            unknowns = ~valid_quote
            high_index = int(np.argmax(extrema[:, 2]))
            low_index = int(np.argmin(extrema[:, 2]))
            running_peak = np.maximum.accumulate(prices)
            running_trough = np.minimum.accumulate(prices)
            absolute = {
                "high_price": float(extrema[high_index, 2]),
                "high_timestamp": float(extrema[high_index, 0]),
                "low_price": float(extrema[low_index, 2]),
                "low_timestamp": float(extrema[low_index, 0]),
                "terminal_price": float(prices[-1]),
                "vwap_price": float(np.sum(notionals) / np.sum(sizes)),
                "peak_to_trough_return": float(np.min(prices / running_peak - 1.0)),
                "trough_to_peak_return": float(np.max(prices / running_trough - 1.0)),
                "buy_notional": float(np.sum(notionals[buys])),
                "sell_notional": float(np.sum(notionals[sells])),
                "unknown_notional": float(np.sum(notionals[unknowns])),
                "observation_count": float(last.shape[0]),
            }
        else:
            absolute = {}
        if absolute_cache is not None:
            absolute_cache[cache_key] = absolute
    observation_count = int(absolute.get("observation_count", 0.0))
    high_point = (
        int(absolute["high_timestamp"]),
        float(absolute["high_price"]),
    ) if absolute else None
    low_point = (
        int(absolute["low_timestamp"]),
        float(absolute["low_price"]),
    ) if absolute else None
    terminal = float(absolute.get("terminal_price", math.nan))
    if (
        not math.isfinite(anchor_price)
        or anchor_price <= 0
        or observation_count < 1
        or (
            (high_point is None or low_point is None)
            and not (exact_phase and exact_phase.get("quality_status") == "clean")
        )
        or end_us <= start_us
    ):
        return np.full(len(RAW_METRIC_NAMES), np.nan, dtype=np.float32), False
    high_return = (
        high_point[1] / anchor_price - 1.0 if high_point is not None else math.nan
    )
    low_return = (
        low_point[1] / anchor_price - 1.0 if low_point is not None else math.nan
    )
    terminal_return = terminal / anchor_price - 1.0
    if exact_phase and exact_phase.get("quality_status") == "clean":
        high_return = exact_phase["high_return"]
        low_return = exact_phase["low_return"]
        terminal_return = exact_phase["terminal_return"]
        high_ts = exact_phase["high_timestamp"] * 1_000_000
        low_ts = exact_phase["low_timestamp"] * 1_000_000
        observation_count = exact_phase["observation_count"]
    else:
        assert high_point is not None and low_point is not None
        high_ts = high_point[0]
        low_ts = low_point[0]
    duration_us = end_us - start_us
    buy = float(absolute.get("buy_notional", 0.0))
    sell = float(absolute.get("sell_notional", 0.0))
    unknown = float(absolute.get("unknown_notional", 0.0))
    total_notional = buy + sell + unknown
    metrics = np.asarray(
        [
            anchor_price,
            high_return,
            low_return,
            terminal_return,
            min(max((high_ts - start_us) / duration_us, 0.0), 1.0),
            min(max((low_ts - start_us) / duration_us, 0.0), 1.0),
            float(absolute["vwap_price"]) / anchor_price - 1.0,
            float(absolute["peak_to_trough_return"]),
            float(absolute["trough_to_peak_return"]),
            buy / total_notional if total_notional else 0.0,
            sell / total_notional if total_notional else 0.0,
            unknown / total_notional if total_notional else 1.0,
            math.nan,
            math.nan,
            observation_count,
            duration_us / 1_000_000,
        ],
        dtype=np.float32,
    )
    return metrics, observation_count >= minimum_observations


def build_windows(
    published: dt.datetime,
    publication_session: str,
    reaction_day: dt.date,
    sessions: list[dt.date],
) -> list[tuple[dt.datetime, dt.datetime] | None]:
    pre, regular, close, extended = _session_bounds(reaction_day)
    phase: dict[str, tuple[dt.datetime, dt.datetime]] = {
        "premarket": (published, regular),
        "regular": (published, close),
        "afterhours": (published, extended),
    }
    result: list[tuple[dt.datetime, dt.datetime] | None] = [None, None, None]
    if publication_session in PHASE_TO_HORIZON:
        index = RESPONSE_WINDOWS.index(PHASE_TO_HORIZON[publication_session][0])
        result[index] = phase[publication_session]
    position = bisect.bisect_left(sessions, reaction_day)
    if publication_session == "closed":
        next_position = position
    else:
        next_position = bisect.bisect_right(sessions, reaction_day)
    if next_position < len(sessions):
        next_day = sessions[next_position]
        result.append((_session_bounds(next_day)[0], _session_bounds(next_day)[3]))
    else:
        result.append(None)
    fifth = next_position + 4
    if next_position < len(sessions) and fifth < len(sessions):
        result.append((_session_bounds(sessions[next_position])[0], _session_bounds(sessions[fifth])[3]))
    else:
        result.append(None)
    return result


def session_days_between(
    sessions: list[dt.date],
    first_day: dt.date,
    last_day: dt.date,
) -> tuple[dt.date, ...]:
    """Return exchange sessions in a closed date interval without a full scan."""
    lower = bisect.bisect_left(sessions, first_day)
    upper = bisect.bisect_right(sessions, last_day)
    return tuple(sessions[lower:upper])


def process_ticker_batch(
    *,
    client: ClickHouseHttpClient,
    config: LoaderConfig,
    v16: dict[str, np.ndarray],
    labels: dict[tuple[str, str, str], dict[str, Any]],
    sessions: list[dt.date],
    items: list[tuple[str, list[int]]],
    split_dates: dict[str, frozenset[dt.date]] | None = None,
    event_loader: Callable[
        [
            ClickHouseHttpClient,
            LoaderConfig,
            list[str] | tuple[str, ...],
            dt.date,
            CancellationController | None,
        ],
        dict[str, np.ndarray],
    ] = event_rows_for_tickers,
    cancellation: CancellationController | None = None,
) -> tuple[list[tuple[int, np.ndarray, np.ndarray]], int, int]:
    """Build one bounded ticker batch with a six-session rolling event cache.

    Every session is queried once for all tickers in this batch that need it.
    Windows are evaluated when their final session arrives, which keeps only
    the six most recent sessions resident while preserving exact event order.
    """
    if cancellation is not None:
        cancellation.raise_if_requested()
    split_dates = split_dates or {}
    output: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    work_by_end_day: dict[
        dt.date,
        list[
            tuple[
                int,
                int,
                str,
                tuple[dt.date, ...],
                tuple[dt.datetime, dt.datetime],
                float,
                dict[str, Any] | None,
            ]
        ],
    ] = {}
    required_tickers_by_day: dict[dt.date, set[str]] = {}
    absolute_caches: dict[str, dict[tuple[int, int], dict[str, float]]] = {
        ticker: {} for ticker, _indices in items
    }

    for ticker, indices in items:
        for row_index in indices:
            raw = np.full(
                (len(RESPONSE_WINDOWS), len(RAW_METRIC_NAMES)),
                np.nan,
                dtype=np.float32,
            )
            masks = np.zeros(len(RESPONSE_WINDOWS), dtype=np.bool_)
            output[row_index] = (raw, masks)
            news_id = _decode(v16["canonical_news_id"][row_index])
            published_text = _decode(v16["published_at_utc"][row_index])
            label = labels.get((news_id, ticker, published_text))
            if label is None or not math.isfinite(
                float(label.get("anchor_price", math.nan))
            ):
                continue
            published = _parse_utc(published_text)
            windows = build_windows(
                published,
                label["publication_session"],
                label["reaction_session_date"],
                sessions,
            )
            for window_index, bounds in enumerate(windows):
                if bounds is None:
                    continue
                split_start = published.astimezone(EASTERN).date()
                split_end = bounds[1].astimezone(EASTERN).date()
                if any(
                    split_start <= split_date <= split_end
                    for split_date in split_dates.get(ticker, ())
                ):
                    continue
                exact = label.get("phase") if window_index < 3 else None
                if exact is not None and exact.get("quality_status") != "clean":
                    continue
                selected_days = session_days_between(
                    sessions,
                    bounds[0].astimezone(EASTERN).date(),
                    bounds[1].astimezone(EASTERN).date(),
                )
                if not selected_days:
                    continue
                for day in selected_days:
                    required_tickers_by_day.setdefault(day, set()).add(ticker)
                work_by_end_day.setdefault(selected_days[-1], []).append(
                    (
                        row_index,
                        window_index,
                        ticker,
                        selected_days,
                        bounds,
                        float(label["anchor_price"]),
                        exact,
                    )
                )

    event_cache: OrderedDict[dt.date, dict[str, np.ndarray]] = OrderedDict()
    query_count = 0
    event_row_count = 0
    for day in sorted(required_tickers_by_day):
        if cancellation is not None:
            cancellation.raise_if_requested()
        requested = sorted(required_tickers_by_day[day])
        loaded = event_loader(client, config, requested, day, cancellation)
        query_count += 1
        event_row_count += sum(int(rows.shape[0]) for rows in loaded.values())
        event_cache[day] = loaded
        event_cache.move_to_end(day)
        for (
            row_index,
            window_index,
            ticker,
            selected_days,
            bounds,
            anchor_price,
            exact,
        ) in work_by_end_day.get(day, ()):
            missing = [value for value in selected_days if value not in event_cache]
            if missing:
                raise RuntimeError(
                    "V17 rolling event cache evicted required sessions before "
                    f"window evaluation for {ticker}: {missing}."
                )
            event_days = [
                event_cache[value].get(ticker, EMPTY_EVENT_ROWS)
                for value in selected_days
            ]
            raw, masks = output[row_index]
            raw[window_index], masks[window_index] = summarize_events(
                event_days,
                start=bounds[0],
                end=bounds[1],
                anchor_price=anchor_price,
                exact_phase=exact,
                minimum_observations=3,
                absolute_cache=absolute_caches[ticker],
            )
        while len(event_cache) > 6:
            event_cache.popitem(last=False)

    return (
        [
            (row_index, output[row_index][0], output[row_index][1])
            for _ticker, indices in items
            for row_index in indices
        ],
        query_count,
        event_row_count,
    )


def build_parser() -> argparse.ArgumentParser:
    defaults = LoaderConfig()
    parser = argparse.ArgumentParser(description="Build V17 target sidecar over completed V16 arrays.")
    parser.add_argument("--prepared-root", default=str(defaults.prepared_dataset_root))
    parser.add_argument("--target-root", default=str(defaults.target_root))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--tickers-per-query", type=int, default=64)
    parser.add_argument("--threshold-quantile", type=float, default=0.35)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Delete only the known V17 v3 sidecar arrays/state before rebuilding.",
    )
    return parser


def clear_target_sidecar(config: LoaderConfig) -> tuple[str, ...]:
    """Remove only files owned by the versioned V17 target sidecar."""
    names = (
        *ARRAY_FILES.values(),
        BUILD_STATE_FILE,
        MANIFEST_FILE,
        THRESHOLDS_FILE,
    )
    removed: list[str] = []
    root = config.target_root.resolve()
    for name in names:
        path = (config.target_root / name).resolve()
        if path.parent != root:
            raise RuntimeError(f"Refusing unsafe V17 restart path: {path}")
        for candidate in (path, path.with_suffix(path.suffix + ".tmp")):
            if candidate.exists():
                candidate.unlink()
                removed.append(candidate.name)
    return tuple(removed)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    config = LoaderConfig(
        prepared_dataset_root=Path(args.prepared_root),
        target_root=Path(args.target_root),
    )
    if not args.execute:
        print(
            f"PREFLIGHT ONLY | V16={config.prepared_dataset_root} "
            f"V17 targets={config.target_root} | add --execute",
            flush=True,
        )
        return 0
    if args.restart:
        removed = clear_target_sidecar(config)
        print(
            f"V17 RESTART | removed={len(removed)} root={config.target_root}",
            flush=True,
        )
    client = ClickHouseHttpClient(
        default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()
    )
    v16, manifest = open_arrays(config)
    identity_sha256 = v16_identity_sha256(v16)
    completed_manifest_path = config.target_root / MANIFEST_FILE
    if completed_manifest_path.exists():
        completed = json.loads(completed_manifest_path.read_text(encoding="utf-8"))
        if (
            completed.get("status") == "complete"
            and int(completed.get("rows", -1)) == int(manifest["rows"])
            and completed.get("v16_identity_sha256") == identity_sha256
        ):
            close_arrays(v16)
            print(
                f"ALREADY COMPLETE | rows={int(manifest['rows']):,} "
                f"root={config.target_root}",
                flush=True,
            )
            return 0
        close_arrays(v16)
        raise RuntimeError(
            "Existing V17 target manifest does not match the completed V16 authority. "
            "Move the V17 target root aside before an intentional rebuild."
        )
    state_path = config.target_root / BUILD_STATE_FILE
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (
            int(state.get("rows", -1)) != int(manifest["rows"])
            or state.get("v16_identity_sha256") != identity_sha256
        ):
            raise RuntimeError(
                "V17 resumable state does not match the completed V16 authority. "
                "Move the V17 target root aside before intentionally rebuilding."
            )
        targets = open_target_arrays_for_resume(config, int(manifest["rows"]))
        resume_month = dt.datetime.fromisoformat(state["next_month"]).replace(tzinfo=UTC)
        print(f"RESUME | next_month={resume_month:%Y-%m}", flush=True)
    else:
        targets = create_target_arrays(config, int(manifest["rows"]))
        resume_month = dt.datetime(2019, 1, 1, tzinfo=UTC)
        write_json_atomic(
            state_path,
            {
                "status": "building",
                "rows": int(manifest["rows"]),
                "v16_identity_sha256": identity_sha256,
                "next_month": resume_month.date().isoformat(),
            },
        )
    sessions = calendar_sessions(client, config)
    split_dates = load_split_dates(client, config)
    cancellation = CancellationController()
    try:
        timestamps = np.asarray(v16["published_at_us"])
        start_us = int(_parse_utc(config.train_start).timestamp() * 1_000_000)
        end_us = int(_parse_utc(config.validation_end_exclusive).timestamp() * 1_000_000)
        lower = int(np.searchsorted(timestamps, start_us))
        upper = int(np.searchsorted(timestamps, end_us))
        month_start = resume_month
        month_number = 0
        while month_start < dt.datetime(2027, 1, 1, tzinfo=UTC):
            month_number += 1
            next_month = (
                month_start.replace(year=month_start.year + 1, month=1)
                if month_start.month == 12
                else month_start.replace(month=month_start.month + 1)
            )
            month_lower = max(
                lower,
                int(np.searchsorted(timestamps, int(month_start.timestamp() * 1_000_000))),
            )
            month_upper = min(
                upper,
                int(np.searchsorted(timestamps, int(next_month.timestamp() * 1_000_000))),
            )
            if month_upper <= month_lower:
                month_start = next_month
                continue
            labels = source_label_rows(client, config, month_start, next_month)
            groups: dict[str, list[int]] = {}
            for row_index in range(month_lower, month_upper):
                groups.setdefault(_decode(v16["ticker"][row_index]), []).append(row_index)
            ticker_items = sorted(groups.items())
            ticker_batch_size = max(1, int(args.tickers_per_query))
            ticker_batches = [
                ticker_items[offset : offset + ticker_batch_size]
                for offset in range(0, len(ticker_items), ticker_batch_size)
            ]
            started = time.monotonic()
            executor = ThreadPoolExecutor(
                max_workers=max(1, args.workers),
                thread_name_prefix="v17-targets",
            )
            futures: dict[Any, int] = {}
            try:
                futures = {
                    executor.submit(
                        process_ticker_batch,
                        client=client,
                        config=config,
                        v16=v16,
                        labels=labels,
                        split_dates=split_dates,
                        sessions=sessions,
                        items=batch,
                        cancellation=cancellation,
                    ): len(batch)
                    for batch in ticker_batches
                }
                completed_batches = 0
                completed_tickers = 0
                query_count = 0
                event_row_count = 0
                for future in as_completed(futures):
                    batch_output, batch_queries, batch_event_rows = future.result()
                    for row_index, raw, masks in batch_output:
                        targets["raw_metrics"][row_index] = raw
                        targets["window_mask"][row_index] = masks
                        targets["row_key_hash"][row_index] = row_key_hash(
                            _decode(v16["canonical_news_id"][row_index]),
                            _decode(v16["ticker"][row_index]),
                            _decode(v16["published_at_utc"][row_index]),
                        )
                    completed_batches += 1
                    completed_tickers += futures[future]
                    query_count += batch_queries
                    event_row_count += batch_event_rows
                    elapsed = max(time.monotonic() - started, 1e-9)
                    fraction = completed_batches / max(len(futures), 1)
                    eta = elapsed * (1.0 - fraction) / max(fraction, 1e-9)
                    print(
                        f"TARGETS {month_start:%Y-%m} "
                        f"batches={completed_batches:,}/{len(futures):,} "
                        f"tickers={completed_tickers:,}/"
                        f"{len(ticker_items):,} queries={query_count:,} "
                        f"events={event_row_count:,} elapsed={elapsed / 60:.1f}m "
                        f"eta={eta / 60:.1f}m",
                        flush=True,
                    )
            except KeyboardInterrupt:
                cancellation.request_stop()
                cancelled_futures = sum(future.cancel() for future in futures)
                cancelled_queries = cancellation.cancel_active_queries(client)
                print(
                    f"STOPPING | month={month_start:%Y-%m} "
                    f"queued_batches_cancelled={cancelled_futures:,} "
                    f"active_queries_cancelled={cancelled_queries:,}",
                    flush=True,
                )
                raise
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
            for array in targets.values():
                array.flush()
            write_json_atomic(
                state_path,
                {
                    "status": "building",
                    "rows": int(manifest["rows"]),
                    "v16_identity_sha256": identity_sha256,
                    "next_month": next_month.date().isoformat(),
                },
            )
            month_start = next_month

        train_upper = int(
            np.searchsorted(
                timestamps,
                int(_parse_utc(config.train_end_exclusive).timestamp() * 1_000_000),
            )
        )
        thresholds = fit_thresholds(
            np.asarray(targets["raw_metrics"][lower:train_upper]),
            np.asarray(targets["window_mask"][lower:train_upper]),
            quantile=args.threshold_quantile,
        )
        for row_index in range(lower, upper):
            for window_index in range(len(RESPONSE_WINDOWS)):
                if not targets["window_mask"][row_index, window_index]:
                    continue
                direction, path, flow = classify_window(
                    targets["raw_metrics"][row_index, window_index],
                    threshold=thresholds.meaningful_return[window_index],
                    contract=thresholds,
                )
                targets["direction"][row_index, window_index] = int(direction)
                targets["path"][row_index, window_index] = int(path)
                targets["flow"][row_index, window_index] = int(flow)
            if targets["window_mask"][row_index].any():
                targets["persistence"][row_index] = int(
                    classify_persistence(
                        targets["direction"][row_index],
                        targets["window_mask"][row_index],
                    )
                )
                targets["persistence_mask"][row_index] = True
        for array in targets.values():
            array.flush()
        target_audit = audit_target_arrays(v16, targets)
        write_json_atomic(config.target_root / THRESHOLDS_FILE, thresholds.as_dict())
        write_json_atomic(
            config.target_root / MANIFEST_FILE,
            {
                "status": "complete",
                "target_version": TARGET_VERSION,
                "rows": int(manifest["rows"]),
                "response_windows": list(RESPONSE_WINDOWS),
                "raw_metric_names": list(RAW_METRIC_NAMES),
                "v16_prepared_root": str(config.prepared_dataset_root),
                "v16_identity_sha256": identity_sha256,
                "threshold_fit_start": config.train_start,
                "threshold_fit_end_exclusive": config.train_end_exclusive,
                "threshold_quantile": args.threshold_quantile,
                "audit": target_audit,
            },
        )
        state_path.unlink(missing_ok=True)
        print(
            f"COMPLETED | V17 target sidecar rows={int(manifest['rows']):,} "
            f"root={config.target_root}",
            flush=True,
        )
        return 0
    except KeyboardInterrupt:
        cancellation.request_stop()
        print(
            f"INTERRUPTED | durable resume remains {state_path} "
            "(the incomplete month will be recomputed)",
            flush=True,
        )
        return 130
    finally:
        close_arrays(v16)
        close_arrays(targets)


if __name__ == "__main__":
    raise SystemExit(main())
