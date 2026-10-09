"""Static batch specialization preserves accounts, actual fills and UTC durations."""
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v6.torch_backtest.genome import StrategySpace
from research.vectorized_backtest.v6.torch_backtest.evolution import sample
from research.vectorized_backtest.v6.torch_backtest.program_runner import ProgramRunner


@pytest.mark.parametrize('lots',[1,3,8,15])
@pytest.mark.parametrize('mode',['mixed','percentage-step','structural-adaptive','swing-step'])
def test_dynamic_slots_and_pruned_native_history_match_reference(lots,mode):
    torch.set_num_threads(1);space=StrategySpace()
    population=sample(np.random.default_rng(2),space,3)
    for lane,candidate in enumerate(population):
        candidate.policy=space.default.tolist();candidate.policy[4]=lots if lane==0 else 1
        candidate.policy[5]=lane;candidate.policy[6]=lane%2
        candidate.policy[7]=lane%2;candidate.policy[8]=lane%2;candidate.policy[9]=lane%2
        candidate.policy[11]=0;candidate.policy[12]=0
        if mode!='mixed':
            candidate.policy[6:10]={'percentage-step':[0,0,0,0],'structural-adaptive':[1,1,1,1],'swing-step':[0,0,1,0]}[mode]
    prices=(10+(torch.arange(120,dtype=torch.float64)-30).clamp_min(0)*.002)[:,None].expand(-1,3).clone()
    tape=synthetic_tape(prices)
    gates=torch.full((120,3,3),5,dtype=torch.uint8);gates[80:]|=2
    reference=ProgramRunner(tape,space,population,gates,backend='eager',maximum_fills=2048,specialize=False)
    optimized=ProgramRunner(tape,space,population,gates,backend='eager',maximum_fills=2048)
    expected=reference.run();actual=optimized.run()
    assert optimized.quantity.shape[-1]==lots and reference.quantity.shape[-1]==15
    assert int(reference.fill_count.sum())>0
    for name,value in expected.items():
        if isinstance(value,torch.Tensor):torch.testing.assert_close(actual[name],value,rtol=1e-12,atol=1e-8,equal_nan=True)
    assert actual['closed_position_duration_samples']==expected['closed_position_duration_samples']
    for lane,count in enumerate(reference.fill_count.tolist()):
        torch.testing.assert_close(optimized.ledger[lane,:count],reference.ledger[lane,:count],rtol=0,atol=0)


def test_captured_specialization_rejects_changed_configuration():
    space=StrategySpace();population=sample(np.random.default_rng(2),space,1)
    gates=torch.zeros((40,1,2),dtype=torch.uint8)
    runner=ProgramRunner(synthetic_tape(seconds=40),space,population,gates)
    population[0].policy[4]=1 if population[0].policy[4]!=1 else 2
    with pytest.raises(ValueError,match='specialization'):runner.set_population(population,gates)


def test_execution_permutation_restores_original_candidate_metrics():
    from research.vectorized_backtest.v6.torch_backtest.batched import merge_metrics
    assert merge_metrics([dict(pnl=[2,0]),dict(pnl=[1])],[2,0,1])==dict(pnl=[0,1,2])
    with pytest.raises(ValueError,match='permutation'):merge_metrics([dict(pnl=[1,2])],[0,0])


def test_batched_sorted_execution_receipts_and_fills_match_reference(tmp_path):
    from types import SimpleNamespace
    from research.vectorized_backtest.v6.torch_backtest.batched import BatchedEvaluator
    from research.vectorized_backtest.v6.torch_backtest.feature_bank import CATALOG
    from research.vectorized_backtest.v6.torch_backtest.program import Node,Op
    from research.vectorized_backtest.v6.torch_backtest.evolution import STAGES
    from research.vectorized_backtest.v6.torch_backtest.profile_execution import compare
    class Bank:
        def listing(self,identity,**kwargs):
            return torch.arange(1,97)*1_000_000,torch.ones(96,len(CATALOG)),torch.ones(96,len(CATALOG),dtype=torch.bool)
    space=StrategySpace();population=sample(np.random.default_rng(2),space,5)
    for candidate,lots in zip(population,[8,1,3,2,6]):
        candidate.policy=space.default.tolist();candidate.policy[4]=lots
        candidate.policy[11]=0;candidate.policy[12]=0
        candidate.clauses={s:[([Node(Op.CONSTANT,value=int(s in ('entry','trail')),unit='bool')],0)] for s in STAGES}
        candidate.connectors={s:[] for s in STAGES}
    binding={k:'synthetic' for k in ('day','execution','feature_certificate','prior_certificate',
             'identity_map_sha256','split_certificate_sha256','previous_split_certificate_sha256')}
    prepared=(synthetic_tape(seconds=96),Bank(),None,[0,1],binding)
    args=SimpleNamespace(device='cpu',backend='eager',output=tmp_path,batch_size=2,ticker_capacity=4,
         maximum_tape_gib=1,feature_gib=1,maximum_gate_gib=1,chunk_candles=40,
         maximum_fills=1024,maximum_state_gib=1,graph_steps=32,reference_execution=True)
    first=BatchedEvaluator(space,args);first.evaluate(binding,population,tmp_path/'reference',(prepared,0.,0.),lambda **v:None);first.close()
    args.reference_execution=False
    second=BatchedEvaluator(space,args);actual=second.evaluate(binding,population,tmp_path/'specialized',(prepared,0.,0.),lambda **v:None)
    assert actual['candidate_order']==[1,3,2,4,0]
    assert sum(actual['metrics']['fill_count'])>0
    audited=compare(tmp_path/'reference',tmp_path/'specialized');assert audited['actual_fills_exact'] and audited['terminal_eligibility_audited']
    reused=second.evaluate(binding,population,tmp_path/'specialized',(prepared,0.,0.),lambda **v:None)
    assert reused==actual;second.close()


@pytest.mark.skipif(not torch.cuda.is_available(),reason='Masked GPU overflow/broadcast qualification')
def test_masked_ledger_handles_inactive_broadcast_rows_and_overflow():
    from research.vectorized_backtest.v6.torch_backtest.ledger_write import ledger_append,masked_ledger_append
    from research.vectorized_backtest.v6.torch_backtest.runtime import configure_caches,DEFAULT
    configure_caches(DEFAULT/'tests'/'masked-ledger-boundaries')
    qty=torch.zeros((2,5,3),dtype=torch.int64);qty[0,1,2]=4;qty[1,0,0]=3;qty[1,3,1]=2
    price=(10+torch.arange(5,dtype=torch.float64)*.1)[None,:,None]
    fee=qty.to(torch.float64)*.005;reason=torch.tensor(2,dtype=torch.int64)
    stamp=torch.tensor(1791393000);clock=torch.tensor(31);axis=torch.arange(15)[None]
    ledger=torch.zeros((2,20,9),dtype=torch.float64);counts=torch.tensor([0,4]);overflow=torch.zeros(2,dtype=torch.bool)
    expected=ledger.clone();expected_counts=counts.clone();expected_overflow=overflow.clone()
    ledger_append(expected,expected_counts,expected_overflow,qty,price,fee,stamp,reason,clock,axis,-1,5)
    actual=ledger.cuda();actual_counts=counts.cuda();actual_overflow=overflow.cuda()
    masked_ledger_append(actual,actual_counts,actual_overflow,qty.cuda(),price.cuda(),fee.cuda(),stamp.cuda(),reason.cuda(),clock.cuda(),axis.cuda(),-1,5)
    assert torch.equal(actual[:,:5].cpu(),expected[:,:5])
    assert torch.equal(actual_counts.cpu(),expected_counts) and torch.equal(actual_overflow.cpu(),expected_overflow)
    assert actual_overflow[1].item() and not actual_overflow[0].item()


@pytest.mark.skipif(not torch.cuda.is_available(),reason='Real CUDA specialization qualification')
@pytest.mark.parametrize('lots',[1,3,8])
def test_captured_dynamic_slot_replay_matches_independent_reference(lots):
    from research.vectorized_backtest.v6.torch_backtest.runtime import configure_caches,DEFAULT
    configure_caches(DEFAULT/'tests'/'specialization-cuda')
    space=StrategySpace();population=sample(np.random.default_rng(2),space,3)
    for member in population:
        member.policy=space.default.tolist();member.policy[4]=lots;member.policy[9]=0
        member.policy[11]=0;member.policy[12]=0
    gates=torch.full((96,3,2),5,dtype=torch.uint8);gates[40:]|=2
    eager=ProgramRunner(synthetic_tape(seconds=96),space,population,gates,backend='eager',maximum_fills=512,specialize=False)
    expected=eager.run(steps=64)
    captured=ProgramRunner(synthetic_tape(seconds=96,device='cuda'),space,population,gates.cuda(),backend='compiled_graph',graph_steps=32,maximum_fills=512)
    captured.compile();actual=captured.run(steps=64)
    assert int(eager.fill_count.sum())>0
    for name,value in expected.items():
        if isinstance(value,torch.Tensor):torch.testing.assert_close(actual[name].cpu(),value,rtol=1e-12,atol=1e-8,equal_nan=True)
    for lane,count in enumerate(eager.fill_count.tolist()):
        torch.testing.assert_close(captured.ledger[lane,:count].cpu(),eager.ledger[lane,:count],rtol=0,atol=0)
