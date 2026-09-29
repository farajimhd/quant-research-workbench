import polars as pl

from research.rl_trading.v6.diagnostic_teacher import diagnose


def test_sparse_quote_diagnostic_reconciles_one_round_trip():
    allocations = pl.DataFrame({'episode_uid': ['a'], 'ticker': ['ABC'],
        'time_us': [1_000_000], 'exit_hint_us': [4_000_000],
        'decision_close': [10.], 'desired_budget': [1000.],
        'future_reservation': [0.], 'score': [.02], 'direction': [1]})
    entry = pl.DataFrame({'episode_uid': ['a'], 'quote_available': [True],
        'arrival_bucket_end_us': [1_100_000],
        'quote_timestamp_us': [1_050_000], 'bid_int': [99000],
        'ask_int': [100000], 'bid_size': [100.], 'ask_size': [100.]})
    exit_rows = pl.DataFrame({'episode_uid': ['a'], 'quote_available': [True],
        'arrival_bucket_end_us': [4_100_000],
        'quote_timestamp_us': [4_050_000], 'bid_int': [110000],
        'ask_int': [111000], 'bid_size': [100.], 'ask_size': [100.]})
    orders, ledger, report = diagnose(allocations, entry, exit_rows)
    assert orders.height == 2 and ledger.height == 1
    assert report['filled_entry_intentions'] == 1
    assert report['unresolved_open_positions'] == 0
    assert report['realized_net_pnl'] > 0
    assert report['scope'] == 'realized_modeled_quotes_only_not_full_session_profit'
