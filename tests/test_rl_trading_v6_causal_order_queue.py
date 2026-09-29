import pytest

from research.rl_trading.v6.causal_order_queue import CausalOrderQueue
from research.rl_trading.v6.oms import BracketAccount, Quote


def _quote(clock, *, size=100., valid=True):
    return Quote(clock, clock-10_000, 10., 10.01, size, size, valid)


def test_decision_reservations_do_not_create_holdings_before_arrival():
    account = BracketAccount()
    queue = CausalOrderQueue(account)
    assert queue.submit_entry('ABC', decision_us=1_000_000,
        order_index=0, decision_close=10., desired_budget=8_000.,
        quote=_quote(1_100_000, size=100.)) == 8_000.
    assert queue.submit_entry('XYZ', decision_us=1_000_000,
        order_index=1, decision_close=10., desired_budget=8_000.,
        quote=_quote(1_100_000, size=100.)) == 2_000.
    assert account.cash == 10_000.
    assert account.positions == {}
    assert queue.advance_to(1_000_000) == ()
    outcomes = queue.advance_to(2_000_000)
    assert [row.ticker for row in outcomes] == ['ABC', 'XYZ']
    assert queue.reserved_cash == 0.
    assert len(account.positions) == 2
    assert sum(row.filled_shares for row in outcomes) > 0


def test_partial_exit_retries_on_later_quote_and_cash_frees_only_then():
    account = BracketAccount()
    queue = CausalOrderQueue(account)
    queue.submit_entry('ABC', decision_us=1_000_000,
        order_index=0, decision_close=10., desired_budget=5_000.,
        quote=_quote(1_100_000, size=500.))
    queue.advance_to(2_000_000)
    opening_cash = account.cash
    queue.submit_exit('ABC', decision_us=2_000_000, order_index=1,
        quotes=iter((_quote(2_100_000, valid=False),
                     _quote(2_200_000, size=100.),
                     _quote(3_100_000, size=500.))))
    assert account.cash == opening_cash
    first = queue.advance_to(3_000_000)
    assert len(first) == 2
    assert first[0].filled_shares == 0
    assert first[1].filled_shares == 100
    assert 'ABC' in queue.pending_exits
    assert 'ABC' in account.positions
    final = queue.advance_to(4_000_000)
    assert final[0].filled_shares > 100
    assert 'ABC' not in queue.pending_exits
    assert 'ABC' not in account.positions


def test_invalid_noncausal_quote_rejected():
    queue = CausalOrderQueue(BracketAccount())
    with pytest.raises(ValueError, match='precedes decision'):
        queue.submit_entry('ABC', decision_us=1_000_000,
            order_index=0, decision_close=10., desired_budget=100.,
            quote=_quote(1_000_000))


def test_same_bucket_completed_exit_is_booked_before_new_entry():
    account = BracketAccount()
    queue = CausalOrderQueue(account)
    queue.submit_entry('ABC', decision_us=1_000_000,
        order_index=0, decision_close=10., desired_budget=9_000.,
        quote=_quote(1_100_000, size=900.))
    queue.advance_to(2_000_000)
    queue.submit_entry('XYZ', decision_us=2_000_000,
        order_index=0, decision_close=10., desired_budget=9_000.,
        quote=_quote(2_100_000, size=900.))
    queue.submit_exit('ABC', decision_us=2_000_000, order_index=1,
                      quotes=iter((_quote(2_100_000, size=900.),)))
    results = queue.advance_to(3_000_000)
    assert [row.action for row in results] == ['exit_long', 'enter_long']
    assert 'ABC' not in account.positions
    assert 'XYZ' in account.positions
