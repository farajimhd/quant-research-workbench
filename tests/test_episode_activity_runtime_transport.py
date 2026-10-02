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


def context():
    source = authority()
    parent = CertifiedPriceReadbackAuthority(source.run_id,source.plan.parent,source)
    day = date.fromisoformat(source.plan.market.sessions[0])
    original = replace(_proposal(),strategy_number=18,boundary_ms=41000,
        momentum=source.plan.parent.momentum.lookup('AAA',41000),
        initial_momentum=source.plan.parent.source.parent.selection_witness('AAA',41000))
    proposal = bind_episode_activity_proposal(parent,
        bind_certified_price_break_proposal(parent.plan,original,strategy_number=36),session_date=day)
    runtime = SimpleNamespace(config=SimpleNamespace(mode=RunMode.BACKTEST,
        strategy_id='early-squeeze-strategy',strategy_revision=37,anchor_date=day),
        run_id=source.run_id,journal=BacktestMemoryJournal(run_id=source.run_id),
        _strategy_one_price_source=None)
    TradingRuntime.bind_strategy_one_price_source(runtime,parent)
    return runtime,parent,proposal,day


def test_runtime_intent_and_atomic_memory_sidecar_preserve_strategy37_identity():
    runtime,source,proposal,day = context()
    intent = TradingRuntime._strategy_one_entry_intent(runtime,proposal)
    record = runtime.journal.append_strategy_one_intent(intent=intent,proposal=proposal,
        session_date=day,account_id=proposal.account_id,strategy_id=runtime.config.strategy_id,
        strategy_revision=37,first_price_source=source)
    assert record.entity_id == intent.intent_id
    assert record.payload['strategy_revision'] == 37
    assert runtime.journal.strategy_one_entry_for_record(record.record_id) == (proposal,day)


def test_runtime_requires_same_backtest_number_and_memory_requires_native_source():
    runtime,source,proposal,day = context()
    intent = TradingRuntime._strategy_one_entry_intent(runtime,proposal)
    runtime.config.strategy_revision = 36
    with pytest.raises(ValueError,match='exact native source'):
        TradingRuntime._strategy_one_entry_intent(runtime,proposal)
    with pytest.raises(ValueError,match='exact native source'):
        runtime.journal.append_strategy_one_intent(intent=intent,proposal=proposal,
            session_date=day,account_id=proposal.account_id,strategy_id=runtime.config.strategy_id,
            strategy_revision=37,first_price_source=None)


def test_runtime_source_binding_rejects_missing_episode_authority():
    runtime,source,_,_ = context()
    runtime._strategy_one_price_source = None
    with pytest.raises(ValueError,match='certified episode prefix'):
        TradingRuntime.bind_strategy_one_price_source(runtime,
            CertifiedPriceReadbackAuthority(source.run_id,source.plan))
