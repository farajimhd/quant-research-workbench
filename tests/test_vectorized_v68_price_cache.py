from types import SimpleNamespace
from pathlib import Path
import numpy as np
import pytest
from research.vectorized_backtest.v68.price_cache import PriceCache,Runs,pack


@pytest.mark.parametrize('view',[lambda a:a,lambda a:a[::-1],lambda a:a[:,::-1]])
def test_column_runs_preserve_every_float_bit_and_layout(view):
    patterns=np.array([0,0x8000000000000000,0x7ff8000000000001,
                       0x7ff8000000000012,0x7ff0000000000000,0xfff0000000000000],np.uint64)
    value=view(np.repeat(patterns,300).view(np.float64).reshape(3,600).T)
    encoded=pack(value)
    assert isinstance(encoded,Runs)
    assert encoded.nbytes<value.nbytes/10
    decoded=encoded.decode()
    assert decoded.shape==value.shape
    np.testing.assert_array_equal(decoded.view(np.uint64),value.view(np.uint64))


@pytest.mark.parametrize('value',[np.empty((0,5)),np.arange(1000,dtype=np.float64).reshape(200,5),
                                  np.zeros((2000,5),np.float32),np.zeros(2000)])
def test_unhelpful_or_unsupported_packing_keeps_original(value):
    assert pack(value) is value


def test_cache_replacement_pop_and_observation_tuple():
    cache=PriceCache();a=np.full((1000,5),3.)
    cache['a']=a;cache['observations']=(np.arange(10),np.arange(10))
    assert cache.nbytes<1000
    np.testing.assert_array_equal(cache['a'],a)
    cache['a']=np.full_like(a,4.)
    assert (cache.pop('a')==4.).all()
    assert 'a' not in cache and len(cache)==1
    assert cache.pop('absent',None) is None
    with pytest.raises(KeyError):cache['absent']


def test_all_price_references_and_repeated_cache_reads_match_original_bits():
    from research.vectorized_backtest.v67.price_references import References as Original
    from research.vectorized_backtest.v68.price_references import References
    from research.vectorized_backtest.v68.price_program import REFERENCES
    n=200;clock=np.arange(n);slots=np.tile([0,1,2,-1,0],(n,1))
    slots[70:110,0]=2
    low=np.repeat((100+np.arange(50)%7).astype(float),4)[:,None]+np.arange(3)[None,:]
    low[::13]=np.nan;high=low+2
    def data():
        return SimpleNamespace(raw_extremes={'low':low,'high':high},
            arrays={'clocks':clock+1790000017},
            host_tensors={k:SimpleNamespace(numpy=lambda v=v:v) for k,v in
                          {'mark':low+1,'bid':low+.5,'ask':low+1.5}.items()})
    old,new=Original(data(),slots),References(data(),slots)
    names=(*REFERENCES,'volatility')
    for name in names:
        expected=old.values(name).view(np.uint64)
        np.testing.assert_array_equal(new.values(name).view(np.uint64),expected)
        np.testing.assert_array_equal(new.values(name).view(np.uint64),expected)
    assert isinstance(new.cache,PriceCache)
    assert new.cache.nbytes<sum(v.nbytes if isinstance(v,np.ndarray) else sum(a.nbytes for a in v)
                                 for v in old.cache.values())*.75


def test_founder_source_guard_permits_only_cache_representation(tmp_path):
    from research.vectorized_backtest.v68.founder_readmission import verify_replay_sources
    original=Path(__file__).resolve().parents[1]/'research/vectorized_backtest/v67/price_references.py'
    folder=tmp_path/'research/vectorized_backtest/v67';folder.mkdir(parents=True)
    text=original.read_text();path=folder/original.name;path.write_text(text)
    verify_replay_sources(tmp_path)
    path.write_text(text.replace('width=left+right+1','width=left+right+2'))
    with pytest.raises(ValueError,match='price_references.py'):verify_replay_sources(tmp_path)
