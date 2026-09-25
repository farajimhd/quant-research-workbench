"""Small exhaustive references for the finite Phase 3 portfolio search."""
from itertools import combinations_with_replacement

import pytest

from src.market_engine.hindsight_phase3 import SearchConfig,advance,initial_node,with_id


def rows(time_us,prices,open_allowed=True):
    return [dict(time_us=time_us,ticker=ticker,side='long',can_open=open_allowed,
        open_value_available=open_allowed,entry_price=price,
        capital_per_share=price,open_value_per_dollar=0.1,
        can_close=True,close_price=price,hold_value_available=True,
        hold_value_per_share=0.) for ticker,price in prices.items()]


def test_unpruned_search_matches_independent_two_ticker_enumeration():
    config = SearchConfig(initial_cash=100.,allocation_step=50.,max_lots=2,
        max_orders_per_second=2,max_candidates=0,beam_width=0)
    start = with_id(initial_node(100.),1)
    first,_ = advance([start],rows(1,{'A':10.,'B':20.}),1,config)
    first = [with_id(x,i+2) for i,x in enumerate(first)]
    finished,_ = advance(first,rows(2,{'A':11.,'B':18.},False),2,config,terminal=True)
    best = max(x.cash for x in finished)
    oracle = [100.]
    for n in (1,2):
        for choices in combinations_with_replacement(('A','B'),n):
            oracle.append(100.+sum(50.*((11./10.-1) if t=='A' else (18./20.-1)) for t in choices))
    assert best == pytest.approx(max(oracle)) == pytest.approx(110.)
    assert all(not x.lots for x in finished)


def test_beam_keeps_cash_path_and_missing_opening_means_no_buy():
    config = SearchConfig(initial_cash=100.,allocation_step=50.,max_lots=2,
        max_orders_per_second=2,max_candidates=1,beam_width=1)
    first,_ = advance([with_id(initial_node(100.),1)],rows(1,{'A':10.}),1,config)
    assert first[0].cash == 100. and not first[0].lots
    second,_ = advance([with_id(first[0],2)],rows(2,{'A':10.},False),2,config)
    assert second[0].cash == 100. and not second[0].actions


def test_joint_allocation_and_forced_liquidation():
    config = SearchConfig(initial_cash=100.,allocation_step=50.,max_lots=2,
        max_orders_per_second=2,max_candidates=0,beam_width=0)
    first,_ = advance([with_id(initial_node(100.),1)],rows(1,{'A':10.,'B':20.}),1,config)
    mixed = next(x for x in first if sorted(l.ticker for l in x.lots)==['A','B'])
    finished,_ = advance([with_id(mixed,2)],rows(2,{'A':11.,'B':22.},False),2,config,terminal=True)
    assert finished[0].cash == pytest.approx(110.)
    assert [x['action'] for x in finished[0].actions] == ['sell','sell']
    assert not finished[0].lots


def test_exact_search_reuses_cash_in_a_sell_and_buy_transition():
    config = SearchConfig(initial_cash=50.,allocation_step=50.,max_lots=1,
        max_orders_per_second=2,max_candidates=0,beam_width=0)
    frontier = [with_id(initial_node(50.),1)]
    for time_us,prices,terminal in ((1,{'A':10.,'B':10.},False),
                                    (2,{'A':20.,'B':5.},False),
                                    (3,{'A':20.,'B':20.},True)):
        frontier,_ = advance(frontier,rows(time_us,prices,not terminal),time_us,
            config,terminal=terminal)
        frontier = [with_id(node,time_us*100_000+i) for i,node in enumerate(frontier)]
    # Buy A, sell it and buy B at t=2, then liquidate B.
    assert max(node.cash for node in frontier) == pytest.approx(250.)
