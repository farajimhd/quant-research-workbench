import numpy as np
import torch
from .data import target_indices
from .model import Forecast, SLOTS, objective
from ..phase1_v29.model import Autoencoder
from ..phase0_v7.test_model import parameters
from ..phase0_v7.objectives import objective as v29_objective


def test_boundary_and_missing_horizon():
    closes = np.array([10,20,40])
    starts = closes - 10
    origins = np.array([0,1,10,11,20,30,31,40])
    expected = [[0,1],[1,2],[1,2],[2,-1],[2,-1],[2,-1],[-1,-1],[-1,-1]]
    np.testing.assert_array_equal(target_indices(closes,starts,origins,2), expected)


def test_forecast_shape_teacher_frozen_and_loss_parity():
    teacher = Autoencoder(parameters()).eval().requires_grad_(False)
    model = Forecast()
    windows = torch.randn(4,5,64,32)
    z = model(windows)
    assert z.shape == (4,SLOTS,32)
    assert model.encode(windows).shape == (4,128)
    out = teacher.decode(z)
    target = torch.randn_like(out["logs"])
    valid = torch.rand_like(target) > .2
    start = 0
    terms = []
    for width in (10,6,1,1,1):
        part = slice(start,start+width)
        terms.append(v29_objective(teacher,{k:v[:,part] for k,v in out.items()},target[:,part],valid[:,part])[0])
        start += width
    loss = objective(teacher,out,target,valid)
    torch.testing.assert_close(loss,torch.stack(terms).mean())
    loss.backward()
    assert model.heads.weight.grad.isfinite().all()
    assert teacher.decoder.weight.grad is None
