from threading import Event
from types import SimpleNamespace
import json
import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.rule_prefetch import RulePrefetch,ReceiptWriter
from research.vectorized_backtest.v6.torch_backtest.batched import BatchedEvaluator
from research.vectorized_backtest.v6.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v6.torch_backtest.evolution import Individual,STAGES
from research.vectorized_backtest.v6.torch_backtest.genome import StrategySpace
from research.vectorized_backtest.v6.torch_backtest.feature_bank import CATALOG
from research.vectorized_backtest.v6.torch_backtest.program import Node,Op
from research.vectorized_backtest.v6.torch_backtest.profile_execution import compare
from research.vectorized_backtest.v6.torch_backtest.gate_compiler import HostFeatureRows,FeatureResident


def test_bounded_producer_runs_ahead_and_orders_consumption():
    started=Event();release=Event()
    def prepare(out):
        started.set()
        assert release.wait(5)
        return torch.ones(2,1,3,dtype=torch.uint8),1.
    with RulePrefetch('cpu') as queue:
        queue.submit(7,(2,1,3),prepare)
        assert started.wait(5)
        with pytest.raises(ValueError,match='Only one'):queue.submit(8,(2,1,3),prepare)
        with pytest.raises(ValueError,match='out of order'):queue.take(8)
        release.set()
        (values,seconds),wait,begin,end=queue.take(7)
        assert values.sum()==6 and seconds==1. and wait>=0 and end>=begin
    with RulePrefetch('cpu',maximum_gib=1e-12) as queue:
        with pytest.raises(MemoryError,match='budget'):queue.submit(0,(2,1,3),prepare)


def test_writer_publishes_in_order_and_propagates_errors():
    order=[]
    with ReceiptWriter() as writer:
        writer.submit(lambda:order.append(1))
        writer.submit(lambda:order.append(2))
    assert order==[1,2]
    with pytest.raises(RuntimeError,match='write failed'):
        with ReceiptWriter() as writer:
            def fail():raise RuntimeError('write failed')
            writer.submit(fail)


@pytest.mark.parametrize('device',['cpu','cuda'])
def test_prefetched_temporal_signals_are_exact_and_active_storage_is_unchanged(device):
    if device=='cuda' and not torch.cuda.is_available():pytest.skip('CUDA required')
    class Sparse:
        def listing(self,identity,**kwargs):
            clocks=torch.arange(1,310)[::identity+1]*1_000_000
            values=torch.zeros(len(clocks),len(CATALOG));values[:,8]=torch.sin(torch.arange(len(clocks))*.3+identity)
            valid=torch.ones_like(values,dtype=torch.bool);valid[12::23,8]=False
            return clocks,values,valid
    space=StrategySpace();members=[]
    for window in (3,120):
        nodes=[Node(Op.FEATURE,feature=8),Node(Op.MEAN,a=0,window=window),Node(Op.GREATER,a=0,b=1)]
        members.append(Individual(space.default.tolist(),{s:[(nodes,2)] for s in STAGES},{s:[] for s in STAGES}))
    tape=synthetic_tape(seconds=320,listings=4,device=device)
    resident=FeatureResident(Sparse(),range(3),device=device)
    active,_=resident.compile(members,tape,chunk_candles=41)
    snapshot=active.clone();spare=torch.empty_like(active)
    with RulePrefetch(device,maximum_gib=1) as queue:
        queue.submit(1,active.shape,lambda out:resident.compile(members,tape,chunk_candles=41,out=out),spare)
        (actual,_),*_=queue.take(1)
        assert actual.data_ptr()==spare.data_ptr() and torch.equal(actual,snapshot)
        assert torch.equal(active,snapshot)


class Bank:
    def listing(self,identity,**kwargs):
        return torch.arange(1,91)*1_000_000,torch.ones(90,len(CATALOG)),torch.ones(90,len(CATALOG),dtype=torch.bool)


def test_host_feature_views_are_computed_once_and_fail_on_context_change():
    class Counting(Bank):
        calls=0
        def listing(self,identity,**kwargs):
            self.calls+=1
            return super().listing(identity,**kwargs)
    bank=Counting();prior=object()
    cached=HostFeatureRows(bank,[0,1],prior,start_us=1_000_000,end_us=90_000_000,maximum_gib=1)
    resident=FeatureResident(cached,[0,1],previous=prior,device='cpu',start_us=1_000_000,end_us=90_000_000)
    assert bank.calls==2 and len(resident.rows)==2
    for got,wanted in zip(resident.rows[0],bank.listing(0)):
        assert torch.equal(got,wanted)
    with pytest.raises(ValueError,match='context changed'):
        cached.listing(0,previous=object(),start_us=1_000_000,end_us=90_000_000)
    with pytest.raises(ValueError,match='context changed'):
        cached.listing(0,previous=prior,start_us=2_000_000,end_us=90_000_000)
    with pytest.raises(MemoryError,match='budget'):
        HostFeatureRows(bank,[0],prior,start_us=1,end_us=90,maximum_gib=1e-12)


def prepared():
    tape=synthetic_tape(seconds=90,listings=3)
    binding=dict(day='synthetic',execution='certified-test',feature_certificate='test',prior_certificate=None,
                 identity_map_sha256='test',split_certificate_sha256='test',previous_split_certificate_sha256=None)
    return (tape,Bank(),None,list(range(3)),binding),0.,0.


def arguments(output,device,pipeline):
    return SimpleNamespace(output=output,device=device,ticker_capacity=4,batch_size=2,maximum_tape_gib=1,
        feature_gib=1,maximum_gate_gib=1,chunk_candles=20,backend='compiled_graph' if device=='cuda' else 'eager',
        maximum_fills=512,maximum_state_gib=1,graph_steps=32,rule_prefetch=pipeline,concurrent_writes=pipeline,
        rule_prefetch_gib=1,profile_seconds=None)


@pytest.mark.parametrize('device',['cpu','cuda'])
def test_pipeline_matches_actual_financial_replay_and_resumes(tmp_path,device):
    if device=='cuda' and not torch.cuda.is_available():pytest.skip('CUDA required')
    if device=='cuda':
        from research.vectorized_backtest.v6.torch_backtest.runtime import configure_caches
        configure_caches(tmp_path/'compiler')
    torch.set_num_threads(1)
    space=StrategySpace();population=[]
    for slots in [3,1,3,1,2,3,2]:
        policy=space.default.tolist();policy[4]=slots;policy[11]=policy[12]=0
        population.append(Individual(policy,{s:[([Node(Op.CONSTANT,value=1,unit='bool')],0)] for s in STAGES},{s:[] for s in STAGES}))
    roots=[]
    for pipeline in (False,True):
        root=tmp_path/f'{device}-{pipeline}';roots.append(root)
        evaluator=BatchedEvaluator(space,arguments(tmp_path,device,pipeline))
        try:
            receipt=evaluator.evaluate({'day':'synthetic'},population,root,prepared(),lambda **event:None)
            assert sum(receipt['metrics']['fill_count'])>0
            assert all('sha256' in row for row in receipt['batch_receipts'])
            # Full and partial resume verify original asynchronous publication.
            assert evaluator.evaluate({'day':'synthetic'},population,root,prepared(),lambda **event:None)==receipt
            (root/'receipt.json').unlink()
            resumed=evaluator.evaluate({'day':'synthetic'},population,root,prepared(),lambda **event:None)
            assert resumed['metrics']==receipt['metrics']
        finally:evaluator.close()
    result=compare(*roots)
    assert result['actual_fills_exact'] and result['terminal_eligibility_audited']


def test_stop_drains_pending_receipts_and_rule_producer(tmp_path):
    (tmp_path/'STOP').write_text('stop')
    space=StrategySpace();policy=space.default.tolist();policy[4]=1
    policy[11]=policy[12]=0
    member=Individual(policy,{s:[([Node(Op.CONSTANT,value=1,unit='bool')],0)] for s in STAGES},{s:[] for s in STAGES})
    root=tmp_path/'stopped-session';evaluator=BatchedEvaluator(space,arguments(tmp_path,'cpu',True))
    try:
        with pytest.raises(InterruptedError):evaluator.evaluate({'day':'synthetic'},[member]*4,root,prepared(),lambda **event:None)
        assert (root/'batch_0000/receipt.json').exists()
        record=json.loads((root/'batch_0000/receipt.json').read_text())
        from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash
        assert record['ledger_sha256']==file_hash(root/'batch_0000/fills.pt')
        assert not (root/'receipt.json').exists()
    finally:evaluator.close()
