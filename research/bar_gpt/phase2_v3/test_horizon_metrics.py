import numpy as np
import pytest
import torch
from .horizon_metrics import HorizonPriceRelative
from .resume_metrics import changed_files
from ..phase1_v10.metrics import PriceRelative


def test_horizon_native_parity_masks_and_denominators():
    n=3
    target=np.ones((n,18,64))*10
    error=np.broadcast_to(np.arange(1,65)*.01,(n,18,64)).copy()
    valid=np.ones_like(target,dtype=bool)
    valid[0,0,0]=False;target[1,0,1]=0;target[2,0,2]=1e-13
    frames=np.zeros(n,dtype=np.int64)
    tensors=[torch.from_numpy(x) for x in (error,target,valid,frames)]
    old=PriceRelative();new=HorizonPriceRelative();old.add(*tensors)
    # Partition/merge follows the two asynchronous native reducer lanes.
    other=HorizonPriceRelative();new.add(*(x[:1] for x in tensors));other.add(*(x[1:] for x in tensors));new.merge(other)
    for key in ('sums','counts','bad','excluded','hist'):np.testing.assert_array_equal(getattr(old,key),getattr(new,key))
    for bar in range(64):
        for c in range(18):
            mask=valid[:,c,bar]
            assert new.h_counts[bar,c]==mask.sum()
            assert new.h_sums[bar,c]==pytest.approx(error[:,c,bar][mask].sum())
            if c%6<4:
                mask=mask&(target[:,c,bar]>0)
                assert new.h_eligible[bar,c]==mask.sum()
                assert new.h_relative[bar,c]==pytest.approx((100*error[:,c,bar][mask]/target[:,c,bar][mask]).sum())
    assert new.h_excluded[1,0]==1 and new.h_tiny[2,0]==1
    result=new.metrics('raw_mae',('1s','10s','1m','10m','1h','1d'),tuple(f'c{i}' for i in range(18)))
    assert result['horizon/bar_64/raw_mae/trade_open']==pytest.approx(.64)
    assert result['horizon/bar_1/price_mape_pct/trade_open']==pytest.approx(.1)


def test_instrumentation_resume_rejects_model_change():
    assert changed_files({}, {'research/bar_gpt/phase2_v3/horizon_metrics.py':'new'})
    with pytest.raises(ValueError,match='non-metric'):
        changed_files({'research/bar_gpt/phase2_v3/model.py':'old'}, {'research/bar_gpt/phase2_v3/model.py':'new'})
