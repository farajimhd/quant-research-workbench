import pytest
import torch
from research.rl_trading.v6.ticker_heads import TickerHeads, supervised_loss


@pytest.mark.parametrize('device', ['cpu']+(['cuda'] if torch.cuda.is_available() else []))
def test_ticker_heads_masked_soft_ce_and_regression_gradients(device):
    torch.manual_seed(19)
    model=TickerHeads(8).to(device)
    x=torch.randn(3,8,device=device,requires_grad=True)
    out=model(x,torch.tensor([False,False,True],device=device))
    p=x.new_tensor([[.8,.2,0,0],[0,1,0,0],[0,0,.3,.7]])
    valid=torch.tensor([True,False,True],device=device)
    bracket=torch.tensor([True,False,False],device=device)
    missing=x.new_tensor([100.,float('nan'),-50.])
    loss,metrics=supervised_loss(out,p,value_bps=missing,value_valid=valid,
        stop_bps=x.new_tensor([80.,float('nan'),float('nan')]),
        target_bps=x.new_tensor([150.,float('nan'),float('nan')]),bracket_valid=bracket)
    assert torch.isfinite(loss) and torch.isfinite(metrics['action_loss'])
    assert torch.isneginf(out.logits[0,2:]).all()
    loss.backward()
    assert torch.isfinite(x.grad).all()
    for head in ('action','value','stop','target'):
        assert getattr(model,head).weight.grad.abs().sum()>0
    # A WAIT-only batch trains actions, not fabricated regression zeros.
    model.zero_grad()
    out=model(x[1:2],torch.tensor([False],device=device))
    zero=torch.tensor([False],device=device);nan=x.new_tensor([float('nan')])
    loss,_=supervised_loss(out,p[1:2],value_bps=nan,value_valid=zero,
        stop_bps=nan,target_bps=nan,bracket_valid=zero)
    loss.backward()
    assert model.value.weight.grad.abs().sum()==0
    assert model.stop.weight.grad.abs().sum()==0
    assert model.target.weight.grad.abs().sum()==0


def test_ticker_outputs_do_not_mix_observations_and_reject_invalid_targets():
    model=TickerHeads(8)
    x=torch.randn(2,8);held=torch.tensor([False,True])
    a=model(x,held);x[1]+=100
    b=model(x,held)
    assert torch.equal(a.logits[0],b.logits[0])
    p=torch.tensor([[0.,0,1,0],[0,0,1,0]])
    z=torch.zeros(2);valid=torch.zeros(2,dtype=torch.bool)
    with pytest.raises(ValueError,match='position state'):
        supervised_loss(a,p,value_bps=z,value_valid=valid,
            stop_bps=z,target_bps=z,bracket_valid=valid)


@pytest.mark.parametrize('device',['cpu']+(['cuda'] if torch.cuda.is_available() else []))
def test_real_ticker_teacher_then_tensor_ppo_reconstruction(device):
    import numpy as np
    from datetime import date
    from pathlib import Path
    from research.rl_trading.v1.common import bounds
    from research.rl_trading.v6.bank import SessionBank
    from research.rl_trading.v6.session_data import PackedSession
    from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
    from research.rl_trading.v6.market_attention import MarketAttentionConfig
    from research.rl_trading.v6.ticker_heads import TickerDecoder
    from research.rl_trading.v6.training import TeacherDecision,train_session
    from research.rl_trading.v6.tensor_broker import TensorBroker
    from research.rl_trading.v6.tensor_rollout import collect_tensor_session
    from research.rl_trading.v6.rollout import audit_reconstruction,update_session
    from test_rl_trading_v6_tensor_broker import bucket
    start=bounds(date(2026,8,18))[0]+1000000
    scalar=np.zeros((12,37),np.float32);scalar[:,3]=np.log(10.)
    scalar[:,8]=np.log1p(10000.);scalar[:,35]=1
    clocks=np.tile(np.arange(start,start+6000000,1000000,dtype=np.int64),2)
    bank=SessionBank(Path('unused'),{'offsets':{'A':[0,6],'B':[6,12]}},clocks,scalar,np.zeros((12,2,5,11),np.float32))
    session=PackedSession(date(2026,8,18),'train',Path('unused'),'cert',bank,None,('A','B'))
    torch.manual_seed(17)
    policy=RankedBracketActorCritic(width=8,config=MarketAttentionConfig(top_r=2),wait_hold=True).to(device)
    policy.decoder=TickerDecoder(8).to(device);policy.independent_episode_supervision=True
    label=TeacherDecision(start,0,1,np.array([10000,10000,0,0,0,0,0],np.float32),
        np.empty(0,np.int64),np.empty((0,11),np.float32),np.array([True,False]),
        np.empty(0,bool),np.empty(0,bool),np.empty(0,bool),soft_tokens=(0,1),
        soft_probabilities=(.2,.8),episode_uid='fixture',opportunity_value_bps=200.,entry_stop_bps=80.,entry_target_bps=150.)
    before=policy.encoder.project.weight.detach().clone();size=policy.decoder.size_head.weight.detach().clone()
    optimizer=torch.optim.Adam(policy.parameters(),lr=.001)
    metrics=train_session(policy,optimizer,session,(label,),(),device=torch.device(device),clocks_per_chunk=2)
    assert metrics.ticker_regression_counts==dict(value=1,stop=1,target=1)
    assert metrics.buy_size_mae is None and torch.equal(size,policy.decoder.size_head.weight)
    assert not torch.equal(before,policy.encoder.project.weight)
    policy.independent_episode_supervision=False
    with torch.no_grad():policy.decoder.heads.action.bias.copy_(torch.tensor([10.,-10.,10.,-10.],device=device))
    broker=TensorBroker(2,device=device)
    tape=(bucket(broker,c,volume=1000.) for c in range(start+100000,start+5000000+1,100000))
    collected=collect_tensor_session(policy,session,broker,tape,device=torch.device(device),max_clocks=5)
    assert collected.summary['buy_fill_orders']>0
    assert (broker.stop[broker.quantity>0]>0).all() and (broker.target[broker.quantity>0]>0).all()
    assert audit_reconstruction(policy,session,collected.frames,device=torch.device(device))['maximum_absolute_error']<1e-5
    result=update_session(policy,optimizer,session,collected.frames,collected.steps,device=torch.device(device),
        epochs=1,clocks_per_chunk=2,decoder_batch_size=2,bootstrap=collected.bootstrap,batch_candle_projection=True)
    assert result['update_epochs']==1
