"""Cold Strategy 1 chart authority from normalized run and ARTE certificates.

This is SELECT-only. A saved run's token is a hash, not a market-data plan;
we reconstruct the certified plan and require the exact original hash before
reading any bars. No QMD builder, local run file, or current unpinned chart
selection can stand in for that proof.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Callable
from uuid import UUID

from src.backend.arte_chart_reader import _RESOLUTIONS, chart_page
from src.backend.backtest_v4_chart_context import (
    context_chart_page, context_chart_pages,
)
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS, assert_select_only,
    certified_market_plan_from_arte, market_day_boundary,
)
from src.backend.backtest_strategy_one_configuration import (
    certify_strategy_one_configuration,
)
from src.backend.backtest_v4_saved_review import load_v4_terminal_review_page
from src.trading_runtime.arte_backtest_definition import load_backtest_definition
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER


def _literal(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _pinned_quote(client: Any, plan: CertifiedMarketDayPlan, *, session: date,
                  ticker: str, boundary_ms: int) -> dict[str, Any] | None:
    """SELECT-only quote projection, bounded by the verified completed cursor."""
    matches = [unit for unit in plan.units if unit.session_date == session.isoformat()
               and unit.ticker == ticker and unit.stage == "broker_100ms"]
    if len(matches) != 1:
        raise RuntimeError("Saved chart has no unique pinned liquidity attempt")
    max_bucket = (boundary_ms + SESSION_OPEN_OFFSET_MS) // 100 - 1
    if max_bucket < 0:
        return None
    query = assert_select_only(
        "SELECT bucket_index,quote_timestamp_us,bid_int,ask_int,bid_size,ask_size "
        "FROM arte.liquidity_100ms_v1 "
        f"WHERE build_id={_literal(plan.build_id)} "
        f"AND session_date=toDate({_literal(session.isoformat())}) "
        f"AND ticker={_literal(ticker)} "
        f"AND attempt_id=toUUID({_literal(matches[0].attempt_id)}) "
        f"AND bucket_index<={max_bucket} AND quote_valid=1 "
        "ORDER BY bucket_index DESC LIMIT 1 FORMAT JSONEachRow"
    )
    lines = [line for line in client.execute(query).splitlines() if line.strip()]
    if not lines:
        return None
    row = json.loads(lines[0])
    quote_us = int(row["quote_timestamp_us"])
    age_us = int(market_day_boundary(session, boundary_ms).timestamp() * 1_000_000) - quote_us
    if age_us < 0:
        raise RuntimeError("Pinned quote is newer than the verified cursor")
    return {"bid": int(row["bid_int"]) / 10_000,
            "ask": int(row["ask_int"]) / 10_000,
            "bid_size": int(row["bid_size"]), "ask_size": int(row["ask_size"]),
            "quote_timestamp_us": quote_us, "age_ms": age_us / 1_000,
            "fresh": age_us <= 1_000_000}


def certified_saved_run_plan(
    journal_client: Any, market_client: Any, *, run_id: str,
    plan_loader: Callable[..., CertifiedMarketDayPlan] = certified_market_plan_from_arte,
) -> tuple[date, dict[str, Any], dict[str, Any], CertifiedMarketDayPlan]:
    """Shared immutable run authority for chart and producer-owned context."""
    normalized = str(UUID(run_id))
    review = load_v4_terminal_review_page(
        journal_client, normalized, after_sequence=0, limit=1)
    context = review["run"]
    cursor = review["market_cursor"]
    if (review["status"] != "completed" or not review["market_cursor_verified"]
            or not isinstance(cursor, dict)
            or context["mode"] != "backtest"
            or context["strategy_id"] != STRATEGY_ID
            or int(context["strategy_revision"]) != STRATEGY_NUMBER
            or context["evaluation_interval_ms"] != 100):
        raise ValueError("Saved Strategy 1 chart needs a completed verified cursor")
    definition = load_backtest_definition(
        journal_client, normalized, run_context=context)
    release = certify_strategy_one_configuration(market_client)
    if (release.payload_hash != context["configuration_hash"]
            or release.revision()["revision_id"]
            != definition["definition"]["configuration_revision_id"]):
        raise RuntimeError("Saved chart configuration differs from the run release")
    session = date.fromisoformat(str(context["session_date"]))
    requested = tuple(row["ticker"] for row in definition["tickers"])
    plan = plan_loader(
        sessions=(session,), tickers=requested, configuration=release.payload)
    if (not isinstance(plan, CertifiedMarketDayPlan)
            or plan.token != context["market_plan_token"]
            or plan.sessions != (session.isoformat(),)):
        raise RuntimeError("Saved chart cannot reproduce its certified market plan")
    return session, context, cursor, plan


def cold_v4_chart_page(
    journal_client: Any, market_client: Any, *, run_id: str,
    ticker: str, timeframe: str, before_boundary_ms: int | None = None,
    row_limit: int = 1000, indicator_columns: tuple[str, ...] = (),
    plan_loader: Callable[..., CertifiedMarketDayPlan] = certified_market_plan_from_arte,
) -> dict[str, Any]:
    """Read a page only after terminal, definition, release, and plan parity."""
    normalized = str(UUID(run_id))
    symbol = ticker.strip().upper()
    context_frame = timeframe in {"1d", "1mo"}
    if (not symbol or (timeframe not in _RESOLUTIONS and not context_frame)
            or type(row_limit) is not int or not 1 <= row_limit <= 5000
            or before_boundary_ms is not None and (
                type(before_boundary_ms) is not int
                or not 0 < before_boundary_ms <= 57_600_000
                or context_frame
                or before_boundary_ms % _RESOLUTIONS[timeframe]
            )
            or not isinstance(indicator_columns, tuple)
            or len(indicator_columns) > 32
            or any(not isinstance(column, str) or not column.isidentifier()
                   or len(column) > 64 for column in indicator_columns)):
        raise ValueError("Saved Strategy 1 chart request is invalid")
    session, run_context, cursor, plan = certified_saved_run_plan(
        journal_client, market_client, run_id=normalized, plan_loader=plan_loader)
    if (symbol not in plan.tickers
            or (not context_frame
                and _RESOLUTIONS[timeframe] not in plan.required_resolutions_ms)):
        raise RuntimeError("Saved chart cannot reproduce its certified market plan")
    cursor_ms = int(cursor["boundary_ms"])
    end_ms = min(cursor_ms, before_boundary_ms or cursor_ms)
    if end_ms <= 0:
        raise ValueError("Saved Strategy 1 chart has no completed boundary")
    if context_frame:
        release = certify_strategy_one_configuration(market_client)
        if release.payload_hash != run_context["configuration_hash"]:
            raise RuntimeError("Saved context release differs from market plan")
        page = context_chart_page(
            market_client, session=session, ticker=symbol,
            boundary_ms=end_ms, timeframe=timeframe, run_plan=plan,
            configuration=release.payload, plan_loader=plan_loader)
    else:
        selected = sorted(set(indicator_columns))
        page = chart_page(
            session=session, ticker=symbol, timeframe=timeframe,
            page_start=market_day_boundary(session, 0),
            page_end=market_day_boundary(session, end_ms), row_limit=row_limit,
            stage="full", indicator_columns=selected,
            include_market_signals=False, include_structure=False,
            allow_persisted_bars=True, mode="backtest", pinned_plan=plan,
            read_client=market_client,
        )
    if page is None:
        raise RuntimeError("Certified saved chart has no persisted ARTE page")
    quote = _pinned_quote(market_client, plan, session=session, ticker=symbol,
                          boundary_ms=end_ms)
    return {
        "schema_version": "strategy-one-v4-chart-page-v1",
        "run_id": normalized, "session_date": session.isoformat(),
        "ticker": symbol, "timeframe": timeframe,
        "market_plan_token": plan.token,
        "verified_boundary_ms": cursor_ms,
        "through_boundary_ms": end_ms,
        "quote": quote,
        **page,
    }


def cold_v4_chart_context_pair(
    journal_client: Any, market_client: Any, *, run_id: str, ticker: str,
    plan_loader: Callable[..., CertifiedMarketDayPlan] = certified_market_plan_from_arte,
) -> dict[str, Any]:
    """One certificate/read for both original Charts & Quotes context slots."""
    normalized = str(UUID(run_id))
    symbol = ticker.strip().upper()
    if not symbol or len(symbol) > 24:
        raise ValueError("Saved context ticker is invalid")
    session, context, cursor, plan = certified_saved_run_plan(
        journal_client, market_client, run_id=normalized,
        plan_loader=plan_loader)
    if symbol not in plan.tickers:
        raise ValueError("Saved context ticker is outside the run")
    release = certify_strategy_one_configuration(market_client)
    if release.payload_hash != context["configuration_hash"]:
        raise RuntimeError("Saved context release differs from run")
    pages = context_chart_pages(
        market_client, session=session, ticker=symbol,
        boundary_ms=int(cursor["boundary_ms"]), run_plan=plan,
        configuration=release.payload, plan_loader=plan_loader)
    return {"schema_version": "strategy-one-v4-chart-context-pair-v1",
            "run_id": normalized, "ticker": symbol,
            "session_date": session.isoformat(),
            "verified_boundary_ms": int(cursor["boundary_ms"]),
            "daily": {"ticker": symbol, "session_date": session.isoformat(),
                      "timeframe": "1d",
                      "verified_boundary_ms": int(cursor["boundary_ms"]),
                      **pages["1d"]},
            "monthly": {"ticker": symbol, "session_date": session.isoformat(),
                        "timeframe": "1mo",
                        "verified_boundary_ms": int(cursor["boundary_ms"]),
                        **pages["1mo"]}}
