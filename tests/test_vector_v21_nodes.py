"""128-node boundary and retained old-rule computation parity."""
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v21.program import Node,Program,Op,TorchPrograms,MAX_NODES
from research.vectorized_backtest.v21.features import CATALOG


def chain(length):
    feature=next(i for i,f in enumerate(CATALOG) if f.unit=='bool')
    return Program((Node(Op.FEATURE,feature=feature),)+tuple(Node(Op.NOT,a=i-1) for i in range(1,length)),length-1)


def test_128_nodes_accept_and_129_reject_with_real_cpu_evaluation():
    assert MAX_NODES==128
    rule=chain(128);assert rule.validate(CATALOG)['active_nodes']==128
    with pytest.raises(ValueError,match='envelope'):chain(129).validate(CATALOG)
    features=torch.ones((4,len(CATALOG)));valid=torch.ones_like(features,dtype=torch.bool)
    values,masks=TorchPrograms([rule],CATALOG)(features,valid)
    assert not values.any() and masks.all()
    from research.vectorized_backtest.v21.genome import sample,effective_rules,assemble
    from research.vectorized_backtest.v21.archive import descriptor
    member=sample(np.random.default_rng(1),1)[0];rules=effective_rules(member);rules['entry']=rule
    assert np.isfinite(descriptor(assemble(rules,member.policy,member.minimum_age))).all()


def test_existing_rules_exactly_match_32_node_evaluator():
    from research.vectorized_backtest.v20.program import TorchPrograms as OldPrograms,Program as OldProgram
    rule=chain(32);old=OldProgram.from_payload(rule.payload())
    features=torch.rand((8,len(CATALOG)));valid=torch.ones_like(features,dtype=torch.bool);valid[3]=False
    actual=TorchPrograms([rule],CATALOG)(features,valid);expected=OldPrograms([old],CATALOG)(features,valid)
    for a,b in zip(actual,expected):torch.testing.assert_close(a,b,rtol=0,atol=0)
