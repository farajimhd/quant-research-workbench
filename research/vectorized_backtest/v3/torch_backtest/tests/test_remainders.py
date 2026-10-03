"""Causal remainder transitions with deterministic financial witnesses."""
from dataclasses import replace
import numpy as np
import pytest
import torch
from research.vectorized_backtest.v3.torch_backtest import Settings, Candidate, SqueezeRunner
from research.vectorized_backtest.v3.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v3.torch_backtest.genome import StrategySpace, NAMES
from research.vectorized_backtest.v3.torch_backtest.search_runner import SearchRunner


def runner(mode=3, **kwargs):
    r=SqueezeRunner(synthetic_tape(seconds=40),[Candidate('signal',positions=1)],
       replace(Settings(),participation=kwargs.pop('participation',0.10),
               remainder_policy_id=mode,entry_deadline_seconds=1,**kwargs))
    r.requested_quantity[0,0,0]=10
    r.remaining[0,0,0]=8
    r.buy_filled[0,0,0]=2
    r.quantity[0,0,0]=2
    r.average[0,0,0]=10
    r.buy_paid[0,0,0]=1
    r.buy_limit[0,0,0]=10.1
    r.buy_reference[0,0,0]=10
    r.buy_created[0,0,0]=1
    r.buy_submitted[0,0,0]=1
    r.buy_last_retry[0,0,0]=1
    r.buy_deadline[0,0,0]=2
    return r


def manage(r,now,ask=10,valid=True):
    r._manage_remainders(torch.tensor(now),torch.full((r.n,),float(ask)),
                         torch.full((r.b,r.n),valid))


def broker(r,now):
    price=torch.full((r.n,),10.,dtype=torch.float64)
    quote=torch.ones(r.n,dtype=torch.bool)
    r._broker(torch.tensor(now),price,price,price,quote,price,price,
              torch.full((r.n,),20.),price,quote)


def test_resubmission_keeps_quantity_fees_and_cannot_fill_same_interval():
    r=runner()
    broker(r,2)  # two shares: 20*0.10, shared interval budget
    assert r.remaining[0,0,0]==6
    manage(r,2)
    assert r.buy_submitted[0,0,0]==2 and r.buy_deadline[0,0,0]==3
    assert r.buy_retries[0,0,0]==1 and r.buy_filled[0,0,0]==4
    broker(r,2)  # amendment cannot act on its observed interval
    assert r.buy_filled[0,0,0]==4
    broker(r,3)
    assert r.buy_filled[0,0,0]==6
    assert r.requested_quantity[0,0,0]==10 and r.buy_paid[0,0,0]==1


def test_approved_default_cap_and_retry_interval_capacity():
    """Twenty eligible shares permit five fills, including a continued parent."""
    from research.vectorized_backtest.v3.torch_backtest.encoding.config import Broker
    assert Settings().participation == Broker().participation == 0.25
    r = runner(participation=Settings().participation)
    broker(r, 2)
    assert r.buy_filled[0, 0, 0] == 7  # Two prior shares plus five now.
    assert r.remaining[0, 0, 0] == 3
    manage(r, 2)
    broker(r, 3)
    assert r.buy_filled[0, 0, 0] == 10
    assert r.remaining.sum() == 0


def test_import_does_not_initialize_compiler_before_runtime_setup():
    """Pinned workstation Triton is configured after source modules import."""
    import os
    import subprocess
    import sys

    source = """
from unittest.mock import patch
with patch('torch.compile', side_effect=AssertionError('premature compiler')):
    import research.vectorized_backtest.v3.torch_backtest.remainder_update
"""
    subprocess.run(
        [sys.executable, '-B', '-c', source],
        check=True,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True,
        text=True,
    )


def test_retry_count_total_age_and_signal_boundaries():
    r=runner(maximum_retries=1)
    manage(r,2); manage(r,3)
    assert r.remaining.sum()==0 and r.expired_entry_shares.sum()==8
    r=runner(maximum_total_order_age_seconds=2)
    manage(r,2); manage(r,3)
    assert r.remaining.sum()==0
    r=runner(require_signal_valid=1)
    manage(r,2,valid=False)
    assert r.remaining.sum()==0 and r.policy_cancelled_entry_shares.sum()==8


def test_cancel_partial_and_retain_have_distinct_policies():
    r=runner(mode=0); manage(r,1)
    assert r.remaining.sum()==0 and r.policy_cancelled_entry_shares.sum()==8
    r=runner(mode=1); manage(r,1)
    assert r.remaining.sum()==8
    manage(r,2)
    assert r.remaining.sum()==0 and r.buy_retries.sum()==0


def test_repricing_bounded_by_reference_and_cash_reservation():
    r=runner(mode=2,maximum_chase_bps=50)
    manage(r,2,ask=12)
    assert r.buy_limit[0,0,0]<=10.05+1e-10
    r=runner(mode=2,maximum_chase_bps=500)
    r.cash.fill_(85)  # existing cover, no free cash for the whole price increase
    manage(r,2,ask=12)
    cover=(r.remaining*r.buy_limit).sum()+r._exit_fee_reserve().sum()
    assert cover<=r.cash[0]+1e-7
    assert r.buy_limit[0,0,0]<10.5


def test_unfilled_orders_do_not_gain_partial_retry_privileges():
    r=runner(); r.buy_filled.zero_(); r.quantity.zero_()
    manage(r,2)
    assert r.buy_retries.sum()==0 and r.remaining.sum()==0


def test_retry_state_checkpoint_and_search_class_ids():
    space=StrategySpace(); rows=np.tile(space.default,(4,1))
    rows[:,space.policy_start+NAMES.index('remainder_policy_id')]=np.arange(4)
    r=SearchRunner(synthetic_tape(seconds=45),space,rows)
    r.run(steps=15)
    checkpoint=r.state_dict()
    clone=SearchRunner(r.tape,space,rows); clone.load_state_dict(checkpoint)
    actual=r.run(); restored=clone.run()
    for name in ('cash','net_pnl','entry_retry_count','policy_cancelled_entry_shares'):
        assert torch.equal(actual[name],restored[name])
    assert space.size==77


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA unavailable')
def test_all_remainder_policy_ids_compiled_gpu_match_cpu(tmp_path):
    from research.vectorized_backtest.v3.torch_backtest.runtime import configure_caches
    configure_caches(tmp_path)
    torch.compiler.reset()
    space=StrategySpace(); rows=np.tile(space.default,(4,1)); rows[:,4]=1
    for name,values in dict(remainder_policy_id=np.arange(4),entry_deadline_seconds=1,
                            maximum_retries=3,minimum_dollar_volume=0,minimum_trade_count=0).items():
        rows[:,space.policy_start+NAMES.index(name)]=values
    tape=synthetic_tape(seconds=45); tape.volume.fill_(20)
    cpu=SearchRunner(tape,space,rows); expected=cpu.run()
    assert expected['entry_retry_count'][2:].sum()>0
    gpu=SearchRunner(tape.to('cuda'),space,rows,backend='compiled_graph',graph_steps=8).compile()
    actual=gpu.run()
    for name in ('cash','net_pnl','entry_retry_count','policy_cancelled_entry_shares',
                 'expired_entry_shares','requested_entry_shares','filled_entry_shares'):
        assert torch.allclose(actual[name].cpu(),expected[name],rtol=0,atol=1e-7),name
    for name in ('remaining','buy_created','buy_submitted','buy_deadline','buy_retries','buy_limit'):
        assert torch.allclose(getattr(gpu,name).cpu(),getattr(cpu,name),rtol=0,atol=1e-7),name
    assert torch.allclose(gpu.ledger.cpu(),cpu.ledger,rtol=0,atol=1e-7)


def test_objective_receipts_include_retry_and_quantity_reconciliation():
    from research.vectorized_backtest.v3.torch_backtest.search_objective import SessionObjective
    space=StrategySpace(); rows=space.default[None].copy(); rows[:,4]=1
    rows[:,space.policy_start+NAMES.index('remainder_policy_id')]=3
    rows[:,space.policy_start+NAMES.index('entry_deadline_seconds')]=1
    tape=synthetic_tape(seconds=45); tape.volume.fill_(20)
    result=SessionObjective(tape,space,1)(rows)
    assert result['entry_retry_count'][0]>0
    assert result['requested_entry_shares'][0]==sum(result[name][0] for name in (
        'filled_entry_shares','pending_entry_shares','expired_entry_shares',
        'policy_cancelled_entry_shares','exit_cancelled_entry_shares','terminal_cancelled_entry_shares'))
