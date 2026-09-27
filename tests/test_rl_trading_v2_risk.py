"""Broker fee examples and causal learned-exit/estimated-band regression cases."""
import json
import numpy as np
import pytest
import torch
from research.rl_trading.v2.config import Config
from research.rl_trading.v2.environment import TradingEnv
from research.rl_trading.v2.fees import charges
from research.rl_trading.v2.market_status import StatusSidecar, VERSION
from research.rl_trading.v2.model import PortfolioPolicy, collate
from research.rl_trading.v2 import estimated_luld
from research.rl_trading.v2.io import write, file_hash
from test_rl_trading_v2 import market, config, act


@pytest.mark.parametrize('q,p,expected',[(100,25,1),(1000,25,5),(1000,.25,2.5),(10,.2,.02),(20000,5,100)])
def test_ibkr_fixed_published_commission_examples(q,p,expected):
    fees = charges(q,p,1,Config())
    assert fees['commission'] == pytest.approx(expected)
    assert fees['sec'] == fees['taf'] == 0
    assert fees['cat'] == pytest.approx(q*.000003)


def test_regulatory_fees_and_no_double_charge_of_generic_fee():
    fee = charges(100000,5,-1,Config())
    assert fee['sec'] == pytest.approx(500000*.0000206)
    assert fee['taf'] == 9.79
    assert fee['cat'] == pytest.approx(.3)
    assert sum(charges(0,5,1,Config()).values()) == 0
    with pytest.raises(ValueError,match='Generic fee'):
        Config(fee_ratio=.001)


def bracket_buy(env, allocation=.5, stop=.1, target=.2):
    c = env.config
    obs = env.observe()
    sizes = np.full((len(obs['ids']),3),.5)
    sizes[0] = [allocation,(stop-c.minimum_stop_ratio)/(c.maximum_stop_ratio-c.minimum_stop_ratio),
                (target-c.minimum_target_ratio)/(c.maximum_target_ratio-c.minimum_target_ratio)]
    modes = np.zeros(len(obs['ids']),dtype=np.int64)
    modes[0] = 1
    return env.step(modes,sizes)


def test_stop_triggers_causally_and_gaps_fill_beyond_stop():
    prices = np.full((1,12),5.)
    prices[0,2] = 4.4
    prices[0,3:] = 4.
    env = TradingEnv(market(n=1,prices=prices),config())
    bracket_buy(env)
    assert env.stop_price[0] == pytest.approx(4.5)
    assert env.target_price[0] == pytest.approx(6.)
    act(env)
    assert env.quantity[0] == 1000
    assert env.observe()['action_mask'][0].tolist() == [True,False,False,False]
    act(env)
    assert env.quantity[0] == 0
    assert env.last_fills[0]['price'] == 4.
    assert env.last_fills[0]['exit_reason'] == 2
    assert env.metrics['stop_fills'] == 1


def test_target_is_exit_trigger_not_guaranteed_limit_fill():
    prices = np.full((1,12),5.)
    prices[0,2] = 6.1
    prices[0,3:] = 5.9
    env = TradingEnv(market(n=1,prices=prices),config())
    bracket_buy(env)
    act(env)
    act(env)
    assert env.last_fills[0]['price'] == 5.9
    assert env.last_fills[0]['exit_reason'] == 3
    assert env.metrics['target_fills'] == 1


def test_adding_shares_does_not_reset_or_widen_bracket():
    env = TradingEnv(market(n=1),config())
    bracket_buy(env,allocation=.25)
    stop,target,entered = env.stop_price[0],env.target_price[0],env.entry_second[0]
    bracket_buy(env,allocation=.25,stop=.4,target=1.)
    assert (env.stop_price[0],env.target_price[0],env.entry_second[0]) == (stop,target,entered)


def test_regular_session_rejects_prior_close_below_75_cents(monkeypatch):
    monkeypatch.setattr(estimated_luld,'REGULAR_FIRST',1)
    monkeypatch.setattr(estimated_luld,'REGULAR_LAST',10)
    session = market(n=1)
    session.arrays['prior_close'][0] = .7499
    env = TradingEnv(session,config())
    act(env)
    assert not env.observe()['action_mask'][0,1]
    session.arrays['prior_close'][0] = .75
    assert env.observe()['action_mask'][0,1]


def test_entry_brackets_are_clipped_inside_estimated_regular_bands(monkeypatch):
    monkeypatch.setattr(estimated_luld,'REGULAR_FIRST',1)
    monkeypatch.setattr(estimated_luld,'REGULAR_LAST',10)
    env = TradingEnv(market(n=1),config())
    bracket_buy(env,stop=.1,target=.2)
    assert env.quantity[0] > 0
    assert env.stop_price[0] == pytest.approx(5*(1-.05+.005))
    assert env.target_price[0] == pytest.approx(5*(1+.05-.005))


def test_reference_uses_only_completed_regular_session_bars(monkeypatch):
    monkeypatch.setattr(estimated_luld,'REGULAR_FIRST',1)
    monkeypatch.setattr(estimated_luld,'REGULAR_LAST',4)
    prices = np.asarray([100.,5.,6.,7.,8.,50.])
    volume = np.ones(len(prices))
    fresh = np.ones(len(prices),dtype=bool)
    reference = estimated_luld.reference_series(prices,volume,fresh,5.)
    np.testing.assert_allclose(reference[:5],[5.,5.,5.5,6.,6.5])
    assert reference[5] == reference[4]


def test_stop_target_samples_participate_in_ppo_gradient():
    policy = PortfolioPolicy(3,width=16,heads=2)
    obs = TradingEnv(market(n=1),config()).observe()
    batch = collate([obs])
    modes = torch.ones((1,1),dtype=torch.long)
    sizes = torch.tensor([[[.5,.2,.8]]])
    _,_,logprob,_,_ = policy.action(batch,modes,sizes)
    (-logprob.mean()).backward()
    assert policy.size.weight.grad[2:].abs().sum() > 0


def test_status_sidecar_uses_available_time_not_future_effective_time(tmp_path):
    session = market(n=1)
    first = session.plan['first_us']
    records = [dict(listing_id='L000',effective_us=first,available_us=first,state=1),
        dict(listing_id='L000',effective_us=first+1000000,available_us=first+3000000,state=2),
        dict(listing_id='L000',effective_us=first+5000000,available_us=first+5000000,state=1)]
    path = tmp_path/'events.jsonl'
    path.write_text('\n'.join(json.dumps(x) for x in records),encoding='utf-8')
    write(tmp_path/'complete.json',dict(version=VERSION,state='complete',date=session.plan['date'],
        authority_table='market_sip_compact.events_2026',producer_owner='canonical_ingestion',
        source_certificate_hash='fixture',first_us=first,end_us=first+(session.seconds-1)*1000000,
        listing_ids=list(session.ids),clock='provider_effective_and_first_available',
        event_count=len(records),events_hash=file_hash(path)))
    sidecar = StatusSidecar(tmp_path,day=session.plan['date'],listings=session.plan['listings'],
                            first_us=first,seconds=session.seconds)
    np.testing.assert_array_equal(sidecar.states('L000')[:6],[1,1,1,2,2,1])
    np.testing.assert_array_equal(sidecar.states('L000',execution=True)[:6],[1,2,2,2,2,1])


def test_unavailable_execution_bar_does_not_fill_without_halt_inference():
    session = market(n=1)
    session.arrays['fresh'][0,1] = False
    env = TradingEnv(session,config())
    bracket_buy(env)
    assert env.quantity[0] == 0
    assert env.metrics['market_unavailable_orders'] == 1
