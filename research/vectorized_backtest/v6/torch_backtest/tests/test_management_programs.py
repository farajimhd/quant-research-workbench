import torch
from research.vectorized_backtest.v6.torch_backtest.evolution import Individual,STAGES,MANAGEMENT_DEFAULT
from research.vectorized_backtest.v6.torch_backtest.program import Node,Op
from research.vectorized_backtest.v6.torch_backtest.program_runner import ProgramRunner
from research.vectorized_backtest.v6.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v6.torch_backtest.genome import StrategySpace,NAMES
from research.vectorized_backtest.v6.torch_backtest.financial_audit import audit_fills


def test_adds_and_partial_profit_reductions_keep_one_position_episode(tmp_path):
    space=StrategySpace();policy=space.default.copy();policy[4]=1
    for name,value in dict(minimum_dollar_volume=0,minimum_trade_count=0,initial_stop_fraction=.01,
        target_step_fraction=.20,terminal_exit_lead_seconds=5,remainder_policy_id=1).items():policy[space.policy_start+NAMES.index(name)]=value
    rules={s:[([Node(Op.CONSTANT,value=int(s=='entry'),unit='bool')],0)] for s in STAGES}
    management=dict(MANAGEMENT_DEFAULT,add_minimum_profit_fraction=0,reduce_minimum_profit_fraction=0,management_cooldown_seconds=3)
    member=Individual(policy.tolist(),rules,{s:[] for s in STAGES},management)
    tape=synthetic_tape(seconds=60,listings=1)
    # Low execution capacity gives the original order several partial fills.
    tape.volume.fill_(40);tape.notional=tape.volume*tape.close
    gates=torch.zeros((60,1,1),dtype=torch.uint8);gates[:10]=1;gates[25]|=16;gates[35]|=32
    runner=ProgramRunner(tape,space,[member],gates,backend='eager',maximum_fills=512)
    metrics=runner.run();count=int(runner.fill_count[0]);fills=runner.ledger[0,:count]
    assert int(runner.add_count.sum())>0
    reductions=fills[fills[:,7]==5]
    assert len(reductions)>0 and float(reductions[:,4].sum())>0
    assert int(metrics['open_quantity'][0])==0 and metrics['closed_positions'][0]==1
    assert len(metrics['closed_position_pnl_samples'][0])==1
    path=tmp_path/'fills.pt';torch.save(dict(ledger=runner.ledger[:,:count].cpu(),counts=runner.fill_count.cpu()),path)
    audit_fills(path,{name:value.tolist() if isinstance(value,torch.Tensor) else value for name,value in metrics.items()})
    assert torch.all(fills[fills[:,3]==1,0]>8)  # no fill at entry's decision boundary
