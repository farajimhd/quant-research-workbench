from __future__ import annotations

import os
import json
import urllib.request
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.trading_runtime.journal import TradingJournal
from src.trading_runtime.orchestrator import historical_run_window
from src.trading_runtime.runtime import RunMode


REPO_ROOT = Path(__file__).resolve().parents[2]


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
