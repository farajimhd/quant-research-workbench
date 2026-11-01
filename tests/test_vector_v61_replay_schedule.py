import pytest
from research.vectorized_backtest.v61.replay_schedule import chunk_clocks


@pytest.mark.parametrize('listings',[1,10,128,137,549,1024])
def test_decision_chunks_cannot_overflow_even_with_two_orders_per_clock(listings):
    block=32;orders=max(8192,2*block*listings);episodes=max(65536,block*listings)
    clocks=chunk_clocks(block,listings,orders,episodes)
    assert clocks%block==0 and 32<=clocks<=512
    assert 2*clocks*listings<=orders
    assert clocks*listings<=episodes


def test_tail_groups_clocks_without_changing_candidate_or_listing_dimensions():
    assert chunk_clocks(32,549,35136,65536,tail=True)==512


def test_insufficient_journal_fails_before_any_replay():
    with pytest.raises(ValueError):chunk_clocks(32,137,8192,65536)
