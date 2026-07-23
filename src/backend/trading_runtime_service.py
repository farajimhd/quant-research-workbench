from __future__ import annotations

import os
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pipelines.reference_data.clickhouse_load_market_references import build_condition_token_rows
from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.orchestrator import historical_run_window
from src.trading_runtime.runtime import RunMode


REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_HISTORICAL_TIMEFRAMES = {
    "100ms",
    "1s",
    "5s",
    "10s",
    "30s",
    "1m",
    "5m",
    "1h",
    "1d",
    "1mo",
}
MACRO_CHART_TIMEFRAMES = {"1d", "1mo"}
HISTORICAL_CHUNK_MINUTES = 15
MARKET_REFERENCE_DIR = REPO_ROOT / "research" / "market_references" / "massive"


@lru_cache(maxsize=1)
def trading_journal() -> TradingJournal:
    configured = os.environ.get("TRADING_JOURNAL_PATH", "").strip()
    path = Path(configured) if configured else REPO_ROOT / "runtime" / "trading" / "journal.sqlite3"
    return TradingJournal(path)


def save_strategy_definition(payload: dict[str, Any]) -> dict[str, Any]:
    strategy_id = str(payload.get("strategy_id") or "").strip()
    name = str(payload.get("name") or "").strip()
    implementation = str(payload.get("implementation") or "").strip()
    if not strategy_id or not name or not implementation:
        raise ValueError("strategy_id, name, and implementation are required")
    revision = int(payload.get("revision") or 0)
    if revision <= 0:
        existing = trading_journal().strategy(strategy_id)
        revision = int(existing["revision"]) + 1 if existing else 1
    trading_journal().save_strategy(
        strategy_id=strategy_id,
        revision=revision,
        name=name,
        implementation=implementation,
        automatic=bool(payload.get("automatic", True)),
        enabled=bool(payload.get("enabled", True)),
        config=dict(payload.get("config") or {}),
    )
    return trading_journal().strategy(strategy_id, revision) or {}


def list_strategy_definitions(latest_only: bool = True) -> list[dict[str, Any]]:
    return trading_journal().strategies(latest_only=latest_only)


def get_strategy_definition(strategy_id: str, revision: int | None = None) -> dict[str, Any]:
    result = trading_journal().strategy(strategy_id, revision)
    if result is None:
        raise KeyError(strategy_id)
    return result


def get_trade_annotation(episode_id: str) -> dict[str, Any]:
    return trading_journal().trade_annotation(episode_id) or {
        "episode_id": episode_id,
        "note": "",
        "tags": [],
        "review_status": "unreviewed",
        "setup_override": "",
        "updated_at": None,
    }


def save_trade_annotation(episode_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_id = str(episode_id or "").strip()
    if not normalized_id:
        raise ValueError("episode_id is required")
    return trading_journal().save_trade_annotation(
        normalized_id,
        note=str(payload.get("note") or "").strip(),
        tags=payload.get("tags") or (),
        review_status=str(payload.get("review_status") or "unreviewed"),
        setup_override=str(payload.get("setup_override") or "").strip(),
    )


def historical_gateway_base_url() -> str:
    configured = os.environ.get("QMD_HISTORY_GATEWAY_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    bind = os.environ.get("QMD_HISTORY_BIND", "127.0.0.1:8801").strip()
    if bind.startswith("http://") or bind.startswith("https://"):
        return bind.rstrip("/")
    host, separator, port = bind.rpartition(":")
    resolved_host = host if separator else bind
    resolved_port = port if separator else "8801"
    if resolved_host in {"0.0.0.0", "::", "[::]"}:
        resolved_host = "127.0.0.1"
    return f"http://{resolved_host}:{resolved_port}"


def historical_gateway_websocket_url(path: str, params: dict[str, Any]) -> str:
    parsed = urllib.parse.urlsplit(historical_gateway_base_url())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("QMD History gateway URL must use http or https")
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
    target_path = f"{parsed.path.rstrip('/')}/{path.lstrip('/')}"
    return urllib.parse.urlunsplit(
        ("wss" if parsed.scheme == "https" else "ws", parsed.netloc, target_path, query, "")
    )


def historical_gateway_snapshot() -> dict[str, Any]:
    base_url = historical_gateway_base_url()
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        ready = (
            payload.get("service") == "qmd_history_gateway"
            and payload.get("host_role") == "historical"
            and payload.get("status") == "ready"
            and payload.get("running") is True
        )
        return {
            "base_url": base_url,
            "online": True,
            "ready": ready,
            "status": "ready" if ready else "degraded",
            "health": payload,
        }
    except Exception as exc:
        return {
            "base_url": base_url,
            "online": False,
            "ready": False,
            "status": "offline",
            "error": str(exc),
            "health": {},
        }


def historical_window_preview(
    *,
    mode: str,
    anchor_date: date,
    session_count: int,
    replay_end_date: date | None,
) -> dict[str, Any]:
    resolved_mode = RunMode(mode)
    # Replay is deliberately a single exchange day. Keep this invariant in the
    # backend so an old or external client cannot silently create a multi-day run.
    if resolved_mode == RunMode.REPLAY:
        replay_end_date = anchor_date
    window = historical_run_window(
        resolved_mode,
        anchor_date,
        session_count=session_count,
        replay_end_date=replay_end_date,
    )
    return {
        "mode": resolved_mode.value,
        "anchor_date": anchor_date.isoformat(),
        "anchor_semantics": "inclusive" if resolved_mode == RunMode.REPLAY else "exclusive",
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "sessions": [session.isoformat() for session in window.sessions],
        "session_count": len(window.sessions),
        "source": "qmd_history_gateway",
        "source_url": historical_gateway_base_url(),
        "broker": "simulated_ibkr",
    }


def historical_preflight(
    *,
    mode: str,
    anchor_date: date,
    session_count: int,
) -> dict[str, Any]:
    window = historical_window_preview(
        mode=mode,
        anchor_date=anchor_date,
        session_count=session_count,
        replay_end_date=anchor_date if mode == RunMode.REPLAY.value else None,
    )
    gateway = historical_gateway_snapshot()
    strategies = list_strategy_definitions(latest_only=True)
    automatic_strategies = [row for row in strategies if row.get("automatic") and row.get("enabled")]
    checks: list[dict[str, Any]] = [
        _preflight_check(
            "historical_source",
            "Historical market source",
            "ready" if gateway.get("ready") else "error",
            "QMD History answered with its historical role and canonical event source."
            if gateway.get("ready")
            else "QMD History did not answer with a ready historical identity.",
            gateway.get("health", {}).get("source") or gateway.get("error") or gateway.get("base_url", ""),
            required=True,
        ),
        _preflight_check(
            "session_window",
            "Exchange-day window",
            "ready",
            "One inclusive exchange day, 04:00-20:00 New York."
            if mode == RunMode.REPLAY.value
            else f"{window['session_count']} sessions strictly before the anchor date.",
            f"{window['start']} -> {window['end']}",
            required=True,
        ),
    ]

    coverage: dict[str, Any] = {}
    data_error = ""
    if gateway.get("ready"):
        try:
            payload = _historical_gateway_get(
                "/coverage",
                {"start": window["start"], "end": window["end"]},
                timeout=30,
            )
            if isinstance(payload, dict):
                coverage = payload
            if int(coverage.get("event_count") or 0) <= 0:
                data_error = "No canonical market events were found in the resolved session window."
        except Exception as exc:
            data_error = str(exc)
    elif gateway.get("error"):
        data_error = str(gateway["error"])

    event_count = int(coverage.get("event_count") or 0)
    ticker_count = int(coverage.get("ticker_count") or 0)
    market_ready = bool(event_count > 0 and ticker_count > 0 and not data_error)
    checks.append(
        _preflight_check(
            "market_data",
            "Canonical event coverage",
            "ready" if market_ready else "error",
            (
                f"{event_count:,} events across {ticker_count:,} symbols are recorded for the selected exchange day set."
                if market_ready
                else data_error or "Historical market data could not be verified."
            ),
            (
                f"Coverage: {coverage.get('coverage_table')}; events: {', '.join(coverage.get('source_tables') or [])}"
                if market_ready
                else "No usable sample evidence."
            ),
            required=True,
        )
    )
    checks.append(
        _preflight_check(
            "strategy_authority",
            "Automatic strategy revisions",
            "ready" if automatic_strategies else "blocked",
            f"{len(automatic_strategies)} enabled automatic revision(s) are available."
            if automatic_strategies
            else "No enabled automatic strategy revision exists in the central trading authority.",
            "Required for strategy execution and every backtest; optional for market-only replay.",
            required=mode != RunMode.REPLAY.value,
        )
    )
    checks.append(
        _preflight_check(
            "run_controller",
            "Trading run controller",
            "blocked",
            "The shared strategy/broker run-controller API is not implemented.",
            "Market replay can run; simulated orders, portfolio, fills, and strategy execution cannot be claimed yet.",
            required=mode != RunMode.REPLAY.value,
        )
    )
    return {
        "mode": mode,
        "window": window,
        "gateway": gateway,
        "checks": checks,
        "market_ready": market_ready,
        "strategy_run_ready": False,
        "automatic_strategy_count": len(automatic_strategies),
        "coverage": coverage,
    }


def historical_bar_chunk(
    *,
    anchor_date: date,
    ticker: str,
    timeframe: str,
    offset_minutes: int,
    window_minutes: int = HISTORICAL_CHUNK_MINUTES,
) -> dict[str, Any]:
    resolved_ticker = _historical_ticker(ticker)
    resolved_timeframe = _historical_timeframe(timeframe)
    if not 0 <= offset_minutes < 960:
        raise ValueError("offset_minutes must be between 0 and 959")
    if not 1 <= window_minutes <= 30:
        raise ValueError("window_minutes must be between 1 and 30")
    window = historical_window_preview(
        mode=RunMode.REPLAY.value,
        anchor_date=anchor_date,
        session_count=1,
        replay_end_date=anchor_date,
    )
    day_start = datetime.fromisoformat(window["start"])
    day_end = datetime.fromisoformat(window["end"])
    chunk_start = day_start + timedelta(minutes=offset_minutes)
    chunk_end = min(chunk_start + timedelta(minutes=window_minutes), day_end)
    snapshot = _historical_gateway_get(
        f"/snapshot/bars/{urllib.parse.quote(resolved_ticker)}",
        {
            "start": chunk_start.isoformat(),
            "end": chunk_end.isoformat(),
            "timeframe": resolved_timeframe,
            "limit": 5_000,
            "event_limit": 1_000_000,
        },
        timeout=45,
    )
    bars = list(snapshot.get("history") or []) if isinstance(snapshot, dict) else []
    if isinstance(snapshot, dict) and snapshot.get("current"):
        bars.append(dict(snapshot["current"]))
    indicators = list(snapshot.get("indicators") or []) if isinstance(snapshot, dict) else []
    structure_events = list(snapshot.get("structure_events") or []) if isinstance(snapshot, dict) else []
    return {
        "ticker": resolved_ticker,
        "timeframe": resolved_timeframe,
        "session_date": anchor_date.isoformat(),
        "offset_minutes": offset_minutes,
        "next_offset_minutes": min(960, offset_minutes + window_minutes),
        "complete": chunk_end >= day_end,
        "start": chunk_start.isoformat(),
        "end": chunk_end.isoformat(),
        "bars": bars,
        "indicators": indicators,
        "structure_events": structure_events,
        "bar_count": len(bars),
        "source": "qmd_history_gateway",
    }


def historical_latest_coverage() -> dict[str, Any]:
    payload = _historical_gateway_get("/coverage/latest", {}, timeout=15)
    if not isinstance(payload, dict):
        raise RuntimeError("QMD History latest coverage response must be an object")
    return payload


def historical_bar_history_before(
    *,
    before: date,
    ticker: str,
    timeframe: str,
    row_limit: int = 20_000,
    session_date: date | None = None,
    as_of: str | None = None,
    before_bar: str | None = None,
    indicator_columns: list[str] | None = None,
) -> dict[str, Any]:
    resolved_ticker = _historical_ticker(ticker)
    resolved_timeframe = _historical_timeframe(timeframe)
    if resolved_timeframe in MACRO_CHART_TIMEFRAMES:
        return historical_macro_bar_history(
            ticker=resolved_ticker,
            timeframe=resolved_timeframe,
            session_date=session_date or before,
            as_of=as_of,
        )
    coverage = None
    if session_date is None:
        coverage = _historical_gateway_get(
            "/coverage/latest",
            {"before": before.isoformat()},
            timeout=15,
        )
    session_date_text = session_date.isoformat() if session_date else str(coverage.get("session_date") or "") if isinstance(coverage, dict) else ""
    if not session_date_text:
        return {
            "ticker": resolved_ticker,
            "timeframe": resolved_timeframe,
            "history": [],
            "indicators": [],
            "structure_events": [],
            "earliest_session_date": "",
            "has_more": False,
            "source": "qmd_history_gateway",
        }
    resolved_session_date = date.fromisoformat(session_date_text)
    window = historical_window_preview(
        mode=RunMode.REPLAY.value,
        anchor_date=resolved_session_date,
        session_count=1,
        replay_end_date=resolved_session_date,
    )
    window_start = datetime.fromisoformat(window["start"])
    window_end = datetime.fromisoformat(window["end"])
    resolved_as_of = datetime.fromisoformat(as_of) if as_of else window_end
    if resolved_as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    resolved_as_of = max(window_start, min(resolved_as_of, window_end))
    snapshot = _historical_gateway_get(
        f"/snapshot/chart-bars/{urllib.parse.quote(resolved_ticker)}",
        {
            "start": window["start"],
            "end": window["end"],
            "as_of": resolved_as_of.isoformat(),
            "before": before_bar,
            "indicator_columns": ",".join(dict.fromkeys(indicator_columns)) if indicator_columns else None,
            "timeframe": resolved_timeframe,
            "limit": max(1, min(row_limit, 50_000)),
        },
        timeout=90,
    )
    bars = list(snapshot.get("bars") or []) if isinstance(snapshot, dict) else []
    indicators = list(snapshot.get("indicators") or []) if isinstance(snapshot, dict) else []
    bars.sort(key=_bar_start_sort_key)
    indicators.sort(key=_bar_start_sort_key)
    has_more_in_session = bool(snapshot.get("has_more")) if isinstance(snapshot, dict) else False
    previous_session_before = ""
    if not has_more_in_session:
        previous = _historical_gateway_get(
            "/coverage/latest",
            {"before": resolved_session_date.isoformat()},
            timeout=15,
        )
        if isinstance(previous, dict) and previous.get("session_date"):
            previous_session_before = resolved_session_date.isoformat()
    return {
        "ticker": resolved_ticker,
        "timeframe": resolved_timeframe,
        "history": bars,
        "indicators": indicators,
        "indicators_available": bool(snapshot.get("indicators_available")) if isinstance(snapshot, dict) else False,
        "earliest_session_date": session_date_text if bars else "",
        "has_more": has_more_in_session or bool(previous_session_before),
        "has_more_in_session": has_more_in_session,
        "next_before": str(snapshot.get("next_before") or "") if isinstance(snapshot, dict) else "",
        "previous_session_before": previous_session_before,
        "as_of": resolved_as_of.isoformat(),
        "source": "qmd_history_gateway",
    }


def historical_macro_bar_history(
    *,
    ticker: str,
    timeframe: str,
    session_date: date,
    as_of: str | None,
) -> dict[str, Any]:
    resolved_as_of = datetime.fromisoformat(as_of) if as_of else datetime.combine(session_date, time(20, 0), tzinfo=ZoneInfo("America/New_York"))
    if resolved_as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    if timeframe == "1mo":
        month_index = resolved_as_of.year * 12 + resolved_as_of.month - 1 - 23
        start = resolved_as_of.replace(
            year=month_index // 12,
            month=month_index % 12 + 1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    else:
        start = (resolved_as_of - timedelta(days=179)).replace(hour=0, minute=0, second=0, microsecond=0)
    payload = _historical_gateway_get(
        f"/snapshot/chart-macro-bars/{urllib.parse.quote(ticker)}",
        {
            "start": start.isoformat(),
            "end": (resolved_as_of + timedelta(days=1)).isoformat(),
            "as_of": resolved_as_of.isoformat(),
            "timeframe": timeframe,
        },
        timeout=30,
    )
    rows = [
        {
            "schema_version": 1,
            "session_date": row.get("session_date"),
            "timeframe": timeframe,
            "sym": ticker,
            "bar_start": row.get("bar_start"),
            "bar_end": row.get("bar_end"),
            "is_closed": bool(row.get("is_closed", True)),
            "open": row.get("open"),
            "high": row.get("high"),
            "low": row.get("low"),
            "close": row.get("close"),
            "volume": row.get("size_sum"),
            "vwap": None,
        }
        for row in (payload.get("bars") or [])
        if isinstance(row, dict) and row.get("bar_family") == "trade"
    ] if isinstance(payload, dict) else []
    rows.sort(key=_bar_start_sort_key)
    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "history": rows,
        "indicators": [],
        "structure_events": [],
        "indicators_available": False,
        "earliest_session_date": str(rows[0].get("session_date") or "") if rows else "",
        "has_more": False,
        "has_more_in_session": False,
        "next_before": "",
        "previous_session_before": "",
        "as_of": resolved_as_of.isoformat(),
        "source": payload.get("source", "qmd_history_gateway") if isinstance(payload, dict) else "qmd_history_gateway",
    }


def _bar_start_sort_key(row: dict[str, Any]) -> float:
    value = row.get("bar_start")
    if not isinstance(value, str) or not value:
        return float("inf")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("inf")


def historical_day_coverage(anchor_date: date) -> dict[str, Any]:
    window = historical_window_preview(
        mode=RunMode.REPLAY.value,
        anchor_date=anchor_date,
        session_count=1,
        replay_end_date=anchor_date,
    )
    payload = _historical_gateway_get(
        "/coverage",
        {"start": window["start"], "end": window["end"]},
        timeout=15,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("QMD History day coverage response must be an object")
    return payload


def historical_compact_events(
    ticker: str,
    *,
    start: str,
    end: str,
    row_limit: int = 500,
) -> list[dict[str, Any]]:
    resolved_ticker = _historical_ticker(ticker)
    payload = _historical_gateway_get(
        f"/snapshot/compact-events/{urllib.parse.quote(resolved_ticker)}",
        {"start": start, "end": end, "limit": row_limit, "tail": "true"},
        timeout=15,
    )
    if not isinstance(payload, list):
        raise RuntimeError("QMD History compact-event response must be an array")
    return [row for row in payload if isinstance(row, dict)]


def historical_market_state(ticker: str, *, start: str, end: str) -> dict[str, Any]:
    """Return QMD-derived halt/resume and estimated LULD state at a historical cutoff."""
    resolved_ticker = _historical_ticker(ticker)
    common = {"start": start, "end": end, "as_of": end, "limit": 50_000}
    conditions = _historical_gateway_get(
        f"/snapshot/condition-bars/{urllib.parse.quote(resolved_ticker)}",
        {**common, "resolution": "1s"},
        timeout=90,
    )
    chart = _historical_gateway_get(
        f"/snapshot/chart-bars/{urllib.parse.quote(resolved_ticker)}",
        {**common, "timeframe": "1s", "limit": 1},
        timeout=90,
    )
    rows = list(conditions.get("rows") or []) if isinstance(conditions, dict) else []
    trading_status = "trading"
    status_as_of = end
    for row in sorted(rows, key=lambda item: int(item.get("last_event_timestamp_us") or 0)):
        if row.get("condition_halt_pause_flag"):
            trading_status = "halted"
            status_as_of = str(row.get("bar_end") or status_as_of)
        if row.get("condition_resume_flag"):
            trading_status = "resumed"
            status_as_of = str(row.get("bar_end") or status_as_of)
    bars = list(chart.get("bars") or []) if isinstance(chart, dict) else []
    bar = bars[-1] if bars else {}
    return {
        "as_of": status_as_of,
        "trading_status": trading_status,
        "is_tradable": trading_status != "halted",
        "luld_active": bool(bar.get("estimated_luld_active")),
        "luld_state": str(bar.get("estimated_luld_state") or "unknown"),
        "luld_lower_price": float(bar.get("estimated_luld_lower_price") or 0),
        "luld_upper_price": float(bar.get("estimated_luld_upper_price") or 0),
        "luld_distance_to_lower_pct": float(bar.get("estimated_luld_distance_to_lower_pct") or 0),
        "luld_distance_to_upper_pct": float(bar.get("estimated_luld_distance_to_upper_pct") or 0),
        "source": "qmd-history-gateway",
    }


def historical_ticker_change(ticker: str, *, as_of: str) -> dict[str, Any]:
    """Compare the point-in-time trade price with the prior 04:00-20:00 ET session close."""
    resolved_ticker = _historical_ticker(ticker)
    resolved_as_of = datetime.fromisoformat(as_of)
    if resolved_as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    exchange_as_of = resolved_as_of.astimezone(ZoneInfo("America/New_York"))
    session_date = exchange_as_of.date()
    macro = historical_macro_bar_history(
        ticker=resolved_ticker,
        timeframe="1d",
        session_date=session_date,
        as_of=resolved_as_of.isoformat(),
    )
    prior_rows = [
        row for row in macro.get("history", [])
        if str(row.get("session_date") or "") < session_date.isoformat() and float(row.get("close") or 0) > 0
    ]
    previous = prior_rows[-1] if prior_rows else {}
    previous_close = float(previous.get("close") or 0)
    session_start = datetime.combine(session_date, time(4, 0), tzinfo=ZoneInfo("America/New_York"))
    session_end = datetime.combine(session_date, time(20, 0), tzinfo=ZoneInfo("America/New_York"))
    current_end = min(exchange_as_of, session_end)
    events = historical_compact_events(
        resolved_ticker,
        start=session_start.isoformat(),
        end=current_end.isoformat(),
        row_limit=5_000,
    ) if current_end > session_start else []
    current_price = _latest_compact_price(events)
    absolute_change = current_price - previous_close if current_price > 0 and previous_close > 0 else 0.0
    percent_change = absolute_change / previous_close * 100 if previous_close > 0 and current_price > 0 else 0.0
    return {
        "as_of": resolved_as_of.isoformat(),
        "current_price": current_price or None,
        "previous_close": previous_close or None,
        "previous_session_date": str(previous.get("session_date") or ""),
        "absolute_change": absolute_change if current_price > 0 and previous_close > 0 else None,
        "percent_change": percent_change if current_price > 0 and previous_close > 0 else None,
        "source": "qmd-history-gateway",
        "ticker": resolved_ticker,
    }


def _latest_compact_price(events: list[dict[str, Any]]) -> float:
    latest_quote_midpoint = 0.0
    for event in reversed(events):
        event_meta = int(event.get("event_meta") or 0)
        primary_scale = 10_000 if event_meta & 0x02 else 100
        if event_meta & 0x01:
            price = float(event.get("price_primary_int") or 0) / primary_scale
            if price > 0:
                return price
        if latest_quote_midpoint <= 0:
            secondary_scale = 10_000 if event_meta & 0x04 else 100
            ask = float(event.get("price_primary_int") or 0) / primary_scale
            bid = float(event.get("price_secondary_int") or 0) / secondary_scale
            if ask > 0 and bid > 0 and ask >= bid:
                latest_quote_midpoint = (ask + bid) / 2
    return latest_quote_midpoint


@lru_cache(maxsize=1)
def market_event_references() -> dict[str, dict[str, dict[str, Any]]]:
    exchanges = _reference_rows(MARKET_REFERENCE_DIR / "stock_exchanges.json")
    conditions = build_condition_token_rows(MARKET_REFERENCE_DIR)
    return {
        "exchanges": {
            str(row["dense_id"]): {
                "acronym": str(row.get("acronym") or ""),
                "mic": str(row.get("mic") or ""),
                "name": str(row.get("name") or "Unknown venue"),
                "participant_id": str(row.get("participant_id") or ""),
                "type": str(row.get("type") or ""),
            }
            for row in exchanges
            if isinstance(row.get("dense_id"), int) and row["dense_id"] > 0
        },
        "conditions": {
            str(row["token_id"]): {
                "name": str(row.get("condition") or "Unknown condition"),
                "sip_mapping": str(row.get("sip_mapping") or ""),
                "type": str(row.get("source_family") or ""),
                "update_high_low": bool(row.get("update_high_low")),
                "update_last": bool(row.get("update_last")),
                "update_volume": bool(row.get("update_volume")),
            }
            for row in conditions
            if isinstance(row.get("token_id"), int) and row["token_id"] > 0
        },
    }


def _reference_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(f"Market reference file must contain a results array: {path}")
    return [row for row in rows if isinstance(row, dict)]


def _historical_gateway_get(path: str, params: dict[str, Any], *, timeout: float) -> Any:
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
    url = f"{historical_gateway_base_url()}{path}?{query}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"QMD History returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"QMD History gateway is not reachable at {historical_gateway_base_url()}. "
            "Start scripts/run_qmd_history_gateway.ps1 and wait for its /health status to be ready."
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"QMD History request failed: {exc}") from exc


def _historical_ticker(value: str) -> str:
    ticker = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.\-]{1,15}", ticker):
        raise ValueError("ticker must contain 1-15 letters, numbers, dots, or hyphens")
    return ticker


def _historical_timeframe(value: str) -> str:
    timeframe = value.strip().lower()
    if timeframe not in SUPPORTED_HISTORICAL_TIMEFRAMES:
        raise ValueError(f"unsupported timeframe {value}")
    return timeframe


def _preflight_check(
    check_id: str,
    label: str,
    status: str,
    summary: str,
    evidence: str,
    *,
    required: bool,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "summary": summary,
        "evidence": evidence,
        "required": required,
    }
