import json
from types import SimpleNamespace
import numpy as np
import pytest
from research.vectorized_backtest.v66 import review_feature_cache as cache
from research.vectorized_backtest.v66 import shard_features


def test_exact_cache_and_fail_closed_source_and_output(tmp_path,monkeypatch):
    monkeypatch.setattr(cache,'require_runtime',lambda p:None)
    monkeypatch.setattr(cache,'write_json',lambda p,v:p.write_text(json.dumps(v)))
    bars=tmp_path/'source/bars/2026-01-05';bars.mkdir(parents=True)
    source=bars/'trade-1000.parquet';source.write_bytes(b'sealed')
    seal=bars/'complete.json';seal.write_text(json.dumps({'files':{source.name:cache.sha(source)}}))
    session=SimpleNamespace(root=tmp_path/'source',manifest={'shard_seals':{'2026-01-05':cache.sha(seal)}},
                            verified_files={('2026-01-05','trade-1000')})
    expected=np.zeros((2,len(shard_features.SPECS)),np.float32);expected[0,0]=np.nan
    calls=[]
    def prepare(*args):calls.append(1);return expected.copy()
    monkeypatch.setattr(shard_features,'materialize',prepare)
    args=(tmp_path/'cache',session,{'input_sha256':'training'},np.array(['A','A']),np.array([1,2]),np.array([1.,2.]))
    a,first=cache.materialize_cached(*args,reserve_bytes=0)
    b,second=cache.materialize_cached(*args,reserve_bytes=0)
    assert len(calls)==1 and not first['hit'] and second['hit']
    assert np.array_equal(a,b,equal_nan=True)
    source.write_bytes(b'changed')
    with pytest.raises(ValueError,match='source bars changed'):cache.materialize_cached(*args,reserve_bytes=0)
    source.write_bytes(b'sealed')
    path=tmp_path/'cache'/first['key']/'values.npy';path.write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='cache bytes changed'):cache.materialize_cached(*args,reserve_bytes=0)


def test_storage_reserve_prevents_materialization(tmp_path,monkeypatch):
    monkeypatch.setattr(cache,'require_runtime',lambda p:None)
    monkeypatch.setattr(cache.shutil,'disk_usage',lambda p:SimpleNamespace(free=0))
    monkeypatch.setattr(shard_features,'materialize',lambda *a:pytest.fail('must not prepare'))
    with pytest.raises(MemoryError,match='reserve'):
        cache.materialize_cached(tmp_path,SimpleNamespace(),{},np.array(['A']),np.array([1]),np.array([1.]))
