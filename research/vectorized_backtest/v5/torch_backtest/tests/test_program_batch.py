import pytest
import torch
from research.vectorized_backtest.v5.torch_backtest.feature_bank import CATALOG
from research.vectorized_backtest.v5.torch_backtest.program import Node, Program, Op, TorchPrograms

def test_one_candle_mean_is_exact_identity_and_invalid_outputs_are_zero():
    features=torch.rand(3,250,len(CATALOG),generator=torch.Generator().manual_seed(9))
    valid=torch.ones_like(features,dtype=torch.bool);valid[:,17,8]=False
    program=Program((Node(Op.FEATURE,feature=8),Node(Op.MEAN,a=0,window=1),Node(Op.GREATER,a=0,b=1)),2)
    values,known=TorchPrograms([program],CATALOG)(features,valid)
    assert not values.any() and not known[:,:,17].any()


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
