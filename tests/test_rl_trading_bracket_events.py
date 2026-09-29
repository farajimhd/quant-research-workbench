import polars as pl
import pytest

from research.rl_trading.v1.bracket_events import bracket_events


def _bars(lows, highs):
    return pl.DataFrame({'boundary_us': [1_100_000, 1_200_000, 1_300_000],
                         'low': lows, 'high': highs,
                         'extremes_valid': [True] * 3})


def _levels(volumes):
    return pl.DataFrame({'boundary_us': [1_100_000, 1_200_000, 1_300_000],
                         'price_int': [110_000] * 3,
                         'execution_volume': volumes})


def test_sparse_target_liquidity_caps_partial_fills_before_stop():
    events = bracket_events(ticker='A', fill_us=1_000_000,
                            exit_us=1_300_000, shares=5., stop=9., target=11.,
                            buckets=_bars([9.5, 9.4, 8.9], [11.2, 11.3, 10.]),
                            price_levels=_levels([2., 2., 100.]))
    assert events['event'].to_list() == ['target_touch', 'target_touch', 'stop_trigger']
    assert events['target_fill_cap'].to_list() == [2., 2., 0.]
    assert events['remaining_cap'].to_list() == [3., 1., 1.]


def test_same_bucket_stop_and_target_is_explicitly_ambiguous():
    events = bracket_events(ticker='A', fill_us=1_000_000,
                            exit_us=1_300_000, shares=5., stop=9., target=11.,
                            buckets=_bars([9.5, 8.9, 9.4], [11.2, 11.3, 11.2]),
                            price_levels=_levels([2., 100., 100.]))
    assert events['event'].to_list() == ['target_touch', 'ambiguous_stop_target']
    assert events['target_fill_cap'].to_list() == [2., 0.]
    assert events['remaining_cap'].to_list() == [3., 3.]


def test_full_target_fill_cancels_later_stop():
    events = bracket_events(ticker='A', fill_us=1_000_000,
                            exit_us=1_300_000, shares=5., stop=9., target=11.,
                            buckets=_bars([9.5, 9.4, 8.9], [11.2, 11.3, 10.]),
                            price_levels=_levels([2., 10., 100.]))
    assert events['event'].to_list() == ['target_touch', 'target_touch']
    assert events['target_fill_cap'].to_list() == [2., 3.]


def test_target_high_without_execution_at_limit_gives_no_fill_cap():
    levels = pl.DataFrame({'boundary_us': [1_100_000],
                           'price_int': [105_000], 'execution_volume': [20.]})
    events = bracket_events(ticker='A', fill_us=1_000_000,
                            exit_us=1_300_000, shares=5., stop=9., target=11.,
                            buckets=_bars([9.5] * 3, [11.1, 10., 10.]),
                            price_levels=levels)
    assert events['target_fill_cap'].to_list() == [0.]


def test_price_levels_must_match_selected_100ms_buckets():
    levels = pl.DataFrame({'boundary_us': [1_400_000],
                           'price_int': [110_000], 'execution_volume': [20.]})
    with pytest.raises(ValueError, match='Invalid or duplicate'):
        bracket_events(ticker='A', fill_us=1_000_000,
                       exit_us=1_300_000, shares=5., stop=9., target=11.,
                       buckets=_bars([9.5] * 3, [10.] * 3), price_levels=levels)
