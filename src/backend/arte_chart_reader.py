"""Read certified, immutable ARTE market-day rows for compatible chart pages.

The QMD history gateway remains authoritative for unsupported indicators,
structure, market signals, macro frames, and incomplete market-day products.
This reader never creates or repairs data.
"""
from __future__ import annotations

import atexit
from datetime import date, datetime, timedelta
from functools import lru_cache
import json
import os
from threading import Lock
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, MarketDayLedger, assert_select_only,
    market_day_boundary,
)


_RESOLUTIONS = {
    "100ms": 100, "1s": 1_000, "5s": 5_000, "10s": 10_000,
    "30s": 30_000, "1m": 60_000, "5m": 300_000, "1h": 3_600_000,
}
_INDICATORS = frozenset({
    "bar_start", "ema_7", "ema_9", "ema_12", "ema_15", "ema_20",
    "ema_26", "ema_50", "macd_line", "macd_signal", "macd_histogram",
    "rsi_14", "atr_14",
})
_NY = ZoneInfo("America/New_York")
_plan_cache: dict[tuple[date, str, str], tuple[float, CertifiedMarketDayPlan | None]] = {}
_plan_lock = Lock()


def _literal(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


@lru_cache(maxsize=1)
def _reader():
    from research.mlops.clickhouse import (
        ClickHouseHttpClient, default_clickhouse_password,
        default_clickhouse_url, default_clickhouse_user,
    )
    return ClickHouseHttpClient(
        os.environ.get("ARTE_CHART_CLICKHOUSE_URL") or default_clickhouse_url(),
        os.environ.get("ARTE_CHART_CLICKHOUSE_USER") or default_clickhouse_user(),
        os.environ.get("ARTE_CHART_CLICKHOUSE_PASSWORD") or default_clickhouse_password(),
        timeout_seconds=15, persistent=True,
        default_query_params={"readonly": 1, "max_threads": 2,
                              "max_execution_time": 15},
    )


def _close_reader() -> None:
    if _reader.cache_info().currsize:
        _reader().close()


atexit.register(_close_reader)


def eligible(*, timeframe: str, stage: str, indicator_columns: list[str] | None,
             include_market_signals: bool, include_structure: bool,
             allow_persisted_bars: bool, mode: str) -> bool:
    return (allow_persisted_bars and mode in {"replay", "backtest", "debug"}
            and timeframe in _RESOLUTIONS and not include_market_signals
            and not include_structure and stage in {"bars", "full"}
            and (stage == "bars" or indicator_columns is not None)
            and (stage == "bars" or set(indicator_columns or ()).issubset(_INDICATORS)))


def certified_chart_plan(session: date, ticker: str, timeframe: str) -> CertifiedMarketDayPlan | None:
    """A short-lived catalogue lookup; absence is retried while backfill progresses."""
    key = (session, ticker, timeframe)
    now = monotonic()
    with _plan_lock:
        cached = _plan_cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
    try:
        plan = MarketDayLedger().certified_plan(
            sessions=(session,), tickers=(ticker,),
            configuration={"strategy": {"execution_interval": timeframe}},
        )
    except (OSError, ValueError):
        plan = None
    with _plan_lock:
        if len(_plan_cache) >= 512:
            _plan_cache.clear()
        _plan_cache[key] = (monotonic() + (15.0 if plan else 2.0), plan)
    return plan


def chart_revision(session: date, ticker: str, timeframe: str, *, stage: str,
                   indicator_columns: list[str] | None, include_market_signals: bool,
                   include_structure: bool, allow_persisted_bars: bool,
                   mode: str) -> str:
    if not eligible(timeframe=timeframe, stage=stage,
                    indicator_columns=indicator_columns,
                    include_market_signals=include_market_signals,
                    include_structure=include_structure,
                    allow_persisted_bars=allow_persisted_bars, mode=mode):
        return ""
    plan = certified_chart_plan(session, ticker, timeframe)
    return plan.token if plan else ""


def chart_page(*, session: date, ticker: str, timeframe: str,
               page_start: datetime, page_end: datetime, row_limit: int,
               stage: str, indicator_columns: list[str] | None,
               include_market_signals: bool, include_structure: bool,
               allow_persisted_bars: bool, mode: str) -> dict[str, Any] | None:
    if not eligible(timeframe=timeframe, stage=stage,
                    indicator_columns=indicator_columns,
                    include_market_signals=include_market_signals,
                    include_structure=include_structure,
                    allow_persisted_bars=allow_persisted_bars, mode=mode):
        return None
    plan = certified_chart_plan(session, ticker, timeframe)
    if plan is None:
        return None
    units = {(unit.stage, unit.session_date, unit.ticker): unit for unit in plan.units}
    bars_unit = units[("bars", session.isoformat(), ticker)]
    technical_unit = units[("technical", session.isoformat(), ticker)]
    resolution = _RESOLUTIONS[timeframe]
    origin = market_day_boundary(session, 0)
    start_ms = max(0, int((page_start.astimezone(_NY) - origin).total_seconds() * 1_000))
    end_ms = min(57_600_000, int((page_end.astimezone(_NY) - origin).total_seconds() * 1_000))
    if end_ms <= start_ms:
        return {"bars": [], "indicators": [], "has_more": False,
                "next_before": "", "source": "arte.market-day-core-v5", "token": plan.token,
                "indicator_provenance": {"authority": "arte.indicators_v1",
                                         "build_id": plan.build_id, "token": plan.token,
                                         "unavailable_columns": []}}
    # Bars-first requests may name optional QMD-only indicators. Never place
    # those names in ARTE SQL or infer them from bars; report unavailability.
    requested = set(indicator_columns or ()).difference({"bar_start"})
    unavailable = sorted(requested.difference(_INDICATORS))
    projected = sorted(requested.intersection(_INDICATORS))
    projection = "i.attempt_id AS indicator_attempt_id," + ",".join(
        f"i.{column} AS {column}" for column in projected)
    join = (f"LEFT JOIN arte.indicators_v1 i ON i.build_id=b.build_id "
            f"AND i.session_date=b.session_date AND i.ticker=b.ticker "
            f"AND i.resolution_ms=b.resolution_ms AND i.bucket_index=b.bucket_index "
            f"AND i.attempt_id=toUUID({_literal(technical_unit.attempt_id)})") if projected else ""
    query = assert_select_only(
        "SELECT b.bucket_index,b.open_int,b.high_int,b.low_int,b.close_int,"
        "b.volume,b.trade_count,b.notional"
        + ("," + projection if projection else "")
        + " FROM arte.bars_v1 b " + join
        + f" WHERE b.build_id={_literal(plan.build_id)} "
        + f"AND b.session_date=toDate({_literal(session.isoformat())}) "
        + f"AND b.ticker={_literal(ticker)} "
        + f"AND b.attempt_id=toUUID({_literal(bars_unit.attempt_id)}) "
        + f"AND b.resolution_ms={resolution} AND b.price_valid=1 AND b.extremes_valid=1 "
        + f"AND b.bucket_index*{resolution}>={start_ms} "
        + f"AND (b.bucket_index+1)*{resolution}<={end_ms} "
        + f"ORDER BY b.bucket_index DESC LIMIT {row_limit + 1} FORMAT JSONEachRow"
    )
    rows = [json.loads(line) for line in _reader().execute(query).splitlines() if line.strip()]
    has_more = len(rows) > row_limit
    selected = list(reversed(rows[:row_limit]))
    bars: list[dict[str, Any]] = []
    indicators: list[dict[str, Any]] = []
    for row in selected:
        start = market_day_boundary(session, int(row["bucket_index"]) * resolution)
        stamp = start.isoformat()
        bars.append({
            "bar_start": stamp, "bar_end": (start + timedelta(milliseconds=resolution)).isoformat(),
            "session_date": session.isoformat(), "timeframe": timeframe,
            "is_closed": True, "open": int(row["open_int"]) / 10_000,
            "high": int(row["high_int"]) / 10_000,
            "low": int(row["low_int"]) / 10_000,
            "close": int(row["close_int"]) / 10_000,
            "volume": float(row["volume"]), "trade_count": int(row["trade_count"]),
            "dollar_volume": float(row["notional"]),
        })
        if projected:
            if (str(row.get("indicator_attempt_id")) != technical_unit.attempt_id
                    or any(row.get(column) is None for column in projected)):
                raise ValueError("Certified ARTE chart has missing pinned indicator rows")
            indicators.append({"bar_start": stamp, **{column: row[column] for column in projected}})
    return {
        "bars": bars, "indicators": indicators, "has_more": has_more,
        "next_before": bars[0]["bar_start"] if has_more and bars else "",
        "source": "arte.market-day-core-v5", "token": plan.token,
        "indicator_provenance": {"authority": "arte.indicators_v1",
                                 "build_id": plan.build_id, "token": plan.token,
                                 "unavailable_columns": unavailable},
    }
