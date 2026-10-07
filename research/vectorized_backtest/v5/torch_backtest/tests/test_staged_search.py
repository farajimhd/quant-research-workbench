from copy import deepcopy
import numpy as np
import torch
import pytest
from research.vectorized_backtest.v5.torch_backtest.staged import balanced_panels,counts,migrate,Stage,validate_schedule
from research.vectorized_backtest.v5.torch_backtest.stability import score,Objective
from research.vectorized_backtest.v5.torch_backtest.genome import StrategySpace
from research.vectorized_backtest.v5.torch_backtest.evolution import sample
from research.vectorized_backtest.v5.torch_backtest.run_search import state
from research.vectorized_backtest.v5.torch_backtest.batched import merge_metrics
from research.vectorized_backtest.v5.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v5.torch_backtest.program_runner import ProgramRunner
from research.vectorized_backtest.v5.torch_backtest.staged_dashboard import render
from rich.console import Console
from io import StringIO

def test_negative_and_inactive_are_rankable_but_nonflat_invalid():
    pnl=torch.tensor([[-10.,0.,20.],[-20.,0.,20.],[-30.,0.,20.]],dtype=torch.float64)
    zeros=torch.zeros_like(pnl);valid=torch.ones_like(pnl,dtype=torch.bool);valid[1,2]=False
    result=score(pnl,zeros,zeros,zeros,zeros,valid,torch.zeros(3))
    assert result['feasible'].tolist()==[True,True,False]
    assert result['score'][0]<result['score'][1]

def test_nonfinite_lane_cannot_contaminate_valid_scores():
    pnl=torch.tensor([[float('nan'),-1.],[1.,-2.],[1.,-3.]],dtype=torch.float64)
    zeros=torch.zeros_like(pnl);valid=torch.ones_like(pnl,dtype=torch.bool)
    result=score(pnl,zeros,zeros,zeros,zeros,valid,torch.zeros(2))
    assert result['feasible'].tolist()==[False,True]
    assert torch.isfinite(result['score']).all() and result['score'][0]<result['score'][1]

def test_current_objective_weights_and_arithmetic_preserved():
    from research.vectorized_backtest.v4.torch_backtest.stability import score as previous, Objective as PreviousObjective
    pnl=torch.tensor([[-100.,0.],[-200.,0.],[1000.,0.]],dtype=torch.float64)
    zeros=torch.zeros_like(pnl);valid=torch.ones_like(pnl,dtype=torch.bool);complexity=torch.tensor([4.,3.])
    old=previous(pnl,zeros,zeros,zeros,zeros,valid,complexity,config=PreviousObjective(minimum_batches=0,maximum_batches=2147483647,require_positive_ex_best=False))
    new=score(pnl,zeros,zeros,zeros,zeros,valid,complexity)
    assert torch.equal(old['score'],new['score'])
    assert all(torch.equal(old['components'][k],new['components'][k]) for k in old['components'])

@pytest.mark.parametrize('count',[3,6,10,7])
def test_panels_unique_reproducible_and_balanced(count):
    left=balanced_panels(np.random.default_rng(4),30,count,30)
    assert left==balanced_panels(np.random.default_rng(4),30,count,30)
    assert all(len(set(panel))==count for panel in left)
    frequencies=np.bincount(np.asarray(left).reshape(-1),minlength=30)
    assert frequencies.max()-frequencies.min()<=1

def test_migration_exact_percentages_and_resume_rng():
    space=StrategySpace();rng=np.random.default_rng(8);population=sample(rng,space,100)
    assert counts(100)==[10,10,10,50,20]
    for n in [128,4096,8192]:assert sum(counts(n))==n
    saved=deepcopy(rng.bit_generator.state)
    first=migrate(rng,population,list(range(100)),100,space,None)
    rng.bit_generator.state=saved
    second=migrate(rng,population,list(range(100)),100,space,None)
    assert [state(v) for v in first]==[state(v) for v in second]
    assert [state(v) for v in first[:10]]==[state(v) for v in population[:10]]
    survivors=[state(v) for v in first[10:20]]
    assert all(v not in [state(p) for p in population[:10]] for v in survivors)
    for child in first:child.programs()

def test_crucial_no_valid_parent_gate():
    with pytest.raises(ValueError):migrate(np.random.default_rng(1),[],[],100,StrategySpace(),None)
    with pytest.raises(ValueError):validate_schedule([Stage(10,4096,31,10,10)])

def test_candidate_batch_parity_full_financial_metrics_and_fills():
    tape=synthetic_tape(seconds=40);space=StrategySpace();population=sample(np.random.default_rng(2),space,7)
    gates=torch.zeros((40,7,2),dtype=torch.uint8);gates[:, :, :]=5
    full=ProgramRunner(tape,space,population,gates,backend='eager',maximum_fills=256)
    expected=full.run();parts=[]
    for left in range(0,7,3):
        runner=ProgramRunner(tape,space,population[left:left+3],gates[:,left:left+3].contiguous(),backend='eager',maximum_fills=256)
        result=runner.run()
        for key,value in expected.items():
            if isinstance(value,torch.Tensor):torch.testing.assert_close(result[key],value[left:left+3],equal_nan=True,rtol=0,atol=0)
        for lane,count in enumerate(runner.fill_count.tolist()):
            assert torch.equal(runner.ledger[lane,:count],full.ledger[left+lane,:count])
        parts.append({'x':result['net_pnl'].tolist(),'samples':result['closed_position_duration_samples']})
    assert merge_metrics(parts)['x']==expected['net_pnl'].tolist()

@pytest.mark.parametrize('view',['financial','objective','positions','performance'])
def test_dashboard_fixed_height_and_three_ranks(view):
    out=StringIO();console=Console(file=out,width=130,height=38,color_system=None)
    console.print(render(dict(top_strategies=[dict(rank=1,score=-.1,metrics={'total_pnl':-20,'objective_components':{'median_reward':-.1}})],evaluation_basis='3-day search ranking'),width=130,height=38,view=view))
    text=out.getvalue();assert len(text.splitlines())==38
    assert '3-day search ranking' in text and '1/2/3 rank' in text
