from dataclasses import replace

import numpy as np
import pytest

from test_backtest_strategy_first_price_source import authority, Bars
from src.backend.backtest_strategy_first_price_source import load_first_price_source
from src.backend.backtest_strategy_certified_price_break import compile_certified_price_break_plan


def test_cold_price_authority_rebuilds_from_native_plan_and_rejects_changed_rows():
    from test_arte_first_price_entry_v4 import graph
    from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
    from src.trading_runtime.arte_first_price_entry_v4 import seal_first_price_rows, project_first_price_entry
    market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    source = CertifiedPriceReadbackAuthority('price-run', plan)
    _, entries, intents, events, _ = graph()
    current = plan.momentum.lookup('AAA', 41000)
    selection = plan.selection_witness('AAA', 41000)
    rows = project_first_price_entry(current, selection, plan.price_witness('AAA', 41000),
        price_source_token=plan.source.token, run_id='price-run',
        batch_id=entries[0]['batch_id'], parent_record_id=entries[0]['parent_record_id'],
        event_month='2026-08-01')
    rebuilt = source.resolve('price-run', entries, intents)
    assert rebuilt[0].current == current and rebuilt[0].selection == selection
    assert rebuilt[0].price_source_token == plan.source.token
    assert seal_first_price_rows(rows, entries, intents, events, rebuilt)
    changed = (dict(rows[0], current_close_int=102),)
    with pytest.raises(ValueError):
        seal_first_price_rows(changed, entries, intents, events, rebuilt)
    with pytest.raises(ValueError, match='certified run'):
        source.resolve('another-run', entries, intents)
    with pytest.raises(ValueError, match='outside admitted'):
        source.resolve('price-run', (dict(entries[0], boundary_ms=42000),), intents)
    with pytest.raises(ValueError, match='native episode'):
        source.resolve('price-run', (dict(entries[0], episode_start_ms=30001),), intents)
    with pytest.raises(ValueError, match='duplicate intent'):
        source.resolve('price-run', entries, intents * 2)
    with pytest.raises(ValueError, match='unrelated entry'):
        source.resolve('price-run', entries * 2, intents)


def test_later_entry_price_witness_uses_original_first_clock_and_bars_attempt():
    market, parent = authority()
    source = load_first_price_source(market, parent, client=Bars())
    plan = compile_certified_price_break_plan(source)
    price = plan.price_witness('AAA', 41000)
    assert price.first_setup_boundary_ms == 31000
    assert price.current_boundary_ms == 31000 and price.prior_boundary_ms == 30000
    assert price.bars_attempt_id == parent.candidates.coverage[0].source_attempts[0]
    assert price.market_plan_token == market.token
    selection = plan.selection_witness('AAA', 41000)
    assert selection.initial.first_setup.boundary_ms == price.first_setup_boundary_ms
    assert selection.selection_token == plan.token
    assert plan.candidates is parent.candidates and plan.entry is parent.entry
    assert plan.momentum is parent.momentum
    assert plan.token != parent.token and plan.token != source.token


def test_missing_price_rejects_native_and_scalar_entry():
    market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars('missing')))
    assert not np.any(plan.eligible_mask)
    with pytest.raises(ValueError, match='outside admitted'):
        plan.selection_witness('AAA', 41000)


@pytest.mark.parametrize('bars_mode', ['valid', 'missing'])
def test_price_static_gate_preserves_inherited_rejections_and_never_promotes(bars_mode):
    from src.backend.backtest_strategy_certified_price_break import compile_certified_price_static_gate
    from src.backend.backtest_strategy_one_static_gate import compile_static_entry_gate, INITIAL_MOMENTUM_REQUIRED
    market, parent = authority()
    source = load_first_price_source(market, parent,
        client=Bars() if bars_mode == 'valid' else Bars('missing'))
    plan = compile_certified_price_break_plan(source)
    inherited = compile_static_entry_gate(parent.candidates, parent.entry,
        strategy_number=19, momentum_plan=parent.momentum, initial_momentum_plan=parent)
    gate = compile_certified_price_static_gate(plan)
    assert gate.facts == inherited.facts
    assert np.array_equal(gate.rejection_mask & inherited.rejection_mask,
                          inherited.rejection_mask)
    expected = inherited.rejection_mask | (
        (~plan.eligible_mask).astype(np.uint8) * INITIAL_MOMENTUM_REQUIRED)
    assert np.array_equal(gate.rejection_mask, expected)
    assert np.array_equal(gate.eligible_indices, np.flatnonzero(expected == 0))
    if bars_mode == 'missing':
        assert gate.eligible_indices.size == 0
    with pytest.raises(ValueError, match='exact certified'):
        compile_certified_price_static_gate(parent)


def test_identity_and_immutable_seal_fail_closed():
    market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    with pytest.raises(ValueError, match='exact typed'):
        plan.lookup('AAA', 41000.0)
    with pytest.raises(ValueError, match='outside admitted'):
        plan.lookup('AAA', 42000)
    with pytest.raises(ValueError, match='content seal'):
        replace(plan, token='f' * 64)
    with pytest.raises(ValueError, match='eligibility differs'):
        replace(plan, eligible_mask=np.array([True, False]))
    with pytest.raises(ValueError):
        plan.eligible_mask.setflags(write=True)
    with pytest.raises(ValueError, match='exact certified'):
        compile_certified_price_break_plan(object())


def test_native_proposal_binding_preserves_financial_fields_and_cannot_submit():
    from datetime import date
    from test_strategy_one_intent import _proposal
    from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent
    from src.backend.backtest_strategy_certified_price_break import bind_certified_price_break_proposal
    market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    original = replace(_proposal(), strategy_number=19, boundary_ms=41000,
        momentum=parent.momentum.lookup('AAA', 41000),
        initial_momentum=parent.selection_witness('AAA', 41000))
    bound = bind_certified_price_break_proposal(plan, original)
    assert bound.strategy_number == 20
    assert bound.first_price == plan.price_witness('AAA', 41000)
    assert bound.price_source_token == plan.source.token
    assert bound.initial_momentum == plan.selection_witness('AAA', 41000)
    assert replace(bound, strategy_number=19, first_price=None, price_source_token=None,
        initial_momentum=original.initial_momentum) == original
    with pytest.raises(ValueError, match='exact numbered'):
        strategy_one_entry_intent(bound, session_date=date(2026, 8, 18))
    with pytest.raises(ValueError, match='unpublished first-price'):
        strategy_one_entry_intent(replace(original, first_price=bound.first_price), session_date=date(2026, 8, 18))
    with pytest.raises(ValueError, match='original parent'):
        bind_certified_price_break_proposal(plan, replace(original,
            initial_momentum=bound.initial_momentum))


def test_price_financial_adapter_preserves_rejections_and_exact_source_facts():
    from test_strategy_one_stateful import _facts
    from src.backend.backtest_strategy_certified_price_break import propose_certified_price_entry
    market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    candidate, _, _, financial = _facts()
    fact = parent.entry.lookup('AAA', 31000)
    activation = next(row for row in parent.entry.activations if row.ticker == 'AAA')
    decision = propose_certified_price_entry(plan, candidate, fact, activation, financial)
    assert decision.reason == 'entry_proposed' and decision.proposal.strategy_number == 20
    assert decision.proposal.first_price == plan.price_witness('AAA', 31000)
    assert decision.proposal.initial_stop == fact.stop_price
    assert decision.proposal.initial_target == fact.target_price
    rejected = propose_certified_price_entry(plan, candidate, fact, activation,
        replace(financial, pending_entry=True))
    assert rejected.reason == 'entry_fill_pending' and rejected.proposal is None
    with pytest.raises(ValueError, match='certified entry facts'):
        propose_certified_price_entry(plan, candidate, replace(fact, stop_price=9.5), activation, financial)
    with pytest.raises(ValueError, match='certified activation'):
        propose_certified_price_entry(plan, candidate, fact, replace(activation, average_gap=1.0), financial)


def test_reviewed_source_accepts_current_guard_and_rejects_its_removal(tmp_path):
    from pathlib import Path
    from src.backend.backtest_fixed_v4_certification import certify_rising_momentum_entry_source
    assert len(certify_rising_momentum_entry_source()) == 64
    relative = 'trading_runtime/strategy_one_intent.py'
    source = (Path(__file__).parents[1] / 'src' / relative).read_text()
    guard = ('    if proposal.first_price is not None or proposal.price_source_token is not None:\n'
             '        raise ValueError("Installed entry cannot carry unpublished first-price evidence")\n')
    assert source.count(guard) == 1
    changed = tmp_path / 'intent.py'
    changed.write_text(source.replace(guard, ''))
    with pytest.raises(ValueError, match='reviewed source authority changed'):
        certify_rising_momentum_entry_source(source_overrides={relative: changed})


@pytest.mark.parametrize('relative,before,after', [
    ('backend/backtest_strategy_certified_price_break.py',
     "price_source_token=plan.source.token)", "price_source_token='f' * 64)"),
    ('backend/backtest_strategy_one_coordinator.py',
     'decision = propose_certified_price_entry(initial_momentum_plan,',
     'decision = propose_certified_price_entry(None,'),
    ('trading_runtime/arte_journal_commit_v4.py',
     'batch_id=batch_id, first_price_source=first_price_source)',
     'batch_id=batch_id)'),
    ('trading_runtime/arte_journal_commit_v4.py',
     'getattr(client, "_v4_writer_snapshot_price_scope", None) != price_scope',
     'False'),
    ('backend/backtest_typed_publisher.py',
     'first_price_source=self._first_price_source,', 'first_price_source=None,'),
    ('trading_runtime/runtime.py',
     'first_price_source=self._strategy_one_price_source)', 'first_price_source=None)'),
    ('trading_runtime/arte_journal_writer.py',
     'journal_batch_id=unit.journal_batch_id, **price_context)',
     'journal_batch_id=unit.journal_batch_id)'),
])
def test_reviewed_source_rejects_price_authority_or_dispatch_changes(tmp_path, relative, before, after):
    from pathlib import Path
    from src.backend.backtest_fixed_v4_certification import certify_rising_momentum_entry_source
    source = (Path(__file__).parents[1] / 'src' / relative).read_text(encoding='utf-8')
    assert before in source
    altered = tmp_path / 'source.py'
    altered.write_text(source.replace(before, after), encoding='utf-8')
    with pytest.raises(ValueError, match='reviewed source authority changed'):
        certify_rising_momentum_entry_source(source_overrides={relative: altered})


def test_projection_carries_exact_normalized_row_and_independent_source_authority():
    from uuid import UUID
    from test_strategy_one_intent import _proposal
    from src.backend.backtest_strategy_certified_price_break import (
        bind_certified_price_break_proposal, project_certified_price_entry,
    )
    from src.trading_runtime.arte_first_price_entry_v4 import restore_first_price_entry
    market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    original = replace(_proposal(), strategy_number=19, boundary_ms=41000,
        momentum=parent.momentum.lookup('AAA', 41000),
        initial_momentum=parent.selection_witness('AAA', 41000))
    bound = bind_certified_price_break_proposal(plan, original)
    kwargs = dict(run_id='projection-test', batch_id=str(UUID(int=11)),
                  parent_record_id=str(UUID(int=12)), event_month='2026-08-01')
    packet = project_certified_price_entry(plan, bound, **kwargs)
    assert packet.rows[0]['price_source_token'] == plan.source.token
    assert packet.authority.price == bound.first_price
    assert restore_first_price_entry(packet.rows, bound.momentum, bound.initial_momentum,
        expected_price=packet.authority.price,
        expected_price_source_token=packet.authority.price_source_token) == bound.first_price
    with pytest.raises(TypeError):
        packet.rows[0]['current_close_int'] = 1
    with pytest.raises(ValueError, match='source-bound'):
        project_certified_price_entry(plan, replace(bound,
            first_price=replace(bound.first_price, current_close_int=102)), **kwargs)
    with pytest.raises(ValueError, match='month differs'):
        project_certified_price_entry(plan, bound, **{**kwargs, 'event_month':'2026-09-01'})


def test_twenty_current_initial_and_price_companions_bind_same_native_entry_graph():
    from uuid import UUID, uuid5, NAMESPACE_URL
    from test_strategy_one_intent import _proposal
    from src.backend.backtest_strategy_certified_price_break import bind_certified_price_break_proposal, project_certified_price_entry
    from src.trading_runtime.arte_rising_momentum_entry_v4 import project_rising_momentum_entry, seal_rising_momentum_rows
    from src.trading_runtime.arte_initial_momentum_entry_v4 import project_initial_momentum_entry, seal_initial_momentum_rows
    from src.trading_runtime.arte_first_price_entry_v4 import seal_first_price_rows
    market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    original = replace(_proposal(), strategy_number=19, boundary_ms=41000,
        momentum=parent.momentum.lookup('AAA', 41000), initial_momentum=parent.selection_witness('AAA', 41000))
    proposal = bind_certified_price_break_proposal(plan, original)
    scope = dict(run_id='price-graph', batch_id=str(UUID(int=11)), event_month='2026-08-01')
    parent_id = str(UUID(int=12))
    kwargs = dict(scope, parent_record_id=parent_id)
    current = project_rising_momentum_entry(proposal, **kwargs)
    initial = project_initial_momentum_entry(proposal, proposal.initial_momentum, **kwargs)
    price = project_certified_price_entry(plan, proposal, **kwargs)
    identity = (f'strategy-20:2026-08-18:{proposal.assignment_id}:{proposal.account_id}:'
                f'AAA:41000:{proposal.episode_start_ms}')
    intent_id = str(uuid5(NAMESPACE_URL, identity))
    entry = dict(scope, parent_record_id=parent_id, strategy_number=20,
        assignment_id=proposal.assignment_id, boundary_ms=41000, episode_start_ms=proposal.episode_start_ms)
    intent = dict(scope, record_id=parent_id, action='enter_long', reason='strategy_one_entry',
        intent_id=intent_id, ticker='AAA', account_id=proposal.account_id)
    event = dict(scope, record_id=parent_id, category='strategy', entity_type='strategy_intent',
        entity_id=intent_id, account_id=proposal.account_id, event_time='2026-08-18T08:00:41+00:00')
    assert len(seal_rising_momentum_rows(current, (entry,), (intent,), (event,))) == 2
    assert len(seal_initial_momentum_rows(initial, (entry,), (intent,), (event,), current)) == 2
    assert len(seal_first_price_rows(price.rows, (entry,), (intent,), (event,), (price.authority,))) == 1
    for family in ('current', 'initial'):
        changed = tuple(dict(row, strategy_number=19) for row in (current if family == 'current' else initial))
        with pytest.raises(ValueError):
            seal_initial_momentum_rows(changed if family == 'initial' else initial,
                (entry,), (intent,), (event,), changed if family == 'current' else current)


def test_recovered_price_proposal_matches_native_binding_and_rejects_resealed_forgery():
    from uuid import UUID
    from test_strategy_one_intent import _proposal
    from src.backend.backtest_strategy_certified_price_break import (
        bind_certified_price_break_proposal, project_certified_price_entry,
        restore_certified_price_proposal, CertifiedPriceReadbackAuthority,
    )
    market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    original = replace(_proposal(), strategy_number=19, boundary_ms=41000,
        momentum=parent.momentum.lookup('AAA', 41000), initial_momentum=parent.selection_witness('AAA', 41000))
    bound = bind_certified_price_break_proposal(plan, original)
    packet = project_certified_price_entry(plan, bound, run_id='recovery',
        batch_id=str(UUID(int=11)), parent_record_id=str(UUID(int=12)), event_month='2026-08-01')
    scope = dict(parent_record_id=str(UUID(int=12)), batch_id=str(UUID(int=11)))
    source = CertifiedPriceReadbackAuthority('recovery', plan)
    reference = replace(bound, first_price=None, price_source_token=None)
    assert restore_certified_price_proposal(source, reference, packet.rows, **scope) == bound
    with pytest.raises(ValueError):
        restore_certified_price_proposal(source, reference,
            tuple(dict(row, current_close_int=102) for row in packet.rows), **scope)
    with pytest.raises(ValueError, match='native source selection'):
        restore_certified_price_proposal(source,
            replace(reference, initial_momentum=original.initial_momentum), packet.rows, **scope)
    with pytest.raises(ValueError, match='already carries'):
        restore_certified_price_proposal(source, bound, packet.rows, **scope)
    with pytest.raises(ValueError):
        restore_certified_price_proposal(source, replace(reference, initial_stop=11.0), packet.rows, **scope)
    for field, value in [('run_id', 'foreign-run'), ('parent_record_id', str(UUID(int=13))),
                         ('batch_id', str(UUID(int=14))), ('event_month', '2026-09-01')]:
        with pytest.raises(ValueError, match='requested run and entry scope'):
            restore_certified_price_proposal(source, reference,
                tuple(dict(row, **{field: value}) for row in packet.rows), **scope)
