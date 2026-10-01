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


def test_class_balance_changes_only_classification_gradient():
    import copy
    torch.manual_seed(33)
    models=[TickerHeads(8)]
    models.append(copy.deepcopy(models[0]))
    x=torch.randn(1,8)
    metrics=[]
    for model,balance in zip(models,(1.,3.)):
        out=model(x,torch.tensor([False]))
        loss,report=supervised_loss(out,torch.tensor([[.8,.2,0.,0.]]),
            value_bps=torch.tensor([200.]),value_valid=torch.tensor([True]),
            stop_bps=torch.tensor([80.]),target_bps=torch.tensor([150.]),
            bracket_valid=torch.tensor([True]),action_weight=balance)
        loss.backward();metrics.append(report)
    torch.testing.assert_close(models[1].action.weight.grad,3*models[0].action.weight.grad)
    for head in ('value','stop','target'):
        torch.testing.assert_close(getattr(models[0],head).weight.grad,getattr(models[1],head).weight.grad)
    torch.testing.assert_close(metrics[0]['action_loss'],metrics[1]['action_loss'])


def test_proposal_entry_probability_does_not_grow_with_ticker_count():
    from research.rl_trading.v6.ticker_heads import TickerDecoder
    decoder=TickerDecoder(8)
    with torch.no_grad():
        decoder.heads.action.weight.zero_()
        decoder.heads.action.bias.copy_(torch.tensor([-3.,0.,0.,0.]))
    masses=[]
    for n in (1,20):
        logits,*_=decoder(torch.zeros(n,8),torch.zeros(7),torch.empty(0,dtype=torch.long),torch.empty(0,11),
            enter_allowed=torch.ones(n,dtype=torch.bool),exit_allowed=torch.empty(0,dtype=torch.bool),
            stop_allowed=torch.empty(0,dtype=torch.bool),target_allowed=torch.empty(0,dtype=torch.bool))
        masses.append(logits.softmax(0)[1:].sum())
    assert torch.allclose(masses[0],masses[1],atol=1e-6)
    assert masses[1]<.05


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


@pytest.mark.parametrize('held',[False,True])
def test_teacher_gather_matches_dense_outputs_and_gradients(held):
    from research.rl_trading.v6.ticker_heads import TickerDecoder
    import copy
    torch.manual_seed(29)
    dense=TickerDecoder(8);fast=copy.deepcopy(dense)
    listings=torch.randn(5,8,requires_grad=True)
    selected=listings.detach().clone().requires_grad_()
    index=torch.tensor([3]) if held else torch.empty(0,dtype=torch.long)
    features=torch.randn(1,11) if held else torch.empty(0,11)
    account=torch.randn(7)
    masks=dict(enter_allowed=torch.ones(5,dtype=torch.bool),
        exit_allowed=torch.ones(len(index),dtype=torch.bool),
        stop_allowed=torch.zeros(len(index),dtype=torch.bool),
        target_allowed=torch.zeros(len(index),dtype=torch.bool))
    dense(listings,account,index,features,**masks)
    fast.supervision_index=3
    fast(selected,account,index,features,**masks)
    def loss(decoder,i):
        out=decoder.ticker_outputs
        return sum(getattr(out,k)[i].masked_fill(~torch.isfinite(getattr(out,k)[i]),0).sum()
            for k in ('logits','value_bps','stop_bps','target_bps'))
    for k in ('logits','value_bps','stop_bps','target_bps'):
        torch.testing.assert_close(getattr(dense.ticker_outputs,k)[3],getattr(fast.ticker_outputs,k)[0])
    loss(dense,3).backward();loss(fast,0).backward()
    torch.testing.assert_close(listings.grad,selected.grad)
    for a,b in zip(dense.parameters(),fast.parameters()):
        if a.grad is not None:torch.testing.assert_close(a.grad,b.grad)
