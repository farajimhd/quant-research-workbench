import polars as pl
import pytest
from hashlib import sha256
import json

from research.rl_trading.v6.allocation import (first_eligible,
                                              intended_budgets, window_scores,
                                              certify_from_candidates)


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


def test_certified_sidecar_reuses_sparse_candidates(tmp_path):
    source, output = tmp_path / 'source', tmp_path / 'sidecar'
    source.mkdir()
    rows = pl.DataFrame({'time_us': [0], 'ticker': ['A'],
                         'listing_id': ['a'], 'episode_uid': ['e1'],
                         'score': [.02]})
    path = source / 'candidates.parquet'
    rows.write_parquet(path)
    (source / 'complete.json').write_text(json.dumps({
        'status': 'complete', 'outputs': {'candidates': {
            'rows': 1, 'sha256': sha256(path.read_bytes()).hexdigest()}}}))
    report = certify_from_candidates(source, output)
    assert report['rows'] == 1
    assert certify_from_candidates(source, output) == report
    assert (pl.read_parquet(output / 'intended_allocations.parquet')
            ['desired_budget'][0] == 10_000)
