import polars as pl
import pytest

from research.rl_trading.v6.allocation import (first_eligible,
                                              intended_budgets, window_scores)


def test_sparse_window_reserves_first_future_episode_once():
    rows = pl.DataFrame({
        'time_us': [0, 1_000_000, 1_000_000, 10_000_000, 20_000_000],
        'ticker': ['A', 'A', 'B', 'C', 'D'],
        'listing_id': ['a', 'a', 'b', 'c', 'd'],
        'episode_uid': ['e1', 'e1', 'e2', 'e3', 'e4'],
        'score': [.02, .03, .01, .03, .04],
    })
    first = first_eligible(rows)
    assert first['episode_uid'].to_list() == ['e1', 'e2', 'e3', 'e4']
    summary = window_scores(first)
    assert summary['future_score'].to_list() == pytest.approx([.04, .03, .04, 0.])
    budget = intended_budgets(first, summary)
    assert round(budget['desired_budget'][0], 2) == 3333.33
    assert round(budget['future_reservation'][0], 2) == 6666.67
    assert budget['desired_budget'].max() <= 10_000


def test_empty_filtered_market_is_valid():
    rows = pl.DataFrame({'time_us': [0], 'ticker': ['A'],
                         'listing_id': ['a'], 'episode_uid': ['e1'],
                         'score': [.009]})
    first = first_eligible(rows)
    assert first.is_empty() and window_scores(first).is_empty()
