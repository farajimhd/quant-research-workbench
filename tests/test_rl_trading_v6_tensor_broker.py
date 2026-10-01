import pytest
import torch
from research.rl_trading.v6.tensor_broker import TensorBroker,BrokerBucket,order_fee
from research.rl_trading.v2.fees import charges
from research.rl_trading.v2.config import Config

DEVICES=['cpu']+(['cuda'] if torch.cuda.is_available() else [])


@pytest.mark.parametrize('device',DEVICES)
def test_required_automatic_brackets_reject_unexecutable_tick_geometry(device):
    b=TensorBroker(1,device=device)
    index=torch.tensor([0],device=device)
    b.update_marks(index,torch.tensor([10.],device=device),1_000_000)
    b.submit(torch.tensor(1,device=device),torch.tensor(.1,device=device),index[:0],1_000_000,
        brackets_bps=(torch.tensor([100.],device=device),torch.tensor([.000001],device=device)))
    cash=b.cash.clone()
    b.advance(bucket(b,1_100_000,volume=1000.))
    assert b.quantity.sum()==0 and torch.equal(b.cash,cash) and b.fees==0
    assert b.summary()['unexecutable_bracket_listing_buckets']==1
    assert b.remaining[0]>0  # Explicit unfilled intent; no fabricated fill.


def bucket(b,clock,price=10.,volume=100.,valid=True,high=None,low=None,paused=False):
    f=lambda v: torch.full((b.n,),v,device=b.device,dtype=torch.float64)
    mask=lambda v: torch.full((b.n,),v,device=b.device,dtype=torch.bool)
    return BrokerBucket(clock,f(price),f(volume),f(high or price),f(low or price),
                        f(0.),mask(valid),mask(True),mask(valid),mask(paused))


@pytest.mark.parametrize('device',DEVICES)
def test_tensor_partial_fills_reservations_and_fee_accounting(device):
    b=TensorBroker(3,device=device)
    index=torch.arange(3,device=device)
    b.update_marks(index,torch.full((3,),10.,device=device),1_000_000)
    b.submit(torch.tensor(1,device=device),torch.tensor(.5,device=device),index[:0],1_000_000)
    assert b.quantity.sum()==0
    missing=bucket(b,1_100_000,valid=False)
    assert b.advance(missing).listing.numel()==0
    for clock in (1_200_000,1_300_000):
        e=b.advance(bucket(b,clock))
        assert e.listing.tolist()==[0] and float(e.filled_fraction[0])==pytest.approx(10/500)
    assert b.quantity[0]==20 and b.cash>=0 and b.reserved()<=b.cash
    expected=sum(charges(20,10,1,Config()).values())
    assert float(b.fees)==pytest.approx(expected)
    obs=b.observe(1_300_000,torch.ones(3,device=device,dtype=torch.bool))
    b.submit(torch.tensor(4,device=device),torch.tensor(0.,device=device),obs.held_index,1_300_000)
    b.advance(bucket(b,1_400_000,price=11.))
    b.advance(bucket(b,1_500_000,price=11.))
    assert b.quantity.sum()==0 and b.closed==1 and b.wins==1
    sell_fee=sum(charges(20,11,-1,Config()).values())
    assert float(b.realized)==pytest.approx(20-expected-sell_fee)
    assert float(b.cash)==pytest.approx(10000+float(b.realized))


@pytest.mark.parametrize('device',DEVICES)
def test_stop_delay_ambiguity_pause_and_terminal_penalty(device):
    b=TensorBroker(1,device=device)
    b.update_marks(torch.tensor([0],device=device),torch.tensor([10.],device=device),1_000_000)
    b.submit(torch.tensor(1,device=device),torch.tensor(.1,device=device),torch.empty(0,device=device,dtype=torch.long),1_000_000)
    b.advance(bucket(b,1_100_000))
    b.stop[0]=9.;b.target[0]=11.
    b.advance(bucket(b,1_200_000,high=12.,low=8.))
    assert b.quantity[0]==10 and b.side[0]==2 and b.ambiguous==1
    b.advance(bucket(b,1_300_000,paused=True))
    assert b.quantity[0]==10 and b.shaping>0
    b.terminal_penalty()
    assert b.shaping>0
    b.advance(bucket(b,1_400_000,price=8.))
    assert b.quantity[0]==0 and b.cash>=0


@pytest.mark.parametrize('device',DEVICES)
def test_target_remainder_stays_a_limit_and_future_bucket_is_not_observed(device):
    b=TensorBroker(1,device=device)
    b.update_marks(torch.tensor([0],device=device),torch.tensor([10.],device=device),1_000_000)
    b.submit(torch.tensor(1,device=device),torch.tensor(.1,device=device),torch.empty(0,device=device,dtype=torch.long),1_000_000)
    b.advance(bucket(b,1_100_000,volume=1000.))
    before=b.mark.clone();b.target[0]=11.
    b.advance(bucket(b,1_200_000,price=11.,high=11.,volume=100.))
    assert b.quantity[0]==89 and b.side[0]==3
    b.advance(bucket(b,1_300_000,price=9.,high=10.,volume=1000.))
    assert b.quantity[0]==89 and torch.equal(b.mark,before)


@pytest.mark.parametrize('device',DEVICES)
def test_passive_target_must_be_inside_modeled_band(device):
    from dataclasses import replace
    b=TensorBroker(1,device=device)
    b.quantity[0]=10;b.cost[0]=100;b.mark[0]=10;b.target[0]=11
    row=bucket(b,100_000,price=10.,high=12.)
    b.advance(replace(row,band_low=torch.full_like(row.vwap,9.),
                      band_high=torch.full_like(row.vwap,10.5)))
    assert b.quantity[0]==10


@pytest.mark.skipif(not torch.cuda.is_available(),reason='CUDA compile parity')
def test_compiled_partial_fills_pause_and_stop_account_parity(tmp_path,monkeypatch):
    monkeypatch.setenv('TORCHINDUCTOR_CACHE_DIR',str(tmp_path/'inductor'))
    monkeypatch.setenv('TRITON_CACHE_DIR',str(tmp_path/'triton'))
    brokers=[TensorBroker(3,device='cuda') for _ in range(2)]
    brokers[1].compile_step()
    for b in brokers:
        b.mark.fill_(10);b.mark_us.fill_(1_000_000)
        b.submit(torch.tensor(1,device='cuda'),torch.tensor(.5,device='cuda'),
                 torch.empty(0,device='cuda',dtype=torch.long),1_000_000)
        for step in range(1,7):
            if step==3:b.stop[0]=9
            b.advance(bucket(b,1_000_000+step*100_000,price=8. if step>=4 else 10.,
                paused=step==2,low=8. if step>=4 else 10.),dense=True)
    for name in ('quantity','cash','fees','realized','remaining','closed','shaping'):
        assert torch.allclose(getattr(brokers[0],name),getattr(brokers[1],name),atol=1e-8,rtol=1e-12)
