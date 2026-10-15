"""Exact same-device rolling-mean wrappers against the retained V20 kernel."""
import pytest
import torch
from research.vectorized_backtest.v21.program import Program,Node,Op,WINDOWS
from research.vectorized_backtest.v21.features import CATALOG


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA workstation check')
@pytest.mark.parametrize('shape',[(1,127),(8,2167)])
def test_fused_mean_wrappers_preserve_prefix_rounding_and_masks(shape):
    from research.vectorized_backtest.v20.fused_program import FusedPrograms as Reference
    from research.vectorized_backtest.v21.fused_program import FusedPrograms
    feature=next(i for i,f in enumerate(CATALOG) if f.unit=='ratio')
    boolean=next(i for i,f in enumerate(CATALOG) if f.unit=='bool')
    programs=[]
    for window in WINDOWS:
        programs.append(Program((Node(Op.FEATURE,feature=feature),Node(Op.MEAN,a=0,window=window)),1))
        programs.append(Program((Node(Op.FEATURE,feature=boolean),Node(Op.BOOL_RATIO,a=0),Node(Op.MEAN,a=1,window=window)),2))
        programs.append(Program((Node(Op.FEATURE,feature=feature),Node(Op.ABS,a=0),Node(Op.MEAN,a=1,window=window),Node(Op.LAG if window<120 else Op.ABS,a=2,window=1)),3))
        programs.append(Program((Node(Op.FEATURE,feature=feature),Node(Op.STD,a=0,window=window),Node(Op.MEAN,a=1,window=1)),2))
    programs.append(Program((Node(Op.FEATURE,feature=feature),),0))
    generator=torch.Generator().manual_seed(21371)
    x=torch.randn((*shape,len(CATALOG)),generator=generator).cuda()
    valid=(torch.rand(x.shape,generator=generator)>.03).cuda()
    x[...,feature]*=1e6
    x[:,::13,feature]=1e-7;x[:,::17,feature]=-1e6
    x[:,::19,feature]=float('inf');valid[:,::19,feature]=False
    old=Reference(programs,CATALOG,'cuda',output_unit='ratio')
    new=FusedPrograms(programs,CATALOG,'cuda',output_unit='ratio')
    expected,mask=old(x,valid);actual,known=new(x,valid)
    assert torch.equal(actual,expected) and torch.equal(known,mask)
    from research.vectorized_backtest.v21.rule_plan import CapturedGroup
    captured=CapturedGroup(new,x.shape,torch.device('cuda'))
    assert torch.equal(captured(x,valid),(expected!=0)&mask)
    # Changed input must flow through every replay, not a cached calculation.
    x.mul_(.125);expected,mask=old(x,valid)
    assert torch.equal(captured(x,valid),(expected!=0)&mask)
