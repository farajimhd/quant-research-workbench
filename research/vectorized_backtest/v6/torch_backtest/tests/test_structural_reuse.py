import json
import shutil
from pathlib import Path
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.prepare import tape_fingerprint
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
from research.vectorized_backtest.v6.torch_backtest.structural import FIELDS,_signature,_cache_key
from research.vectorized_backtest.v6.torch_backtest.structural_reuse import DenseCacheReuse,compatible_algorithm


def artifacts(tmp_path):
    source=Path(__file__).resolve().parents[5]
    old=tmp_path/'old';package=Path('research/vectorized_backtest/v4/torch_backtest')
    files=[Path('src/backend/fixed_v7_stream.py'),Path('src/backend/structural_v7_seed.py'),
           Path('src/trading_runtime/strategy_one_v7.py')]
    files.extend(p.relative_to(source) for p in (source/'src/market_engine').glob('*.py'))
    for name in files:
        (old/name).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/name,old/name)
    for name in ('structural.py','offline_structure.py','reference_prefetch.py'):
        (old/package).mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source/'research/vectorized_backtest/v6/torch_backtest'/name,old/package/name)
    algorithm,_=compatible_algorithm(old)
    day='2026-07-30';execution=tmp_path/'execution';execution.mkdir()
    clocks=np.array([1,2,3],dtype=np.int64);asks=np.array([2.,2.2,2.4])
    provenance=dict(source_key='certified-full-history',fingerprint='unused')
    provenance['fingerprint']=tape_fingerprint(provenance)
    torch.save(dict(clocks=torch.from_numpy(clocks),ask=torch.from_numpy(asks[:,None]),tickers=('X',),provenance=provenance),execution/'tape.pt')
    (execution/'receipt.json').write_text(json.dumps(dict(identity=dict(session=dict(day=day)),sha256=file_hash(execution/'tape.pt'),source_fingerprint=provenance['fingerprint'])))
    rows={name:np.array([1,3],dtype=np.int64) for name in FIELDS}
    rows['time_us']*=1000000;rows['volume_1000']=np.array([1.,3.])
    seed=dict(checkpoint_hash='seed',source_checkpoint_hash='source-seed',session='2026-07-29',available_at=0.)
    signature=_signature('X',day,seed,[],rows,asks,clocks,provenance['source_key'],algorithm)
    cache=tmp_path/'cache';entry=cache/_cache_key(signature);entry.mkdir(parents=True)
    targets=np.full((3,15),np.inf);targets[:,0]=[3.,4.,5.]
    np.savez_compressed(entry/'arrays.npz',targets=targets,valid=np.ones(3,dtype=bool))
    (entry/'complete.json').write_text(json.dumps(dict(signature=signature,array_hash=file_hash(entry/'arrays.npz'))))
    return execution,cache,old,day,clocks,seed,rows,asks,entry,targets


def test_exact_dense_cache_reindexes_only_matching_causal_observations(tmp_path):
    execution,cache,old,day,clocks,seed,rows,asks,entry,targets=artifacts(tmp_path)
    reuse=DenseCacheReuse(execution,cache,old,day=day,clocks=clocks)
    result,reason=reuse.lookup('X',day,seed,[],rows,asks[[0,2]],clocks[[0,2]])
    assert reason is None
    np.testing.assert_array_equal(result[0],targets[[0,2]])
    assert result[2]['status']=='exact_dense_cache_reused'
    changed=dict(seed,checkpoint_hash='different-opening-state')
    assert reuse.lookup('X',day,changed,[],rows,asks[[0,2]],clocks[[0,2]])[1]=='exact_dense_signature_not_cached'
    assert reuse.lookup('X',day,seed,[],rows,np.array([2.,9.]),clocks[[0,2]])[1]=='current_observation_quotes_differ'
    assert reuse.lookup('Y',day,seed,[],rows,asks[[0,2]],clocks[[0,2]])[1]=='listing_not_in_legacy_universe'
    with (entry/'arrays.npz').open('ab') as handle:handle.write(b'changed')
    with pytest.raises(ValueError,match='integrity mismatch'):
        reuse.lookup('X',day,seed,[],rows,asks[[0,2]],clocks[[0,2]])


def test_engine_changes_and_dense_snapshot_changes_fail_closed(tmp_path):
    execution,cache,old,day,clocks,*_=artifacts(tmp_path)
    with (execution/'tape.pt').open('ab') as handle:handle.write(b'changed')
    with pytest.raises(ValueError,match='snapshot bytes changed'):
        DenseCacheReuse(execution,cache,old,day=day,clocks=clocks)
    with (old/'src/backend/fixed_v7_stream.py').open('ab') as handle:handle.write(b'changed')
    with pytest.raises(ValueError,match='engine bytes differ'):
        compatible_algorithm(old)
