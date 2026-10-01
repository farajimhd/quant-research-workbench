"""Relaxed first momentum still requires exact certified first-price authority."""
from dataclasses import replace

import numpy as np
import pytest

from test_backtest_strategy_initial_momentum import plans
from test_backtest_strategy_initial_momentum_growth import market_for
from test_backtest_strategy_first_price_source import Bars
from src.backend.backtest_strategy_initial_momentum_growth import compile_initial_momentum_growth_plan
from src.backend.backtest_strategy_initial_ten_percent import compile_initial_ten_percent_plan
from src.backend.backtest_strategy_first_price_source import load_first_price_source
from src.backend.backtest_strategy_initial_price_break import stage_initial_price_break_plan
from src.backend.backtest_strategy_certified_price_break import compile_certified_price_break_plan


def authority():
    candidates, entry, momentum, _ = plans()
    parent = compile_initial_ten_percent_plan(candidates, entry, momentum)
    market = market_for(candidates)
    bars = replace(market.units[0], stage='bars', attempt_id=candidates.coverage[0].source_attempts[0])
    return replace(market, units=(*market.units, bars)), parent


def test_relaxed_parent_requests_only_original_first_and_restores_same_scalar():
    market, parent = authority()
    client = Bars()
    source = load_first_price_source(market, parent, client=client)
    plan = compile_certified_price_break_plan(source)
    assert source.requested_mask.tolist() == [True, False]
    assert source.observations[0].tolist() == [31_000, 0]
    assert source.observations[1].tolist() == [30_000, 0]
    assert plan.eligible_mask.tolist() == [True, True]
    assert plan.lookup('AAA', 41_000).first_setup == parent.momentum.lookup('AAA', 31_000)
    assert plan.price_witness('AAA', 41_000).first_setup_boundary_ms == 31_000
    assert len(client.queries) == 1
    assert parent.candidates.coverage[0].source_attempts[0] in client.queries[0]


def test_old_fifty_parent_keeps_original_rejections_and_distinct_source_seal():
    market, parent = authority()
    old = compile_initial_momentum_growth_plan(parent.candidates, parent.entry, parent.momentum)
    old_client, new_client = Bars(), Bars()
    old_source = load_first_price_source(market, old, client=old_client)
    new_source = load_first_price_source(market, parent, client=new_client)
    assert not np.any(compile_certified_price_break_plan(old_source).eligible_mask)
    assert compile_certified_price_break_plan(new_source).eligible_mask.tolist() == [True, True]
    assert old_source.token != new_source.token
    assert old_client.queries == [] and len(new_client.queries) == 1
    with pytest.raises(ValueError, match='content seal'):
        replace(new_source, parent=old, requested_mask=old_source.requested_mask,
                observations=old_source.observations)


def test_price_equal_to_prior_high_rejects_entire_episode_even_if_later_breaks():
    market, parent = authority()
    source = load_first_price_source(market, parent, client=Bars())
    columns = [value.copy() for value in source.observations]
    columns[2][0] = columns[3][0]
    staged = stage_initial_price_break_plan(parent, tuple(columns))
    assert not np.any(staged.eligible_mask)


@pytest.mark.parametrize('mode', ['duplicate', 'outside', 'flags'])
def test_relaxed_price_source_still_rejects_malformed_producer_rows(mode):
    market, parent = authority()
    with pytest.raises(ValueError):
        load_first_price_source(market, parent, client=Bars(mode))


def test_missing_prior_bar_rejects_without_carrying_or_reselecting():
    market, parent = authority()
    source = load_first_price_source(market, parent, client=Bars('missing'))
    assert not np.any(compile_certified_price_break_plan(source).eligible_mask)
    assert parent.initial.first_indices.tolist() == [0, 0]


def test_wrong_attempt_rejected_before_any_query():
    market, parent = authority()
    units = tuple(replace(unit, attempt_id='f'*8+'-ffff-ffff-ffff-'+'f'*12)
                  if unit.stage == 'bars' else unit for unit in market.units)
    client = Bars()
    with pytest.raises(ValueError, match='bars attempt differs'):
        load_first_price_source(replace(market, units=units), parent, client=client)
    assert client.queries == []
