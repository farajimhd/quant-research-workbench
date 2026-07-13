from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.trading_runtime.journal import TradingJournal


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
