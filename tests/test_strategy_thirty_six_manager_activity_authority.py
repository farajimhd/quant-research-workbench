"""Prepared manager evidence must independently admit the original entry."""
from dataclasses import replace
from datetime import date
from uuid import UUID

import pytest

from test_arte_entry_activity_v4 import plan
from test_strategy_twenty_entry_recovery import prepared_entry
from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
from src.backend.backtest_strategy_entry_activity_source import (
    EntryActivityReadbackAuthority, certified_entry_activity_witness,
)
from src.trading_runtime.numbered_fixed_strategy import NumberedFixedStrategyContract


def entry_case():
    prepared = plan()
    activity = EntryActivityReadbackAuthority('manager-activity', prepared)
    source = CertifiedPriceReadbackAuthority(activity.run_id, prepared.parent, activity)
    _, proposal, _ = prepared_entry(source, 1, 31000, str(UUID(int=0)), strategy_number=36)
    return source, proposal


def test_manager_uses_cached_completed_counts_for_original_entry():
    source, proposal = entry_case()
    witness = certified_entry_activity_witness(source, proposal, session_date=date(2026, 8, 18))
    assert witness == source.entry_activity_source.plan.witness('AAA', 31000)
    assert [bar.trade_count for bar in witness.candles] == [100] * 4


@pytest.mark.parametrize('mode', ['normal', 'fade', 'missing'])
def test_actual_snapshot_publication_rechecks_entry_activity(mode):
    from src.backend.backtest_strategy_one_management import StrategyOneManagementState
    from src.trading_runtime.strategy_one_management_snapshot import project_manager_snapshot
    from src.backend.backtest_strategy_entry_activity_source import load_entry_activity_plan
    from test_backtest_strategy_entry_activity_source import ActivityBars
    source, proposal = entry_case()
    prepared = load_entry_activity_plan(source.entry_activity_source.plan.market,
                                       source.plan, client=ActivityBars(mode))
    activity = EntryActivityReadbackAuthority(source.run_id, prepared)
    source = CertifiedPriceReadbackAuthority(source.run_id, prepared.parent, activity)
    key = (proposal.account_id, proposal.assignment_id, proposal.ticker)
    state = StrategyOneManagementState(41000, ((key, proposal),), (), ())
    arguments = dict(run_id=source.run_id, session_date=date(2026, 8, 18),
                     checkpoint_sequence=1, state=state, first_price_source=source)
    if mode == 'normal':
        assert project_manager_snapshot(**arguments) is not None
    else:
        with pytest.raises(ValueError):
            project_manager_snapshot(**arguments)


@pytest.mark.parametrize('mode', ['fade', 'missing', 'empty'])
def test_parent_price_admission_cannot_restore_rejected_activity(mode):
    _, proposal = entry_case()
    prepared = plan(mode)
    activity = EntryActivityReadbackAuthority('manager-activity', prepared)
    source = CertifiedPriceReadbackAuthority(activity.run_id, prepared.parent, activity)
    assert prepared.parent.eligible_mask[0]
    with pytest.raises(ValueError):
        certified_entry_activity_witness(source, proposal, session_date=date(2026, 8, 18))


@pytest.mark.parametrize('mutation', ['price_only', 'wrong_day', 'parent_number', 'unknown_key'])
def test_manager_rejects_missing_or_foreign_entry_authority(mutation):
    source, proposal = entry_case()
    session_date = date(2026, 8, 18)
    if mutation == 'price_only':
        source = CertifiedPriceReadbackAuthority(source.run_id, source.plan)
    elif mutation == 'wrong_day':
        session_date = date(2026, 8, 19)
    elif mutation == 'parent_number':
        proposal = replace(proposal, strategy_number=35)
    else:
        proposal = replace(proposal, boundary_ms=32000)
    with pytest.raises(ValueError):
        certified_entry_activity_witness(source, proposal, session_date=session_date)


@pytest.mark.parametrize('boundary', [0, 1000, 19499900, 19500000, 19740000,
                                      19800000, 43200000, 43201000, 57000000, 57300000, 57600000])
def test_prepared_contract_preserves_parent_session_and_management_behavior(boundary):
    parent, child = NumberedFixedStrategyContract(35), NumberedFixedStrategyContract(36)
    for name in ('allows_session_exit', 'allows_adds', 'allows_completed_30s_trailing',
                 'allows_target_escalation', 'caps_entry_at_reference_ask',
                 'allows_followthrough_failure_exit'):
        assert getattr(child, name) == getattr(parent, name)
    for name in ('entry_allowed', 'acquisition_cutoff', 'liquidation_due'):
        assert getattr(child, name)(boundary) == getattr(parent, name)(boundary)
    for episode in (0, 1000, 43200000, 43201000):
        assert child.activation_allowed(boundary, episode) == parent.activation_allowed(boundary, episode)
