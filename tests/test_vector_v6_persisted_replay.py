from copy import deepcopy
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.tests.test_sparse_runner import fixture
from research.vectorized_backtest.v6.torch_backtest.compact_runner import CompactProgramRunner
from research.vectorized_backtest.v6.torch_backtest.genome import NAMES,StrategySpace
from research.vectorized_backtest.v6.torch_backtest.history_bank import calculate,SWING_WINDOWS


@pytest.mark.parametrize('swing,adaptive',[(False,False),(False,True),(True,False),(True,True)])
def test_persisted_full_finance_matches_online_history(swing,adaptive):
    tape,x,space,member,gates=fixture()
    lows=tape.low.numpy().copy();lows[6,:]=9.5;lows[30,:]=9.7
    x.market['low']=torch.from_numpy(lows.T.copy().reshape(-1))
    member.policy[8]=int(swing);member.policy[7]=int(adaptive)
    for name,value in dict(swing_left_seconds=2,swing_right_seconds=3,adaptive_window=10).items():
        member.policy[space.policy_start+NAMES.index(name)]=value
    reference=CompactProgramRunner(x,space,[deepcopy(member)],gates,holding_capacity=2,maximum_fills=512)
    before=reference.run()
    values=[]
    for listing in range(2):
        fields={name:x.market[name][listing*60:(listing+1)*60].numpy() for name in ('mark','high','low','observed','notional')}
        values.append(calculate(**fields))
    ids=torch.arange(60)[:,None]+torch.arange(2)[None]*60
    x.history_binding=dict(values=torch.from_numpy(np.concatenate(values)),ids=ids.to(torch.int32),source_rows=ids,receipt_sha256='synthetic')
    actual=CompactProgramRunner(x,space,[deepcopy(member)],gates,holding_capacity=2,maximum_fills=512)
    actual._source_reduce=lambda *args,**kwargs:(_ for _ in ()).throw(AssertionError('Online history reduction called'))
    after=actual.run()
    assert not actual.source_rings and actual.source_swing.numel()==0
    torch.testing.assert_close(reference.fill_count,actual.fill_count,rtol=0,atol=0)
    count=int(actual.fill_count[0])
    torch.testing.assert_close(reference.ledger[0,:count],actual.ledger[0,:count],rtol=0,atol=0)
    for name,value in before.items():
        if isinstance(value,torch.Tensor):torch.testing.assert_close(value,after[name],rtol=0,atol=0,equal_nan=True)


def test_sampling_and_repair_respect_persisted_ladder():
    space=StrategySpace();rows=space.sample(np.random.default_rng(2),200)
    for name in ('swing_left_seconds','swing_right_seconds'):
        assert set(rows[:,space.policy_start+NAMES.index(name)]).issubset(SWING_WINDOWS)
    row=space.default.copy();row[space.policy_start+NAMES.index('swing_left_seconds')]=7
    with pytest.raises(ValueError,match='ladder'):space.validate([row])
    repaired=space.repair([row])
    assert repaired[0,space.policy_start+NAMES.index('swing_left_seconds')]==5


def test_history_residency_uses_bounded_cohorts(tmp_path):
    from research.vectorized_backtest.v6.torch_backtest.resident_evaluator import ResidentSessionEvaluator
    class Bounded(ResidentSessionEvaluator):
        def __init__(self):self.history_root=tmp_path;self.cohorts=[];self.closed=0
        def close(self):self.closed+=1
        def prepare_pass(self,training,population,output,*,workers=2,prime_only=False):
            if len(training)>workers:return super().prepare_pass(training,population,output,workers=workers,prime_only=prime_only)
            self.cohorts.append([v['day'] for v in training]);return [{'day':v['day']} for v in training]
    evaluator=Bounded();training=[dict(day=str(i)) for i in range(30)]
    result=evaluator.prepare_pass(training,[],tmp_path,workers=8)
    assert [len(v) for v in evaluator.cohorts]==[8,8,8,6]
    assert len(result)==30 and evaluator.closed==5
