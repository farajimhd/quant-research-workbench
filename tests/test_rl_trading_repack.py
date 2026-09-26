from datetime import date

import numpy as np

from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v1.data import SessionShard
from research.rl_trading.v1.repack_shards import repack
from src.market_engine.level_book_store import read, write
from test_rl_trading_train import _shard


def test_overlay_marks_current_equity_without_changing_certified_source(tmp_path,monkeypatch):
    root = _shard(tmp_path/'source',date(2026,8,22))
    plan = read(root/'plan.json')
    plan.pop('account_clock')
    plan['version'] = 'rl-trading-structural-shards-v3'
    plan.pop('plan_hash')
    plan['plan_hash'] = digest(plan)
    write(root/'plan.json',plan,immutable=False)
    for name in ('execution','lots','lot_slots','account'):
        array = np.load(root/(name+'.npy'),mmap_mode='r+')
        if name == 'execution':
            array[0,0] = (10.,10.,10.)
            array[0,1] = (12.,12.,12.)
        elif name == 'lots':
            array[1,0] = (5.,10.,1/3600)
        elif name == 'lot_slots':
            array[1,0] = 0
        else:
            array[1,0] = .5
            array[1,1] = 1.
        array.flush()
    complete = read(root/'complete.json')
    complete['plan_hash'] = plan['plan_hash']
    complete['teacher_profit'] = 10.
    for name in ('execution','lots','lot_slots','account'):
        complete['files'][name+'.npy'] = file_hash(root/(name+'.npy'))
    write(root/'complete.json',complete,immutable=False)
    source_account_hash = complete['files']['account.npy']
    monkeypatch.setenv('QW_RUNTIME_ROOT',str(tmp_path))

    derived = repack(root)
    shard = SessionShard(derived)
    assert shard.plan['account_clock'] == 'current_completed_second'
    assert np.isclose(shard.arrays['account'][1,1],1.1)
    assert np.isclose(np.load(root/'account.npy')[1,1],1.)
    assert file_hash(root/'account.npy') == source_account_hash
    assert repack(root) == derived
