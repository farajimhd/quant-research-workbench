"""Exact same-device parity of storage/scatter-only rule optimizations."""
import numpy as np
import pytest
import torch

pytestmark=pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA required')


@pytest.mark.parametrize('sparse',[False,True])
@pytest.mark.parametrize('packed',[False,True])
def test_gate_scatter_matches_existing_indexed_or(sparse,packed):
    from research.vectorized_backtest.v26.gate_scatter import write
    generator=torch.Generator(device='cuda').manual_seed(26)
    dtype=torch.int16 if packed else torch.uint8
    actual=torch.zeros((7,19,11),device='cuda',dtype=dtype);expected=actual.clone()
    target=torch.tensor([6,1,4],device='cuda');source=torch.tensor([2,0,2],device='cuda')
    first=torch.arange(11,device='cuda')%5
    member=torch.rand((19,11),device='cuda',generator=generator)>.3
    following=torch.rand((19,11),device='cuda',generator=generator)>.2
    if sparse:
        clock=torch.tensor([1,3,5,9,17],device='cuda');ticker=torch.tensor([2,7,3,8,1],device='cuda')
        signal=torch.rand((3,1,8),device='cuda',generator=generator)>.5;prefix=0
    else:
        clock=torch.arange(5,18,device='cuda');ticker=torch.tensor([9,2,7],device='cuda')
        signal=torch.rand((3,4,17),device='cuda',generator=generator)>.5;prefix=2
    for shift in ([0,5,10] if packed else [0]):
        for bit in range(2 if shift==10 else 5):
            write(actual,signal,target,source,clock,ticker,bit=bit,shift=shift,sparse=sparse,
                  prefix=prefix,first=first,membership=member,following=following)
            if sparse:
                selected=signal[source,0,:len(clock)]
                expected[target[:,None],clock[None],ticker[None]]|=selected.to(dtype)*(1<<(bit+shift))
            else:
                selected=signal[source,:len(ticker),prefix:prefix+len(clock)]
                eligible=clock[:,None]>=first[ticker][None]
                if bit==0:eligible &= member[clock[:,None],ticker[None]] & following[clock[:,None],ticker[None]]
                elif bit!=4:eligible &= following[clock[:,None],ticker[None]]
                selected &= eligible.T[None]
                expected[target[:,None,None],clock[None,None,:],ticker[None,:,None]]|=selected.to(dtype)*(1<<(bit+shift))
    assert torch.equal(actual,expected)


def test_compact_node_storage_keeps_exact_fused_values_masks_and_capture():
    from research.vectorized_backtest.v26.fused_program import FusedPrograms
    from research.vectorized_backtest.v25.fused_program import FusedPrograms as Previous
    from research.vectorized_backtest.v26.genome import sample,effective_rules
    from research.vectorized_backtest.v26.features import CATALOG
    from research.vectorized_backtest.v26.complexity import simplify
    from research.vectorized_backtest.v26.rule_plan import CapturedGroup
    programs=[simplify(p) for m in sample(np.random.default_rng(26),8) for p in effective_rules(m).values()]
    current=FusedPrograms(programs,CATALOG,'cuda',literal_catalog=CATALOG)
    old=Previous(programs,CATALOG,'cuda',literal_catalog=CATALOG)
    assert current.live_nodes==sum(len(p.nodes) for p in programs)
    assert current.live_nodes<current.width*len(programs)
    generator=torch.Generator(device='cuda').manual_seed(26)
    values=torch.randn((2,131,len(CATALOG)),generator=generator,device='cuda')
    known=torch.rand(values.shape,generator=generator,device='cuda')>.1
    reference=old(values,known);actual=current(values,known)
    assert torch.equal(actual[0],reference[0]) and torch.equal(actual[1],reference[1])
    capture=CapturedGroup(current,values.shape,'cuda')
    assert torch.equal(capture(values,known),(reference[0]!=0)&reference[1])
