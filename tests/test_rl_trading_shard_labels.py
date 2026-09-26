import json

import numpy as np

from research.rl_trading.v1.features import FEATURE_NAMES, SECONDS
from research.rl_trading.v1.shard_labels import pack


def test_teacher_actions_align_with_changing_market_and_held_slot():
    bank = np.zeros((2,SECONDS,len(FEATURE_NAMES)),dtype=np.float32)
    bank[:,:,FEATURE_NAMES.index('price_available')] = 1
    bank[:,:,FEATURE_NAMES.index('log_trades_60s')] = np.log1p(20)
    volume = np.zeros((2,SECONDS),dtype=np.float64)
    volume[:,0] = [100,10]
    volume[:,1] = [1,200]
    execution = np.ones((2,SECONDS,3),dtype=np.float64)
    execution[0,0] = (10.,10.,10.)
    execution[0,1] = (11.,11.,11.)
    left = 10_000_000
    lot = dict(ticker='A',quantity=5.,entry_price=10.,capital_per_share=10.,entry_us=left)
    trajectory = [
        dict(time_us=left,cash_before=100.,equity_before=100.,reward=0.,
            return_to_go=5.,done=False,
            action_legs_json=json.dumps([dict(action='buy',ticker='A',quantity=5.,
                price=10.,capital=50.,entry_us=left)]),
            positions_after_json=json.dumps([lot])),
        dict(time_us=left+1_000_000,cash_before=50.,equity_before=105.,reward=5.,
            return_to_go=0.,done=True,
            action_legs_json=json.dumps([dict(action='sell',ticker='A',quantity=5.,
                price=11.,capital=0.,entry_us=left)]),
            positions_after_json='[]'),
    ]
    result = pack(trajectory,bank,volume,execution,['A','B'],left_us=left,top_n=1,
        max_lots=1,max_orders=1,allocation_step=50.,initial_cash=100.,
        min_volume=0.,min_trades=0)
    assert result['slots'].tolist() == [[0],[0]]
    assert result['rank'].tolist() == [[0],[1]]
    assert result['held_slots'].tolist() == [[False],[True]]
    assert result['actions'].tolist() == [[1],[2]]
    assert result['action_mask'][1,0,2]
    assert np.isclose(result['account'][1,1],1.05)
