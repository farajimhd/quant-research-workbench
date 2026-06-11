from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QMD_BASE_URL = "http://127.0.0.1:8795"


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


def qmd_status() -> dict[str, Any]:
    payload = qmd_get_json("/health", timeout=2)
    if not isinstance(payload, dict):
        raise RuntimeError("QMD health response was not an object.")
    payload.setdefault("base_url", qmd_base_url().rstrip("/"))
    payload.setdefault("provider", "qmd-gateway")
    return payload


def qmd_scanner_snapshot(row_limit: int = 250) -> dict[str, Any]:
    primitive_payload = qmd_get_json("/snapshot/scanner-primitives", {"limit": row_limit}, timeout=3)
    primitive_rows = primitive_payload.get("rows", []) if isinstance(primitive_payload, dict) else []
    if primitive_rows:
        rows = [normalize_qmd_scanner_primitive(row) for row in primitive_rows if isinstance(row, dict)]
        return qmd_scanner_payload(rows, primitive_payload, row_limit, source="scanner-primitives")

    snapshot_payload = qmd_get_json("/snapshot/scanner", {"limit": row_limit}, timeout=3)
    snapshot_rows = snapshot_payload.get("rows", []) if isinstance(snapshot_payload, dict) else []
    rows = [normalize_qmd_symbol_snapshot(row) for row in snapshot_rows if isinstance(row, dict)]
    return qmd_scanner_payload(rows, snapshot_payload if isinstance(snapshot_payload, dict) else {}, row_limit, source="scanner")


def qmd_bars(symbol: str, *, timeframe: str = "1m", row_limit: int = 500) -> dict[str, Any]:
    if not symbol.strip():
        raise ValueError("symbol is required for QMD bars.")
    payload = qmd_get_json(f"/snapshot/bars/{urllib.parse.quote(symbol.strip().upper())}", {"timeframe": timeframe, "limit": row_limit}, timeout=3)
    return payload if isinstance(payload, dict) else {"ticker": symbol.upper(), "timeframe": timeframe, "history": [], "current": None}


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
    now = datetime.utcnow()
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
