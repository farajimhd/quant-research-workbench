from datetime import date
from copy import deepcopy
from pathlib import Path
import numpy as np
import pytest
import torch
from research.rl_trading.v1.common import bounds
from research.rl_trading.v6.bank import SessionBank
from research.rl_trading.v6.session_data import PackedSession
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.tensor_broker import TensorBroker
from research.rl_trading.v6.tensor_rollout import collect_tensor_session
from research.rl_trading.v6.rollout import audit_reconstruction,update_session
from test_rl_trading_v6_tensor_broker import bucket,DEVICES


@pytest.mark.parametrize('device',DEVICES)
@pytest.mark.parametrize('execution_features',[False,True])
def test_real_tensor_collection_reconstruction_and_optimizer(device,tmp_path,execution_features):
    start=bounds(date(2026,8,18))[0]+1_000_000
    scalar=np.zeros((12,37),dtype=np.float32);scalar[:,3]=np.log(10.)
    scalar[:,8]=np.log1p(10000.);scalar[:,35]=1
    clocks=np.tile(np.arange(start,start+6_000_000,1_000_000,dtype=np.int64),2)
    bank=SessionBank(Path('unused'),{'offsets':{'A':[0,6],'B':[6,12]}},clocks,
        scalar,np.zeros((12,2,5,11),dtype=np.float32))
    session=PackedSession(date(2026,8,18),'train',Path('unused'),'cert',bank,None,('A','B'))
    torch.manual_seed(15)
    policy=RankedBracketActorCritic(width=8,config=MarketAttentionConfig(top_r=2)).to(device)
    if execution_features:
        from research.rl_trading.v6.execution_features import VERSION
        policy.configure_execution_features(dict(version=VERSION,scope='train_only',mean=[0.]*147,std=[1.]*147))
        policy.to(device)
    with torch.no_grad():
        policy.decoder.hold_head.bias.fill_(-10)
        policy.decoder.enter_head.bias.fill_(10)
    broker=TensorBroker(2,device=device)
    broker.expose_cost_features=execution_features
    from dataclasses import replace
    def evidence(c):
        row=bucket(broker,c,volume=1000.)
        return replace(row,bid=row.vwap-.01,ask=row.vwap+.01,
            quote_timestamp_us=torch.full((broker.n,),c-1000,device=device,dtype=torch.long))
    tape=(evidence(c) for c in range(start+100_000,start+5_000_000+1,100_000))
    result=collect_tensor_session(policy,session,broker,tape,device=torch.device(device),max_clocks=5)
    assert len(result.steps)==5 and result.summary['buy_fill_orders']>0
    assert all(not s.old_log_prob.requires_grad for s in result.steps)
    audit=audit_reconstruction(policy,session,result.frames,device=torch.device(device))
    assert audit['maximum_absolute_error']<1e-5
    before=policy.decoder.enter_head.weight.detach().clone()
    optimizer=torch.optim.Adam(policy.parameters(),lr=.001)
    metrics=update_session(policy,optimizer,session,result.frames,result.steps,
        device=torch.device(device),epochs=1,clocks_per_chunk=2,decoder_batch_size=2,
        bootstrap=result.bootstrap,batch_candle_projection=True)
    assert metrics['update_epochs']==1
    assert not torch.equal(before,policy.decoder.enter_head.weight)
    from research.rl_trading.v6.tensor_artifacts import save_tensor_replay
    checkpoint=tmp_path/'model.pt';torch.save(policy.state_dict(),checkpoint)
    proof=tmp_path/'evidence.json';proof.write_text('{}')
    root,summary=save_tensor_replay(result,session,checkpoint,runtime_root=tmp_path,
        source_commit='fixture',quote_evidence_certificate=proof)
    assert (root/'complete.json').is_file() and (root/'positions.parquet').is_file()
    assert summary['environment_version']=='rl-v6-tensor-participation-100ms-v1'


@pytest.mark.parametrize('device',DEVICES)
def test_fused_execution_gru_value_and_gradient_equivalence(device):
    torch.manual_seed(5)
    p=RankedBracketActorCritic(width=8).to(device)
    x=torch.randn(5,8,device=device,requires_grad=True)
    action=torch.tensor([1,2,3,4,1],device=device)
    requested=torch.rand(5,device=device);filled=torch.rand(5,device=device);net=torch.randn(5,device=device)
    initial=p.initial_action_state(device=device,dtype=torch.float32)
    serial=initial
    for i in range(5):
        serial=p.remember_execution(serial,x[i],action=int(action[i]),requested_fraction=requested[i],
            filled_fraction=filled[i],realized_net_over_equity=net[i])
    serial.memory.sum().backward();gradient=p.action_gru.weight_ih.grad.clone()
    p.zero_grad();x.grad=None
    keys=tuple(p.state_dict())
    fused=p.remember_sequence(initial,x,action,requested,filled,net)
    fused.memory.sum().backward()
    assert tuple(p.state_dict())==keys
    assert torch.allclose(serial.memory,fused.memory,atol=2e-6,rtol=2e-6)
    assert torch.allclose(gradient,p.action_gru.weight_ih.grad,atol=2e-6,rtol=2e-6)


@pytest.mark.parametrize('device',DEVICES)
def test_chunk_projection_preserves_prefix_values_and_encoder_gradients(device):
    from research.rl_trading.v6.rollout import _initialize,_advance,prepare_candle_chunk,PolicyFrame
    start=bounds(date(2026,8,18))[0]+1_000_000
    rng=np.random.default_rng(17)
    scalar=rng.normal(0,.1,(12,37)).astype(np.float32)
    scalar[:,8]=np.abs(scalar[:,8]);scalar[:,35:]=1
    clocks=np.tile(np.arange(start,start+6_000_000,1_000_000,dtype=np.int64),2)
    bank=SessionBank(Path('fixture'),{'offsets':{'A':[0,6],'B':[6,12]}},clocks,
        scalar,np.zeros((12,2,5,11),np.float32))
    session=PackedSession(date(2026,8,18),'train',Path('fixture'),'fixture',bank,None,('A','B'))
    frames=[PolicyFrame(e.close_us,e.listing_index,e.bank_row,()) for e in session.candle_events()]
    serial=RankedBracketActorCritic(width=8,config=MarketAttentionConfig(top_r=2)).to(device)
    batched=deepcopy(serial)
    state,_=_initialize(serial,session,torch.device(device));other,_=_initialize(batched,session,torch.device(device))
    prepared=prepare_candle_chunk(batched,session,frames,torch.device(device))
    losses=[];parallel=[]
    # Only consume the prefix, despite later projected candles being resident.
    for frame,projection in zip(frames[:3],prepared[:3]):
        _advance(serial,state,session,frame,torch.device(device))
        _advance(batched,other,session,frame,torch.device(device),prepared=projection)
        assert torch.allclose(state.encoded,other.encoded,atol=2e-6,rtol=2e-6)
        losses.append(state.encoded.square().sum());parallel.append(other.encoded.square().sum())
    sum(losses).backward();sum(parallel).backward()
    for (name,a),(_,b) in zip(serial.encoder.named_parameters(),batched.encoder.named_parameters()):
        if a.grad is not None:
            assert torch.allclose(a.grad,b.grad,atol=3e-6,rtol=3e-6),name
