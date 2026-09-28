"""Manager recovery is scalar, sealed, and lossless across all owned families."""
from dataclasses import replace
from datetime import date

import pytest

from src.backend.backtest_strategy_one_management import StrategyOneManagementState
from src.trading_runtime.strategy_one_management_snapshot import (
    TABLES, project_manager_snapshot, restore_manager_snapshot,
)
from src.trading_runtime.strategy_one_position import ProtectionState, ResistanceBreak
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal


KEY = ("DU1", "A1", "AAA")


def _rows():
    proposal = StrategyOneEntryProposal(
        "A1", "DU1", "AAA", 30_100, 30_000, 10.01, 9.69,
        10.3, "R3", .5, 30_000, "S1")
    state = StrategyOneManagementState(
        31_000, ((KEY, proposal),),
        ((KEY, ProtectionState(30_100, 9.69, 10.3)),),
        ((KEY, (ResistanceBreak(31_000, {
            "unified_level_id": "B1", "lower": 9.79, "upper": 9.81,
            "role": "resistance", "side": "resistance"}),)),))
    return project_manager_snapshot(
        run_id="backtest:one", session_date=date(2026, 8, 18),
        checkpoint_sequence=42, state=state)


def test_manager_snapshot_roundtrips_all_owned_state_without_json_columns():
    rows = _rows()
    state = restore_manager_snapshot(rows)
    assert state.submitted[0][1].target_level_id == "R3"
    assert state.positions[0][1].stop == 9.69
    assert state.pending_breaks[0][1][0].level["unified_level_id"] == "B1"
    assert rows.snapshot["source_count"] == 1
    assert rows.snapshot["pending_break_count"] == 1
    assert rows.snapshot["protection_hash"] == rows.protection.snapshot["content_hash"]
    assert all("live_market_ssd" in table.ddl() for table in TABLES)
    assert all("json" not in name and "blob" not in name
               for table in TABLES for name, _ in table.columns)


def test_manager_snapshot_rejects_missing_or_modified_children():
    rows = _rows()
    with pytest.raises(ValueError, match="seal differs"):
        restore_manager_snapshot(replace(rows, sources=()))
    with pytest.raises(ValueError, match="break children differ"):
        restore_manager_snapshot(replace(
            rows, pending_breaks=({**rows.pending_breaks[0], "lower": "1.0"},)))
    with pytest.raises(ValueError, match="seal differs"):
        restore_manager_snapshot(replace(
            rows, protection=replace(rows.protection, snapshot={
                **rows.protection.snapshot, "content_hash": "0" * 64})))


def test_empty_manager_snapshot_seals_no_source_or_position():
    rows = project_manager_snapshot(
        run_id="backtest:one", session_date=date(2026, 8, 18),
        checkpoint_sequence=42,
        state=StrategyOneManagementState(31_000, (), (), ()))
    assert restore_manager_snapshot(rows) == StrategyOneManagementState(
        31_000, (), (), ())
