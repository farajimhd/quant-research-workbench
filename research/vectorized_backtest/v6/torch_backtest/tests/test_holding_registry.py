import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.holding_registry import HoldingRegistry


def test_top_changes_preserve_different_candidate_holdings_and_pending_orders():
    registry=HoldingRegistry(2,4,2)
    changed,member=registry.reconcile(torch.tensor([0,1]),torch.zeros((2,4),dtype=torch.bool))
    assert registry.ids.tolist()==[[0,1,-1,-1]]*2
    retained=torch.tensor([[True,False,False,False],[False,True,False,False]])
    changed,member=registry.reconcile(torch.tensor([2,3]),retained)
    assert registry.ids.tolist()==[[0,2,3,-1],[2,1,3,-1]]
    assert not member[0,0] and not member[1,1]
    assert not changed[0,0] and not changed[1,1]
    assert member.sum(-1).tolist()==[2,2]
    registry.require_valid()


def test_padding_never_overwrites_last_new_slot_and_overflow_fails_closed():
    registry=HoldingRegistry(1,2,2)
    registry.reconcile(torch.tensor([7,8]),torch.zeros((1,2),dtype=torch.bool))
    registry.reconcile(torch.tensor([9,-1]),torch.tensor([[True,False]]))
    assert registry.ids.tolist()==[[7,9]]
    registry.reconcile(torch.tensor([10,11]),torch.ones((1,2),dtype=torch.bool))
    assert registry.ids.tolist()==[[7,9]]
    with pytest.raises(RuntimeError,match='exhausted'):registry.require_valid()
