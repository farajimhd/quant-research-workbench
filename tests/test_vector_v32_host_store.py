from types import SimpleNamespace
from pathlib import Path
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v32.host_store import HostStore


def fixture(root):
    source=root/'source';source.write_bytes(b'certified')
    s=source.stat()
    path=root/'original.npy';np.save(path,np.arange(16,dtype=np.float64));mapped=np.load(path,mmap_mode='r')
    stat=path.stat()
    return SimpleNamespace(device=torch.device('cpu'),file_stats={str(source):(s.st_size,s.st_mtime_ns),str(path):(stat.st_size,stat.st_mtime_ns)},
        host_tensors={'mark':torch.arange(8,dtype=torch.float64)},tensors={},
        market={'mark':np.arange(8,dtype=np.float64)},feature_cache=None,
        arrays={'clocks':mapped},bank={'values':mapped},indicator_values=mapped,enriched_values=None,
        resident_features=SimpleNamespace(host={'base':torch.arange(12,dtype=torch.float32).reshape(3,4)},
            column_plans={},device=torch.device('cpu')))


def test_prepared_roundtrip_exact_and_private(tmp_path,monkeypatch):
    import research.vectorized_backtest.v32.host_store as module
    monkeypatch.setattr(module.shutil,'disk_usage',lambda p:SimpleNamespace(free=100*2**30))
    store=HostStore(tmp_path/'runtime');item=fixture(tmp_path)
    assert store.get('day') is None
    assert store.put('day',item)
    a=store.get('day');b=store.get('day')
    assert torch.equal(a.host_tensors['mark'],item.host_tensors['mark'])
    assert torch.equal(a.resident_features.host['base'],item.resident_features.host['base'])
    assert np.array_equal(a.market['mark'],item.market['mark'])
    assert np.array_equal(a.bank['values'],item.bank['values'])
    a.host_tensors['mark'][0]=999
    a.resident_features.host['base'][0,0]=999
    a.market['mark'][0]=999
    assert b.host_tensors['mark'][0]==0 and b.resident_features.host['base'][0,0]==0 and b.market['mark'][0]==0
    assert store.get('day').host_tensors['mark'][0]==0
    assert store.hits==3 and store.misses==1
    store.close()


def test_metadata_does_not_retain_authoritative_mapping(tmp_path,monkeypatch):
    import weakref,gc
    import research.vectorized_backtest.v32.host_store as module
    monkeypatch.setattr(module.shutil,'disk_usage',lambda p:SimpleNamespace(free=100*2**30))
    store=HostStore(tmp_path/'runtime');item=fixture(tmp_path)
    reference=weakref.ref(item.bank['values']);store.put('day',item)
    metadata=store.metadata('day')
    assert metadata.arrays=={} and metadata.bank=={} and metadata.indicator_values is None
    del item;gc.collect()
    assert reference() is None
    borrowed=store.get('day')
    assert np.array_equal(borrowed.bank['values'],np.arange(16,dtype=np.float64))


@pytest.mark.parametrize('which',['source','derived'])
def test_changed_bytes_fail_closed(tmp_path,monkeypatch,which):
    import research.vectorized_backtest.v32.host_store as module
    monkeypatch.setattr(module.shutil,'disk_usage',lambda p:SimpleNamespace(free=100*2**30))
    store=HostStore(tmp_path/'runtime');store.put('day',fixture(tmp_path))
    path=tmp_path/'source' if which=='source' else next(store.root.rglob('*.npy'))
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):store.get('day')


def test_no_disk_capacity_falls_back_to_verified_load(tmp_path,monkeypatch):
    import research.vectorized_backtest.v32.host_store as module
    monkeypatch.setattr(module.shutil,'disk_usage',lambda p:SimpleNamespace(free=0))
    store=HostStore(tmp_path/'runtime')
    assert not store.put('day',fixture(tmp_path))
    assert store.get('day') is None
