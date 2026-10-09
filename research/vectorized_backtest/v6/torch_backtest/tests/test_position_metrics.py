import pytest
import torch
from research.vectorized_backtest.v6.torch_backtest.position_metrics import duration_summary
from research.vectorized_backtest.v6.torch_backtest.program_runner import ProgramRunner


def test_partial_fills_preserve_first_fill_duration_and_slot_reuse():
    runner=ProgramRunner.__new__(ProgramRunner)
    rows=[[10,0,0,1,5,10,1,0,1],[12,0,0,1,5,11,1,0,2],
          [20,0,0,-1,7,12,1,1,3],[22,0,0,-1,3,13,1,1,4],
          [100,0,0,1,1,10,1,0,5],[104,0,0,-1,1,13,1,1,6]]
    runner.ledger=torch.tensor([rows],dtype=torch.float64)
    runner.fill_count=torch.tensor([4]);runner._report_counts=torch.tensor([0])
    runner._report_lots=[{}];runner._trade_totals=torch.zeros(1,5,dtype=torch.float64)
    runner._report_durations=[[]];runner._report_pnls=[[]];runner._holding_totals=torch.zeros(1,2,dtype=torch.float64)
    runner._update_trade_report()
    assert runner._report_pnls==[[14.]]
    assert runner._report_durations==[[12]]
    assert runner._holding_totals.tolist()==[[106,10]]
    runner.fill_count[0]=6;runner._update_trade_report();runner._update_trade_report()
    assert runner._report_durations==[[12,4]] # reused slot resets first-fill timestamp
    assert runner._holding_totals.tolist()==[[110,11]] # no double-counting on refresh
    stats=duration_summary(runner._report_durations[0])
    assert stats['closed_hold_mean_seconds']==8
    assert stats['closed_hold_median_seconds']==8
    assert stats['closed_hold_p90_seconds']==pytest.approx(11.2)
    assert all(v is None for v in duration_summary([]).values())


def test_duration_summary_rejects_negative_or_nonfinite_time():
    for samples in ([-1],[float('nan')],[float('inf')]):
        with pytest.raises(ValueError,match='elapsed seconds'):duration_summary(samples)


def test_cross_session_quantiles_pool_positions_instead_of_averaging_daily_quantiles():
    from research.vectorized_backtest.v6.torch_backtest.metrics import financial_metrics
    base={name:[0.] for name in ('net_pnl','drawdown','filled_batches','positions_opened',
          'fill_count','open_positions','sold_share_seconds','sold_shares',
          'stop_risk_dollar_seconds','capital_dollar_seconds')}
    results=[dict(base,closed_position_duration_samples=[[1,10]]),
             dict(base,closed_position_duration_samples=[[100]])]
    metrics=financial_metrics(results)
    assert metrics['closed_hold_mean_seconds'].item()==37
    assert metrics['closed_hold_median_seconds'].item()==10
    assert metrics['closed_hold_p90_seconds'].item()==pytest.approx(82)


def test_live_open_position_age_uses_replay_timestamp():
    import numpy as np
    from research.vectorized_backtest.v6.torch_backtest.fixtures import synthetic_tape
    from research.vectorized_backtest.v6.torch_backtest.genome import StrategySpace
    from research.vectorized_backtest.v6.torch_backtest.evolution import sample
    tape=synthetic_tape(seconds=90,listings=1)
    space=StrategySpace();population=sample(np.random.default_rng(12),space,4)
    for candidate in population:
        candidate.policy=space.default.tolist();candidate.policy[4]=1
        candidate.policy[11]=0;candidate.policy[12]=0
    gates=torch.ones((90,4,1),dtype=torch.uint8)
    runner=ProgramRunner(tape,space,population,gates,backend='eager',maximum_fills=512).compile()
    runner.run(steps=20)
    live=runner.live_metrics()
    buy=runner.ledger[0,:int(runner.fill_count[0])]
    first=float(buy[buy[:,3]==1][0,0])
    assert live['open_age_mean_seconds']==float(tape.clocks[19])-first
    assert live['closed_hold_mean_seconds'] is None
