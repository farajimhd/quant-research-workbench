from datetime import date
from pathlib import Path

import numpy as np
import pytest
import torch

from research.rl_trading.v1.common import bounds, digest, file_hash
from research.rl_trading.v1.features import FEATURE_NAMES, SECONDS
from research.rl_trading.v1 import train, evaluate_supervised, evaluate_replay
from src.market_engine.level_book_store import write


def _shard(root: Path, day: date):
    root.mkdir()
    left,_ = bounds(day)
    plan = dict(date=str(day),tickers=['A'],top_n=1,history_seconds=4,max_lots=1,
        max_orders=1,segment=True,feature_names=FEATURE_NAMES,initial_cash=100.,
        account_clock='current_completed_second',
        allocation_step=50.,liquidity_filter=dict(min_volume_60s=0.,min_trades_60s=0))
    plan['plan_hash'] = digest(plan)
    write(root/'plan.json',plan)
    arrays = dict(features=np.ones((1,SECONDS,len(FEATURE_NAMES)),dtype=np.float32),
        volume_60s=np.ones((1,SECONDS),dtype=np.float64),
        execution=np.ones((1,SECONDS,3),dtype=np.float64),
        closeable=np.ones((1,SECONDS),dtype=np.bool_),
        time_us=np.asarray([left,left+1_000_000],dtype=np.int64),
        slots=np.zeros((2,1),dtype=np.int32),rank=np.zeros((2,1),dtype=np.int32),
        held_slots=np.zeros((2,1),dtype=np.bool_),
        actions=np.asarray([[1],[0]],dtype=np.int16),
        action_mask=np.asarray([[[True,True,False]],[[True,True,False]]],dtype=np.bool_),
        lots=np.zeros((2,1,3),dtype=np.float32),lot_slots=np.full((2,1),-1,dtype=np.int16),
        account=np.ones((2,3),dtype=np.float32),reward=np.zeros(2,dtype=np.float32),
        return_to_go=np.zeros(2,dtype=np.float32),done=np.asarray([False,True]))
    hashes = {}
    for name,array in arrays.items():
        path = root/(name+'.npy')
        np.save(path,array)
        hashes[path.name] = file_hash(path)
    write(root/'complete.json',dict(plan_hash=plan['plan_hash'],rows=2,
        teacher_optimality='approximate_beam',teacher_profit=0.,files=hashes))
    return root


def test_flat_or_losing_replay_cannot_be_selected_as_profitable():
    report = {'val_profit': 0., 'validation': [{'buys': 0}]}
    assert not train._eligible_replay(report)
    report['val_profit'] = -1.
    report['validation'][0]['buys'] = 1
    assert not train._eligible_replay(report)
    report['val_profit'] = 1.
    assert train._eligible_replay(report)


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required by training contract')
def test_cuda_training_launcher_reads_disk_shards_and_checkpoints(tmp_path,monkeypatch):
    train_root = _shard(tmp_path/'train',date(2026,8,20))
    val_root = _shard(tmp_path/'val',date(2026,8,21))
    monkeypatch.setenv('QW_RUNTIME_ROOT',str(tmp_path))
    assert train.main(['--train-shards',str(train_root),'--val-shards',str(val_root),
        '--run-name','smoke','--epochs','1','--batch-size','2','--d-model','32',
        '--layers','1','--heads','4','--max-steps','1','--allow-segment',
        '--value-weight','0.03','--no-require-gpu-bound','--wandb-mode','disabled']) == 0
    root = tmp_path/'rl-trading'/'v1'/'train'/'smoke'
    assert (root/'run_manifest.json').is_file()
    assert (root/'checkpoints'/'checkpoint_latest.pt').is_file()
    assert (root/'checkpoints'/'checkpoint_best_val.pt').is_file()
    assert not (root/'checkpoints'/'checkpoint_best_replay.pt').exists()
    assert (root/'closed_loop_epoch_001.json').is_file()
    test_root = _shard(tmp_path/'test',date(2026,8,22))
    assert evaluate_supervised.main(['--run',str(root),'--test-shards',str(test_root),
        '--batch-size','2','--allow-segment']) == 0
    assert evaluate_replay.main(['--run',str(root),'--test-shards',str(test_root),
        '--allow-segment','--max-seconds','2','--checkpoint','best-val']) == 0
