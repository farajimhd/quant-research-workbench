"""Real captured independent accounts, with a global preparation barrier."""
from copy import deepcopy
import json
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest import resident_evaluator,runtime
from research.vectorized_backtest.v6.torch_backtest.resident_evaluator import ResidentSessionEvaluator
from research.vectorized_backtest.v6.torch_backtest.tests.test_sparse_runner import fixture
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash,DEFAULT,configure_caches


@pytest.mark.skipif(not torch.cuda.is_available(),reason='Resident execution requires CUDA')
def test_all_captures_precede_parallel_replay_and_receipts_resume(tmp_path,monkeypatch):
    configure_caches(DEFAULT/'tests'/'resident-cuda')
    source=tmp_path/'inputs';structures=tmp_path/'structures';sessions=[];prepared={}
    for day in ('synthetic-a','synthetic-b'):
        tape,x,space,member,gates=fixture()
        session=dict(day=day,start='1970-01-01T00:00:00+00:00',end='1970-01-01T00:01:00+00:00')
        sessions.append(session);x.root=source/day;x.root.mkdir(parents=True);(x.root/'complete.json').write_text('{}')
        x.receipt['identity']['session']=session;x.receipt['files']['market_keys.npy']='synthetic-keys'
        x.arrays['clocks']=tape.clocks.numpy();x.arrays['market_keys']=x.tensors['market_keys'].numpy()
        x.device=torch.device('cuda',torch.cuda.current_device())
        x.tensors={k:v.to(x.device) for k,v in x.tensors.items()};x.market={k:v.to(x.device) for k,v in x.market.items()}
        signals=gates.to(x.device)
        x.compile=lambda members,_signals=signals,**kwargs:(_signals.expand(len(members),-1),0.)
        prepared[day]=x
        side=structures/day;side.mkdir(parents=True)
        np.save(side/'targets.npy',np.full((120,15),np.inf));np.save(side/'valid.npy',np.zeros(120,dtype=bool))
        (side/'complete.json').write_text(json.dumps(dict(status='complete',version='v6-sparse-structural-v1',validation_opened=False,
            input_receipt_sha256=file_hash(x.root/'complete.json'),market_keys_sha256='synthetic-keys',listing_ids=[0,1],
            files={n:file_hash(side/n) for n in ('targets.npy','valid.npy')})))
    monkeypatch.setattr(resident_evaluator,'SparseInputs',lambda root,**kwargs:prepared[root.name])
    def mkdir(path):path.mkdir(parents=True,exist_ok=True);return path
    monkeypatch.setattr(runtime,'require_runtime',mkdir);monkeypatch.setattr(resident_evaluator,'require_runtime',mkdir)
    events=[];original_compile=resident_evaluator.CompactProgramRunner.compile;original_run=resident_evaluator.CompactProgramRunner.run
    def capture(runner):events.append('capture');return original_compile(runner)
    def replay(runner,**kwargs):events.append('replay');return original_run(runner,**kwargs)
    monkeypatch.setattr(resident_evaluator.CompactProgramRunner,'compile',capture)
    monkeypatch.setattr(resident_evaluator.CompactProgramRunner,'run',replay)
    evaluate=ResidentSessionEvaluator(source,structures,batch_size=2,holding_capacity=2,maximum_fills=512,
        maximum_input_gib=.01,maximum_state_gib=.01,backend='cudagraph')
    population=[member,deepcopy(member)];output=mkdir(tmp_path/'result')
    evaluate.prepare_pass(sessions,population,output,workers=2)
    assert events==['capture','capture','replay','replay']
    records=[evaluate(session,population,output/session['day']) for session in sessions]
    assert records[0]['metrics']==records[1]['metrics'] and records[0]['metrics']['fill_count'][0]>0
    evaluate.prepare_pass(sessions,population,output,workers=2)
    assert events==['capture','capture','replay','replay']
