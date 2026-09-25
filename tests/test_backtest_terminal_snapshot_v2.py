from datetime import datetime, timezone
import math
import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.backend.backtest_terminal_snapshot_v2 import (
    assert_float64_readback, finite_float64, position_set_sha256,
    project_account_scalars, project_position_scalars,
    project_snapshot_group, recover_snapshot_group,
)
from src.trading_runtime.arte_journal_schema import (
    BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES,
    TABLES, backtest_terminal_snapshot_v2_ddl,
)
from src.trading_runtime.ibkr_schema import AccountSummary, PortfolioPosition
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.runtime import RunMode, TradingRuntime


def test_v2_contract_is_staged_normalized_and_preserves_existing_v1() -> None:
    tables = BACKTEST_TERMINAL_SNAPSHOT_V2_TABLES
    assert len(tables) == 2
    assert not {table.name for table in tables} & {table.name for table in TABLES}
    account, position = (dict(table.columns) for table in tables)
    assert account["expected_position_count"] == "UInt32"
    assert account["position_set_sha256"] == "FixedString(64)"
    assert account["snapshot_id"] == "UUID"
    assert position["parent_snapshot_id"] == "UUID"
    for name in ("net_liquidation", "total_cash_value", "buying_power",
                 "gross_position_value", "available_funds", "excess_liquidity"):
        assert account[name] == "Float64"
    for name in ("quantity", "market_price", "market_value", "average_cost",
                 "average_price", "realized_pnl", "unrealized_pnl"):
        assert position[name] == "Float64"
    for statement in backtest_terminal_snapshot_v2_ddl():
        assert "storage_policy = 'live_market_ssd'" in statement
        assert not any(word in statement.lower() for word in ("json", "blob", "map("))


def test_real_domain_payloads_project_exact_float64_and_population_hash() -> None:
    at = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    account = AccountSummary("DU1", 100.12345678901234, 90.0, 80.0,
                             10.12345678901234, 70.0, 60.0, timestamp=at)
    position = PortfolioPosition("DU1", 42, "ABCD", 0.12345678901234,
                                 81.23456789012345, 10.01234567890123,
                                 80.0, 80.0, 1.0, 0.5)
    account_row = project_account_scalars(account.to_cpapi())
    position_row = project_position_scalars(position.to_cpapi(), account_id="DU1")
    assert account_row["net_liquidation"] == account.netliquidation
    assert account_row["source_timestamp_ms"] == int(at.timestamp() * 1000)
    assert position_row["quantity"] == position.position
    assert position_row["market_price"] == position.mktPrice
    digest = position_set_sha256((position_row,))
    assert len(digest) == 64
    assert digest == position_set_sha256((position_row,))
    assert digest != position_set_sha256(())
    assert_float64_readback(position.position, position_row["quantity"])


def test_numeric_and_population_contract_fail_closed() -> None:
    for value in (float("nan"), float("inf"), -float("inf"), 1, True, "1"):
        with pytest.raises(ValueError, match="finite Float64"):
            finite_float64(value)
    with pytest.raises(ValueError, match="source bits"):
        assert_float64_readback(-0.0, 0.0)
    position = PortfolioPosition("DU1", 42, "ABCD", 1.0, 2.0, 2.0,
                                 1.0, 1.0, 0.0, 1.0)
    payload = position.to_cpapi()
    payload["unexpected"] = "not persisted"
    with pytest.raises(ValueError, match="missing, extra"):
        project_position_scalars(payload, account_id="DU1")
    payload.pop("unexpected")
    row = project_position_scalars(payload, account_id="DU1")
    with pytest.raises(ValueError, match="repeats a conid"):
        position_set_sha256((row, row))
    changed = dict(row, unrealized_pnl=math.nextafter(1.0, 2.0))
    assert position_set_sha256((row,)) != position_set_sha256((changed,))


def test_fixed_runtime_emits_atomic_account_manifest_and_bound_positions() -> None:
    run_id = "00000000-0000-0000-0000-000000000a91"
    at = datetime(2026, 8, 18, 14, 0, tzinfo=timezone.utc)
    summary = AccountSummary("DU1", 101.0, 90.0, 90.0, 11.0, 80.0, 80.0,
                             timestamp=at)
    positions = [PortfolioPosition("DU1", 42, "ABCD", 1.25, 8.8, 11.0,
                                   8.0, 8.0, 0.0, 1.0)]

    class Broker:
        async def account_summary(self, account_id):
            assert account_id == "DU1"
            return summary

        async def positions(self, account_id):
            assert account_id == "DU1"
            return positions

    runtime = object.__new__(TradingRuntime)
    runtime.run_id = run_id
    runtime.last_event_time = at
    runtime.config = SimpleNamespace(mode=RunMode.BACKTEST, account_ids=("DU1",))
    runtime.broker = Broker()
    runtime.journal = BacktestMemoryJournal(run_id=run_id)
    asyncio.run(runtime.snapshot_portfolios())
    records = runtime.journal.unfenced_records()
    assert [(row.category, row.entity_type) for row in records] == [
        ("snapshot", "portfolio"), ("snapshot", "position")]
    manifest, child = records
    snapshot_id = manifest.payload["snapshot_id"]
    UUID(snapshot_id)
    assert manifest.payload["expected_position_count"] == 1
    assert child.payload["parent_snapshot_id"] == snapshot_id
    assert child.payload["ordinal"] == 0
    assert manifest.payload["position_set_sha256"] == position_set_sha256((
        project_position_scalars(positions[0].to_cpapi(), account_id="DU1"),))
    assert manifest.payload["netliquidation"] == summary.to_cpapi()["netliquidation"]
    batch_id = "00000000-0000-0000-0000-000000000a92"
    parent, children = project_snapshot_group(manifest, (child,), batch_id=batch_id)
    assert parent["snapshot_id"] == snapshot_id
    assert children[0]["parent_snapshot_id"] == snapshot_id
    recovered_account, recovered_positions = recover_snapshot_group(parent, children)
    assert recovered_account == summary.to_cpapi()
    assert recovered_positions == (positions[0].to_cpapi(),)
    with pytest.raises(ValueError, match="incomplete"):
        project_snapshot_group(manifest, (), batch_id=batch_id)
    with pytest.raises(ValueError, match="hash differs"):
        recover_snapshot_group(parent, (dict(children[0], quantity=2.0),))
