"""Certified activity context survives immutable native transport compaction."""
from dataclasses import replace
from datetime import date
from uuid import UUID
from types import SimpleNamespace

import pytest

from test_arte_entry_activity_v4 import plan, graph, project, BATCH, PARENT
from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
from src.backend.backtest_strategy_entry_activity_source import EntryActivityReadbackAuthority
from src.trading_runtime.arte_journal_writer import TypedJournalBatch, V4StrategyOneEntryBatch
from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, _compound_price_source


def activity_unit():
    prepared = plan()
    activity = EntryActivityReadbackAuthority('activity-run', prepared)
    source = CertifiedPriceReadbackAuthority('activity-run', prepared.parent, activity)
    witness, entry, _ = graph(prepared)
    entry['record_id'] = str(UUID(int=100))
    event = dict(record_id=PARENT, run_id=source.run_id, batch_id=BATCH,
                 sequence=1, category='strategy', entity_type='strategy_intent')
    base = TypedJournalBatch(source.run_id, date(2026, 8, 1), str(UUID(int=10)),
        BATCH, str(UUID(int=0)), 1, 1, 'start', 'running', (event,))
    return V4StrategyOneEntryBatch(base, (entry,),
        entry_activity_evidence=(project(witness),), first_price_source=source)


def test_price_context_requires_exact_activity_run_and_parent_plan():
    unit = activity_unit()
    activity = unit.first_price_source.entry_activity_source
    with pytest.raises(ValueError, match='same certified run and parent plan'):
        CertifiedPriceReadbackAuthority('different-run', activity.plan.parent, activity)
    with pytest.raises(ValueError, match='same certified run and parent plan'):
        CertifiedPriceReadbackAuthority('activity-run', plan().parent, activity)
    with pytest.raises(ValueError, match='same certified run and parent plan'):
        CertifiedPriceReadbackAuthority('activity-run', activity.plan.parent, object())


def test_strategy36_envelope_cannot_drop_or_forge_certified_source():
    unit = activity_unit()
    with pytest.raises(ValueError, match='certified activity source'):
        replace(unit, first_price_source=None)
    with pytest.raises(ValueError, match='foreign certified price source'):
        replace(unit, first_price_source=object())
    with pytest.raises(TypeError):
        unit.entry_activity_evidence[0]['candle_0_trade_count'] = 999


def test_compound_preserves_activity_counts_source_and_original_batch():
    unit = activity_unit()
    next_id = str(UUID(int=38))
    next_event = dict(record_id=str(UUID(int=39)), run_id=unit.base.run_id,
                     batch_id=next_id, sequence=2, category='risk', entity_type='continuous_risk_state')
    following = replace(unit.base, batch_id=next_id, prior_batch_id=BATCH,
                        first_sequence=2, last_sequence=2, events=(next_event,))
    merged = coalesce_v4_units((unit, following))
    row = merged.children['entry_activity_evidence'][0]
    assert row['batch_id'] == next_id and row['parent_record_id'] == PARENT
    assert [row[f'candle_{i}_trade_count'] for i in range(4)] == [100] * 4
    assert unit.entry_activity_evidence[0]['batch_id'] == BATCH
    assert _compound_price_source(merged, None) is unit.first_price_source
    assert _compound_price_source(merged, unit.first_price_source) is unit.first_price_source
    with pytest.raises(ValueError, match='conflicting certified source contexts'):
        _compound_price_source(merged, activity_unit().first_price_source)


def test_scalar_runtime_rechecks_same_admitted_activity_keys_without_market_io():
    from src.trading_runtime.runtime import TradingRuntime, RunMode
    from src.backend.backtest_journal_memory import BacktestMemoryJournal
    from src.backend.backtest_strategy_certified_price_break import bind_certified_price_break_proposal
    from test_strategy_one_intent import _proposal
    prepared = plan('fade')
    activity = EntryActivityReadbackAuthority('activity-run', prepared)
    source = CertifiedPriceReadbackAuthority('activity-run', prepared.parent, activity)
    runtime = SimpleNamespace(config=SimpleNamespace(mode=RunMode.BACKTEST,
        strategy_id='early-squeeze-strategy', strategy_revision=36, anchor_date=date(2026, 8, 18)),
        run_id='activity-run', journal=BacktestMemoryJournal(run_id='activity-run'),
        _strategy_one_price_source=None)
    TradingRuntime.bind_strategy_one_price_source(runtime, source)
    def proposal(boundary):
        original = replace(_proposal(), strategy_number=18, boundary_ms=boundary,
            momentum=source.plan.momentum.lookup('AAA', boundary),
            initial_momentum=source.plan.source.parent.selection_witness('AAA', boundary))
        return bind_certified_price_break_proposal(source.plan, original, strategy_number=36)
    with pytest.raises(ValueError):
        TradingRuntime._strategy_one_entry_intent(runtime, proposal(31000))
    admitted = TradingRuntime._strategy_one_entry_intent(runtime, proposal(41000))
    assert admitted.action == 'enter_long' and admitted.ticker == 'AAA'
    assert prepared.eligible_mask.tolist() == [False, True]


def test_backtest_activity_family_requires_ssd_preflight_without_live_grants():
    from src.trading_runtime.arte_entry_activity_v4 import ENTRY_ACTIVITY
    from src.trading_runtime.arte_journal_writer import v4_storage_contracts, v4_journal_write_tables
    from src.backend.live_strategy_one_v4_principal import desired_plan
    assert ENTRY_ACTIVITY in v4_storage_contracts()
    assert ENTRY_ACTIVITY.name in v4_journal_write_tables()
    assert "storage_policy = 'live_market_ssd'" in ENTRY_ACTIVITY.ddl()
    assert ENTRY_ACTIVITY.name not in desired_plan().insert_arte
