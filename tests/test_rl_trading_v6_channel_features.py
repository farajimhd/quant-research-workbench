import numpy as np
import pytest
import torch
from research.rl_trading.v6.channel_features import engineer, CHANGE_NAMES, EXTRA_NAMES, install
from research.rl_trading.v6.features import SCALAR_NAMES
from research.rl_trading.v6.candle_stream import SparseCandleState
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic


def raw_rows():
    x=torch.zeros(1,4,147)
    x[0,:,:4]=torch.log(torch.tensor([[10.,12.,8.,11.],[20.,18.,16.,22.],[21.,19.,17.,23.],[22.,20.,18.,24.]]))
    x[...,6:8]=1.;x[...,20]=1.;x[...,23]=1.;x[...,35:37]=1.
    x[...,8]=torch.log1p(torch.tensor([0.,100.,0.,200.]))
    x[...,14]=torch.tensor([-.01,.01,0.,-.01]);x[...,15]=x[...,14]
    x[...,17]=.01
    return x


def test_own_channel_not_previous_close():
    x=raw_rows();y=engineer(x,torch.ones(1,4,dtype=torch.bool))
    assert y[0,1,0].item()==pytest.approx(np.log(2.))
    assert y[0,1,1].item()==pytest.approx(np.log(18/12))
    assert y[0,1,9].item()==pytest.approx(np.log1p(100.))
    assert torch.isfinite(y).all()
    assert y[0,0,:len(CHANGE_NAMES)*2].eq(0).all()
    assert y[0,1,14]>0 and y[0,3,14]<0


def test_no_future_and_missing_predecessor():
    x=raw_rows();mask=torch.ones(1,4,dtype=torch.bool)
    before=engineer(x,mask);x[:,3]*=7
    assert torch.equal(before[:,:3],engineer(x,mask)[:,:3])
    mask[:,1]=False
    assert engineer(raw_rows(),mask)[0,2,:34].eq(0).all()


def test_session_boundary_and_indicator_unavailable():
    x=raw_rows();x[:,1,23]=0.;x[:,1,24]=1.
    y=engineer(x,torch.ones(1,4,dtype=torch.bool))
    assert y[0,1,-1]==1 and y[0,1,:34].eq(0).all()
    x=raw_rows();x[:,1,20]=0.
    y=engineer(x,torch.ones(1,4,dtype=torch.bool))
    assert y[0,1,17+14]==0 and y[0,1,14]==0


def test_sparse_train_eval_and_projection_refresh_parity():
    torch.set_num_threads(1);torch.manual_seed(17)
    p=RankedBracketActorCritic(width=16,wait_hold=True)
    norm=dict(mean=[0.]*147,std=[1.]*147)
    extra=dict(mean=[0.]*len(EXTRA_NAMES),std=[1.]*len(EXTRA_NAMES))
    install(p,norm,extra);e=p.encoder
    raw=raw_rows()[0];scalar=raw[:,:37];levels=raw[:,37:].reshape(4,2,5,11)
    training=SparseCandleState.empty(e,1,device=torch.device('cpu'),dtype=torch.float32,refreshable=True)
    inference=SparseCandleState.empty(e,1,device=torch.device('cpu'),dtype=torch.float32,refreshable=True)
    for i in range(4):
        training.advance(e,torch.tensor([0]),scalar[i:i+1],levels[i:i+1])
        with torch.no_grad():inference.advance(e,torch.tensor([0]),scalar[i:i+1],levels[i:i+1])
        assert torch.equal(training.encoded,inference.encoded)
    with torch.no_grad():
        assert torch.allclose(e.encode_listing(scalar,levels)[-1],inference.encoded[0],atol=1e-6)
        inference.refresh_projection(e)
        assert torch.equal(training.encoded,inference.encoded)


def test_seed_context_and_optimizer_refresh_use_ordered_features():
    from types import SimpleNamespace
    from research.rl_trading.v6.candle_stream import seed_previous_session
    torch.set_num_threads(1); torch.manual_seed(9)
    policy=RankedBracketActorCritic(width=16,wait_hold=True)
    install(policy,dict(mean=[0.]*147,std=[1.]*147),
            dict(mean=[0.]*len(EXTRA_NAMES),std=[1.]*len(EXTRA_NAMES)))
    encoder=policy.encoder; raw=raw_rows()[0]
    scalar=raw[:,:37]; levels=raw[:,37:].reshape(4,2,5,11)
    previous=SimpleNamespace(manifest={'offsets':{'A':0}},
        listing_tail=lambda identity,length=120: SimpleNamespace(
            close_us=np.arange(4,dtype=np.int64),scalar=scalar.numpy(),levels=levels.numpy()))
    state=SparseCandleState.empty(encoder,2,device=torch.device('cpu'),dtype=torch.float32,refreshable=True)
    seed_previous_session(state,encoder,('A','missing'),previous,batch_size=1)
    torch.testing.assert_close(state.encoded[0],encoder.encode_listing(scalar,levels)[-1])
    assert not state.encoded[1].any()
    state.advance(encoder,torch.tensor([0]),scalar[-1:],levels[-1:])
    optimizer=torch.optim.Adam(encoder.parameters(),lr=.001)
    state.embeddings()[0].square().sum().backward()
    assert encoder.project.extra[0].weight.grad is not None
    assert torch.isfinite(encoder.project.extra[0].weight.grad).all()
    optimizer.step();state.detach();state.refresh_projection(encoder,batch_size=1)
    torch.testing.assert_close(state.encoded[0],encoder.encode_listing(
        torch.cat((scalar,scalar[-1:])),torch.cat((levels,levels[-1:])))[-1],atol=2e-5,rtol=2e-5)
