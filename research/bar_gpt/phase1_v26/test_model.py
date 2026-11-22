import torch
from .model import Fusion
from .gates import check


def test_identity_fusion_and_learnable_gradient():
    torch.manual_seed(8);z=torch.randn(3,64,64);m=Fusion(17);out=m(z)
    torch.testing.assert_close(out,z,rtol=0,atol=0);out.square().mean().backward()
    assert m.output.weight.grad.abs().sum()>0
    assert all(p.grad is None or p.grad.isfinite().all() for p in m.parameters())


def test_retention_gate_rejects_price_regression():
    m={'all_visible/raw_mae/1s/trade_close':1.,'all_visible/raw_mae/1s/trade_volume':2.,
       'all_visible/candle/1s/trade/count':100,'all_visible/candle/1s/trade/moving_direction_pct':90.,
       'all_visible/range/1s/trade/count':100,'all_visible/range/1s/trade/bin_accuracy_pct':40.,
       'activity/all_visible/1s/trade_volume/count':100,'activity/all_visible/1s/trade_volume/balanced_direction_pct':69.}
    assert check(m,m)['accepted'];bad=dict(m);bad['all_visible/candle/1s/trade/moving_direction_pct']=88.
    assert not check(bad,m)['accepted']
