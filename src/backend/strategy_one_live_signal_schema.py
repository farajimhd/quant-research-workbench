"""Isolated normalized signal families for immutable Strategy 1 live runs.

The staged shared signal tables have an INSERT grant for the general journal
principal. A Strategy 1 cold recovery must never infer exclusivity from their
currently empty row inventory: a delayed shared-principal INSERT could arrive
after the scan. These distinct physical tables retain the same typed scalar
contract, partition and order while a dedicated principal owns publication.
This module declares schema only; neither Backtest nor live code creates it.
"""
from __future__ import annotations

from src.backend.live_signal_journal_preflight import LIVE_SIGNAL_TABLES
from src.trading_runtime.arte_journal_schema import TableContract


LEGACY_TO_STRATEGY_ONE_SIGNAL = {
    table.name: f"trading_strategy_one_{table.name}"
    for table in LIVE_SIGNAL_TABLES
}

STRATEGY_ONE_SIGNAL_TABLES = tuple(
    TableContract(LEGACY_TO_STRATEGY_ONE_SIGNAL[table.name],
                  table.columns, "toYYYYMM(session_key)", table.order)
    for table in LIVE_SIGNAL_TABLES
)


def strategy_one_signal_table(logical_name: str) -> str:
    """Resolve one staged logical family to its Strategy 1-only relation."""
    try:
        return LEGACY_TO_STRATEGY_ONE_SIGNAL[logical_name]
    except KeyError as exc:
        raise ValueError("Unknown normalized live signal family") from exc
