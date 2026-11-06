"""Startup sequencing must retain all sessions and preserve the reserve."""
from types import SimpleNamespace
import pytest
from research.vectorized_backtest.v68.global_runner import Global


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
