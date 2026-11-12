import math

import pytest
import torch

from research.bar_gpt.v5.engine_v3 import LossReadback
from research.bar_gpt.v5.run_encoder_v3 import arguments
from research.bar_gpt.v5.wandb_identity_v1 import signature


def test_readback_flag_is_operational_and_defaults_to_original_behavior():
    baseline=arguments([])
    candidate=arguments(['--async-loss-readback'])
    assert baseline.async_loss_readback is False
    assert candidate.async_loss_readback is True
    assert signature(vars(baseline))==signature(vars(candidate))


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA scalar lifetime/readback test')
def test_readback_preserves_scalar_gradients_rng_and_nonfinite_detection():
    readback=LossReadback()
    parameter=torch.tensor([1.,2.,3.],device='cuda',requires_grad=True)
    rng=torch.cuda.get_rng_state().clone()
    loss=parameter.square().sum()
    readback.submit(loss)
    loss.backward()
    assert readback.value()==14.
    assert torch.equal(parameter.grad,torch.tensor([2.,4.,6.],device='cuda'))
    assert torch.equal(rng,torch.cuda.get_rng_state())
    readback.submit(torch.tensor(float('nan'),device='cuda'))
    assert not math.isfinite(readback.value())
