"""Real CUDA captured-prefix equivalence against eager independent replay."""
import numpy as np
import torch
import pytest
from research.vectorized_backtest.v5.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v5.torch_backtest.genome import StrategySpace
from research.vectorized_backtest.v5.torch_backtest.evolution import sample
from research.vectorized_backtest.v5.torch_backtest.program_runner import ProgramRunner
from research.vectorized_backtest.v5.torch_backtest.runtime import configure_caches,DEFAULT


@pytest.mark.skipif(not torch.cuda.is_available(),reason='Real CUDA qualification required on workstation')
def test_captured_whole_block_prefix_matches_eager_metrics_and_fills():
    configure_caches(DEFAULT/'tests'/'prefix-cuda')
    space=StrategySpace();population=sample(np.random.default_rng(7),space,3)
    gates=torch.full((96,3,2),5,dtype=torch.uint8)
    eager=ProgramRunner(synthetic_tape(seconds=96),space,population,gates,backend='eager',maximum_fills=256)
    expected=eager.run(steps=64)
    captured=ProgramRunner(synthetic_tape(seconds=96,device='cuda'),space,population,gates.cuda(),backend='compiled_graph',graph_steps=32,maximum_fills=256)
    captured.compile();result=captured.run(steps=64)
    assert not result['terminal'] and captured.completed==64
    for key,value in expected.items():
        if isinstance(value,torch.Tensor):torch.testing.assert_close(result[key].cpu(),value,rtol=1e-12,atol=1e-12,equal_nan=True)
    for lane,count in enumerate(eager.fill_count.tolist()):
        torch.testing.assert_close(captured.ledger[lane,:count].cpu(),eager.ledger[lane,:count],rtol=1e-12,atol=1e-12)
    with pytest.raises(ValueError):captured.run(steps=1)
    with pytest.raises(ValueError):captured.run(reset=False,steps=32)
