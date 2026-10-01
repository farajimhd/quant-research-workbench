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
