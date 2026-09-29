import numpy as np
import pytest

from research.rl_trading.v1.v5_replay import Intent, ReplayAccount, ReplayGrid
from research.rl_trading.v2.config import Config


def _grid(*, arrival_volume=1_000.):
    return ReplayGrid(('A',),
        close=np.asarray([[10., 10., 12., 12.]]),
        next_open=np.asarray([[10., 10., 12., 12.]]),
        volume=np.asarray([[1_000., arrival_volume, 1_000., 1_000.]]),
        fresh=np.asarray([[True, True, True, True]]),
        prior_close=np.asarray([10.]),
        estimated_reference=np.zeros((1, 4)))


def _config(*, max_volume_participation=1.):
    return Config(initial_cash=100., max_volume_participation=max_volume_participation,
                  commission_model='research', base_slippage_ratio=0.,
                  impact_ratio=0., volatility_slippage_ratio=0.)


def test_v5_replay_compounds_cash_and_reconciles_completed_position():
    account = ReplayAccount(_grid(), _config())
    account.advance([Intent(0, 1, .5)])
    assert account.cash == 50. and account.positions[0].shares == 5
    account.advance([])
    assert account.equity(2) == 110.
    account.advance([Intent(0, -1)])
    result = account.summary()
    assert result['net_profit'] == 10.
    assert result['realized_position_pnl'] == 10.
    assert result['open_marked_pnl'] == 0.
    assert result['fees'] == result['max_drawdown'] == 0.
    assert result['buy_fills'] == result['sell_fills'] == 1
    assert result['share_weighted_holding_seconds'] == 2.
    assert result['valid_terminal']
    assert result['periods'][0]['marked_net_profit'] == 10.
    assert account.position_ledger[0]['ticker'] == 'A'


def test_v5_replay_records_partial_execution_and_rejects_unavailable_bar():
    account = ReplayAccount(_grid(arrival_volume=20.),
                            _config(max_volume_participation=.1))
    account.advance([Intent(0, 1, .5)])
    assert account.positions[0].shares == 2
    assert account.order_trace[0]['status'] == 'partial'
    account.advance([])
    account.advance([Intent(0, -1)])
    assert account.summary()['net_profit'] == 4.
    grid = ReplayGrid(('A',), close=np.asarray([[10., 10.]]),
        next_open=np.asarray([[10., 0.]]), volume=np.asarray([[100., 0.]]),
        fresh=np.asarray([[True, False]]), prior_close=np.asarray([10.]),
        estimated_reference=np.zeros((1, 2)))
    missing = ReplayAccount(grid, _config())
    missing.advance([Intent(0, 1, .5)])
    assert missing.summary()['unfilled_orders'] == 1
    assert missing.cash == 100.


def test_v5_replay_fails_closed_on_duplicate_or_overspending_intents():
    account = ReplayAccount(_grid(), _config())
    with pytest.raises(ValueError, match='duplicate'):
        account.advance([Intent(0, 1, .5), Intent(0, 1, .5)])
    with pytest.raises(ValueError, match='Invalid'):
        account.advance([Intent(0, -1)])
    assert account.second == 0 and account.cash == 100.


def test_v5_replay_allows_sell_then_new_buy_of_same_listing():
    account = ReplayAccount(_grid(), _config())
    account.advance([Intent(0, 1, .5)])
    account.advance([Intent(0, -1), Intent(0, 1, .5)])
    assert account.summary()['completed_position_rows'] == 1
    assert account.positions[0].shares == 4
    assert account.cash == 62.
    assert account.summary()['net_profit'] == 10.
