import torch
from .precision_audit import precise_logs
from ..phase1_v11.transforms import decode


def test_sub_ulp_body_survives_double_coordinate_assembly():
    code = torch.zeros(1, 64, 18)
    for base in (0, 6, 12):
        code[..., base] = 5.
        code[..., base+1] = 1e-8
    old = decode(code, torch.zeros(1, 9), constrain=False)
    new = precise_logs(code)
    assert (old[..., 0] == old[..., 3]).all()
    assert (new[..., 0] > new[..., 3]).all()
    torch.testing.assert_close(new[..., 0]-new[..., 3], torch.full((1, 64), 1e-8, dtype=torch.float64), rtol=1e-6, atol=0)
