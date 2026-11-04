import json
from dataclasses import replace
import numpy as np
import pytest
from research.vectorized_backtest.v66.genome import sample,Network,Neuron
from research.vectorized_backtest.v66.authority import construct,ALLOWED
from research.vectorized_backtest.v66.lineage import program_key
from research.vectorized_backtest.v66.review_backlog import reconcile
from research.vectorized_backtest.v66.investigation_results import combine


def test_reconcile_keeps_existing_work_and_old_epoch_candidates(tmp_path):
    m=sample(np.random.default_rng(38),1)[0]
    m=construct(replace(m,networks=dict(m.networks,entry=Network((Neuron((ALLOWED[0],),(1.,),0.,op='identity'),)))))
    parent=dict(serial=17006,payload=m.payload(),record=dict(program_sha256=program_key(m)),
                sessions={'2026-01-01':dict(net_pnl=0,closed_positions=0,winning_positions=0)})
    packet=tmp_path/'packet';packet.mkdir();out=tmp_path/'backlog';job=tmp_path/'job.json'
    job.write_text(json.dumps(dict(training_sha256='training',execution={},objective={})))
    (packet/'packet.json').write_text(json.dumps(dict(scope='training',training_sha256='training',source_generation=20)))
    (packet/'parents.json').write_text(json.dumps([parent]))
    first=reconcile(packet,out,job);task=out/first['tasks'][0]['key']/'task.json'
    raw=task.read_bytes();progress=task.parent/'progress.json';progress.write_text('{"completed_checks":3}')
    assert reconcile(packet,out,job)['tasks']==first['tasks']
    (packet/'parents.json').write_text('[]')
    assert reconcile(packet,out,job)['tasks']==first['tasks']
    assert task.read_bytes()==raw and json.loads(progress.read_bytes())['completed_checks']==3


def test_combiner_cannot_label_partial_investigation_complete(tmp_path):
    job=tmp_path/'job.json';job.write_text(json.dumps(dict(training_days=['2026-01-01'])))
    with pytest.raises(ValueError,match='incomplete'):combine(tmp_path,job)
