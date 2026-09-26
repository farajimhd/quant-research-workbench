"""An empty simulated account remains a valid normalized Float64 snapshot."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from src.backend.backtest_terminal_snapshot_v2 import project_account_scalars
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter


def test_empty_account_summary_uses_float64_for_gross_value():
    broker = object.__new__(SimulatedBrokerAdapter)
    broker._cash = {"SIM": 100_000.0}
    broker.config = SimpleNamespace(base_currency="USD")
    summary = broker._summary_from_positions(
        "SIM", [], datetime(2026, 8, 18, tzinfo=timezone.utc))
    assert type(summary.grosspositionvalue) is float
    assert project_account_scalars(summary.to_cpapi())["gross_position_value"] == 0.0
