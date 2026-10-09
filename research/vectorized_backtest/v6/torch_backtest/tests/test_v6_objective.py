import torch
from research.vectorized_backtest.v6.torch_backtest.stability import LowerTailDollarObjective,score


def test_lower_tail_rewards_weak_days_and_keeps_best_day():
    pnl=torch.tensor([[10.,10.,10.],[20.,20.,20.],[30.,40.,30.],[40.,40.,50.]],dtype=torch.float64)
    zeros=torch.zeros_like(pnl);valid=torch.ones_like(pnl,dtype=torch.bool)
    config=LowerTailDollarObjective(tail_fraction=.5,drawdown_weight=0,stop_risk_weight=0,capital_time_weight=0,complexity_weight=0,inactivity_weight=0)
    result=score(pnl,zeros,zeros,zeros,zeros,valid,torch.zeros(3),config=config,inactivity=torch.zeros(3))
    assert result['score'][1]>result['score'][0]
    assert result['score'][2]>result['score'][0]
    assert 'tail_penalty' not in result['components']
    assert float(result['components']['lower_tail_profit_reward'][0])==15.
