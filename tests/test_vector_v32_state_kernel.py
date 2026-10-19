"""Exact regression against the pre-fusion V31/V32 state kernel."""
import pytest
import torch


@pytest.mark.skipif(not torch.cuda.is_available(), reason='CUDA required')
@pytest.mark.parametrize('u', [1, 3, 255, 256, 257, 622])
def test_fused_state_channels_exact(u):
    import triton
    from research.vectorized_backtest.v31.network_cuda import state_kernel as reference
    from research.vectorized_backtest.v32.network_cuda import state_kernel
    from research.vectorized_backtest.v32.config import STATES
    generator = torch.Generator(device='cuda').manual_seed(2020)
    b, s = 32, len(STATES)
    def random(shape, dtype=torch.float64):
        return torch.randn(shape, device='cuda', dtype=dtype, generator=generator)
    position = random((b, u, 9))
    position[..., 0] *= 100
    position[..., 1].abs_().add_(1)
    opened = random((b, u))
    prices = random((u,)) * 20
    if u > 3:
        prices[:3] = torch.tensor([float('nan'), float('inf'), 0.], device='cuda')
    clock = torch.tensor(19799, device='cuda')
    feedback = random((b, u, s - 7), torch.float32)
    expected = torch.empty((b, s, u), device='cuda')
    actual = torch.empty_like(expected)
    inputs = position, opened, prices, clock, feedback
    reference[(b, s, triton.cdiv(u, 256))](*inputs, expected, u, s, 256, enable_fp_fusion=False)
    state_kernel[(b, triton.cdiv(u, 256))](*inputs, actual, u, s, 256, enable_fp_fusion=False)
    assert torch.equal(expected, actual)
