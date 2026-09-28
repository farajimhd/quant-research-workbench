"""V2 account, causal universe, execution, stochastic learning and restart contracts."""
from datetime import date
from dataclasses import replace
import json
import numpy as np
import pytest
import torch

from research.rl_trading.v1.common import bounds, digest, file_hash
from research.rl_trading.v2.config import Config, share_cap
from research.rl_trading.v2.data import MarketSession, DATA_VERSION, ARRAYS
from research.rl_trading.v2.environment import TradingEnv
from research.rl_trading.v2.model import PortfolioPolicy, collate
from research.rl_trading.v2.objectives import advantages, ticker_returns, ppo_loss
from research.rl_trading.v2.io import write, read
from research.rl_trading.v2 import train, evaluate


def market(n=3, seconds=12, day='2026-08-20', prices=None):
    price = np.full((n,seconds),5.,dtype=np.float64) if prices is None else np.asarray(prices,dtype=np.float64)
    arrays = dict(features=np.zeros((n,seconds,3),dtype=np.float32),prices=price,
        execution_open=price.copy(),
        volume=np.full((n,seconds),100000.,dtype=np.float64),
        volume_60s=np.broadcast_to(np.arange(n,0,-1)[:,None]*100000.,(n,seconds)).copy(),
        trades_60s=np.full((n,seconds),100.),fresh=np.ones((n,seconds),dtype=bool),
        estimated_reference=np.full((n,seconds),5.,dtype=np.float32),
        prior_close=np.full(n,5.,dtype=np.float32))
    arrays['features'][:,:,0] = np.log(price)
    plan = dict(version=DATA_VERSION,date=day,clock='completed_second',step_us=1000000,
        first_us=bounds(date.fromisoformat(day))[0],rows=seconds,segment=True,
        feature_names=['log_price','x','y'],teacher_dependency=False,
        listings=[dict(ticker=f'T{i:03d}',listing_id=f'L{i:03d}') for i in range(n)])
    plan['plan_hash'] = digest(plan)
    return MarketSession(plan,arrays)


def config(**overrides):
    return replace(Config(),entry_rank=overrides.pop('entry_rank',2),hold_rank=overrides.pop('hold_rank',3),
        commission_model=overrides.pop('commission_model','research'),
        history_seconds=4,liquidation_buffer_seconds=2,min_volume_60s=0,min_trades_60s=0,
        fee_ratio=overrides.pop('fee_ratio',0),base_slippage_ratio=overrides.pop('base_slippage_ratio',0),
        impact_ratio=overrides.pop('impact_ratio',0),volatility_slippage_ratio=0,
        max_volume_participation=1.,**overrides)


def act(env, ticker=None, mode=0, size=1.):
    obs = env.observe()
    modes = np.zeros(len(obs['ids']),dtype=np.int64)
    sizes = np.full((len(modes),3),.5)
    sizes[:,0] = size
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
    assert report['realized_net_pnl'] == pytest.approx(report['net_profit'])
    assert report['open_unrealized_pnl'] == 0
    assert report['policy_buy_decisions'] == 1
    assert report['policy_pass_decisions'] == env.session.seconds-2


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
    env.step(np.array([1,1,0]),np.ones((3,3)))
    assert env.cash >= 0
    assert np.all(env.quantity*5/env.equity <= .6)
    assert env.quantity[:2].tolist() == [1000,1000]


def test_stale_price_does_not_fill_and_unresolved_terminal_is_reported():
    session = market(n=1)
    env = TradingEnv(session,config())
    session.arrays['fresh'][0,1] = False
    act(env,0,1)
    assert not env.quantity.any() and env.metrics['unfilled_orders'] == 1
    session.arrays['fresh'][0,1] = True
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


def test_all_1000_candidates_and_outside_holdings_are_visible():
    env = TradingEnv(market(n=1005),replace(Config(),history_seconds=2,liquidation_buffer_seconds=2))
    obs = env.observe()
    assert len(obs['ids']) == 1000
    assert obs['action_mask'][:,1].sum() == 900
    assert obs['action_mask'][899,1] and not obs['action_mask'][900:,1].any()
    env.quantity[[900,999,1004]] = 1
    env.cash -= 15
    obs = env.observe()
    assert len(obs['ids']) == 1001 and obs['ids'][-1] == 1004
    assert obs['action_mask'][900].tolist() == [True,False,True,True]
    assert obs['action_mask'][999].tolist() == [True,False,True,True]
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


def test_policy_scheduler_selects_one_discretionary_action_or_passes():
    obs = TradingEnv(market(),config()).observe()
    policy = PortfolioPolicy(3,width=16,heads=2)
    with torch.no_grad():
        policy.trade_gate.weight.zero_()
        policy.trade_gate.bias.fill_(2.)
    batch = collate([obs])
    modes,sizes,logprob,_,_ = policy.action(batch,deterministic=True)
    assert int((modes != 0).sum()) == 1
    _,_,recomputed,_,_ = policy.action(batch,modes,sizes)
    torch.testing.assert_close(logprob,recomputed)
    (-recomputed.mean()).backward()
    assert policy.trade_gate.bias.grad.abs().sum() > 0
    assert policy.actor.weight.grad[1:].abs().sum() > 0
    order = np.array([2,0,1])
    permuted = {key:(value[order] if key != 'account' else value) for key,value in obs.items()}
    other,_,_,_,_ = policy.action(collate([permuted]),deterministic=True)
    torch.testing.assert_close(modes[:,order],other)
    empty = TradingEnv(market(),replace(config(),min_volume_60s=1e9)).observe()
    passed,_,score,_,_ = policy.action(collate([empty]))
    assert torch.count_nonzero(passed) == 0 and torch.isfinite(score).all()


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


def test_per_ticker_ppo_kl_and_entropy_do_not_scale_with_universe_width():
    one = torch.full((1,1),.01,requires_grad=True)
    wide = torch.full((1,1000),.01,requires_grad=True)
    wide_mask = torch.ones_like(wide,dtype=torch.bool)
    wide_mask[:,-1] = False
    value = torch.zeros(1)
    single_loss,single = ppo_loss(one,torch.zeros_like(one),torch.ones_like(one),value,value,
        torch.ones_like(one),token_mask=torch.ones_like(one,dtype=torch.bool))
    wide_loss,many = ppo_loss(wide,torch.zeros_like(wide),torch.ones_like(wide),value,value,
        torch.ones_like(wide),token_mask=wide_mask)
    assert many['approx_kl'] == pytest.approx(single['approx_kl'])
    assert many['entropy'] == pytest.approx(single['entropy'])
    assert many['policy_loss'] == pytest.approx(single['policy_loss'])
    wide_loss.backward()
    assert wide.grad[0,-1] == 0
    assert wide.grad[0,0] != 0


def test_ticker_reward_reconciles_and_uses_stable_listing_identity():
    env = TradingEnv(market(n=2,seconds=5,prices=[[5,5,5,5,5],[5,6,7,8,9]]),config())
    first = env.observe()
    modes = np.zeros(len(first['ids']),dtype=np.int64)
    sizes = np.full((len(modes),3),.5)
    ticker = int(np.flatnonzero(first['ids'] == 1)[0])
    modes[ticker] = 1
    _,reward,_,_ = env.step(modes,sizes)
    assert env.last_reward_by_ticker.sum() == pytest.approx(reward)
    assert env.last_reward_by_ticker[0] == 0
    second = env.observe()
    _,reward,_,_ = env.step(np.zeros(len(second['ids']),dtype=np.int64),
        np.full((len(second['ids']),3),.5))
    assert env.last_reward_by_ticker.sum() == pytest.approx(reward)
    assert env.last_reward_by_ticker[1] > 0
    third = env.observe()
    modes = np.zeros(len(third['ids']),dtype=np.int64)
    _,reward,_,_ = env.step(modes,np.full((len(modes),3),.5))
    assert env.last_reward_by_ticker.sum() == pytest.approx(reward)
    assert env.quantity[1] == 0
    trajectory = [dict(ids=np.array([1,0]),ticker_rewards=np.array([1.,0.]),done=False),
        dict(ids=np.array([0,1]),ticker_rewards=np.array([0.,2.]),done=True),
        dict(ids=np.array([1]),ticker_rewards=np.array([4.]),done=False)]
    local = ticker_returns(trajectory,2,gae_lambda=1.)
    np.testing.assert_allclose(local[0],[3.,0.])
    np.testing.assert_allclose(local[1],[0.,2.])
    np.testing.assert_allclose(local[2],[4.])


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
        train.main(common+['--run-name','resume','--iterations','3','--resume','--extra-venue-fee-per-share','.02'])
    assert read(run/'status.json')['status'] == 'complete'


def test_single_account_cycles_complete_sessions_before_checkpoint_selection(tmp_path,monkeypatch):
    monkeypatch.setenv('QW_RUNTIME_ROOT',str(tmp_path))
    days = ['2026-08-19','2026-08-20','2026-08-21']
    training = [save_market(tmp_path/day,market(n=1,seconds=8,day=day)) for day in days]
    validation = save_market(tmp_path/'validation',market(n=1,seconds=8,day='2026-08-24'))
    common = ['--train-sessions',*[str(path) for path in training],
        '--val-sessions',str(validation),'--run-name','single-account',
        '--allow-segment','--device','cpu','--width','16','--heads','2',
        '--threads','1','--rollout-steps','5','--environments','1',
        '--capital-multipliers','1','--session-order','cycle',
        '--stream-sessions',
        '--min-completed-episodes','3','--selection-min-episodes','1',
        '--epochs','1','--batch-size','5','--history-seconds','4',
        '--liquidation-buffer-seconds','2','--eval-every','2']
    with pytest.raises(ValueError,match='cannot complete'):
        train.main(common+['--iterations','4'])
    assert train.main(common+['--iterations','5']) == 0
    run = tmp_path/'rl-trading/v2/train/single-account'
    reports = [read(run/'metrics'/f'{iteration:06d}.json') for iteration in range(1,6)]
    assert [episode['date'] for report in reports for episode in report['episodes']] == days
    assert 'validation' not in reports[0]
    assert 'validation' in reports[1]
    assert read(run/'status.json')['completed_episodes'] == 3
    saved = torch.load(run/'checkpoint_latest.pt',weights_only=False)
    assert saved['next_session_index'] == 4
    assert saved['session_indices'] == [0]
    assert read(run/'run_manifest.json')['arguments']['stream_sessions'] is True


def test_completed_session_run_continues_exact_state_in_new_versioned_run(tmp_path,monkeypatch):
    monkeypatch.setenv('QW_RUNTIME_ROOT',str(tmp_path))
    days = ['2026-08-19','2026-08-20','2026-08-21']
    training = [save_market(tmp_path/day,market(n=1,seconds=8,day=day)) for day in days]
    validation = save_market(tmp_path/'validation',market(n=1,seconds=8,day='2026-08-24'))
    common = ['--train-sessions',*[str(path) for path in training],
        '--val-sessions',str(validation),'--allow-segment','--device','cpu',
        '--width','16','--heads','2','--threads','1','--rollout-steps','5',
        '--environments','1','--capital-multipliers','1','--session-order','cycle',
        '--stream-sessions','--selection-min-episodes','1','--epochs','1',
        '--batch-size','5','--history-seconds','4','--liquidation-buffer-seconds','2',
        '--eval-every','2']
    assert train.main(common+['--run-name','pilot','--min-completed-episodes','3',
                              '--iterations','5']) == 0
    pilot = tmp_path/'rl-trading/v2/train/pilot'
    assert train.main(common+['--run-name','continued','--min-completed-episodes','6',
                              '--iterations','10','--continue-from-run',str(pilot)]) == 0
    assert train.main(common+['--run-name','continuous','--min-completed-episodes','6',
                              '--iterations','10']) == 0
    extended = tmp_path/'rl-trading/v2/train/continued'
    child = torch.load(extended/'checkpoint_latest.pt',weights_only=False)
    uninterrupted = torch.load(tmp_path/'rl-trading/v2/train/continuous/checkpoint_latest.pt',
                               weights_only=False)
    assert child['completed_episodes'] == uninterrupted['completed_episodes'] == 6
    assert child['iteration'] == uninterrupted['iteration'] == 9
    assert child['next_session_index'] == uninterrupted['next_session_index']
    assert child['environments'][0]['t'] == uninterrupted['environments'][0]['t']
    np.testing.assert_array_equal(child['environments'][0]['quantity'],
                                  uninterrupted['environments'][0]['quantity'])
    for key in child['policy']:
        torch.testing.assert_close(child['policy'][key],uninterrupted['policy'][key],rtol=0,atol=0)
    assert read(extended/'run_manifest.json')['lineage']['parent_iteration'] == 5
    assert (extended/'checkpoint_best.pt').is_file()
    with pytest.raises(ValueError,match='only increase'):
        train.main(common+['--run-name','bad-target','--min-completed-episodes','3',
                          '--iterations','10','--continue-from-run',str(pilot)])
    with pytest.raises(ValueError,match='Continuation changes parent contract'):
        train.main(common+['--run-name','bad-contract','--min-completed-episodes','6',
                          '--iterations','10','--continue-from-run',str(pilot),
                          '--extra-venue-fee-per-share','.02'])


def test_early_exit_window_blocks_late_entries_and_forces_existing_holds():
    session = market(n=1,seconds=1000)
    env = TradingEnv(session,replace(config(),liquidation_buffer_seconds=900))
    assert env.config.liquidation_buffer_seconds == 900
    act(env,0,1,.5)
    assert env.quantity[0] > 0
    while env.t < 99:
        act(env)
    obs = env.observe()
    assert not obs['action_mask'][0,1]
    assert env.forced[0]
    act(env)
    assert env.quantity[0] == 0
    assert env.metrics['forced_fills'] > 0


def test_best_policy_migration_verifies_lineage_and_resets_account(tmp_path):
    current = dict(version='rl-trading-v2-ppo-single-account-sessions-4',job='train',
        config=dict(version='rl-trading-v2-ppo-single-account-sessions-4',liquidation_buffer_seconds=900),
        arguments=dict(liquidation_buffer_seconds=900,min_completed_episodes=15),
        model=dict(features=3,width=16,heads=2),feature_names=['a','b','c'],
        train=[dict(date='2026-08-19')],validation=[dict(date='2026-08-24')],
        teacher_supervision=False,torch_version=torch.__version__,numpy_version=np.__version__,
        wandb=dict(mode='disabled'),code=dict(files={**train.PRE_EARLY_EXIT_HASHES,'other.py':'same'}))
    parent = json.loads(json.dumps(current))
    parent['version'] = parent['config']['version'] = 'rl-trading-v2-ppo-single-account-sessions-3'
    parent['config']['liquidation_buffer_seconds'] = parent['arguments']['liquidation_buffer_seconds'] = 120
    parent['code']['files'].update(train.PRE_EARLY_EXIT_HASHES)
    parent['contract_hash'] = digest(parent)
    root = tmp_path/'parent'
    (root/'metrics').mkdir(parents=True)
    write(root/'run_manifest.json',parent)
    write(root/'metrics/000001.json',dict(validation_all_flat=True,validation_mean_return=-.02))
    torch.save(dict(contract_hash=parent['contract_hash'],best=-.02,iteration=1,
                    completed_episodes=1,policy=dict(weight=torch.ones(1)),optimizer=dict()),
               root/'checkpoint_best.pt')
    lineage,best = train._best_initialization(root,current,run_root=tmp_path/'child',device='cpu')
    assert lineage['parent_best_iteration'] == 1
    assert lineage['account_state'] == 'fresh'
    assert lineage['transferred'] == ['policy','optimizer']
    assert best['policy']['weight'].item() == 1
    current['config']['initial_cash'] = 20000
    with pytest.raises(ValueError,match='other execution assumptions'):
        train._best_initialization(root,current,run_root=tmp_path/'bad',device='cpu')


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


def test_fill_uses_next_second_open_without_exposing_it_to_decision():
    session = market(n=1,seconds=12)
    session.arrays['execution_open'][0,1] = 6.
    env = TradingEnv(session,config(initial_cash=1000.))
    assert env.observe()['market'][0,-1,0] == pytest.approx(np.log(5.))
    act(env,0,1,size=.5)
    assert env.last_fills[0]['price'] == pytest.approx(6.)
    assert env.session.arrays['prices'][0,1] == 5.


def test_validation_samples_fixed_policy_rollouts_without_changing_training_rng():
    session = market(n=1,seconds=12)
    policy = PortfolioPolicy(3,width=16,heads=2)
    with torch.no_grad():
        policy.trade_gate.weight.zero_()
        policy.trade_gate.bias.fill_(3.)
    before = torch.get_rng_state().clone()
    first = train.evaluate(policy,[session],config(),device='cpu',rollouts=2,seed=41)
    assert torch.equal(before,torch.get_rng_state())
    second = train.evaluate(policy,[session],config(),device='cpu',rollouts=2,seed=41)
    assert first == second
    assert len(first) == 2 and all(row['valid_terminal'] for row in first)
    assert sum(row['filled_orders'] for row in first) > 0


def test_validation_records_unfillable_terminal_without_selecting_it():
    session = market(n=1,seconds=12)
    session.arrays['fresh'][0,2:] = False
    session.arrays['volume'][0,2:] = 0
    class BuyOnly:
        training = True
        def eval(self):
            self.training = False
        def train(self, value):
            self.training = value
        def action(self, batch):
            modes = torch.zeros((1,1),dtype=torch.long)
            if batch['action_mask'][0,0,1]:
                modes[0,0] = 1
            return modes,torch.full((1,1,3),.5),None,None,None
    result = train.evaluate(BuyOnly(),[session],config(),device='cpu',rollouts=1)
    assert len(result) == 1
    assert not result[0]['valid_terminal']
    assert result[0]['open_positions'] == 1


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
    monkeypatch.setattr(build_data.arte_source,'population',lambda *a:(listings,{
        'certificate':{'tradable_count':4},'selected_ticker_days':3,
        'tradable_without_canonical_events':1}))
    monkeypatch.setattr(build_data,'FEATURE_NAMES',tuple(session.plan['feature_names']))
    # Exercise full-day disk contract with a tiny feature count and zeroed market.
    seconds = build_data.SECONDS
    calls = []
    def extraction(c,s,d,listing):
        calls.append(listing['ticker'])
        arrays = {name:np.zeros((seconds,3) if name == 'features' else () if name == 'prior_close' else seconds,
                  dtype=np.bool_ if name == 'fresh' else np.float32 if name in ('features','prior_close','estimated_reference') else np.float64)
                  for name in ARRAYS}
        return arrays,{'certificate':'test'}
    monkeypatch.setattr(build_data,'extract',extraction)
    args = ['--manifest',str(manifest),'--ledger',str(tmp_path/'unused.db'),'--date','2026-08-20',
            '--workers','1']
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
    monkeypatch.setattr(build_data.arte_source,'population',lambda *a:(listings,{
        'certificate':{'tradable_count':5},'selected_ticker_days':3,
        'tradable_without_canonical_events':1}))
    with pytest.raises(ValueError,match='every certified event-bearing'):
        build_data.main(args)


def test_direct_extraction_price_clock_and_rolling_activity(monkeypatch):
    import polars as pl
    from research.rl_trading.v2 import build_data
    bars = pl.DataFrame(dict(bucket_index=[14400,14401,14460],price_valid=[1,0,1],
        open_int=[49000,990000,61000],close_int=[50000,990000,60000],
        volume=[100.,200.,300.],trade_count=[2,3,4]))
    calls = []
    monkeypatch.setattr(build_data.arte_source,'verify_listing',lambda *a:calls.append(a[-1]))
    monkeypatch.setattr(build_data,'read_reference',lambda *a:({},[],{},{}))
    monkeypatch.setattr(build_data,'read_arte_seconds',lambda *a:(bars,None))
    monkeypatch.setattr(build_data,'encode',lambda *a:(np.zeros((build_data.SECONDS,3)),np.zeros(build_data.SECONDS)))
    monkeypatch.setattr(build_data,'read_prior_close',lambda *a:np.float32(5.))
    arrays,_ = build_data.extract(None,{},date(2026,8,20),{'ticker':'A'})
    assert calls == ['A','A']
    assert arrays['prices'][0] == 0
    assert arrays['prices'][1] == 5 and arrays['prices'][2] == 5
    assert arrays['prices'][60] == 5 and arrays['prices'][61] == 6
    assert arrays['execution_open'][1] == 4.9 and arrays['execution_open'][61] == 6.1
    assert not arrays['fresh'][2] and arrays['fresh'][61]
    assert arrays['trades_60s'][60] == 5
    assert arrays['trades_60s'][61] == 7


@pytest.mark.parametrize('device',['cpu',pytest.param('cuda',marks=pytest.mark.skipif(
    not torch.cuda.is_available(),reason='Laptop CUDA unavailable'))])
def test_actual_launcher_with_1000_candidates(tmp_path,monkeypatch,device):
    import os
    import subprocess
    import sys
    from pathlib import Path
    monkeypatch.setenv('QW_RUNTIME_ROOT',str(tmp_path))
    training = save_market(tmp_path/'training',market(n=1005,seconds=8))
    validation = save_market(tmp_path/'validation',market(n=1005,seconds=8,day='2026-08-21'))
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
    assert manifest['config']['entry_rank'] == 900 and manifest['config']['hold_rank'] == 1000
    assert read(run/'metrics/000001.json')['updates'] > 0


def test_wandb_logs_completed_iterations_and_recovers_missing_upload(tmp_path,monkeypatch):
    monkeypatch.setenv('QW_RUNTIME_ROOT',str(tmp_path))
    training = save_market(tmp_path/'training',market(n=1,seconds=8))
    validation = save_market(tmp_path/'validation',market(n=1,seconds=8,day='2026-08-21'))
    uploaded,finished,resume_modes = [],[],[]
    class FakeRun:
        url = 'https://wandb.ai/fixture/rl-trading-v2/runs/fixture'
        def log(self,values,step):
            uploaded.append((step,values))
        def finish(self):
            finished.append(True)
    def connect(**kwargs):
        resume_modes.append(kwargs['resume_mode'])
        return FakeRun()
    monkeypatch.setattr(train,'init_wandb',connect)
    common = ['--train-sessions',str(training),'--val-sessions',str(validation),
        '--run-name','wandb-fixture','--allow-segment','--iterations','1',
        '--rollout-steps','4','--environments','1','--epochs','1','--batch-size','4',
        '--width','16','--heads','2','--history-seconds','4',
        '--liquidation-buffer-seconds','2','--wandb-mode','online']
    assert train.main(common) == 0
    run = tmp_path/'rl-trading/v2/train/wandb-fixture'
    assert uploaded[0][0] == 1 and 'validation/net_return_mean' in uploaded[0][1]
    (run/'wandb_synced.json').unlink()  # Simulate a crash after checkpoint publication.
    assert train.main(common+['--resume','--iterations','2']) == 0
    assert [step for step,_ in uploaded] == [1,1,2]
    assert resume_modes == ['never','must'] and len(finished) == 2
    assert read(run/'wandb_synced.json')['iteration'] == 2
