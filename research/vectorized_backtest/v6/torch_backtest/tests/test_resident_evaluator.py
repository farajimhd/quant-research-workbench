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


def test_listing_union_preserves_order_and_excludes_unfilled_slots():
    top=np.array([[9,-1,2,9],[-1,0,2,5]],dtype=np.int64)
    assert ResidentSessionEvaluator._listing_union(top)==[0,2,5,9]
    assert ResidentSessionEvaluator._listing_union(np.full((3,10),-1))==[]


def test_capture_variants_retain_exact_shapes_and_evict_least_recent():
    evaluate=ResidentSessionEvaluator.__new__(ResidentSessionEvaluator)
    evaluate.capture_variants=2;evaluate._resident_runners={}
    cache=evaluate._capture_cache('day')
    left,right,replacement=object(),object(),object()
    evaluate._reserve_capture(cache,('history',31));evaluate._touch_capture(cache,('history',31),left)
    evaluate._reserve_capture(cache,('history',32));evaluate._touch_capture(cache,('history',32),right)
    assert cache[('history',31)] is left and cache[('history',32)] is right
    evaluate._touch_capture(cache,('history',31),left)
    evaluate._reserve_capture(cache,('history',33));evaluate._touch_capture(cache,('history',33),replacement)
    assert cache=={('history',31):left,('history',33):replacement}


@pytest.mark.parametrize('variants',[0,3,True])
def test_capture_variant_bounds_before_loading(variants):
    with pytest.raises(ValueError,match='capture variants'):
        ResidentSessionEvaluator('unused','unused',capture_variants=variants)


@pytest.mark.parametrize('steps',[0,65,True])
def test_capture_block_bounds_before_loading(steps):
    with pytest.raises(ValueError,match='capture steps'):
        ResidentSessionEvaluator('unused','unused',graph_steps=steps)


def test_partial_resume_budgets_next_complete_generation(tmp_path,monkeypatch):
    class BudgetOnly(ResidentSessionEvaluator):
        def contract(self,*args):return {}
        def __call__(self,*args):return {}
    evaluate=BudgetOnly.__new__(BudgetOnly)
    evaluate.inputs=tmp_path/'inputs';evaluate.structures=tmp_path/'structures'
    evaluate.device=torch.device('cuda');evaluate.maximum_input_gib=1.;evaluate.maximum_state_gib=1.
    evaluate._cohort_identity=None
    sessions=[dict(day='done'),dict(day='pending')];output=tmp_path/'output'
    for session in sessions:
        for root in (evaluate.inputs,evaluate.structures):
            folder=root/session['day'];folder.mkdir(parents=True);(folder/'complete.json').write_text('{}')
    completed=output/'done';completed.mkdir(parents=True);(completed/'receipt.json').write_text('{}')
    monkeypatch.setattr(resident_evaluator,'require_runtime',lambda path:(path.mkdir(parents=True,exist_ok=True) or path))
    # One remaining session would fit (8.5 GiB); the complete next generation
    # requires 10.5 GiB and must fail before any device input is loaded.
    monkeypatch.setattr(torch.cuda,'mem_get_info',lambda device:(int(13*1024**3),int(16*1024**3)))
    with pytest.raises(MemoryError,match='Resident cohort'):
        evaluate.prepare_pass(sessions,[],output,workers=1)


@pytest.mark.skipif(not torch.cuda.is_available(),reason='Resident execution requires CUDA')
@pytest.mark.parametrize('variants',[1,2])
def test_all_captures_precede_parallel_replay_and_receipts_resume(tmp_path,monkeypatch,variants):
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
    loads=[]
    def load(root,**kwargs):
        loads.append(root.name);return prepared[root.name]
    monkeypatch.setattr(resident_evaluator,'SparseInputs',load)
    memberships=[];original_union=ResidentSessionEvaluator._listing_union
    def union(values):
        memberships.append(values.shape)
        return original_union(values)
    monkeypatch.setattr(ResidentSessionEvaluator,'_listing_union',staticmethod(union))
    def mkdir(path):path.mkdir(parents=True,exist_ok=True);return path
    monkeypatch.setattr(runtime,'require_runtime',mkdir);monkeypatch.setattr(resident_evaluator,'require_runtime',mkdir)
    events=[];original_compile=resident_evaluator.CompactProgramRunner.compile;original_run=resident_evaluator.CompactProgramRunner.run
    def capture(runner):
        assert runner.graph_steps==7
        events.append('capture');return original_compile(runner)
    def replay(runner,**kwargs):events.append('replay');return original_run(runner,**kwargs)
    monkeypatch.setattr(resident_evaluator.CompactProgramRunner,'compile',capture)
    monkeypatch.setattr(resident_evaluator.CompactProgramRunner,'run',replay)
    evaluate=ResidentSessionEvaluator(source,structures,batch_size=2,holding_capacity=2,maximum_fills=512,
        maximum_input_gib=.01,maximum_state_gib=.01,backend='cudagraph',graph_steps=7,capture_variants=variants)
    population=[member,deepcopy(member),deepcopy(member),deepcopy(member)];output=mkdir(tmp_path/'result')
    evaluate.prepare_pass(sessions,population,output,workers=2)
    assert events==['capture','capture','replay','replay','replay','replay']
    assert events.count('capture')==2
    assert len(loads)==2
    assert len(memberships)==2
    assert evaluate._resident_listing_ids=={'synthetic-a':[0,1],'synthetic-b':[0,1]}
    next_output=mkdir(tmp_path/'next-generation')
    evaluate.prepare_pass(sessions,population,next_output,workers=2)
    assert len(loads)==2
    assert len(memberships)==2
    assert events.count('capture')==2
    for session in sessions:
        before=evaluate(session,population,output/session['day'])
        after=evaluate(session,population,next_output/session['day'])
        assert before['metrics']==after['metrics']
    evaluate.close()
    assert not evaluate._resident_inputs and not evaluate._resident_runners and not evaluate._resident_listing_ids
    records=[evaluate(session,population,output/session['day']) for session in sessions]
    assert records[0]['metrics']==records[1]['metrics'] and records[0]['metrics']['fill_count'][0]>0
    evaluate.prepare_pass(sessions,population,output,workers=2)
    assert events.count('capture')==2
    assert len(loads)==2
