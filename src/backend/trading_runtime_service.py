from __future__ import annotations

import os
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.orchestrator import historical_run_window
from src.trading_runtime.runtime import RunMode


REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_HISTORICAL_TIMEFRAMES = {"1s", "10s", "30s", "1m", "5m", "1h"}
HISTORICAL_CHUNK_MINUTES = 15


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
        "bar_count": len(bars),
        "source": "qmd_history_gateway",
    }


def historical_latest_coverage() -> dict[str, Any]:
    payload = _historical_gateway_get("/coverage/latest", {}, timeout=15)
    if not isinstance(payload, dict):
        raise RuntimeError("QMD History latest coverage response must be an object")
    return payload


@lru_cache(maxsize=256)
def historical_bar_history_before(
    *,
    before: date,
    ticker: str,
    timeframe: str,
    row_limit: int = 20_000,
) -> dict[str, Any]:
    resolved_ticker = _historical_ticker(ticker)
    resolved_timeframe = _historical_timeframe(timeframe)
    coverage = _historical_gateway_get(
        "/coverage/latest",
        {"before": before.isoformat()},
        timeout=15,
    )
    session_date_text = str(coverage.get("session_date") or "") if isinstance(coverage, dict) else ""
    if not session_date_text:
        return {
            "ticker": resolved_ticker,
            "timeframe": resolved_timeframe,
            "history": [],
            "earliest_session_date": "",
            "has_more": False,
            "source": "qmd_history_gateway",
        }
    session_date = date.fromisoformat(session_date_text)
    window = historical_window_preview(
        mode=RunMode.REPLAY.value,
        anchor_date=session_date,
        session_count=1,
        replay_end_date=session_date,
    )
    snapshot = _historical_gateway_get(
        f"/snapshot/bars/{urllib.parse.quote(resolved_ticker)}",
        {
            "start": window["start"],
            "end": window["end"],
            "timeframe": resolved_timeframe,
            "limit": max(1, min(row_limit, 20_000)),
            "event_limit": 2_000_000,
        },
        timeout=90,
    )
    bars = list(snapshot.get("history") or []) if isinstance(snapshot, dict) else []
    if isinstance(snapshot, dict) and snapshot.get("current"):
        bars.append(dict(snapshot["current"]))
    bars.sort(key=lambda row: str(row.get("bar_start") or ""))
    return {
        "ticker": resolved_ticker,
        "timeframe": resolved_timeframe,
        "history": bars,
        "earliest_session_date": session_date_text if bars else "",
        "has_more": bool(bars),
        "source": "qmd_history_gateway",
    }


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


def _historical_gateway_get(path: str, params: dict[str, Any], *, timeout: float) -> Any:
    query = urllib.parse.urlencode(params)
    url = f"{historical_gateway_base_url()}{path}?{query}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"QMD History returned HTTP {exc.code}: {detail}") from exc
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
