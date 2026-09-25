"""Policy selections resolve to existing typed catalogue rows without I/O."""
from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_policy_selection_v3 import project_policy_selection_v3
from src.trading_runtime.arte_portfolio_policy import _policy_rows
from src.trading_runtime.portfolio import PortfolioPolicy


def _record(policy_payload, **changes):
    payload = {"event": "portfolio_policy_selected", "policy": policy_payload,
               "entries_paused": True, "reason": "operator", **changes}
    journal = BacktestMemoryJournal(run_id="policy-test")
    return journal.append(run_id="policy-test", category="portfolio_management",
                          entity_type="portfolio_control", entity_id="primary",
                          account_id="DU1", payload=payload,
                          event_time=datetime(2026, 8, 18, tzinfo=timezone.utc))


def _payload():
    policy = PortfolioPolicy()
    return {**asdict(policy), "identity": policy.identity}


def test_selection_reuses_existing_normalized_catalogue_hash():
    record = _record(_payload())
    projected = project_policy_selection_v3(record, account_key="primary")
    assert projected.policy == PortfolioPolicy()
    assert projected.policy_hash == _policy_rows(PortfolioPolicy())[0]
    assert projected.reason == "operator"


@pytest.mark.parametrize("change", [
    {"entries_paused": False},
    {"unexpected": "opaque"},
    {"policy": {**_payload(), "identity": "wrong"}},
    {"policy": {key: value for key, value in _payload().items()
                if key != "allowed_currencies"}},
    {"policy": {**_payload(), "allowed_currencies": ("USD", "USD")}},
    {"policy": {**_payload(), "maximum_open_positions": True}},
    {"policy": {**_payload(), "maximum_order_notional": float("nan")}},
])
def test_selection_rejects_noncanonical_or_incomplete_payload(change):
    with pytest.raises((ValueError, TypeError)):
        project_policy_selection_v3(_record(_payload(), **change),
                                    account_key="primary")


def test_selection_requires_exact_account_identity():
    with pytest.raises(ValueError, match="identity"):
        project_policy_selection_v3(_record(_payload()), account_key="different")
