"""Strategy 13 manager images reference the committed momentum entry authority."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner, StrategyOneManagementState
from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix, publish_strategy_one_entry_batch_v4
from src.trading_runtime.strategy_one_management_snapshot import attach_committed_momentum_sources
from tests.test_arte_rising_momentum_entry_v4 import BitClient, unit
from tests.test_arte_journal_commit_v4 import attached_v4_client


def source():
    proposal, batch = unit()
    client = attached_v4_client(BitClient())
    publish_strategy_one_entry_batch_v4(client, batch.base,
        entry_evidence=batch.entry_evidence, momentum_evidence=batch.momentum_evidence)
    prefix = load_verified_v4_prefix(client, batch.base.run_id)
    key = (proposal.account_id, proposal.assignment_id, proposal.ticker)
    reference = replace(proposal, momentum=None)
    state = StrategyOneManagementState(32_000, ((key, reference),), (), ())
    return client, prefix, state, proposal


def manager():
    runtime = SimpleNamespace(config=SimpleNamespace(strategy_revision=13),
        submit_strategy_one_proposal=AsyncMock(), submit_strategy_one_add=AsyncMock(),
        submit_strategy_one_protection=AsyncMock())
    return StrategyOneManagementRunner(runtime=runtime,
        evidence=SimpleNamespace(management_evidence=AsyncMock()), tick_for_ticker=lambda _: .01)


def test_cold_source_join_recovers_exact_witness_and_runnable_manager():
    client, prefix, reference, proposal = source()
    restored = attach_committed_momentum_sources(client, prefix, reference)
    assert restored.submitted[0][1] == proposal
    with pytest.raises(ValueError):
        manager().restore_state(reference)
    recovered = manager()
    recovered.restore_state(restored)
    assert recovered.capture_state(boundary_ms=32_000).submitted[0][1].momentum == proposal.momentum


def test_snapshot_reference_cannot_change_original_source_prices():
    client, prefix, state, _ = source()
    key, proposal = state.submitted[0]
    state = replace(state, submitted=((key, replace(proposal, reference_ask=10.02)),))
    with pytest.raises(RuntimeError, match='differs from snapshot reference'):
        attach_committed_momentum_sources(client, prefix, state)


def test_missing_original_entry_cannot_be_reconstructed_from_snapshot():
    client, prefix, state, _ = source()
    key, proposal = state.submitted[0]
    state = replace(state, submitted=((key, replace(proposal, boundary_ms=31_100)),))
    with pytest.raises(RuntimeError, match='missing or exceeds'):
        attach_committed_momentum_sources(client, prefix, state)
