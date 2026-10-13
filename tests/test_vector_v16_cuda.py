"""Same-device bit-exact numerical rolling regions and masks, including warm capture."""
import pytest
import torch
from research.vectorized_backtest.v16.program import Program,Node,Op,TorchPrograms,WINDOWS
from research.vectorized_backtest.v16.features import CATALOG


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA workstation gate')
def test_fused_numeric_rolling_values_exact():
    from research.vectorized_backtest.v16.fused_program import FusedPrograms
    i=next(i for i,f in enumerate(CATALOG) if f.unit=='ratio')
    programs=[Program((Node(Op.FEATURE,feature=i),Node(op,a=0,window=w)),1)
              for op in (Op.MEAN,Op.MINIMUM,Op.MAXIMUM) for w in WINDOWS]
    generator=torch.Generator().manual_seed(16)
    values=torch.randn((4,256,len(CATALOG)),generator=generator).cuda();valid=(torch.rand(values.shape,generator=generator)>.01).cuda()
    original=TorchPrograms(programs,CATALOG,'cuda',output_unit='ratio')
    fused=FusedPrograms(programs,CATALOG,'cuda',output_unit='ratio')
    expected,mask=original(values,valid);actual,known=fused(values,valid)
    torch.testing.assert_close(actual,expected,rtol=0,atol=0)
    assert torch.equal(mask,known)


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA workstation gate')
def test_fused_boolean_mean_values_exact():
    from research.vectorized_backtest.v16.fused_program import FusedPrograms
    i=next(i for i,f in enumerate(CATALOG) if f.unit=='bool')
    programs=[Program((Node(Op.FEATURE,feature=i),Node(Op.BOOL_RATIO,a=0),Node(Op.MEAN,a=1,window=w)),2) for w in WINDOWS]
    values=torch.ones((4,256,len(CATALOG)),device='cuda');values[...,i][:,::7]=0
    valid=torch.ones_like(values,dtype=torch.bool);valid[:,::31,i]=False
    expected,mask=TorchPrograms(programs,CATALOG,'cuda',output_unit='ratio')(values,valid)
    actual,known=FusedPrograms(programs,CATALOG,'cuda',output_unit='ratio')(values,valid)
    torch.testing.assert_close(actual,expected,rtol=0,atol=0)
    assert torch.equal(mask,known)
