from __future__ import annotations

import pytest
from types import SimpleNamespace

from src.backend.backtest_strategy_one_assignments import (
    certified_strategy_one_assignments,
)
from src.backend.backtest_strategy_one_identity import CertifiedIdentityPlan
from src.trading_runtime.strategy_one_contract import STRATEGY_ID
from src.trading_runtime.runtime import RunMode


IDENTITIES = CertifiedIdentityPlan(
    "a" * 64, "2026-08-18", "00000000-0000-0000-0000-000000000001",
    "b" * 64, ("AAA", "BBB"), (101, 202), "c" * 64, "d" * 64)
CONFIG = {"strategy": {
    "strategy_id": STRATEGY_ID, "strategy_number": 1, "revision": 1,
    "parameters": {"execution": {"tick_size": 0.01}},
}, "assignments": []}


def _build(configuration=CONFIG, *, candidates=("BBB", "AAA")):
    return certified_strategy_one_assignments(
        configuration, IDENTITIES, candidate_tickers=candidates,
        account_keys=("account-1",))


def test_assignments_are_complete_deterministic_and_certified():
    rows = _build()
    assert [(row["ticker"], row["conid"]) for row in rows] == [
        ("AAA", 101), ("BBB", 202)]
    assert all(row["source"] == "arte_dated_identity_v1" for row in rows)
    assert all(row["permissions"]["enter"] for row in rows)


@pytest.mark.parametrize("configuration,candidates", [
    ({**CONFIG, "strategy": {**CONFIG["strategy"], "revision": 2}}, ("AAA",)),
    ({**CONFIG, "strategy": {**CONFIG["strategy"], "parameters": {}}}, ("AAA",)),
    ({**CONFIG, "assignments": [{"account_key": "account-1", "ticker": "AAA",
                                "conid": 999}]}, ("AAA",)),
    ({**CONFIG, "assignments": [{"account_key": "account-1", "ticker": "AAA",
                                "status": "disabled"}]}, ("AAA",)),
    (CONFIG, ("MISSING",)),
])
def test_assignments_reject_foreign_missing_or_stale_identity(configuration, candidates):
    with pytest.raises(ValueError):
        _build(configuration, candidates=candidates)


def test_fixed_controller_never_calls_legacy_identity_lookup(monkeypatch):
    from src.backend.replay_run_service import ReplayRunController
    from src.backend import backtest_strategy_one_preparation as preparation

    monkeypatch.setattr(preparation, "strategy_one_v7_tickers",
                        lambda _prepared: ("AAA", "BBB"))
    config = {**CONFIG, "accounts": {"bindings": [
        {"account_key": "account-1", "enabled": True, "modes": ["backtest"]}]}}
    fake = SimpleNamespace(
        definition=SimpleNamespace(
            configuration_revision={"payload": config}, tickers=(),
            assignment_ids=(), mode=RunMode.BACKTEST,
            execution_interval="100ms"),
        _strategy_one_fixed_plans=SimpleNamespace(
            identities=IDENTITIES, candidates=SimpleNamespace(prepared=())),
        _v7_excluded_tickers=set(),
        _historical_watchlist_members=lambda: pytest.fail("legacy Watchlist called"),
        _historical_signal_assignment_identities=lambda: pytest.fail("QMD identity called"),
    )
    rows = ReplayRunController._selected_assignments(fake)
    assert [(row["ticker"], row["conid"]) for row in rows] == [
        ("AAA", 101), ("BBB", 202)]
