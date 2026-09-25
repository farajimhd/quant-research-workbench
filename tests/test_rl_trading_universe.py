from research.rl_trading.v1.universe import slots, volume_order
from research.rl_trading.v1.phase3_search import SearchConfig, advance, initial_node, with_id


def _row(ticker, volume, time_us, price=10.):
    return dict(ticker=ticker, volume_60s=volume, time_us=time_us, side='long',
        can_open=True, open_value_available=True, entry_price=price,
        capital_per_share=price, open_value_per_dollar=.1,
        can_close=True, close_price=price, hold_value_available=True,
        hold_value_per_share=0.)


def test_rank_changes_preserve_identity_and_held_slot():
    first = volume_order([_row('A', 10, 1), _row('B', 20, 1), _row('C', 30, 1)])
    second = volume_order([_row('A', 40, 2), _row('B', 20, 2), _row('C', 1, 2)])
    assert slots(first, (), 2) == ('C', 'B')
    assert slots(second, ('C',), 2) == ('C', 'A')


def test_search_cannot_buy_outside_top_n_but_can_close_dropped_holding():
    config = SearchConfig(initial_cash=100., allocation_step=50., max_lots=1,
        max_orders_per_second=1, max_candidates=0, beam_width=0, top_n=1)
    first = [with_id(initial_node(100.), 1)]
    candidates, _ = advance(first, [_row('A', 20, 1), _row('B', 10, 1)], 1, config)
    assert all(not n.lots or n.lots[0].ticker == 'A' for n in candidates)
    held = with_id(next(n for n in candidates if n.lots), 2)
    next_rows = [_row('A', 1, 2), _row('B', 100, 2)]
    later, _ = advance([held], next_rows, 2, config)
    assert any(n.lots and n.lots[0].ticker == 'A' for n in later)
    assert any(not n.lots and n.actions[0]['action'] == 'sell' for n in later)
    assert all(not n.lots or n.lots[0].ticker != 'B' for n in later)
