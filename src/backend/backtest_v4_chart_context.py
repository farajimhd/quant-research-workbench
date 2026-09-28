"""Causal daily/monthly chart context from certified ARTE 30-second bars.

This is presentation-only and SELECT-only. A context candle may be partial at
the saved run cursor, but every contributing source bar is completed. It is
never fed back into Strategy 1 or used as a market-day certificate shortcut.
"""
from __future__ import annotations

from datetime import date
import json
from typing import Any, Callable, Mapping

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS, assert_select_only,
    certified_market_plan_from_arte, market_day_boundary,
)

_RESOLUTION = 30_000
_MAX_SESSIONS = 500


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def context_chart_page(
    client: Any, *, session: date, ticker: str, boundary_ms: int,
    timeframe: str, run_plan: CertifiedMarketDayPlan,
    configuration: Mapping[str, Any],
    plan_loader: Callable[..., CertifiedMarketDayPlan] = certified_market_plan_from_arte,
) -> dict[str, Any]:
    if timeframe not in {"1d", "1mo"} or not 0 < boundary_ms <= 57_600_000:
        raise ValueError("Saved context chart timeframe or cursor is invalid")
    if ticker not in run_plan.tickers or session.isoformat() not in run_plan.sessions:
        raise ValueError("Saved context ticker is outside its run")
    # Planned scope discovers dates only. It is not authority to read bars;
    # the separate Keeper-attested plan below pins every selected attempt.
    candidates = [json.loads(line)["session_date"] for line in client.execute(
        assert_select_only(
            "SELECT DISTINCT session_date FROM arte.market_day_planned_scope_v1 "
            f"WHERE build_id={_literal(run_plan.build_id)} "
            f"AND ticker={_literal(ticker)} "
            f"AND session_date<=toDate({_literal(session.isoformat())}) "
            f"ORDER BY session_date DESC LIMIT {_MAX_SESSIONS + 1} FORMAT JSONEachRow"
        )).splitlines() if line.strip()]
    if (not candidates or len(candidates) > _MAX_SESSIONS
            or candidates[0] != session.isoformat()
            or len(candidates) != len(set(candidates))):
        raise RuntimeError("Saved context historical scope is missing or unbounded")
    days = tuple(reversed(candidates))
    history = plan_loader(sessions=days, tickers=(ticker,), configuration=configuration)
    if (history.build_id != run_plan.build_id or history.sessions != days
            or history.tickers != (ticker,)):
        raise RuntimeError("Saved context certificate differs from run build")
    units = {(unit.session_date, unit.ticker): unit for unit in history.units
             if unit.stage == "bars"}
    if len(units) != len(days) or any((day, ticker) not in units for day in days):
        raise RuntimeError("Saved context lacks a certified bar attempt")
    scopes = ",".join(
        f"(toDate({_literal(day)}),toUUID({_literal(units[(day, ticker)].attempt_id)}))"
        for day in days)
    complete_bucket = (boundary_ms + SESSION_OPEN_OFFSET_MS) // _RESOLUTION
    query = assert_select_only(
        "SELECT session_date,argMin(open_int,bucket_index) AS open_int,"
        "max(high_int) AS high_int,min(low_int) AS low_int,"
        "argMax(close_int,bucket_index) AS close_int,sum(volume) AS volume,"
        "count() AS source_rows FROM arte.bars_v1 "
        f"WHERE build_id={_literal(run_plan.build_id)} "
        f"AND ticker={_literal(ticker)} AND resolution_ms={_RESOLUTION} "
        "AND price_valid=1 AND extremes_valid=1 "
        f"AND (session_date,attempt_id) IN ({scopes}) "
        f"AND (session_date<toDate({_literal(session.isoformat())}) "
        f"OR bucket_index<{complete_bucket}) "
        "GROUP BY session_date ORDER BY session_date FORMAT JSONEachRow"
    )
    rows = [json.loads(line) for line in client.execute(query).splitlines()
            if line.strip()]
    if (len(rows) > len(days) or len({row["session_date"] for row in rows}) != len(rows)
            or any(row["session_date"] not in days or int(row["source_rows"]) <= 0
                   for row in rows)):
        raise RuntimeError("Saved context aggregation returned unexpected sessions")
    daily = []
    for row in rows:
        day = date.fromisoformat(row["session_date"])
        current = day == session
        end = market_day_boundary(day, boundary_ms if current else 57_600_000)
        daily.append({
            "bar_start": market_day_boundary(day, 0).isoformat(),
            "bar_end": end.isoformat(), "session_date": day.isoformat(),
            "open": int(row["open_int"]) / 10_000,
            "high": int(row["high_int"]) / 10_000,
            "low": int(row["low_int"]) / 10_000,
            "close": int(row["close_int"]) / 10_000,
            "volume": int(row["volume"]),
            "is_closed": not current or boundary_ms == 57_600_000,
        })
    if timeframe == "1d":
        bars = daily
    else:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for bar in daily:
            grouped.setdefault(bar["session_date"][:7], []).append(bar)
        bars = []
        for month, items in grouped.items():
            first, last = items[0], items[-1]
            bars.append({
                "bar_start": first["bar_start"], "bar_end": last["bar_end"],
                "session_date": last["session_date"],
                "open": first["open"], "high": max(item["high"] for item in items),
                "low": min(item["low"] for item in items),
                "close": last["close"],
                "volume": sum(item["volume"] for item in items),
                # Session certificates do not prove that this ticker has every
                # exchange day in a calendar month. Do not label a composed
                # monthly candle fully closed until period coverage exists.
                "is_closed": False,
            })
    return {
        "bars": bars, "indicators": [], "has_more": False, "next_before": "",
        "source": "arte.bars_v1@30s-certified-context",
        "history_first_session": days[0], "history_session_count": len(days),
        "history_limited": True,
        "indicator_provenance": {
            "authority": "arte.indicators_v1", "build_id": run_plan.build_id,
            "token": history.token,
            "unavailable_columns": ["daily/monthly indicators not persisted"],
        },
    }
