"""Real coordinator deferral-only drains, separate limits, and v2 protection."""
import json
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import polars as pl
import pytest

from research.bar_gpt.v5.phase2_ar_shards import run
from research.bar_gpt.v5.phase2_ar_shards.contract import Geometry, BAR, ORIGIN, BLOCK
from research.bar_gpt.v5.phase2_shards.training_guard import TrainingGuard


def test_coordinator_drains_deferrals_then_schedules_remaining_queue(tmp_path, monkeypatch):
    from research.bar_gpt.v5 import quote_repair_v1
    parent=tmp_path/'parent';parent.mkdir();(parent/'repair-contract.json').write_text('{}')
    output=tmp_path/'output';output.mkdir()
    units=[dict(ticker='TEST',month=f'2025-{m:02d}',split=0,liquid=1) for m in range(1,7)]
    pl.DataFrame(units).write_parquet(output/'sample-units.parquet')
    plan=dict(hash='binding',seed=17,geometry=Geometry().manifest(),jobs=[dict(ticker='TEST',years=[dict(year=2025,events=100)])],sample_units=dict(name='sample-units.parquet'),authority=dict(corporate_actions=dict(rows=[])))
    monkeypatch.setattr(run,'describe',lambda *_:(plan,{}))
    monkeypatch.setattr(run,'freeze',lambda *a,**kw:plan)
    monkeypatch.setattr(quote_repair_v1,'require_complete',lambda *_:None)
    monkeypatch.setattr(run,'ProcessPoolExecutor',lambda *a,**kw:ThreadPoolExecutor(1))
    visited=[]
    def produce(unit):
        visited.append(unit['month']);outputs=[]
        for d in (2,3):
            day=unit['month']+f'-{d:02d}'
            session=dict(unit,month=day,session=day)
            outputs.append((session,dict(compiled=dict(blocks=np.empty((0,BLOCK)),stats=dict(source_origins=32),sampling=dict(reason='session_has_fewer_than4096_origins')))))
        return outputs
    monkeypatch.setattr(run,'produce',produce)
    assert run.main(['--execute','--parent',str(parent),'--output',str(output),'--source-revision','test','--workers','1','--validation-lanes','1','--io-workers','1'])==0
    result=json.loads((output/'result.json').read_text())
    assert visited==[u['month'] for u in units]
    assert result['state']=='complete' and result['queued_units']==0
    assert result['completed_months_this_run']==6 and result['deferred_sessions']==12
    assert result['capacity']['training']['blocked'] is False


def test_validation_limit_does_not_close_training():
    caps={0:4_000_000_000_000,1:250_000_000}
    status=run.capacity_status({0:406_720_703_548,1:248_587_869},{0:0,1:0},caps,{0:0,1:0},{0:0,1:12})
    assert not status['training']['blocked']
    assert status['validation']['blocked'] and status['validation']['reason']=='minimum_block_fit'
    assert status['validation']['remaining_bytes']==1_412_131
    assert status['validation']['deferred_units']==12


def test_minimum_fit_bound_never_rejects_a_publishable_block():
    minimum=1_000_000+BLOCK*(BAR.itemsize+ORIGIN.itemsize)
    # One 1s bar per origin is unavoidable, independently of context sharing.
    for budget in (minimum-1,minimum,minimum+1):
        s=run.capacity_status({}, {0:0,1:0},{0:budget,1:budget},{0:0,1:0},{0:0,1:0})
        assert s['training']['blocked']==(budget<minimum)


@pytest.mark.parametrize('stage,state,age,expected',[('training','running',0,32),('validation','running',0,4),('probe','running',0,4),('training','running',31,4),('training','stopped',0,4)])
def test_direct_v2_status_is_read_only_and_conservative(tmp_path,stage,state,age,expected):
    path=tmp_path/'progress.json';raw=json.dumps(dict(stage=stage,state=state,updated=time.time()-age));path.write_text(raw)
    guard=TrainingGuard(tmp_path,32,4)
    assert guard.refresh()==expected and path.read_text()==raw
