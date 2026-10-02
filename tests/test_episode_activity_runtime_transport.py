from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

from test_backtest_strategy_episode_activity_source import authority
from test_strategy_one_intent import _proposal
from src.backend.backtest_strategy_certified_price_break import (
    CertifiedPriceReadbackAuthority, bind_certified_price_break_proposal,
)
from src.backend.backtest_strategy_episode_activity_source import bind_episode_activity_proposal
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.runtime import TradingRuntime, RunMode


def context(number=37):
    source = authority(number=number)
    parent = CertifiedPriceReadbackAuthority(source.run_id,source.plan.parent,source)
    day = date.fromisoformat(source.plan.market.sessions[0])
    original = replace(_proposal(),strategy_number=18,boundary_ms=41000,
        momentum=source.plan.parent.momentum.lookup('AAA',41000),
        initial_momentum=source.plan.parent.source.parent.selection_witness('AAA',41000))
    proposal = bind_episode_activity_proposal(parent,
        bind_certified_price_break_proposal(parent.plan,original,strategy_number=36),session_date=day)
    runtime = SimpleNamespace(config=SimpleNamespace(mode=RunMode.BACKTEST,
        strategy_id='early-squeeze-strategy',strategy_revision=number,anchor_date=day),
        run_id=source.run_id,journal=BacktestMemoryJournal(run_id=source.run_id),
        _strategy_one_price_source=None)
    TradingRuntime.bind_strategy_one_price_source(runtime,parent)
    return runtime,parent,proposal,day


@pytest.mark.parametrize('number', [37, 38])
def test_runtime_intent_and_atomic_memory_sidecar_preserve_strategy37_identity(number):
    runtime,source,proposal,day = context(number)
    intent = TradingRuntime._strategy_one_entry_intent(runtime,proposal)
    record = runtime.journal.append_strategy_one_intent(intent=intent,proposal=proposal,
        session_date=day,account_id=proposal.account_id,strategy_id=runtime.config.strategy_id,
        strategy_revision=number,first_price_source=source)
    assert record.entity_id == intent.intent_id
    assert record.payload['strategy_revision'] == number
    assert runtime.journal.strategy_one_entry_for_record(record.record_id) == (proposal,day)


@pytest.mark.parametrize('number', [37, 38])
def test_runtime_requires_same_backtest_number_and_memory_requires_native_source(number):
    runtime,source,proposal,day = context(number)
    intent = TradingRuntime._strategy_one_entry_intent(runtime,proposal)
    runtime.config.strategy_revision = 36
    with pytest.raises(ValueError,match='exact native source'):
        TradingRuntime._strategy_one_entry_intent(runtime,proposal)
    with pytest.raises(ValueError,match='exact native source'):
        runtime.journal.append_strategy_one_intent(intent=intent,proposal=proposal,
            session_date=day,account_id=proposal.account_id,strategy_id=runtime.config.strategy_id,
            strategy_revision=number,first_price_source=None)


@pytest.mark.parametrize('number', [37, 38])
def test_runtime_source_binding_rejects_missing_episode_authority(number):
    runtime,source,_,_ = context(number)
    runtime._strategy_one_price_source = None
    with pytest.raises(ValueError,match='certified episode prefix'):
        TradingRuntime.bind_strategy_one_price_source(runtime,
            CertifiedPriceReadbackAuthority(source.run_id,source.plan))


@pytest.mark.parametrize('number', [37, 38])
def test_actual_memory_prefix_projects_all_strategy37_companions_with_prefix_source(number):
    from uuid import UUID
    from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
    runtime,source,proposal,day = context(number)
    intent = TradingRuntime._strategy_one_entry_intent(runtime,proposal)
    runtime.journal.append_strategy_one_intent(intent=intent,proposal=proposal,
        session_date=day,account_id=proposal.account_id,strategy_id=runtime.config.strategy_id,
        strategy_revision=number,first_price_source=source)
    units = project_pending_backtest_v4_prefix(runtime.journal,attempt_id=str(UUID(int=3701)),
        run_month=day.replace(day=1),prior_sequence=0,through_sequence=1,
        first_price_source=source,expected_config={'mode':'backtest',
            'strategy_id':runtime.config.strategy_id,'strategy_revision':number})
    assert len(units) == 1
    unit = units[0]
    assert unit.entry_evidence[0]['strategy_number'] == number
    assert len(unit.momentum_evidence) == 2
    assert unit.initial_momentum_evidence and unit.first_price_evidence
    assert unit.first_price_source is source
    assert unit.entry_activity_evidence[0]['strategy_number'] == number
    assert unit.entry_activity_evidence[0]['activity_source_token'] == source.entry_activity_source.gate.token
    assert all(row['strategy_number'] == number for family in (unit.momentum_evidence,
        unit.initial_momentum_evidence,unit.first_price_evidence) for row in family)


def test_manager_capture_requires_strategy37_episode_prefix_authority():
    from src.backend.backtest_strategy_one_management import StrategyOneManagementState
    from src.trading_runtime.strategy_one_management_snapshot import project_manager_snapshot
    runtime, source, proposal, day = context()
    key = (proposal.account_id, proposal.assignment_id, proposal.ticker)
    state = StrategyOneManagementState(proposal.boundary_ms, ((key, proposal),), (), ())
    rows = project_manager_snapshot(run_id=runtime.run_id, session_date=day,
        checkpoint_sequence=1, state=state, first_price_source=source)
    assert rows is not None
    parent_only = CertifiedPriceReadbackAuthority(source.run_id, source.plan)
    with pytest.raises(ValueError, match='episode'):
        project_manager_snapshot(run_id=runtime.run_id, session_date=day,
            checkpoint_sequence=1, state=state, first_price_source=parent_only)


@pytest.mark.parametrize('number', [37, 38])
def test_strategy37_entry_envelope_rejects_parent_activity_authority(number):
    from uuid import UUID
    from src.backend.backtest_typed_projection import project_pending_backtest_v4_prefix
    from src.backend.backtest_strategy_entry_activity_source import EntryActivityReadbackAuthority
    runtime, source, proposal, day = context(number)
    intent = TradingRuntime._strategy_one_entry_intent(runtime, proposal)
    runtime.journal.append_strategy_one_intent(intent=intent, proposal=proposal,
        session_date=day, account_id=proposal.account_id, strategy_id=runtime.config.strategy_id,
        strategy_revision=number, first_price_source=source)
    unit = project_pending_backtest_v4_prefix(runtime.journal, attempt_id=str(UUID(int=3702)),
        run_month=day.replace(day=1), prior_sequence=0, through_sequence=1,
        first_price_source=source, expected_config={'mode':'backtest',
            'strategy_id':runtime.config.strategy_id,'strategy_revision':number})[0]
    parent = CertifiedPriceReadbackAuthority(source.run_id, source.plan,
        EntryActivityReadbackAuthority(source.run_id, source.entry_activity_source.plan))
    with pytest.raises(ValueError, match='episode prefix'):
        replace(unit, first_price_source=parent, entry_activity_evidence=())
