"""Staged Strategy 1 evidence stays scalar and linked to a typed parent."""
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from src.trading_runtime.arte_strategy_one_entry_journal import (
    ENTRY_EVIDENCE, project_strategy_one_entry_evidence,
)
from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal


def _source():
    proposal = StrategyOneEntryProposal(
        "assignment-1", "DU1", "AAA", 31_000, 30_000, 10.01, 9.89,
        12., "R4", .5, 30_000, "S1",
    )
    session = date(2026, 8, 18)
    return proposal, strategy_one_entry_intent(proposal, session_date=session), session


def test_entry_evidence_contract_is_normalized_nonredundant_and_ordered():
    proposal, intent, session = _source()
    parent = str(uuid4())
    batch = str(uuid4())
    row = project_strategy_one_entry_evidence(
        proposal, intent, session_date=session, run_id="run-1",
        batch_id=batch, parent_record_id=parent)
    assert row == project_strategy_one_entry_evidence(
        proposal, intent, session_date=session, run_id="run-1",
        batch_id=batch, parent_record_id=parent)
    assert row["parent_record_id"] == parent
    assert row["event_month"] == "2026-08-01"
    assert row["frozen_gap"] == "0.500000000000000000"
    assert row["target_level_id"] == "R4"
    assert set(row) == {column for column, _ in ENTRY_EVIDENCE.columns} - {"content_hash"}
    assert not {"account_id", "ticker", "reference_ask", "initial_stop",
                "initial_target", "json", "payload"} & set(row)
    assert "live_market_ssd" in ENTRY_EVIDENCE.ddl()
    assert ENTRY_EVIDENCE.partition == "toYYYYMM(event_month)"


def test_entry_evidence_rejects_parent_intent_or_causal_mismatch():
    proposal, intent, session = _source()
    kwargs = dict(session_date=session, run_id="run-1", batch_id=str(uuid4()),
                  parent_record_id=str(uuid4()))
    with pytest.raises(ValueError, match="differs"):
        project_strategy_one_entry_evidence(
            proposal, replace(intent, profit_target_price=13.), **kwargs)
    with pytest.raises(ValueError, match="causal boundaries"):
        project_strategy_one_entry_evidence(
            replace(proposal, bos_break_boundary_ms=32_000),
            strategy_one_entry_intent(replace(proposal, bos_break_boundary_ms=32_000),
                                      session_date=session), **kwargs)
