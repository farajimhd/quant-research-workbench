from types import SimpleNamespace
import torch
from .loader_audit import describe


def test_frame_counts_and_source_valid_last_bar():
    grid = torch.ones(3, 64, 18)
    valid = torch.ones_like(grid, dtype=torch.bool)
    valid[0, -1, 4] = False
    grid[0, -1, 4] = 999
    result = describe(SimpleNamespace(grid=grid, valid=valid, frames=torch.tensor([1, 1, 2])))
    assert result['1']['windows'] == 2
    assert result['1']['last_bar']['4']['valid'] == 1
    assert result['1']['last_bar']['4']['raw_sum'] == 1
