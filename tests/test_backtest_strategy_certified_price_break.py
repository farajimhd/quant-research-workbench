from dataclasses import replace

import numpy as np
import pytest

from test_backtest_strategy_first_price_source import authority, Bars
from src.backend.backtest_strategy_first_price_source import load_first_price_source
from src.backend.backtest_strategy_certified_price_break import compile_certified_price_break_plan


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
