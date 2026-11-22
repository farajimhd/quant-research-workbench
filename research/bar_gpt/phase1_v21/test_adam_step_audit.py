import copy
import math
import torch
from ..phase1_v20.test_model import build
from .model import initialize
from .adam_step_audit import measure
from ..v5.shards.phase1_contract import FRAMES


def test_measure_preserves_objective_and_counts_visibility():
    model,teacher,_=build()
    saved=dict(model=copy.deepcopy(model.state_dict()),contract=dict(bar_config=vars(teacher.config),temporal_config=vars(model.temporal.config),input_calibration=None))
    model=initialize(None,saved,None,'joint_prefloor')
    x=torch.rand(2,64,27);v=torch.ones_like(x,dtype=torch.bool);raw=x[...,:18].exp()
    args=(x,v,raw,torch.full((2,),FRAMES[0]),torch.arange(16).expand(2,-1),torch.zeros(2,64,16))
    total,result=measure(model,args,torch.ones(6,6))
    # Splitting the FP32 grouped raw loss changes only addition roundoff.
    assert math.isclose(sum(result['components'].values()),result['loss'],rel_tol=1e-7,abs_tol=1e-6)
    from .objectives import loss
    expected,_=loss(model,*args,torch.ones(6,6))
    torch.testing.assert_close(total,expected,rtol=0,atol=0)
    for key,row in result['raw'].items():
        assert row['count']==(32 if key.startswith('visible/') else 96)
    total.backward()
    assert all(torch.isfinite(p.grad).all() for p in model.parameters() if p.grad is not None)
