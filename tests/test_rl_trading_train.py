from datetime import date
from pathlib import Path

import numpy as np
import pytest
import torch

from research.rl_trading.v1.common import bounds, digest, file_hash
from research.rl_trading.v1.features import FEATURE_NAMES, SECONDS
from research.rl_trading.v1 import train, evaluate_supervised, evaluate_replay
from research.rl_trading.v1.data import SessionShard
from research.rl_trading.v1.model import MarketPolicy
from src.market_engine.level_book_store import write


@pytest.fixture
def allow_cuda_pooling():
    previous = torch.are_deterministic_algorithms_enabled()
    torch.use_deterministic_algorithms(False)
    try:
        yield
    finally:
        torch.use_deterministic_algorithms(previous)


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


def test_streamed_shard_releases_touched_maps_without_changing_values(tmp_path):
    shard = SessionShard(_shard(tmp_path/'day',date(2026,8,20)))
    before = shard.arrays['features']
    expected = float(before[0,0,0])
    shard.release_mapped_pages()
    assert before._mmap.closed
    assert shard.arrays['features'] is not before
    assert float(shard.arrays['features'][0,0,0]) == expected
    shard.release_mapped_pages()
    assert float(shard.arrays['features'][0,0,0]) == expected


def test_feature_cache_matches_certified_float16_values_and_rejects_mutation(tmp_path):
    shard = SessionShard(_shard(tmp_path/'day',date(2026,8,20)))
    cache_root = tmp_path/'runtime-cache'
    certificate = shard.build_feature_cache(cache_root)
    assert certificate['source_feature_hash'] == shard.complete['files']['features.npy']
    assert shard.feature_cache.dtype == np.float16
    assert np.array_equal(shard.feature_cache,
        shard.arrays['features'].astype(np.float16))
    old = shard.feature_cache
    shard.release_mapped_pages()
    assert old._mmap.closed
    assert np.array_equal(shard.feature_cache,
        shard.arrays['features'].astype(np.float16))
    shard.attach_feature_cache(cache_root)
    cached = cache_root/shard.plan['plan_hash']/'features.npy'
    with cached.open('r+b') as handle:
        handle.seek(-1,2)
        handle.write(b'X')
    with pytest.raises(ValueError,match='cache hash'):
        shard.attach_feature_cache(cache_root)


def test_warm_start_transfers_ticker_identity_by_name(tmp_path):
    source = tmp_path/'source'
    (source/'checkpoints').mkdir(parents=True)
    source_shard = tmp_path/'source-shard'
    source_shard.mkdir()
    contract = dict(top_n=1,history_seconds=4,max_lots=1,max_orders=1,
        feature_names=FEATURE_NAMES)
    write(source_shard/'plan.json',contract)
    config = dict(config_hash='source-hash',feature_names=list(FEATURE_NAMES),
        train_shards=[str(source_shard)],training=dict(epochs=2))
    write(source/'config.json',config)
    write(source/'best_closed_loop.json',dict(config_hash='source-hash',epoch=2,
        val_profit=1.,validation=[{'buys':1}]))
    old_vocab = {'A':1,'B':2}
    old_model = MarketPolicy(features=len(FEATURE_NAMES),tickers=2,top_n=1,
        max_lots=1,max_orders=1,d_model=16,layers=1,heads=4)
    with torch.no_grad():
        old_model.identity.weight[1].fill_(.25)
        old_model.identity.weight[2].fill_(.5)
        old_model.identity.weight[-1].fill_(.75)
    saved = dict(config_hash='source-hash',epoch=1,model=old_model.state_dict(),
        ticker_vocabulary=old_vocab)
    torch.save(saved,source/'checkpoints/checkpoint_best_replay.pt')
    torch.save(saved,source/'checkpoints/checkpoint_latest.pt')
    new_vocab = {'B':1,'C':2,'A':3}
    model = MarketPolicy(features=len(FEATURE_NAMES),tickers=3,top_n=1,
        max_lots=1,max_orders=1,d_model=16,layers=1,heads=4)
    new_before = model.identity.weight[2].detach().clone()
    result = train._warm_start_model(model,new_vocab,contract,
        source/'checkpoints/checkpoint_best_replay.pt')
    assert result['reused_tickers'] == 2 and result['new_tickers'] == 1
    assert torch.all(model.identity.weight[1] == .5)
    assert torch.all(model.identity.weight[3] == .25)
    assert torch.all(model.identity.weight[-1] == .75)
    assert torch.equal(model.identity.weight[2],new_before)

    config['training']['epochs'] = 3
    write(source/'config.json',config,immutable=False)
    (source/'STOP').write_text('validation overfit\n')
    (source/'metrics.jsonl').write_text('{"step":2}\n')
    with pytest.raises(FileNotFoundError):
        train._warm_start_model(model,new_vocab,contract,
            source/'checkpoints/checkpoint_best_replay.pt')
    certificate = dict(version='rl-trading-early-stop-v1',config_hash='source-hash',
        completed_epochs=2,planned_epochs=3,
        checkpoint_hash=file_hash(source/'checkpoints/checkpoint_latest.pt'),
        best_replay_hash=file_hash(source/'checkpoints/checkpoint_best_replay.pt'),
        stop_hash=file_hash(source/'STOP'),metrics_hash=file_hash(source/'metrics.jsonl'))
    write(source/'early_stop_complete.json',certificate)
    assert train._warm_start_model(model,new_vocab,contract,
        source/'checkpoints/checkpoint_best_replay.pt')['source_epoch'] == 2
    (source/'metrics.jsonl').write_text('{"step":3}\n')
    with pytest.raises(ValueError,match='early-stop certificate'):
        train._warm_start_model(model,new_vocab,contract,
            source/'checkpoints/checkpoint_best_replay.pt')


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required by training contract')
def test_cuda_training_launcher_reads_disk_shards_and_checkpoints(tmp_path,monkeypatch,
                                                                   allow_cuda_pooling):
    train_root = _shard(tmp_path/'train',date(2026,8,20))
    val_root = _shard(tmp_path/'val',date(2026,8,21))
    monkeypatch.setenv('QW_RUNTIME_ROOT',str(tmp_path))
    assert train.main(['--train-shards',str(train_root),'--val-shards',str(val_root),
        '--run-name','smoke','--epochs','1','--batch-size','2','--d-model','32',
        '--layers','1','--heads','4','--max-steps','1','--allow-segment',
        '--data-mode','session_stream',
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
