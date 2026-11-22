import pytest
import torch
from .gradient_audit import geometry


def test_gradient_alignment_and_zero_are_distinguished():
    result = geometry(dict(a=torch.tensor([1., 2.]), b=torch.tensor([-2., -4.]), c=torch.zeros(2)))
    assert result['cosines']['a:b'] == pytest.approx(-1.)
    assert result['cosines']['a:c'] is None
    with pytest.raises(FloatingPointError):
        geometry(dict(a=torch.tensor([float('nan')])))


def test_real_loss_audit_does_not_update_weights():
    from .gradient_audit import audit
    from ..phase1_v20.test_model import build
    model, _, _ = build()
    model.repair_arm = 'joint_repaired'
    before = {k: v.clone() for k, v in model.state_dict().items()}
    values = torch.rand(2, 64, 27)
    result = audit(model, (values, torch.ones_like(values, dtype=torch.bool), values.exp(),
                          torch.ones(2), torch.arange(16).expand(2, -1), torch.zeros(2, 64, 16)))
    assert set(result['gradients']) == {'bar', 'temporal', 'decoder'}
    for k, v in model.state_dict().items():
        torch.testing.assert_close(v, before[k], rtol=0, atol=0)
