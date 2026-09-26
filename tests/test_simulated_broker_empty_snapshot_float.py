"""An empty simulated account remains a valid normalized Float64 snapshot."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import pytest

from src.backend.backtest_terminal_snapshot_v2 import project_account_scalars
from src.trading_runtime.simulated_broker import SimulationConfig, SimulatedBrokerAdapter


def test_empty_account_summary_uses_float64_for_gross_value():
    broker = object.__new__(SimulatedBrokerAdapter)
    broker._cash = {"SIM": 100_000.0}
    broker.config = SimpleNamespace(base_currency="USD")
    summary = broker._summary_from_positions(
        "SIM", [], datetime(2026, 8, 18, tzinfo=timezone.utc))
    assert type(summary.grosspositionvalue) is float
    assert project_account_scalars(summary.to_cpapi())["gross_position_value"] == 0.0


def test_integer_initial_cash_is_exact_float64_before_empty_snapshot():
    config = SimulationConfig(initial_cash=100_000)
    assert type(config.initial_cash) is float
    broker = object.__new__(SimulatedBrokerAdapter)
    broker._cash = {"SIM": config.initial_cash}
    broker.config = config
    summary = broker._summary_from_positions(
        "SIM", [], datetime(2026, 8, 18, tzinfo=timezone.utc))
    projected = project_account_scalars(summary.to_cpapi())
    assert projected["total_cash_value"] == 100_000.0
    assert type(projected["buying_power"]) is float


@pytest.mark.parametrize("cash", [True, float("nan"), float("inf"), 2**53 + 1])
def test_initial_cash_rejects_non_float64_evidence(cash):
    with pytest.raises(ValueError, match="initial_cash"):
        SimulationConfig(initial_cash=cash)
