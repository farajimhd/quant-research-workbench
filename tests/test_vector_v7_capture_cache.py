"""Cache residency and capture workspace are separate resource budgets."""
from collections import OrderedDict
from types import SimpleNamespace
import pytest
import torch
from research.vectorized_backtest.v7 import rule_plan


def cache(monkeypatch,free=10000,size=30):
    plan=rule_plan.RulePlan.__new__(rule_plan.RulePlan)
    plan.device=torch.device('cuda');plan.workspace_gib=1.
    plan.groups=[dict(candidates=[0],program=SimpleNamespace(width=32))]
    plan.captures=OrderedDict();plan.capture_bytes=0;plan.maximum_capture_bytes=100
    plan.builds=plan.hits=plan.evictions=0;plan.capture_seconds=0.
    monkeypatch.setattr(torch.cuda,'synchronize',lambda *a:None)
    monkeypatch.setattr(torch.cuda,'mem_get_info',lambda *a:(free,free))
    monkeypatch.setattr(torch.cuda,'empty_cache',lambda:None)
    class Capture:
        def __init__(self,*args):self.memory_bytes=size
        def __call__(self,values,known):return values.shape
    monkeypatch.setattr(rule_plan,'CapturedGroup',Capture)
    return plan


def test_workspace_estimate_does_not_evict_graphs_that_fit(monkeypatch):
    plan=cache(monkeypatch)
    for shape in [(1,2,1),(1,3,1),(1,2,1)]:
        values=SimpleNamespace(shape=shape)
        assert plan.execute(0,values,None)==shape
    assert (plan.builds,plan.hits,plan.evictions,plan.capture_bytes)==(2,1,0,60)


def test_measured_pools_still_enforce_cache_budget_and_lru(monkeypatch):
    plan=cache(monkeypatch,size=60)
    for shape in [(1,2,1),(1,3,1),(1,3,1)]:plan.execute(0,SimpleNamespace(shape=shape),None)
    assert (plan.builds,plan.hits,plan.evictions,plan.capture_bytes)==(2,1,1,60)


def test_capture_fails_closed_without_physical_workspace(monkeypatch):
    plan=cache(monkeypatch,free=100)
    with pytest.raises(MemoryError,match='headroom'):plan.execute(0,SimpleNamespace(shape=(1,2,1)),None)
    assert not plan.captures and plan.builds==0
