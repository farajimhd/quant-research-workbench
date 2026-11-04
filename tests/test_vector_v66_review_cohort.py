import json
from dataclasses import replace
import numpy as np
import psutil
import os
from research.vectorized_backtest.v66.genome import sample,Network,Neuron
from research.vectorized_backtest.v66.authority import construct,ALLOWED
from research.vectorized_backtest.v66.review_cohort import prepare
from research.vectorized_backtest.v66.review_scheduler import alive


def test_cohort_freezes_all_parents_and_resumes_identically(tmp_path,monkeypatch):
    from research.vectorized_backtest.v66 import agent_intervention
    folder=tmp_path/'sealed';folder.mkdir();packet=tmp_path/'packet';packet.mkdir()
    parents=[construct(replace(m,networks=dict(m.networks,entry=Network((Neuron((ALLOWED[0],),(1.,),0.,op='identity'),)))))
             for m in sample(np.random.default_rng(83),4)]
    (folder/'population.json').write_text(json.dumps(dict(ids=[1,2,3,4],population=[m.payload() for m in parents])))
    monkeypatch.setattr(agent_intervention,'evidence',lambda root,generation:(folder,{}))
    job=tmp_path/'job.json';job.write_text(json.dumps(dict(campaign=str(tmp_path),training_sha256='train',training_days=['2026-01-01'],execution={})))
    (packet/'packet.json').write_text(json.dumps(dict(scope='training',training_sha256='train',source_generation=36,review_epoch=35,evidence={})))
    (packet/'parents.json').write_text(json.dumps([dict(serial=i+1,payload=m.payload()) for i,m in enumerate(parents)]))
    first=prepare(packet,job,tmp_path/'out');second=prepare(packet,job,tmp_path/'out')
    assert first==second and len(first['records'])==4
    assert [r['parent_index'] for r in first['records']]==[0,1,2,3]
    from research.vectorized_backtest.v66.identity import digest
    assert digest(first['members'][:4])==digest([m.payload() for m in parents])
    assert len({r['child_sha256'] for r in first['records'] if r['child_index'] is not None})==first['controls']


def test_pid_reuse_is_not_liveness():
    worker=dict(pid=os.getpid(),creation_time=psutil.Process().create_time())
    assert alive(worker)
    assert not alive(dict(worker,creation_time=worker['creation_time']-10))
