"""Same-device bit-exact numerical rolling regions and masks, including warm capture."""
import pytest
import torch
from research.vectorized_backtest.v17.program import Program,Node,Op,TorchPrograms,WINDOWS
from research.vectorized_backtest.v17.features import CATALOG


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA workstation gate')
def test_fused_numeric_rolling_values_exact():
    from research.vectorized_backtest.v17.fused_program import FusedPrograms
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
def test_fused_ragged_programs_preserve_numeric_scan_shape():
    from research.vectorized_backtest.v17.fused_program import FusedPrograms
    i=next(i for i,f in enumerate(CATALOG) if f.unit=='ratio')
    programs=[Program((Node(Op.FEATURE,feature=i),),0),
              Program((Node(Op.FEATURE,feature=i),Node(Op.MEAN,a=0,window=60)),1),
              Program((Node(Op.FEATURE,feature=i),Node(Op.ABS,a=0),Node(Op.MEAN,a=1,window=60)),2)]
    generator=torch.Generator().manual_seed(162237)
    values=torch.randn((4,2119,len(CATALOG)),generator=generator).cuda()
    valid=(torch.rand(values.shape,generator=generator)>.005).cuda()
    reference=TorchPrograms(programs,CATALOG,'cuda',output_unit='ratio')
    fused=FusedPrograms(programs,CATALOG,'cuda',output_unit='ratio')
    expected,mask=reference(values,valid);actual,known=fused(values,valid)
    torch.testing.assert_close(actual,expected,rtol=0,atol=0)
    assert torch.equal(mask,known)
    assert [v.numel() for v in fused.active_rows]==[3,2,1]


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA workstation gate')
def test_single_pass_feature_gather_exact_all_columns_and_strides(tmp_path):
    import numpy as np
    from test_vector_v7_lightweight import fixture_files
    from research.vectorized_backtest.v7.data import SessionData
    from research.vectorized_backtest.v17.resident_features import ResidentFeatures
    from research.vectorized_backtest.v17.features import VARIANTS
    root,history=fixture_files(tmp_path);data=SessionData(root,history)
    generator=np.random.default_rng(162237)
    data.indicator_values=generator.normal(size=(len(data.arrays['features']),len(VARIANTS))).astype(np.float32)
    data.indicator_values[::3,::7]=np.nan
    resident=ResidentFeatures(data);resident.activate('cuda')
    try:
        a=resident.tensors
        for columns in (list(range(len(CATALOG))),list(reversed(range(len(CATALOG)))),[len(CATALOG)-1,0,0,764,763,50]):
            for source,ids,clock in ((a['source'].T,a['ids'].T.to(torch.int64),a['clocks'][None]),
                                     (a['source'].T[:,::2],a['ids'].T[:,::2].to(torch.int64),a['clocks'][::2][None])):
                expected,mask=resident._values_reference(source,ids,clock,columns)
                actual,known=resident._values(source,ids,clock,columns)
                torch.testing.assert_close(actual,expected,rtol=0,atol=0)
                assert torch.equal(mask,known)
    finally:resident.deactivate();data.close()


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA workstation gate')
def test_fused_boolean_mean_values_exact():
    from research.vectorized_backtest.v17.fused_program import FusedPrograms
    i=next(i for i,f in enumerate(CATALOG) if f.unit=='bool')
    programs=[Program((Node(Op.FEATURE,feature=i),Node(Op.BOOL_RATIO,a=0),Node(Op.MEAN,a=1,window=w)),2) for w in WINDOWS]
    values=torch.ones((4,256,len(CATALOG)),device='cuda');values[...,i][:,::7]=0
    valid=torch.ones_like(values,dtype=torch.bool);valid[:,::31,i]=False
    expected,mask=TorchPrograms(programs,CATALOG,'cuda',output_unit='ratio')(values,valid)
    actual,known=FusedPrograms(programs,CATALOG,'cuda',output_unit='ratio')(values,valid)
    torch.testing.assert_close(actual,expected,rtol=0,atol=0)
    assert torch.equal(mask,known)
