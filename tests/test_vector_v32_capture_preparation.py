import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import torch

from research.vectorized_backtest.v32.runtime import shared_compiler_cache
from research.vectorized_backtest.v32.io_pipeline import TransferStaging


def test_compiler_cache_is_source_bound_short_and_concurrent(monkeypatch,tmp_path):
    from research.vectorized_backtest.v6.torch_backtest import runtime
    monkeypatch.setattr(runtime,'require_runtime',lambda p:tmp_path/p.name)
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths=list(pool.map(shared_compiler_cache,['a'*64]*8))
    assert len(set(paths))==1
    assert len(paths[0].name)==20
    assert json.loads((paths[0]/'identity.json').read_bytes())['source_sha256']=='a'*64
    assert shared_compiler_cache('b'*64)!=paths[0]
    (paths[0]/'identity.json').write_text('{}')
    with pytest.raises(ValueError,match='identity mismatch'):shared_compiler_cache('a'*64)
    with pytest.raises(ValueError,match='source SHA256'):shared_compiler_cache('unknown')


@pytest.mark.skipif(not torch.cuda.is_available(),reason='Pinned arena requires CUDA')
def test_background_staging_exact_and_reuses_arena():
    data=SimpleNamespace(host_tensors={'a':torch.arange(33,dtype=torch.float64).reshape(3,11)},
                         resident_features=SimpleNamespace(host={'b':torch.arange(7,dtype=torch.int32)}))
    with TransferStaging() as staging:
        first=staging.begin(data).result(timeout=10)
        assert torch.equal(first[0]['a'],data.host_tensors['a'])
        assert torch.equal(first[1]['b'],data.resident_features.host['b'])
        assert first[0]['a'].is_pinned()
        pointer=staging.slots[0]['buffer'].data_ptr()
        data.host_tensors['a'].add_(1)
        second=staging.begin(data).result(timeout=10)
        assert torch.equal(second[0]['a'],data.host_tensors['a'])
        assert staging.slots[0]['buffer'].data_ptr()==pointer
