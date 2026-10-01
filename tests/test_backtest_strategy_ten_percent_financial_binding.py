"""A relaxed native source cannot be relabeled as an older numbered policy."""
from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from test_backtest_strategy_ten_percent_price_source import authority
from test_backtest_strategy_first_price_source import Bars, authority as old_authority
from test_strategy_one_intent import _proposal
from src.backend.backtest_strategy_first_price_source import load_first_price_source
from src.backend.backtest_strategy_certified_price_break import (
    compile_certified_price_break_plan, compile_certified_price_static_gate,
    bind_certified_price_break_proposal, certified_price_entry_intent,
    propose_certified_price_entry,
)
from src.trading_runtime.strategy_one_intent import strategy_one_entry_intent


def source():
    market, parent = authority()
    plan = compile_certified_price_break_plan(load_first_price_source(market, parent, client=Bars()))
    original = replace(_proposal(), strategy_number=18, boundary_ms=41_000,
        momentum=parent.momentum.lookup('AAA', 41_000),
        initial_momentum=parent.selection_witness('AAA', 41_000))
    return market, parent, plan, original


def test_native_26_binding_preserves_every_financial_field_and_has_own_identity():
    _, parent, plan, original = source()
    bound = bind_certified_price_break_proposal(plan, original, strategy_number=26)
    assert bound.strategy_number == 26
    assert replace(bound, strategy_number=18, first_price=None, price_source_token=None,
                   initial_momentum=original.initial_momentum) == original
    assert bound.first_price == plan.price_witness('AAA', 41_000)
    assert bound.initial_momentum == plan.selection_witness('AAA', 41_000)
    intent = certified_price_entry_intent(plan, bound, session_date=date(2026, 8, 18))
    parent_intent = strategy_one_entry_intent(original, session_date=date(2026, 8, 18))
    assert intent.intent_id != parent_intent.intent_id
    assert replace(intent, intent_id=parent_intent.intent_id) == parent_intent
    with pytest.raises(ValueError, match='exact numbered'):
        strategy_one_entry_intent(bound, session_date=date(2026, 8, 18))
    # The older first 50% scalar validation still rejects this first 20% source.
    with pytest.raises(ValueError, match='50pct'):
        strategy_one_entry_intent(replace(original, strategy_number=19), session_date=date(2026, 8, 18))


@pytest.mark.parametrize('number', [20, 21, 22, 23, 24, 25])
def test_relaxed_source_cannot_authorize_older_number(number):
    _, _, plan, original = source()
    with pytest.raises(ValueError, match='source policy'):
        bind_certified_price_break_proposal(plan, original, strategy_number=number)


def test_old_fifty_source_cannot_authorize_26_and_foreign_parent_is_rejected():
    market, old = old_authority()
    old_plan = compile_certified_price_break_plan(load_first_price_source(market, old, client=Bars()))
    _, _, plan, original = source()
    with pytest.raises(ValueError, match='source policy'):
        bind_certified_price_break_proposal(old_plan, original, strategy_number=26)
    with pytest.raises(ValueError, match='policy-matched parent'):
        bind_certified_price_break_proposal(plan, replace(original, strategy_number=19), strategy_number=26)
    with pytest.raises(ValueError, match='original parent'):
        bind_certified_price_break_proposal(plan, replace(original,
            initial_momentum=plan.selection_witness('AAA', 41_000)), strategy_number=26)


def test_relaxed_native_static_gate_keeps_shared_structural_and_current_rejections():
    from src.backend.backtest_strategy_one_static_gate import compile_static_entry_gate, INITIAL_MOMENTUM_REQUIRED
    _, parent, plan, _ = source()
    inherited = compile_static_entry_gate(parent.candidates, parent.entry, strategy_number=18,
        momentum_plan=parent.momentum, initial_momentum_plan=parent.initial)
    gate = compile_certified_price_static_gate(plan)
    expected = inherited.rejection_mask | ((~plan.eligible_mask).astype(np.uint8) * INITIAL_MOMENTUM_REQUIRED)
    assert gate.facts == inherited.facts and np.array_equal(gate.rejection_mask, expected)


def test_relaxed_financial_decision_preserves_pending_rejection_and_protection():
    from test_strategy_one_stateful import _facts
    _, parent, plan, _ = source()
    candidate, _, _, financial = _facts()
    fact = parent.entry.lookup('AAA', 31_000)
    activation = next(row for row in parent.entry.activations if row.ticker == 'AAA')
    decision = propose_certified_price_entry(plan, candidate, fact, activation, financial, strategy_number=26)
    assert decision.reason == 'entry_proposed' and decision.proposal.strategy_number == 26
    assert decision.proposal.initial_stop == fact.stop_price
    assert decision.proposal.initial_target == fact.target_price
    rejected = propose_certified_price_entry(plan, candidate, fact, activation,
        replace(financial, pending_entry=True), strategy_number=26)
    assert rejected.reason == 'entry_fill_pending' and rejected.proposal is None
