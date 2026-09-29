import pytest

from research.rl_trading.v6.exit_stream import apply_exit_quote_stream
from research.rl_trading.v6.oms import BracketAccount, Quote


def _quote(clock, *, bid_size=100., valid=True):
    return Quote(clock, clock-10_000, 10., 10.01, bid_size, 100., valid)


def test_exit_retries_partial_and_stale_buckets_without_inventing_a_fill():
    account = BracketAccount()
    shares = account.enter_long('ABC', decision_us=1_000_000,
        decision_close=10.01, budget=5_000.,
        quote=Quote(1_100_000, 1_090_000, 10., 10.01, 100., 500., True))
    assert shares > 100
    result = apply_exit_quote_stream(account, 'ABC', decision_us=2_000_000,
        quotes=(_quote(2_100_000, valid=False),
                _quote(2_200_000, bid_size=100.),
                _quote(2_300_000, bid_size=500.)))
    assert result.attempts == 3
    assert result.filled_shares == shares
    assert result.remaining_shares == 0
    assert [row['status'] for row in account.orders[-3:]] == [
        'unfilled', 'partial', 'filled']


def test_exit_evidence_ending_leaves_real_open_position():
    account = BracketAccount()
    shares = account.enter_long('ABC', decision_us=1_000_000,
        decision_close=10.01, budget=5_000.,
        quote=Quote(1_100_000, 1_090_000, 10., 10.01, 100., 500., True))
    result = apply_exit_quote_stream(account, 'ABC', decision_us=2_000_000,
        quotes=(_quote(2_100_000, bid_size=10.),))
    assert result.remaining_shares == shares-10
    assert account.positions['ABC'].shares == shares-10
    with pytest.raises(ValueError, match='increasing later buckets'):
        apply_exit_quote_stream(account, 'ABC', decision_us=2_100_000,
            quotes=(_quote(2_200_000), _quote(2_200_000)))
