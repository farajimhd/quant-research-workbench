import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.feature_bank import CATALOG
from research.vectorized_backtest.v6.torch_backtest.program import Node, Program, Op, TorchPrograms

def test_one_candle_mean_is_exact_identity_and_invalid_outputs_are_zero():
    features=torch.rand(3,250,len(CATALOG),generator=torch.Generator().manual_seed(9))
    valid=torch.ones_like(features,dtype=torch.bool);valid[:,17,8]=False
    program=Program((Node(Op.FEATURE,feature=8),Node(Op.MEAN,a=0,window=1),Node(Op.GREATER,a=0,b=1)),2)
    values,known=TorchPrograms([program],CATALOG)(features,valid)
    assert not values.any() and not known[:,:,17].any()


def test_mixed_instruction_lengths_preserve_earlier_outputs_and_missing_history():
    features=torch.zeros(2,8,len(CATALOG))
    features[:,:,8]=torch.tensor([[0.,2.,8.,4.,10.,6.,12.,8.],[12.,8.,6.,10.,4.,8.,2.,0.]])
    valid=torch.ones_like(features,dtype=torch.bool);valid[0,3,8]=False
    short=Program((Node(Op.FEATURE,feature=8),
        Node(Op.CONSTANT,value=5.,unit=CATALOG[8].unit),Node(Op.GREATER,a=0,b=1)),2)
    long=Program((Node(Op.FEATURE,feature=8),Node(Op.ABS,a=0),
        Node(Op.MEAN,a=1,window=3),Node(Op.GREATER,a=1,b=2),Node(Op.NOT,a=3)),3)
    evaluator=TorchPrograms([short,long],CATALOG)
    for scale in (1.,-1.):
        data=features*scale;actual,known=evaluator(data,valid)
        expected=torch.zeros_like(actual);mask=torch.zeros_like(known)
        mask[0]=valid[:,:,8];expected[0]=((data[:,:,8]>5)&mask[0]).to(expected.dtype)
        for listing in range(2):
            for clock in range(2,8):
                mask[1,listing,clock]=valid[listing,clock-2:clock+1,8].all()
                mean=data[listing,clock-2:clock+1,8].abs().mean()
                expected[1,listing,clock]=(data[listing,clock,8].abs()>mean)&mask[1,listing,clock]
        assert torch.equal(actual,expected) and torch.equal(known,mask)


@pytest.mark.parametrize('operation', [Op.LAG, Op.DIFFERENCE, Op.MEAN, Op.MINIMUM, Op.MAXIMUM])
def test_listing_batch_matches_independent_temporal_evaluation(operation):
    generator = torch.Generator().manual_seed(20261005)
    features = torch.randn(4, 127, len(CATALOG), generator=generator)
    features[1] += 100  # Large differences expose accidental cross-listing windows.
    valid = torch.rand(features.shape, generator=generator) > .05
    windows = (1, 3, 60) if operation in (Op.LAG, Op.DIFFERENCE) else (1, 3, 120)
    programs = [Program((Node(Op.FEATURE, feature=8),
                         Node(operation, a=0, window=k),
                         Node(Op.GREATER, a=0, b=1)), 2) for k in windows]
    evaluator = TorchPrograms(programs, CATALOG)
    values, masks = evaluator(features, valid)
    for listing in range(4):
        expected, known = evaluator(features[listing], valid[listing])
        torch.testing.assert_close(values[:, listing], expected)
        assert torch.equal(masks[:, listing], known)
    changed = features.clone()
    changed[:, 100:] += 1000
    future_values, future_masks = evaluator(changed, valid)
    torch.testing.assert_close(values[..., :100], future_values[..., :100])
    assert torch.equal(masks[..., :100], future_masks[..., :100])


def test_listing_batch_constants_crosses_and_missing_evidence():
    features = torch.zeros(2, 8, len(CATALOG))
    features[0, :, 8] = torch.arange(8.)
    features[1, :, 8] = torch.arange(8.).flip(0)
    valid = torch.ones_like(features, dtype=torch.bool)
    valid[0, 4, 8] = False
    program = Program((Node(Op.FEATURE, feature=8),
                       Node(Op.CONSTANT, value=3.5, unit=CATALOG[8].unit),
                       Node(Op.CROSS_ABOVE, a=0, b=1)), 2)
    evaluator = TorchPrograms([program], CATALOG)
    values, masks = evaluator(features, valid)
    for listing in range(2):
        expected, known = evaluator(features[listing], valid[listing])
        torch.testing.assert_close(values[:, listing], expected)
        assert torch.equal(masks[:, listing], known)
    assert not masks[0, 0, 4:6].any()


@pytest.mark.parametrize('operation',[Op.LAG,Op.DIFFERENCE,Op.MEAN,Op.MINIMUM,Op.MAXIMUM])
@pytest.mark.parametrize('device',['cpu','cuda'])
def test_window_groups_match_unpruned_evaluator_exactly(operation,device):
    if device=='cuda' and not torch.cuda.is_available():pytest.skip('CUDA required')
    generator=torch.Generator().manual_seed(20261005)
    features=torch.randn(3,257,len(CATALOG),generator=generator).to(device)
    valid=(torch.rand(features.shape,generator=torch.Generator(device=device).manual_seed(4),device=device)>.03)
    windows=[1,3,10,60,30 if operation in (Op.LAG,Op.DIFFERENCE) else 120]*3
    programs=[Program((Node(Op.FEATURE,feature=8),Node(operation,a=0,window=k),Node(Op.GREATER,a=0,b=1)),2) for k in windows]
    actual,mask=TorchPrograms(programs,CATALOG,device=device)(features,valid)
    expected,known=TorchPrograms(programs,CATALOG,device=device,specialize_windows=False)(features,valid)
    assert torch.equal(actual,expected) and torch.equal(mask,known)
