"""Restartable sparse-label fragment compilation."""
import polars as pl

from research.rl_trading.v6.build import (_combine_fragments,
                                          _save_fragment)


def test_sparse_fragments_are_bound_and_reconciled(tmp_path):
    episodes = pl.DataFrame({'ticker': ['ABC'], 'episode_id': [1],
                             'entry_hint_us': [5]})
    candidates = pl.DataFrame({'time_us': [5], 'ticker': ['ABC'],
                               'episode_id': [1], 'score': [.02]})
    report = {'ticker': 'ABC', 'candles': 10}
    _save_fragment(tmp_path, 'id-abc', episodes, candidates, report)
    _save_fragment(tmp_path, 'id-abc', episodes, candidates, report)
    combined = _combine_fragments(tmp_path, ['id-abc'])
    assert combined['totals'] == {'candles': 10, 'episodes': 1,
                                   'candidates': 1, 'zero_candle_listings': 0}
    assert pl.read_parquet(tmp_path / 'candidates.parquet')['score'][0] == .02
