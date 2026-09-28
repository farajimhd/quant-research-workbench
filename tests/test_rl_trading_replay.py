from datetime import date
from types import SimpleNamespace

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

    trace = {}
    result = replay_session(shard,buy_first_second,trace=trace)
    assert result['complete']
    assert result['terminal_cash'] == 110.
    assert result['profit'] == 10.
    assert result['buys'] == 1
    assert result['forced_liquidations'] == 1
    assert result['position_seconds'] == 1
    assert result['exposure_seconds'] == 1
    assert result['max_open_lots'] == 1
    assert result['average_holding_seconds'] == 1.
    assert result['median_holding_seconds'] == 1.
    assert result['p90_holding_seconds'] == 1.
    assert result['closed_positions'] == 1
    assert result['winning_positions'] == 1
    assert result['turnover_notional'] == 110.
    assert len(trace['orders']) == 2
    assert trace['positions'] == [dict(ticker='A',entry_us=int(shard.arrays['time_us'][0]),
        exit_us=int(shard.arrays['time_us'][1]),quantity=5.,entry_price=10.,
        exit_price=12.,entry_fee=0.,exit_fee=0.,gross_pnl=10.,net_pnl=10.,
        holding_seconds=1.,forced_exit=True)]


def test_replay_volume_order_breaks_ties_by_stable_ticker_identity():
    volume = np.asarray([[10.,5.],[10.,20.],[1.,2.]])
    ranked = _rank_chunk(volume,['Z','A','M'],np.asarray([0,1]))
    assert ranked.tolist() == [[1,0,2],[1,0,2]]


def test_replay_sell_token_uses_teacher_sorted_lot_order():
    from research.rl_trading.v1.common import bounds
    from research.rl_trading.v1.features import FEATURE_NAMES, SECONDS
    left,_ = bounds(date(2026,8,22))
    features = np.zeros((2,SECONDS,len(FEATURE_NAMES)),dtype=np.float32)
    features[:,:,FEATURE_NAMES.index('log_trades_60s')] = np.log1p(20)
    execution = np.full((2,SECONDS,3),10.,dtype=np.float64)
    execution[0,1] = (20.,20.,20.)  # A
    execution[0,2] = (1.,1.,1.)
    volume = np.zeros((2,SECONDS),dtype=np.float64)
    volume[1] = 100.  # Z ranks before A.
    shard = SimpleNamespace(plan=dict(date='2026-08-22',tickers=['A','Z'],
        top_n=2,max_lots=2,max_orders=2,initial_cash=100.,allocation_step=50.,
        liquidity_filter=dict(min_volume_60s=0.,min_trades_60s=0)),
        complete=dict(rows=3),arrays=dict(time_us=np.asarray(
            [left,left+1_000_000,left+2_000_000],dtype=np.int64),
            done=np.asarray([False,False,True]),features=features,
            execution=execution,closeable=np.ones((2,SECONDS),dtype=np.bool_),
            volume_60s=volume))

    def teacher(state):
        def select(step,mask,previous):
            return ([1,2] if state['index'] == 0 else [3,0])[step]
        return select

    trace = {}
    result = replay_session(shard,teacher,trace=trace)
    assert result['profit'] == 50.
    assert result['sells'] == 2
    assert len(trace['positions']) == 2
    assert [item['forced_exit'] for item in trace['positions']] == [False,True]
    assert sum(item['net_pnl'] for item in trace['positions']) == result['profit']


def test_training_replay_persists_position_ledger(tmp_path,monkeypatch):
    import research.rl_trading.v1.train as train
    root = _shard(tmp_path/'session',date(2026,8,22))
    shard = SessionShard(root)

    class Model:
        def eval(self): pass
        def train(self): pass

    monkeypatch.setattr(train,'_session_data',lambda *_: None)
    monkeypatch.setattr(train,'ModelSelector',lambda *_:
        lambda state:lambda step,mask,previous: 1 if state['index'] == 0 else 0)
    reports = train._run_closed_loop(Model(),[shard],None,None,None,
        trace_root=tmp_path/'trace',split='val',epoch=3,config_hash='test-hash')
    assert len(reports) == 1
    artifact = read(reports[0]['trace_file'])
    assert artifact['config_hash'] == 'test-hash'
    assert artifact['epoch'] == 3
    assert len(artifact['orders']) == 2
    assert len(artifact['positions']) == 1
    assert artifact['summary']['profit'] == sum(x['net_pnl'] for x in artifact['positions'])
