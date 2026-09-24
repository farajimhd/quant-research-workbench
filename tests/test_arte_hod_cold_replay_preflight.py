from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, ExecutionInterval, MarketDayUnit,
)
from src.trading_runtime.arte_hod_cold_replay import _hash
from src.trading_runtime.arte_hod_cold_replay_preflight import (
    preflight_hod_replay_pins, require_hod_readonly_access,
)


HASH = "a" * 64
SESSION = "2026-09-24"


def _evidence():
    units = tuple(MarketDayUnit("build", SESSION, "TEST", stage, attempt,
                                HASH, 1, HASH)
                  for stage, attempt in (("bars", "bars-1"),
                                         ("technical", "technical-1"),
                                         ("broker_100ms", "broker-1")))
    plan = CertifiedMarketDayPlan(
        execution_interval=ExecutionInterval.fixed(1000), build_id="build",
        definition_hash=HASH, sessions=(SESSION,), tickers=("TEST",),
        units=units, required_resolutions_ms=(1000, 5000), token=HASH,
    )
    manifest = dict(run_id="run", assignment_id="assignment", ticker="TEST",
                    session=SESSION, market_build_id="build",
                    market_source_plan_hash=HASH, market_coverage_hash=HASH,
                    bars_attempt_id="bars-1", indicators_attempt_id="technical-1",
                    liquidity_attempt_id="broker-1", seed_checkpoint_hash=HASH,
                    seed_source_plan_hash=HASH, seed_coverage_hash=HASH)
    from src.trading_runtime.arte_hod_cold_replay import MANIFEST_TABLE
    manifest = {**{key: None for key, _ in MANIFEST_TABLE.columns if key != "content_hash"},
                **manifest}
    manifest["content_hash"] = _hash(manifest)
    coverage = dict(ticker="TEST", session_date="2026-09-23", state="empty",
                    available_at="2026-09-23 20:00:00.000000000",
                    source_checkpoint_hash=HASH, source_plan_hash=HASH,
                    level_count=0, observation_count=0)
    return manifest, plan, coverage


def test_matching_one_ticker_session_source_pins_pass():
    manifest, plan, coverage = _evidence()
    preflight_hod_replay_pins(manifest, market_plan=plan,
                              seed_coverage=coverage, seed_plan_token=HASH)


@pytest.mark.parametrize("mode", ["attempt", "resolution", "scope", "seed", "late", "tamper"])
def test_missing_or_changed_source_authority_fails_closed(mode):
    manifest, plan, coverage = _evidence()
    if mode == "attempt":
        plan = replace(plan, units=(replace(plan.units[0], attempt_id="other"), *plan.units[1:]))
    elif mode == "resolution":
        plan = replace(plan, required_resolutions_ms=(1000,))
    elif mode == "scope":
        plan = replace(plan, tickers=("TEST", "OTHER"))
    elif mode == "seed":
        coverage["source_checkpoint_hash"] = "b" * 64
    elif mode == "late":
        coverage["available_at"] = datetime(2026, 9, 24, 20, tzinfo=timezone.utc).isoformat()
    else:
        manifest["bars_attempt_id"] = "other"
    with pytest.raises(ValueError):
        preflight_hod_replay_pins(manifest, market_plan=plan,
                                  seed_coverage=coverage, seed_plan_token=HASH)


def test_dedicated_readonly_credentials_and_ledger_are_required(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("BACKTEST_CLICKHOUSE_URL", raising=False)
    monkeypatch.delenv("BACKTEST_CLICKHOUSE_USER", raising=False)
    with pytest.raises(ValueError, match="dedicated"):
        require_hod_readonly_access(ledger_path=tmp_path / "ledger.sqlite3")
    monkeypatch.setenv("BACKTEST_CLICKHOUSE_URL", "http://127.0.0.1:8123")
    monkeypatch.setenv("BACKTEST_CLICKHOUSE_USER", "readonly")
    with pytest.raises(ValueError, match="ledger"):
        require_hod_readonly_access(ledger_path=tmp_path / "ledger.sqlite3")
