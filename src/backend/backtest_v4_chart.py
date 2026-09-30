"""Cold Strategy 1 chart authority from normalized run and ARTE certificates.

This is SELECT-only. A saved run's token is a hash, not a market-data plan;
we reconstruct the certified plan and require the exact original hash before
reading any bars. No QMD builder, local run file, or current unpinned chart
selection can stand in for that proof.
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import date, datetime
from threading import Lock
from typing import Any, Callable
from uuid import UUID

from src.backend.arte_chart_reader import _INDICATORS, _RESOLUTIONS, chart_page
from src.backend.backtest_v4_chart_context import (
    context_chart_page, context_chart_pages,
)
from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS, assert_select_only,
    certified_market_plan_from_arte, market_day_boundary, project_market_day_plan,
)
from src.backend.backtest_strategy_one_configuration import (
    certify_strategy_one_configuration, certify_numbered_configuration,
)
from src.backend.backtest_v4_saved_review import load_v4_terminal_review_page
from src.trading_runtime.arte_backtest_definition import load_backtest_definition
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER
from src.trading_runtime.numbered_fixed_strategy import is_numbered_fixed_strategy


# A terminal saved run and its market-plan token are immutable. Keep only a
# bounded number of fully certified per-ticker V7 interval roots in memory so
# chart paging clips them without re-reading the whole structural product.
_v7_chart_cache: OrderedDict[tuple[str, str, str, str], tuple[Any, ...]] = OrderedDict()
_v7_chart_cache_lock = Lock()
_V7_CHART_CACHE_MAX = 16


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


def _pinned_execution_vwap(client: Any, plan: CertifiedMarketDayPlan, *,
                           session: date, ticker: str, timeframe: str,
                           bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project the persisted causal session VWAP at each completed bar end.

    A sparse quote/trade bucket may not update VWAP. Carry only the last
    already-persisted value; never calculate a second VWAP from chart bars.
    """
    if not bars:
        return []
    matches = [unit for unit in plan.units if unit.session_date == session.isoformat()
               and unit.ticker == ticker and unit.stage == "broker_100ms"]
    if len(matches) != 1:
        raise RuntimeError("Saved VWAP has no unique pinned liquidity attempt")
    resolution = _RESOLUTIONS[timeframe]
    first_start = int((datetime.fromisoformat(bars[0]["bar_start"])
                       - market_day_boundary(session, 0)).total_seconds() * 1000)
    last_end = int((datetime.fromisoformat(bars[-1]["bar_end"])
                    - market_day_boundary(session, 0)).total_seconds() * 1000)
    first_bucket = (first_start + SESSION_OPEN_OFFSET_MS) // 100
    last_bucket = (last_end + SESSION_OPEN_OFFSET_MS) // 100 - 1
    if not 0 <= first_start < last_end <= 57_600_000 or last_bucket < first_bucket:
        raise RuntimeError("Saved VWAP has an invalid completed bar clock")
    scope = (" FROM arte.liquidity_100ms_v1 "
             f"WHERE build_id={_literal(plan.build_id)} "
             f"AND session_date=toDate({_literal(session.isoformat())}) "
             f"AND ticker={_literal(ticker)} "
             f"AND attempt_id=toUUID({_literal(matches[0].attempt_id)}) "
             "AND execution_vwap>0 ")
    anchor_sql = assert_select_only(
        "SELECT bucket_index,execution_vwap" + scope
        + f"AND bucket_index<{first_bucket} "
        "ORDER BY bucket_index DESC LIMIT 1 FORMAT JSONEachRow")
    anchor = [json.loads(line) for line in client.execute(anchor_sql).splitlines()
              if line.strip()]
    factor = resolution // 100
    page_sql = assert_select_only(
        f"SELECT intDiv(bucket_index,{factor}) AS bar_bucket,"
        "argMax(execution_vwap,bucket_index) AS vwap_value"
        + scope + f"AND bucket_index>={first_bucket} "
        f"AND bucket_index<={last_bucket} GROUP BY bar_bucket "
        "ORDER BY bar_bucket FORMAT JSONEachRow")
    updates = {int(row["bar_bucket"]): float(row["vwap_value"])
               for line in client.execute(page_sql).splitlines() if line.strip()
               for row in (json.loads(line),)}
    value = float(anchor[0]["execution_vwap"]) if anchor else 0.0
    result = []
    for bar in bars:
        start = int((datetime.fromisoformat(bar["bar_start"])
                     - market_day_boundary(session, 0)).total_seconds() * 1000)
        bucket = (start + SESSION_OPEN_OFFSET_MS) // resolution
        value = updates.get(bucket, value)
        if value > 0:
            result.append({"bar_start": bar["bar_start"], "execution_vwap": value})
    return result


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
    # A failed terminal run remains failed, but its cold-verified market cursor
    # is still a safe read-only ceiling for chart inspection. Do not turn that
    # evidence into Backtest acceptance or admit stopped/unverified prefixes.
    if (review["status"] not in {"completed", "failed"}
            or not review["market_cursor_verified"]
            or not isinstance(cursor, dict)
            or context["mode"] != "backtest"
            or not is_numbered_fixed_strategy(context["strategy_id"], int(context["strategy_revision"]))
            or context["evaluation_interval_ms"] != 100):
        raise ValueError("Saved Strategy 1 chart needs a terminal verified cursor")
    definition = load_backtest_definition(
        journal_client, normalized, run_context=context)
    release = (certify_strategy_one_configuration(market_client) if int(context["strategy_revision"]) == 1
                   else certify_numbered_configuration(market_client, int(context["strategy_revision"])))
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


def _causal_v7_chart_segments(journal_client: Any, market_client: Any, *,
                              run_id: str, run_context: dict[str, Any],
                              session: date, ticker: str,
                              plan: CertifiedMarketDayPlan,
                              bars: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Project the certified scalar V7 stream derivative into this chart page.

    The producer made these intervals once from prior ARTE checkpoints and
    completed 1s bars. Chart and Backtest only SELECT the coverage-last product;
    retrospective structural_levels_v7 is never used as an intraday shortcut.
    """
    if not bars or len(bars) > 1000:
        return [], "No completed bars in this chart page"
    cache_key = (run_id, plan.token, session.isoformat(), ticker)
    with _v7_chart_cache_lock:
        rows = _v7_chart_cache.get(cache_key)
        if rows is not None:
            _v7_chart_cache.move_to_end(cache_key)
    if rows is None:
        from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan
        from src.backend.backtest_strategy_one_preparation import strategy_one_v7_tickers
        from src.backend.backtest_strategy_one_v7_interval_store import certify_v7_interval_plan
        from src.backend.structural_v7_seed import certified_seed_plan
        from src.trading_runtime.strategy_one_candidate_schema import RULE_DIGEST

        definition = load_backtest_definition(
            journal_client, run_id, run_context=run_context)
        candidates = certify_candidate_plan(
            plan, candidate_rule_digest=RULE_DIGEST,
            through_boundary_ms=57_600_000, client=market_client)
        selected = strategy_one_v7_tickers(candidates.prepared)
        if ticker not in selected:
            return [], "No certified V7 seed for this run ticker"
        execution_plan = project_market_day_plan(plan, selected)
        seeds = certified_seed_plan(execution_plan, market_client)
        if seeds.token != definition["definition"]["causal_v7_plan_token"]:
            raise RuntimeError("Saved chart V7 seed certificate differs from the run")
        # The full saved seed token proves the run's selected population. The
        # chart needs only this ticker's interval children.
        ticker_plan = project_market_day_plan(plan, (ticker,))
        intervals = certify_v7_interval_plan(
            ticker_plan, seeds, session_date=session.isoformat(),
            candidate_tickers=(ticker,), client=market_client)
        selected_rows = next((values for symbol, values in intervals.intervals
                              if symbol == ticker), None)
        if selected_rows is None:
            raise RuntimeError("Certified V7 interval plan omitted chart ticker")
        rows = tuple(selected_rows)
        with _v7_chart_cache_lock:
            _v7_chart_cache[cache_key] = rows
            _v7_chart_cache.move_to_end(cache_key)
            if len(_v7_chart_cache) > _V7_CHART_CACHE_MAX:
                _v7_chart_cache.popitem(last=False)
    first_ms = int((datetime.fromisoformat(bars[0]["bar_end"])
                    - market_day_boundary(session, 0)).total_seconds() * 1000)
    last_ms = int((datetime.fromisoformat(bars[-1]["bar_end"])
                   - market_day_boundary(session, 0)).total_seconds() * 1000)
    resolution_ms = int((datetime.fromisoformat(bars[-1]["bar_end"])
                         - datetime.fromisoformat(bars[-1]["bar_start"])).total_seconds() * 1000)
    if not 0 <= first_ms <= last_ms <= 57_600_000 or resolution_ms <= 0:
        raise RuntimeError("Saved V7 chart has an invalid completed bar clock")
    origin = market_day_boundary(session, 0).timestamp()
    segments = [{"level_id": row.level_id, "role": row.role,
                 "historical": row.historical,
                 "lower": row.lower, "upper": row.upper,
                 "start": origin + max(first_ms, row.valid_from_ms) / 1000,
                 "end": origin + min(last_ms + resolution_ms,
                                       row.valid_to_ms) / 1000}
                for row in rows
                if row.valid_from_ms < last_ms + resolution_ms
                and row.valid_to_ms > first_ms]
    return [row for row in segments if row["end"] > row["start"]], ""


def cold_v4_chart_page(
    journal_client: Any, market_client: Any, *, run_id: str,
    ticker: str, timeframe: str, before_boundary_ms: int | None = None,
    row_limit: int = 1000, indicator_columns: tuple[str, ...] = (),
    include_structure: bool = False,
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
            or not isinstance(include_structure, bool)
            or include_structure and (row_limit > 1000 or context_frame)
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
        release = (certify_strategy_one_configuration(market_client) if int(run_context["strategy_revision"]) == 1
                   else certify_numbered_configuration(market_client, int(run_context["strategy_revision"])))
        if release.payload_hash != run_context["configuration_hash"]:
            raise RuntimeError("Saved context release differs from market plan")
        page = context_chart_page(
            market_client, session=session, ticker=symbol,
            boundary_ms=end_ms, timeframe=timeframe, run_plan=plan,
            configuration=release.payload, plan_loader=plan_loader)
    else:
        selected = sorted(set(indicator_columns) - {"execution_vwap"})
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
    if "execution_vwap" in indicator_columns and not context_frame:
        vwap = _pinned_execution_vwap(
            market_client, plan, session=session, ticker=symbol,
            timeframe=timeframe, bars=page["bars"])
        by_start = {row["bar_start"]: row for row in page["indicators"]}
        for row in vwap:
            by_start.setdefault(row["bar_start"], {"bar_start": row["bar_start"]}).update(row)
        page["indicators"] = [by_start[key] for key in sorted(by_start)]
    quote = _pinned_quote(market_client, plan, session=session, ticker=symbol,
                          boundary_ms=end_ms)
    structure, structure_reason = (_causal_v7_chart_segments(
        journal_client, market_client, run_id=normalized, run_context=run_context,
        session=session,
        ticker=symbol, plan=plan, bars=page["bars"])
        if include_structure else ([], "Not requested"))
    return {
        "schema_version": "strategy-one-v4-chart-page-v1",
        "run_id": normalized, "session_date": session.isoformat(),
        "ticker": symbol, "timeframe": timeframe,
        "market_plan_token": plan.token,
        "verified_boundary_ms": cursor_ms,
        "through_boundary_ms": end_ms,
        "quote": quote,
        "structural_levels": structure,
        "structural_provenance": {"authority": "prior V7 seed + completed ARTE 1s bars",
                                  "available": not structure_reason,
                                  "reason": structure_reason},
        **page,
    }


def cold_v4_chart_overlays(
    journal_client: Any, market_client: Any, *, run_id: str,
    ticker: str, timeframe: str, bucket_indices: tuple[int, ...],
    indicator_columns: tuple[str, ...] = (), include_structure: bool = False,
    plan_loader: Callable[..., CertifiedMarketDayPlan] = certified_market_plan_from_arte,
) -> dict[str, Any]:
    """Read pinned overlays for client-held candles without selecting bars again."""
    normalized = str(UUID(run_id))
    symbol = ticker.strip().upper()
    if (not re.fullmatch(r"[A-Z0-9.\-]{1,24}", symbol)
            or timeframe not in _RESOLUTIONS
            or not isinstance(bucket_indices, tuple)
            or not 1 <= len(bucket_indices) <= 1000
            or any(type(bucket) is not int or bucket < 0 for bucket in bucket_indices)
            or any(left >= right for left, right in zip(bucket_indices, bucket_indices[1:]))
            or not isinstance(indicator_columns, tuple)
            or len(indicator_columns) > 32
            or any(column not in (_INDICATORS | {"execution_vwap"}) or column == "bar_start"
                   for column in indicator_columns)
            or not isinstance(include_structure, bool)):
        raise ValueError("Saved chart overlay request is invalid")
    session, context, cursor, plan = certified_saved_run_plan(
        journal_client, market_client, run_id=normalized, plan_loader=plan_loader)
    resolution = _RESOLUTIONS[timeframe]
    if symbol not in plan.tickers or resolution not in plan.required_resolutions_ms:
        raise RuntimeError("Saved overlay is outside the certified market plan")
    last_end_ms = (bucket_indices[-1] + 1) * resolution - SESSION_OPEN_OFFSET_MS
    first_start_ms = bucket_indices[0] * resolution - SESSION_OPEN_OFFSET_MS
    if not 0 <= first_start_ms < last_end_ms <= int(cursor["boundary_ms"]):
        raise ValueError("Saved overlay exceeds the completed run cursor")
    bars = [
        {"bar_start": (market_day_boundary(session, bucket * resolution
                                             - SESSION_OPEN_OFFSET_MS)).isoformat(),
         "bar_end": (market_day_boundary(session, (bucket + 1) * resolution
                                           - SESSION_OPEN_OFFSET_MS)).isoformat()}
        for bucket in bucket_indices
    ]
    selected = sorted(set(indicator_columns) - {"execution_vwap"})
    indicators: list[dict[str, Any]] = []
    if selected:
        matches = [unit for unit in plan.units if unit.session_date == session.isoformat()
                   and unit.ticker == symbol and unit.stage == "technical"]
        if len(matches) != 1:
            raise RuntimeError("Saved overlay lacks a unique pinned technical attempt")
        columns = ",".join(selected)
        buckets = ",".join(str(bucket) for bucket in bucket_indices)
        query = assert_select_only(
            f"SELECT bucket_index,{columns} FROM arte.indicators_v1 "
            f"WHERE build_id={_literal(plan.build_id)} "
            f"AND session_date=toDate({_literal(session.isoformat())}) "
            f"AND ticker={_literal(symbol)} "
            f"AND attempt_id=toUUID({_literal(matches[0].attempt_id)}) "
            f"AND resolution_ms={resolution} AND bucket_index IN ({buckets}) "
            "ORDER BY bucket_index FORMAT JSONEachRow")
        rows = [json.loads(line) for line in market_client.execute(query).splitlines()
                if line.strip()]
        # A missing row must not be disguised as an unavailable/stale overlay.
        if ([int(row["bucket_index"]) for row in rows] != list(bucket_indices)
                or any(row.get(column) is None for row in rows for column in selected)):
            raise RuntimeError("Certified ARTE overlay has missing pinned indicator rows")
        indicators = [{"bar_start": bars[index]["bar_start"],
                       **{column: row[column] for column in selected}}
                      for index, row in enumerate(rows)]
    if "execution_vwap" in indicator_columns:
        by_start = {row["bar_start"]: row for row in indicators}
        for row in _pinned_execution_vwap(market_client, plan, session=session,
                                          ticker=symbol, timeframe=timeframe,
                                          bars=bars):
            by_start.setdefault(row["bar_start"], {"bar_start": row["bar_start"]}).update(row)
        indicators = [by_start[key] for key in sorted(by_start)]
    structure, reason = (_causal_v7_chart_segments(
        journal_client, market_client, run_id=normalized, run_context=context,
        session=session, ticker=symbol, plan=plan, bars=bars)
        if include_structure else ([], "Not requested"))
    return {"schema_version": "strategy-one-v4-chart-overlays-v1",
            "run_id": normalized, "ticker": symbol, "timeframe": timeframe,
            "market_plan_token": plan.token, "bucket_indices": list(bucket_indices),
            "indicators": indicators, "structural_levels": structure,
            "structural_provenance": {"authority": "prior V7 seed + completed ARTE 1s bars",
                                      "available": not reason, "reason": reason}}


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
    release = (certify_strategy_one_configuration(market_client) if int(context["strategy_revision"]) == 1
                   else certify_numbered_configuration(market_client, int(context["strategy_revision"])))
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
