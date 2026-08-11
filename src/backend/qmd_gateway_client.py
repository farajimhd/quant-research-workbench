from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QMD_BASE_URL = "http://127.0.0.1:8795"
ENRICHED_QMD_TIMEFRAMES = frozenset({"100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h"})
MACRO_QMD_TIMEFRAMES = frozenset({"1d", "1w", "1mo", "1y"})


def load_qmd_env() -> None:
    for env_path in (Path.cwd() / ".env", REPO_ROOT / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)
    load_dotenv(override=False)


def qmd_base_url() -> str:
    load_qmd_env()
    return os.environ.get("REAL_LIVE_QMD_GATEWAY_URL") or os.environ.get("QMD_GATEWAY_URL") or DEFAULT_QMD_BASE_URL


def qmd_enabled() -> bool:
    load_qmd_env()
    return os.environ.get("REAL_LIVE_QMD_GATEWAY_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def qmd_get_json(path: str, params: dict[str, Any] | None = None, *, timeout: int = 3) -> Any:
    if not qmd_enabled():
        raise RuntimeError("QMD gateway is disabled by REAL_LIVE_QMD_GATEWAY_ENABLED.")
    query = urllib.parse.urlencode({key: value for key, value in (params or {}).items() if value is not None})
    url = f"{qmd_base_url().rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"QMD GET {safe_qmd_url(url)} failed with HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"QMD GET {safe_qmd_url(url)} failed: {exc.reason}") from exc
    return json.loads(text) if text.strip() else {}


def qmd_websocket_url(path: str, params: dict[str, Any] | None = None) -> str:
    if not qmd_enabled():
        raise RuntimeError("QMD gateway is disabled by REAL_LIVE_QMD_GATEWAY_ENABLED.")
    parsed = urllib.parse.urlsplit(qmd_base_url().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("QMD gateway URL must use http or https.")
    query = urllib.parse.urlencode({key: value for key, value in (params or {}).items() if value is not None})
    target_path = f"{parsed.path.rstrip('/')}/{path.lstrip('/')}"
    return urllib.parse.urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, target_path, query, ""))


def qmd_status() -> dict[str, Any]:
    payload = qmd_get_json("/health", timeout=2)
    if not isinstance(payload, dict):
        raise RuntimeError("QMD health response was not an object.")
    payload.setdefault("base_url", qmd_base_url().rstrip("/"))
    payload.setdefault("provider", "qmd-gateway")
    return payload


def qmd_service_status() -> dict[str, Any]:
    """Return QMD's standardized Service Core snapshot without altering health consumers."""
    payload = qmd_get_json("/snapshot/status", timeout=2)
    if not isinstance(payload, dict):
        raise RuntimeError("QMD Service Core status response was not an object.")
    return payload


def qmd_live_market_state(ticker: str) -> dict[str, Any]:
    payload = qmd_get_json(
        f"/snapshot/live-market-state/{urllib.parse.quote(ticker.strip().upper())}",
        timeout=3,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("QMD live market-state response was not an object.")
    return payload


def qmd_scanner_snapshot(row_limit: int = 250) -> dict[str, Any]:
    cross_section_limit = 5_000
    with ThreadPoolExecutor(max_workers=4) as executor:
        scanner_future = executor.submit(
            qmd_get_json, "/snapshot/scanner", {"limit": row_limit}, timeout=3
        )
        active_signal_future = executor.submit(
            qmd_get_json, "/snapshot/signals", {"limit": cross_section_limit}, timeout=3
        )
        signal_event_future = executor.submit(
            qmd_get_json, "/snapshot/signal-events", {"limit": row_limit}, timeout=3
        )
        indicator_future = executor.submit(
            qmd_get_json,
            "/snapshot/scanner-indicators",
            {"limit": cross_section_limit, "timeframe": "10s"},
            timeout=3,
        )
        snapshot_payload = scanner_future.result()
        active_signal_payload = active_signal_future.result()
        signal_event_payload = signal_event_future.result()
        indicator_payload = indicator_future.result()
    snapshot_rows = snapshot_payload.get("rows", []) if isinstance(snapshot_payload, dict) else []
    rows = [normalize_qmd_symbol_snapshot(row) for row in snapshot_rows if isinstance(row, dict)]
    active_rows = [
        normalize_qmd_market_signal(row)
        for row in (
            active_signal_payload.get("rows", [])
            if isinstance(active_signal_payload, dict)
            else []
        )
        if isinstance(row, dict)
    ]
    strongest_by_ticker: dict[str, dict[str, Any]] = {}
    for signal in active_rows:
        ticker = str(signal.get("ticker") or "")
        current = strongest_by_ticker.get(ticker)
        signal_rank = (
            float_value(signal.get("signal_rank_score")),
            float_value(signal.get("signal_confidence")),
        )
        current_rank = (
            float_value(current.get("signal_rank_score")) if current else -1.0,
            float_value(current.get("signal_confidence")) if current else -1.0,
        )
        if current is None or signal_rank > current_rank:
            strongest_by_ticker[ticker] = signal
    active_counts: dict[str, int] = {}
    for signal in active_rows:
        ticker = str(signal.get("ticker") or "")
        active_counts[ticker] = active_counts.get(ticker, 0) + 1
    indicator_by_ticker = {
        str(row.get("sym") or "").strip().upper(): normalize_qmd_indicator_scanner_row(row)
        for row in (
            indicator_payload.get("rows", [])
            if isinstance(indicator_payload, dict)
            else []
        )
        if isinstance(row, dict) and str(row.get("sym") or "").strip()
    }
    rows = [
        {
            **row,
            **indicator_by_ticker.get(str(row.get("ticker") or ""), {}),
            **strongest_by_ticker.get(str(row.get("ticker") or ""), {}),
            "active_signal_count": active_counts.get(str(row.get("ticker") or ""), 0),
            "signal_rank_score": float_value(
                strongest_by_ticker.get(str(row.get("ticker") or ""), {}).get(
                    "signal_rank_score"
                )
            ),
        }
        for row in rows
    ]
    payload = qmd_scanner_payload(
        rows,
        snapshot_payload if isinstance(snapshot_payload, dict) else {},
        row_limit,
        source="scanner",
    )
    payload["signal_rows"] = [
        normalize_qmd_market_signal(row)
        for row in (
            signal_event_payload.get("rows", [])
            if isinstance(signal_event_payload, dict)
            else []
        )
        if isinstance(row, dict)
    ]
    payload["signal_row_count"] = len(payload["signal_rows"])
    return payload


def qmd_market_signals(
    symbol: str,
    *,
    include_history: bool = False,
    row_limit: int = 250,
) -> dict[str, Any]:
    ticker = symbol.strip().upper()
    if not ticker:
        raise ValueError("symbol is required for QMD market signals.")
    path = "/snapshot/signal-events" if include_history else "/snapshot/signals"
    payload = qmd_get_json(path, {"limit": row_limit}, timeout=3)
    source_rows = payload.get("rows", []) if isinstance(payload, dict) else []
    rows = [
        normalize_qmd_market_signal(row)
        for row in source_rows
        if isinstance(row, dict) and str(row.get("ticker") or "").strip().upper() == ticker
    ]
    return {
        "as_of": payload.get("as_of") if isinstance(payload, dict) else None,
        "mode": "lifecycle_history" if include_history else "active",
        "row_count": len(rows),
        "rows": rows,
        "source": "qmd-gateway",
        "ticker": ticker,
    }


def qmd_bars(symbol: str, *, timeframe: str = "1m", row_limit: int = 500) -> dict[str, Any]:
    if not symbol.strip():
        raise ValueError("symbol is required for QMD bars.")
    payload = qmd_get_json(f"/snapshot/bars/{urllib.parse.quote(symbol.strip().upper())}", {"timeframe": timeframe, "limit": row_limit}, timeout=3)
    return payload if isinstance(payload, dict) else {"ticker": symbol.upper(), "timeframe": timeframe, "history": [], "current": None}


def qmd_compact_events(symbol: str, *, row_limit: int = 250) -> list[dict[str, Any]]:
    """Return the live canonical compact-event buffer without changing its wire semantics."""
    ticker = symbol.strip().upper()
    if not ticker:
        raise ValueError("symbol is required for QMD compact events.")
    payload = qmd_get_json(
        f"/snapshot/compact-events/{urllib.parse.quote(ticker)}",
        {"limit": row_limit},
        timeout=3,
    )
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def qmd_chart_bars(symbol: str, *, timeframe: str = "1m", row_limit: int = 500) -> dict[str, Any]:
    if timeframe in MACRO_QMD_TIMEFRAMES:
        return qmd_macro_bars(symbol, timeframe=timeframe, row_limit=row_limit)
    if timeframe in ENRICHED_QMD_TIMEFRAMES:
        return qmd_bars(symbol, timeframe=timeframe, row_limit=row_limit)
    if not symbol.strip():
        raise ValueError("symbol is required for QMD chart bars.")
    payload = qmd_get_json(
        f"/snapshot/family-bars/{urllib.parse.quote(symbol.strip().upper())}",
        {"family": "trade", "limit": row_limit, "price_only": True, "resolution": timeframe},
        timeout=3,
    )
    return normalize_qmd_family_bar_snapshot(payload, symbol=symbol, timeframe=timeframe)


def qmd_macro_bars(symbol: str, *, timeframe: str, row_limit: int = 500) -> dict[str, Any]:
    if not symbol.strip():
        raise ValueError("symbol is required for QMD macro bars.")
    payload = qmd_get_json(
        f"/snapshot/macro-bars/{urllib.parse.quote(symbol.strip().upper())}",
        {"limit": row_limit, "timeframe": timeframe},
        timeout=3,
    )
    return normalize_qmd_macro_bar_snapshot(payload, symbol=symbol, timeframe=timeframe)


def normalize_qmd_macro_bar_snapshot(payload: Any, *, symbol: str, timeframe: str) -> dict[str, Any]:
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    normalized = [
        normalize_qmd_macro_bar(row, timeframe=timeframe)
        for row in rows
        if isinstance(row, dict) and is_qmd_trade_price_bar(row)
    ]
    normalized.sort(key=lambda row: str(row.get("bar_start") or ""))
    current = normalized[-1] if normalized and not normalized[-1]["is_closed"] else None
    return {
        "ticker": symbol.strip().upper(),
        "timeframe": timeframe,
        "history": normalized[:-1] if current is not None else normalized,
        "current": current,
    }


def normalize_qmd_macro_bar(row: dict[str, Any], *, timeframe: str) -> dict[str, Any]:
    return {
        "schema_version": row.get("schema_version"),
        "session_date": row.get("session_date"),
        "timeframe": timeframe,
        "sym": str(row.get("ticker") or "").upper(),
        "bar_start": row.get("bar_start"),
        "bar_end": row.get("bar_end"),
        "is_closed": row.get("state") != "partial",
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "close": row.get("close"),
        "volume": row.get("size_sum"),
        "vwap": None,
    }


def normalize_qmd_family_bar_snapshot(payload: Any, *, symbol: str, timeframe: str) -> dict[str, Any]:
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    normalized = [
        normalize_qmd_family_bar(row, timeframe=timeframe)
        for row in rows
        if isinstance(row, dict) and is_qmd_trade_price_bar(row)
    ]
    normalized.sort(key=lambda row: row["bar_start"])
    current = normalized[-1] if normalized and not normalized[-1]["is_closed"] else None
    history = normalized[:-1] if current is not None else normalized
    return {
        "ticker": symbol.strip().upper(),
        "timeframe": timeframe,
        "history": history,
        "current": current,
    }


def normalize_qmd_family_bar(row: dict[str, Any], *, timeframe: str) -> dict[str, Any]:
    return {
        "schema_version": row.get("schema_version"),
        "session_date": row.get("local_date"),
        "timeframe": timeframe,
        "sym": str(row.get("ticker") or "").upper(),
        "bar_start": row.get("bar_start"),
        "bar_end": row.get("bar_end"),
        "is_closed": row.get("state") != "partial",
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "close": row.get("close"),
        "volume": row.get("size_sum"),
        "vwap": None,
    }


def is_qmd_trade_price_bar(row: dict[str, Any]) -> bool:
    if row.get("bar_family") != "trade":
        return False
    try:
        open_price, high, low, close = (
            float(row[field]) for field in ("open", "high", "low", "close")
        )
    except (KeyError, TypeError, ValueError):
        return False
    return (
        all(math.isfinite(value) and value > 0 for value in (open_price, high, low, close))
        and high >= max(open_price, close)
        and low <= min(open_price, close)
        and high >= low
    )


def qmd_indicators(symbol: str, *, timeframe: str = "1m", row_limit: int = 500) -> dict[str, Any]:
    if not symbol.strip():
        raise ValueError("symbol is required for QMD indicators.")
    payload = qmd_get_json(f"/snapshot/indicators/{urllib.parse.quote(symbol.strip().upper())}", {"timeframe": timeframe, "limit": row_limit}, timeout=3)
    return payload if isinstance(payload, dict) else {"ticker": symbol.upper(), "timeframe": timeframe, "history": [], "current": None, "tick": None}


def qmd_catalogs() -> dict[str, Any]:
    indicators = qmd_get_json("/indicator-catalog", timeout=3)
    signals = qmd_get_json("/signal-catalog", timeout=3)
    return {
        "indicator_catalog": indicators if isinstance(indicators, list) else [],
        "signal_catalog": signals if isinstance(signals, list) else [],
        "provider": "qmd-gateway",
    }


def qmd_scanner_payload(rows: list[dict[str, Any]], raw_payload: dict[str, Any], row_limit: int, *, source: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    rows = rows[: max(1, min(int(row_limit or 250), 5000))]
    return {
        "provider": "qmd-gateway",
        "source": source,
        "session_date": now.date().isoformat(),
        "market_time": now.strftime("%H:%M"),
        "rows": rows,
        "row_count": len(rows),
        "market_rows": rows,
        "market_row_count": len(rows),
        "status": {
            "as_of": raw_payload.get("as_of"),
            "base_url": qmd_base_url().rstrip("/"),
            "total_symbols": raw_payload.get("total_symbols"),
            "source": source,
        },
    }


def normalize_qmd_symbol_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    last_price = float_value(row.get("last_price"))
    bid = float_value(row.get("bid"))
    ask = float_value(row.get("ask"))
    spread = float_value(row.get("spread"))
    spread_bps = spread / last_price * 10_000 if spread > 0 and last_price > 0 else 0.0
    trade_rate_10s = float_value(row.get("trade_rate_10s"))
    trade_rate_60s = float_value(row.get("trade_rate_60s"))
    day_dollar_volume = float_value(row.get("day_dollar_volume"))
    return {
        "ticker": str(row.get("ticker") or "").upper(),
        "bar_time_market": str(row.get("last_event_ts") or ""),
        "current_open": last_price,
        "last_close": last_price,
        "bid": bid or None,
        "ask": ask or None,
        "spread_bps_abs": spread_bps or None,
        "last_day_volume_so_far": float_value(row.get("day_volume")),
        "last_day_dollar_volume_so_far": day_dollar_volume,
        "last_transactions": int(float_value(row.get("day_trade_count"))),
        "trade_rate_10s": trade_rate_10s,
        "trade_rate_60s": trade_rate_60s,
        "trade_accel_10s_60s": trade_rate_10s - trade_rate_60s,
        "provider": "qmd-gateway",
        "live_priority": day_dollar_volume / 1_000_000 + trade_rate_10s * 100,
    }


def normalize_qmd_scanner_primitive(row: dict[str, Any]) -> dict[str, Any]:
    close = float_value(row.get("close"))
    score = float_value(row.get("score"))
    return {
        "ticker": str(row.get("ticker") or "").upper(),
        "bar_time_market": str(row.get("detected_at") or ""),
        "timeframe": str(row.get("timeframe") or ""),
        "current_open": close,
        "last_close": close,
        "last_vwap": float_value(row.get("vwap")),
        "spread_bps_abs": optional_float(row.get("spread_bps")),
        "scanner_score": score,
        "signal_type": str(row.get("primitive_key") or ""),
        "market_state": str(row.get("side_bias") or ""),
        "live_reasons": str(row.get("trigger_reason") or ""),
        "live_risks": str(row.get("reject_reason") or ""),
        "last_day_volume_so_far": float_value(row.get("volume")),
        "last_day_dollar_volume_so_far": float_value(row.get("dollar_volume")),
        "trade_rate_10s": float_value(row.get("trade_rate")),
        "quote_rate_10s": float_value(row.get("quote_rate")),
        "tape_imbalance": float_value(row.get("tape_imbalance")),
        "liquidity_score": float_value(row.get("liquidity_score")),
        "provider": "qmd-gateway",
        "live_priority": score,
    }


def normalize_qmd_market_signal(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
    clock = row.get("clock") if isinstance(row.get("clock"), dict) else {}
    score = float_value(row.get("score"))
    rank_score = (
        float_value(row.get("rank_score"))
        if row.get("rank_score") is not None
        else abs(score)
    )
    confidence = float_value(row.get("confidence"))
    return {
        "ticker": str(row.get("ticker") or "").upper(),
        "bar_time_market": str(row.get("effective_at") or ""),
        "event_time": str(row.get("effective_at") or ""),
        "timeframe": str(row.get("working_timeframe") or ""),
        "working_timeframe": str(row.get("working_timeframe") or ""),
        "confirmation_timeframe": row.get("confirmation_timeframe"),
        "current_open": float_value(evidence.get("close")),
        "last_close": float_value(evidence.get("close")),
        "last_vwap": float_value(evidence.get("vwap")),
        "spread_bps_abs": optional_float(evidence.get("spread_bps")),
        "signal_id": str(row.get("signal_id") or ""),
        "signal_event_id": str(row.get("event_id") or ""),
        "signal_version": int(row.get("signal_version") or 1),
        "signal_type": str(row.get("signal_key") or ""),
        "signal_domain": str(row.get("domain") or "market"),
        "signal_producer": str(row.get("producer") or "qmd"),
        "signal_state": str(row.get("state") or ""),
        "direction": str(row.get("direction") or "neutral"),
        "market_state": str(row.get("direction") or "neutral"),
        "signal_score": score,
        "signal_rank_score": rank_score,
        "signal_confidence": confidence,
        "scanner_score": rank_score,
        "live_reasons": str(row.get("trigger_reason") or ""),
        "live_risks": str(row.get("resolution_reason") or ""),
        "evidence": str(row.get("trigger_reason") or ""),
        "source": "QMD market signal",
        "last_day_volume_so_far": float_value(evidence.get("volume")),
        "last_day_dollar_volume_so_far": float_value(evidence.get("dollar_volume")),
        "trade_rate_10s": float_value(evidence.get("trade_rate")),
        "quote_rate_10s": float_value(evidence.get("quote_rate")),
        "tape_imbalance": float_value(evidence.get("tape_imbalance")),
        "liquidity_score": float_value(evidence.get("liquidity_score")),
        "provider": "qmd-gateway",
        "live_priority": rank_score,
        "input_basis": str(clock.get("input_basis") or "bar_derived"),
        "calculation_window": str(clock.get("calculation_window") or row.get("working_timeframe") or ""),
        "evaluation_mode": str(clock.get("evaluation_mode") or "closed_only"),
        "update_trigger": str(clock.get("update_trigger") or "bar_close"),
        "publication_cadence": str(clock.get("publication_cadence") or "bar_close"),
        "publication_interval_ms": clock.get("publication_interval_ms"),
    }


def normalize_qmd_indicator_scanner_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    payload.pop("qmd_structure_active_levels", None)
    payload["ticker"] = str(row.get("sym") or "").strip().upper()
    payload["indicator_timeframe"] = str(row.get("timeframe") or "")
    payload["indicator_as_of"] = str(row.get("bar_end") or "")
    payload["indicator_type"] = "qmd"
    payload["indicator_producer"] = "qmd"
    payload["indicator_input_basis"] = "event_native"
    payload["indicator_calculation_window"] = str(row.get("timeframe") or "")
    payload["indicator_evaluation_mode"] = "closed_only"
    payload["indicator_update_trigger"] = "bar_close"
    payload["indicator_publication_cadence"] = "bar_close"
    payload["indicator_publication_interval_ms"] = None
    return payload


def optional_float(value: Any) -> float | None:
    number = float_value(value)
    return number if number else None


def float_value(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number else 0.0


def safe_qmd_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
