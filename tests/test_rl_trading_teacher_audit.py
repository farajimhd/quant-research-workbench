import json

import polars as pl

from research.rl_trading.v1.audit_dynamic_teacher import _prior_orders


def test_prior_order_audit_counts_lots_and_distinct_entry_seconds(tmp_path):
    path = tmp_path/'trajectory.parquet'
    legs = [dict(action='buy', ticker='ABC', fee=.5, realized_pnl=0),
            dict(action='buy', ticker='ABC', fee=.5, realized_pnl=0),
            dict(action='sell', ticker='ABC', fee=.6, realized_pnl=2),
            dict(action='sell', ticker='ABC', fee=.6, realized_pnl=2)]
    pl.DataFrame({'time_us':[1,2], 'action':['trade','trade'],
        'action_legs_json':[json.dumps(legs[:2]),json.dumps(legs[2:])]}).write_parquet(path)
    result = _prior_orders(path)
    assert result['buys'] == result['sells'] == 2
    assert result['unique_ticker_entry_seconds'] == 1
    assert abs(result['fees']-2.2) < 1e-12
    assert result['realized_net_from_legs'] == 4
