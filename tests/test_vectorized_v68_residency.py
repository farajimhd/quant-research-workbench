"""Startup sequencing must retain all sessions and preserve the reserve."""
from types import SimpleNamespace
import pytest
from research.vectorized_backtest.v68.global_runner import Global


def test_releasing_source_lru_preserves_prepared_arrays_and_provenance():
    import numpy as np
    import polars as pl
    from research.vectorized_backtest.v68.market_memory import release_completed_source_cache
    frame=pl.DataFrame({'close':[10.,11.,12.]});view=frame['close'].to_numpy()
    session=SimpleNamespace(cache={'bars':frame},cache_used=frame.estimated_size(),verified_files={('day','bars')})
    item=SimpleNamespace(shard_session=session,resident_features=object(),shard_values=view,
                         host_tensors={'close':view},quote_coverage={})
    original=view.copy();assert release_completed_source_cache(item)>0
    assert not session.cache and session.cache_used==0
    assert session.verified_files=={('day','bars')}
    np.testing.assert_array_equal(item.shard_values,original)
    np.testing.assert_array_equal(item.host_tensors['close'],original)


def test_source_lru_cannot_be_released_before_preparation():
    from research.vectorized_backtest.v68.market_memory import release_completed_source_cache
    with pytest.raises(ValueError,match='Complete prepared'):
        release_completed_source_cache(SimpleNamespace(shard_session=object()))


@pytest.mark.parametrize('slots',[0,1,3,64,4.0,True])
def test_invalid_global_startup_slot_count_fails_closed(slots):
    from research.vectorized_backtest.v68.global_contract import check
    with pytest.raises(ValueError,match='startup load slots'):check({'host_load_slots':slots})


def test_explicit_admission_cannot_cross_certified_split(tmp_path):
    model=object.__new__(Global);model.root=tmp_path
    model.job={'assets':{'training':{'a':{}},'validation':{'b':{}}}}
    with pytest.raises(ValueError,match='certified split'):
        model.prepare_resident_sessions({'training':['b'],'validation':['b']})


@pytest.mark.parametrize('deferred',[False,True])
def test_admission_finishes_training_before_validation_and_rejects_deferral(tmp_path,monkeypatch,deferred):
    import psutil
    monkeypatch.setattr(psutil,'virtual_memory',lambda:SimpleNamespace(available=256*2**30))
    completed=[];submitted=[]
    class Pool:
        def __init__(self,role,lane):self.role=role;self.lane=lane
        def submit(self,function,days):
            if self.role=='validation':assert completed==['training','training']
            submitted.append(self.role)
            role=self.role
            def result():
                completed.append(role)
                return dict(ready=0 if deferred else 1,deferred=days if deferred else [])
            return SimpleNamespace(result=result)
    model=object.__new__(Global);model.root=tmp_path;model.workers=2
    model.job=dict(training_days=['a','b'],validation_days=['c','d'],machines={'workstation':{'reserve_gib':128}})
    model.publish=lambda **values:None
    model.get_lanes=lambda role:SimpleNamespace(pools=[Pool(role,0),Pool(role,1)])
    if deferred:
        with pytest.raises(MemoryError,match='All-session RAM residency'):model.prepare_resident_sessions()
    else:model.prepare_resident_sessions()
    assert submitted==['training','training','validation','validation']
