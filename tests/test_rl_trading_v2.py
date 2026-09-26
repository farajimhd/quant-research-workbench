"""V2 account, causal universe, execution, stochastic learning and restart contracts."""
from datetime import date
from dataclasses import replace
import numpy as np
import pytest
import torch

from research.rl_trading.v1.common import bounds, digest, file_hash
from research.rl_trading.v2.config import Config, share_cap
from research.rl_trading.v2.data import MarketSession, DATA_VERSION, ARRAYS
from research.rl_trading.v2.environment import TradingEnv
from research.rl_trading.v2.model import PortfolioPolicy, collate
from research.rl_trading.v2.objectives import advantages
from research.rl_trading.v2.io import write, read
from research.rl_trading.v2 import train, evaluate


def market(n=3, seconds=12, day='2026-08-20', prices=None):
    price = np.full((n,seconds),5.,dtype=np.float64) if prices is None else np.asarray(prices,dtype=np.float64)
    arrays = dict(features=np.zeros((n,seconds,3),dtype=np.float32),prices=price,
        volume=np.full((n,seconds),100000.,dtype=np.float64),
        volume_60s=np.broadcast_to(np.arange(n,0,-1)[:,None]*100000.,(n,seconds)).copy(),
        trades_60s=np.full((n,seconds),100.),fresh=np.ones((n,seconds),dtype=bool))
    arrays['features'][:,:,0] = np.log(price)
    plan = dict(version=DATA_VERSION,date=day,clock='completed_second',step_us=1000000,
        first_us=bounds(date.fromisoformat(day))[0],rows=seconds,segment=True,
        feature_names=['log_price','x','y'],teacher_dependency=False,
        listings=[dict(ticker=f'T{i:03d}',listing_id=f'L{i:03d}') for i in range(n)])
    plan['plan_hash'] = digest(plan)
    return MarketSession(plan,arrays)


def config(**overrides):
    return replace(Config(),entry_rank=overrides.pop('entry_rank',2),hold_rank=overrides.pop('hold_rank',3),
        history_seconds=4,liquidation_buffer_seconds=2,min_volume_60s=0,min_trades_60s=0,
        fee_ratio=overrides.pop('fee_ratio',0),base_slippage_ratio=overrides.pop('base_slippage_ratio',0),
        impact_ratio=overrides.pop('impact_ratio',0),volatility_slippage_ratio=0,
        max_volume_participation=1.,**overrides)


def act(env, ticker=None, mode=0, size=1.):
    obs = env.observe()
    modes = np.zeros(len(obs['ids']),dtype=np.int64)
    sizes = np.full(len(modes),size)
    if ticker is not None:
        modes[list(obs['ids']).index(ticker)] = mode
    return env.step(modes,sizes)


@pytest.mark.parametrize('price,cap',[(.5,40000),(.9999,40000),(1,35000),(5,35000),
    (5.0001,30000),(10,30000),(10.001,25000),(20,25000),(20.01,20000),
    (50,20000),(50.01,15000)])
def test_price_band_boundaries(price,cap):
    assert share_cap(price) == cap


def test_dynamic_buffer_identity_and_sticky_partial_exit():
    session = market(n=5)
    env = TradingEnv(session,config())
    act(env,0,1,.5)
    held = env.quantity[0]
    assert held == 1000
    session.arrays['volume_60s'][0,1:] = 150000  # Rank 4: outside top 3.
    session.arrays['volume'][0,2:] = 100
    obs = env.observe()
    slot = list(obs['ids']).index(0)
    assert obs['position'][slot,0] == pytest.approx(.5)
    assert obs['action_mask'][slot].tolist() == [True,False,False,False]
    act(env)
    assert env.quantity[0] == 900
    assert env.last_fills[0]['forced']
    # Reentry into top N does not cancel an already-issued mandatory liquidation.
    session.arrays['volume_60s'][0,2:] = 1000000
    assert not env.observe()['action_mask'][0,1]
    act(env)
    assert env.quantity[0] == 800


def test_buffer_allows_holding_and_closing_but_not_increasing():
    session = market(n=4)
    env = TradingEnv(session,config())
    act(env,0,1,.5)
    session.arrays['volume_60s'][0,1:] = 150000  # Below 300k,200k; above100k.
    obs = env.observe()
    slot = list(obs['ids']).index(0)
    assert obs['action_mask'][slot].tolist() == [True,False,True,True]
    act(env)
    assert env.quantity[0] == 1000
    act(env,0,3)
    assert env.quantity[0] == 0


def test_no_future_price_fill_and_net_reward_reconciliation():
    prices = np.full((1,12),5.)
    prices[:,1:] = 6.
    env = TradingEnv(market(n=1,prices=prices),config(fee_ratio=.001,base_slippage_ratio=.01))
    act(env,0,1,1.)
    fill = env.last_fills[0]
    assert fill['price'] == pytest.approx(6.06)
    assert fill['fill_second'] > fill['decision_second']
    assert env.cash >= 0
    while not env.done:
        act(env)
    report = env.summary()
    assert report['valid_terminal']
    assert env.reward_sum == pytest.approx((env.cash-env.initial)/env.initial)
    assert report['fees'] > 0 and report['slippage_dollars'] > 0
    assert report['net_profit'] == pytest.approx(-report['fees']-report['slippage_dollars'])


def test_share_cap_and_consecutive_impact():
    env = TradingEnv(market(n=1),config(initial_cash=1e6,impact_ratio=.01))
    act(env,0,1,1.)
    first = env.last_fills[0]
    assert first['shares'] == 35000
    act(env,0,1,1.)
    second = env.last_fills[0]
    assert second['shares'] == 35000
    assert second['slippage_ratio'] > first['slippage_ratio']


def test_budget_and_percentage_cap_joint_demands():
    env = TradingEnv(market(),config(max_ticker_weight=.6))
    env.step(np.array([1,1,0]),np.ones(3))
    assert env.cash >= 0
    assert np.all(env.quantity*5/env.equity <= .6)
    assert env.quantity[:2].tolist() == [1000,1000]


def test_stale_price_does_not_fill_and_unresolved_terminal_is_reported():
    session = market(n=1)
    env = TradingEnv(session,config())
    session.arrays['fresh'][0,1] = False
    act(env,0,1)
    assert not env.quantity.any() and env.metrics['unfilled_orders'] == 1
    act(env,0,1)
    session.arrays['fresh'][0,3:] = False
    while not env.done:
        act(env)
    assert env.quantity[0] > 0
    assert not env.summary()['valid_terminal']


def test_no_future_information_in_observation_or_rank():
    session = market()
    env = TradingEnv(session,config())
    before = env.observe()
    session.arrays['volume_60s'][:,1:] *= 100
    session.arrays['prices'][:,1:] *= 7
    session.arrays['features'][:,1:] = 99
    after = env.observe()
    for key in before:
        np.testing.assert_array_equal(before[key],after[key])


def test_all_120_candidates_and_outside_holdings_are_visible():
    env = TradingEnv(market(n=125),replace(Config(),history_seconds=2,liquidation_buffer_seconds=2))
    obs = env.observe()
    assert len(obs['ids']) == 120
    assert obs['action_mask'][:,1].sum() == 100
    env.quantity[124] = 1
    env.cash -= 5
    obs = env.observe()
    assert len(obs['ids']) == 121 and obs['ids'][-1] == 124
    assert obs['action_mask'][-1].tolist() == [True,False,False,False]


def test_normalized_state_and_capacity_context():
    first = TradingEnv(market(n=1),config(initial_cash=10000))
    second = TradingEnv(market(n=1),config(initial_cash=20000))
    np.testing.assert_array_equal(first.observe()['account'],second.observe()['account'])
    assert second.observe()['position'][0,5] > first.observe()['position'][0,5]
    act(first,0,1,.5)
    act(second,0,1,.5)
    np.testing.assert_allclose(first.observe()['account'],second.observe()['account'])
    assert second.quantity[0] == 2*first.quantity[0]


def test_model_equivariance_and_sampled_probability_reproduction():
    torch.manual_seed(4)
    obs = TradingEnv(market(),config()).observe()
    policy = PortfolioPolicy(3,width=16,heads=2)
    order = np.array([2,0,1])
    reordered = {key:(value[order] if key != 'account' else value) for key,value in obs.items()}
    first,second = collate([obs]),collate([reordered])
    p1,b1,v1 = policy(first)
    p2,b2,v2 = policy(second)
    torch.testing.assert_close(p1.probs[:,order],p2.probs)
    torch.testing.assert_close(b1.mean[:,order],b2.mean)
    torch.testing.assert_close(v1,v2)
    modes,sizes,logprob,_,_ = policy.action(first)
    _,_,recomputed,_,_ = policy.action(first,modes,sizes)
    torch.testing.assert_close(logprob,recomputed)


def test_empty_universe_and_padding_are_finite():
    session = market()
    session.arrays['volume_60s'][:] = 0
    env = TradingEnv(session,replace(config(),min_volume_60s=1))
    policy = PortfolioPolicy(3,width=16,heads=2)
    outputs = policy.action(collate([env.observe()]))
    assert all(torch.isfinite(x).all() for x in outputs)
    act(env)


def test_advantages_bootstrap_chunks_but_not_terminal():
    adv,ret = advantages([1.,2.],[.5,.5],[False,False],3.,gae_lambda=1.)
    np.testing.assert_allclose(ret,[6.,5.])
    adv,ret = advantages([1.,2.],[.5,.5],[False,True],999.,gae_lambda=1.)
    np.testing.assert_allclose(ret,[3.,2.])


def save_market(root,session):
    root.mkdir(parents=True)
    write(root/'plan.json',session.plan)
    for name,array in session.arrays.items():
        np.save(root/(name+'.npy'),array,allow_pickle=False)
    write(root/'complete.json',dict(plan_hash=session.plan['plan_hash'],listing_count=session.n,
        files={name+'.npy':file_hash(root/(name+'.npy')) for name in ARRAYS}))
    return root


def test_disk_integrity_rejects_changed_arrays(tmp_path):
    root = save_market(tmp_path/'data',market())
    MarketSession.load(root,allow_segment=True)
    with (root/'prices.npy').open('ab') as stream:
        stream.write(b'changed')
    with pytest.raises(ValueError,match='integrity'):
        MarketSession.load(root,allow_segment=True)


def test_cpu_training_resume_and_heldout_cli(tmp_path,monkeypatch):
    monkeypatch.setenv('QW_RUNTIME_ROOT',str(tmp_path))
    train_root = save_market(tmp_path/'training',market(seconds=8))
    val_root = save_market(tmp_path/'validation',market(seconds=8,day='2026-08-21'))
    test_root = save_market(tmp_path/'test',market(seconds=8,day='2026-08-24'))
    common = ['--train-sessions',str(train_root),'--val-sessions',str(val_root),
        '--allow-segment','--device','cpu','--width','16','--heads','2','--threads','1',
        '--history-seconds','4','--rollout-steps','5','--environments','2',
        '--epochs','2','--batch-size','5','--liquidation-buffer-seconds','2','--eval-every','1',
        '--entry-rank','2','--hold-rank','3']
    assert train.main(common+['--run-name','resume','--iterations','1']) == 0
    run = tmp_path/'rl-trading/v2/train/resume'
    first = torch.load(run/'checkpoint_latest.pt',weights_only=False)
    assert train.main(common+['--run-name','resume','--iterations','2','--resume']) == 0
    assert train.main(common+['--run-name','continuous','--iterations','2']) == 0
    resumed = torch.load(run/'checkpoint_latest.pt',weights_only=False)
    continuous = torch.load(tmp_path/'rl-trading/v2/train/continuous/checkpoint_latest.pt',weights_only=False)
    assert any(not torch.equal(first['policy'][k],resumed['policy'][k]) for k in first['policy'])
    for key in resumed['policy']:
        torch.testing.assert_close(resumed['policy'][key],continuous['policy'][key],rtol=0,atol=0)
    assert evaluate.main(['--run',str(run),'--test-sessions',str(test_root),'--allow-segment']) == 0
    with pytest.raises(ValueError,match='strictly later'):
        evaluate.main(['--run',str(run),'--test-sessions',str(val_root),'--allow-segment'])
    with pytest.raises(ValueError,match='Resume contract'):
        train.main(common+['--run-name','resume','--iterations','3','--resume','--fee-ratio','.02'])
    assert read(run/'status.json')['status'] == 'complete'


def test_arrival_band_cap_and_fees_are_applied_on_both_sides():
    prices = np.full((1,12),.9)
    prices[:,1:] = 1.1
    env = TradingEnv(market(n=1,prices=prices),config(initial_cash=1e6,fee_per_share=.005,minimum_fee=1.))
    act(env,0,1)
    assert env.last_fills[0]['shares'] == 35000  # Arrival band overrides 40k decision cap.
    assert env.last_fills[0]['fee'] == 175
    act(env,0,3)
    assert env.metrics['fees'] == 350
    assert env.equity == pytest.approx(env.initial-350)


def test_order_volume_limits_partial_fills():
    session = market(n=1)
    session.arrays['volume'][0,1] = 1000
    env = TradingEnv(session,replace(config(),max_volume_participation=.1))
    act(env,0,1)
    assert env.quantity[0] == 100
    assert env.metrics['partial_orders'] == 1


def test_direct_builder_restart_and_certificate_without_teacher(tmp_path,monkeypatch):
    from research.rl_trading.v2 import build_data
    monkeypatch.setenv('QW_RUNTIME_ROOT',str(tmp_path))
    manifest = tmp_path/'source.json'
    manifest.write_text('{}',encoding='utf-8')
    session = market()
    session.plan['segment'] = False
    listings = session.plan['listings']
    fake_source = dict(build_id='certified-source',definition_hash='source-hash',
                       units={'2026-08-20':{x['ticker']:{} for x in listings}})
    class Client:
        def close(self):
            pass
    monkeypatch.setattr(build_data,'ArteReader',lambda _:Client())
    monkeypatch.setattr(build_data,'load_env_files',lambda *a,**k:None)
    monkeypatch.setattr(build_data,'discover_clickhouse_env_files',lambda:[])
    monkeypatch.setattr(build_data.arte_source,'load_build',lambda *a:fake_source)
    monkeypatch.setattr(build_data.arte_source,'storage_check',lambda c:None)
    monkeypatch.setattr(build_data,'storage_check',lambda c:None)
    monkeypatch.setattr(build_data,'missing_seeds',lambda *a:[])
    monkeypatch.setattr(build_data.arte_source,'population',lambda *a:(listings,{'certificate':{'tradable_count':3}}))
    monkeypatch.setattr(build_data,'FEATURE_NAMES',tuple(session.plan['feature_names']))
    # Exercise full-day disk contract with a tiny feature count and zeroed market.
    seconds = build_data.SECONDS
    calls = []
    def extraction(c,s,d,listing):
        calls.append(listing['ticker'])
        arrays = {name:np.zeros((seconds,3) if name == 'features' else seconds,
                  dtype=np.bool_ if name == 'fresh' else np.float32 if name == 'features' else np.float64)
                  for name in ARRAYS}
        return arrays,{'certificate':'test'}
    monkeypatch.setattr(build_data,'extract',extraction)
    args = ['--manifest',str(manifest),'--ledger',str(tmp_path/'unused.db'),'--date','2026-08-20']
    assert build_data.main(args) == 0
    assert calls == [x['ticker'] for x in listings]
    assert build_data.main(args) == 0
    assert len(calls) == 3
    root = next((tmp_path/'rl-trading/v2/market/2026-08-20').iterdir())
    loaded = MarketSession.load(root)
    assert loaded.n == 3 and loaded.seconds == seconds
    assert loaded.plan['teacher_dependency'] is False
    (root/'complete.json').unlink()  # Simulate interrupted publication, retain checked listing prefixes.
    assert build_data.main(args) == 0
    assert len(calls) == 3
    # A selected source build must never masquerade as the full candidate population.
    monkeypatch.setattr(build_data.arte_source,'population',lambda *a:(listings,{'certificate':{'tradable_count':4}}))
    with pytest.raises(ValueError,match='entire certified'):
        build_data.main(args)


def test_direct_extraction_price_clock_and_rolling_activity(monkeypatch):
    import polars as pl
    from research.rl_trading.v2 import build_data
    bars = pl.DataFrame(dict(bucket_index=[14400,14401,14460],price_valid=[1,0,1],
        close_int=[50000,990000,60000],volume=[100.,200.,300.],trade_count=[2,3,4]))
    calls = []
    monkeypatch.setattr(build_data.arte_source,'verify_listing',lambda *a:calls.append(a[-1]))
    monkeypatch.setattr(build_data,'read_reference',lambda *a:({},[],{},{}))
    monkeypatch.setattr(build_data,'read_arte_seconds',lambda *a:(bars,None))
    monkeypatch.setattr(build_data,'encode',lambda *a:(np.zeros((build_data.SECONDS,3)),np.zeros(build_data.SECONDS)))
    arrays,_ = build_data.extract(None,{},date(2026,8,20),{'ticker':'A'})
    assert calls == ['A','A']
    assert arrays['prices'][0] == 0
    assert arrays['prices'][1] == 5 and arrays['prices'][2] == 5
    assert arrays['prices'][60] == 5 and arrays['prices'][61] == 6
    assert not arrays['fresh'][2] and arrays['fresh'][61]
    assert arrays['trades_60s'][60] == 5
    assert arrays['trades_60s'][61] == 7


@pytest.mark.parametrize('device',['cpu',pytest.param('cuda',marks=pytest.mark.skipif(
    not torch.cuda.is_available(),reason='Laptop CUDA unavailable'))])
def test_actual_launcher_with_120_candidates(tmp_path,monkeypatch,device):
    import os
    import subprocess
    import sys
    from pathlib import Path
    monkeypatch.setenv('QW_RUNTIME_ROOT',str(tmp_path))
    training = save_market(tmp_path/'training',market(n=125,seconds=8))
    validation = save_market(tmp_path/'validation',market(n=125,seconds=8,day='2026-08-21'))
    launcher = Path(train.__file__).with_name('run_train.py')
    command = [sys.executable,'-B',str(launcher),'--train-sessions',str(training),
        '--val-sessions',str(validation),'--run-name','wide-smoke','--allow-segment',
        '--device',device,'--iterations','1','--rollout-steps','4','--environments','2',
        '--epochs','1','--batch-size','4','--width','16','--heads','2','--threads','1',
        '--history-seconds','4','--liquidation-buffer-seconds','2']
    result = subprocess.run(command,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),
                            capture_output=True,text=True,timeout=90)
    assert result.returncode == 0, result.stdout+result.stderr
    run = tmp_path/'rl-trading/v2/train/wide-smoke'
    manifest = read(run/'run_manifest.json')
    assert manifest['config']['entry_rank'] == 100 and manifest['config']['hold_rank'] == 120
    assert read(run/'metrics/000001.json')['updates'] > 0
