import torch
from .pretraining_v3 import local_references, CLOSES, ACTIVITY


def test_cpu_medians_match_legacy_exactly_with_missing_and_extreme_values():
    torch.manual_seed(91)
    values = torch.exp(torch.rand(127, 64, 18) * 60 - 20)
    valid = torch.rand(values.shape) > .37
    values[..., ACTIVITY] *= (torch.rand(127, 64, 6) > .3)
    valid[0, :, ACTIVITY] = False
    price, quantity = local_references(values, valid)
    closes = torch.where(valid[..., CLOSES], values[..., CLOSES], torch.nan)
    expected_price = torch.nanquantile(closes.flatten(1).double(), .5, dim=1)[:, None, None]
    activity = torch.where(valid[..., ACTIVITY], values[..., ACTIVITY], torch.nan)
    expected_quantity = torch.nanquantile(activity.transpose(1, 2).contiguous().double(), .5, dim=-1)
    expected_quantity = torch.where(valid[..., ACTIVITY].any(1), expected_quantity, 0)
    assert torch.equal(price, expected_price)
    assert torch.equal(quantity, expected_quantity)
