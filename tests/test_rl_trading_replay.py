from datetime import date

import numpy as np

from research.rl_trading.v1.common import file_hash
from research.rl_trading.v1.data import SessionShard
from research.rl_trading.v1.replay import replay_session, _rank_chunk
from src.market_engine.level_book_store import read, write
from test_rl_trading_train import _shard


def test_replay_uses_own_cash_and_forced_terminal_liquidation(tmp_path):
    root = _shard(tmp_path/'session',date(2026,8,22))
    path = root/'execution.npy'
    prices = np.load(path,mmap_mode='r+')
    prices[0,0] = (10.,10.,10.)
    prices[0,1] = (12.,12.,12.)
    prices.flush()
    complete = read(root/'complete.json')
    complete['files']['execution.npy'] = file_hash(path)
    write(root/'complete.json',complete,immutable=False)
    shard = SessionShard(root)

    def buy_first_second(state):
        def select(step,mask,previous):
            return 1 if state['index'] == 0 else 0
        return select

    result = replay_session(shard,buy_first_second)
    assert result['complete']
    assert result['terminal_cash'] == 110.
    assert result['profit'] == 10.
    assert result['buys'] == 1
    assert result['forced_liquidations'] == 1


def test_replay_volume_order_breaks_ties_by_stable_ticker_identity():
    volume = np.asarray([[10.,5.],[10.,20.],[1.,2.]])
    ranked = _rank_chunk(volume,['Z','A','M'],np.asarray([0,1]))
    assert ranked.tolist() == [[1,0,2],[1,0,2]]
