"""Different session widths retain exact independent-account fills on reuse."""
import numpy as np
import torch
from research.vectorized_backtest.v4.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v4.torch_backtest.evolution import sample
from research.vectorized_backtest.v4.torch_backtest.genome import StrategySpace
from research.vectorized_backtest.v4.torch_backtest.program_runner import ProgramRunner
from research.vectorized_backtest.v4.torch_backtest.session_pool import padded_tape,bind_tape


def test_packed_program_graph_rebinds_different_widths_and_real_timestamps():
    torch.set_num_threads(1)
    space=StrategySpace();population=sample(np.random.default_rng(12),space,4)
    for candidate in population:
        candidate.policy=space.default.tolist();candidate.policy[4]=1
        candidate.policy[11]=0;candidate.policy[12]=0
    reusable=None
    for listings,offset in ((3,0),(5,86400),(2,172800)):
        source=synthetic_tape(seconds=90,listings=listings)
        source.structural_targets=source.level_lower[None].expand(90,-1,-1).clone()
        for name in ('clocks','admission','level_from','level_to'):
            getattr(source,name).add_(offset)
        source.provenance.update(start_second=1+offset,end_second=90+offset)
        source.validate()
        raw=torch.full((90,4,listings),1|4|8,dtype=torch.uint8)
        raw[30:]|=2
        reference=ProgramRunner(source,space,population,raw,backend='eager',maximum_fills=512).compile()
        expected=reference.run()
        working=padded_tape(source,8,'cpu')
        gates=torch.zeros((90,4,8),dtype=torch.uint8);gates[:,:,:listings]=raw
        if reusable is None:
            reusable=ProgramRunner(working,space,population,gates,backend='eager',maximum_fills=512).compile()
        else:
            bind_tape(reusable.tape,working)
            reusable.start_boundary.fill_(1+offset);reusable.end_boundary.fill_(90+offset)
            reusable.set_population(population,gates)
        actual=reusable.run()
        for name in ('cash','equity','fees','realized','drawdown','fill_count',
                     'sold_share_seconds','capital_dollar_seconds','stop_risk_dollar_seconds',
                     'closed_positions','winning_positions','losing_positions','gross_profit','gross_loss'):
            torch.testing.assert_close(actual[name],expected[name],rtol=1e-12,atol=1e-8)
        for lane,count in enumerate(reference.fill_count.tolist()):
            assert count>0
            torch.testing.assert_close(reusable.ledger[lane,:count],reference.ledger[lane,:count],rtol=0,atol=0)
        assert not reusable.quantity[:,listings:].any()
