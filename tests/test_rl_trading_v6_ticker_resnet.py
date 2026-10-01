import pytest
import torch
from research.rl_trading.v6.model import INPUT_WIDTH
from research.rl_trading.v6.ticker_resnet import TickerResNet
from research.rl_trading.v6.ticker_heads import supervised_loss


@pytest.mark.parametrize('device',['cpu']+(['cuda'] if torch.cuda.is_available() else []))
def test_local_resnet_isolation_padding_gradients_and_optimizer(device):
    torch.manual_seed(42)
    model=TickerResNet(8).to(device)
    x=torch.randn(2,120,INPUT_WIDTH,device=device)
    present=torch.ones(2,120,dtype=torch.bool,device=device);present[:,:10]=False
    account=torch.zeros(2,7,device=device)
    held=torch.tensor([False,True],device=device)
    features=torch.zeros(2,11,device=device);cost=torch.zeros(2,11,device=device)
    out=model(x,present,account,held,features,cost)
    changed=x.clone();changed[1]+=100;changed[0,:10]=float('nan')
    other=model(changed,present,account,held,features,cost)
    torch.testing.assert_close(out.logits[0],other.logits[0])
    assert torch.isneginf(out.logits[0,2:]).all()
    assert torch.isneginf(out.logits[1,:2]).all()
    p=x.new_tensor([[.8,.2,0,0],[0,0,.2,.8]])
    missing=x.new_full((2,),float('nan'));valid=torch.zeros(2,dtype=torch.bool,device=device)
    loss,_=supervised_loss(out,p,value_bps=missing,value_valid=valid,
        stop_bps=missing,target_bps=missing,bracket_valid=valid)
    before=model.encoder[0].weight.detach().clone()
    optimizer=torch.optim.Adam(model.parameters(),lr=.001)
    loss.backward();assert model.encoder[0].weight.grad.abs().sum()>0
    optimizer.step();assert not torch.equal(before,model.encoder[0].weight)
    state={k:v.clone() for k,v in model.state_dict().items()}
    model.eval()
    with torch.no_grad():model(x,present,account,held,features,cost)
    assert all(torch.equal(v,model.state_dict()[k]) for k,v in state.items())


def test_window_builder_uses_actual_candles_and_never_future_rows():
    import numpy as np
    from types import SimpleNamespace
    from research.rl_trading.v6.execution_features import VERSION
    from research.rl_trading.v6.ticker_resnet_data import prepare_windows
    scalar=np.zeros((3,37),np.float32);scalar[:,8]=[1.,2.,3.]
    source=SimpleNamespace(close_us=np.array([100,300,900]),scalar=scalar,
        levels=np.zeros((3,2,5,11),np.float32))
    session=SimpleNamespace(listings=('A',),previous=None,
        bank=SimpleNamespace(listing=lambda name:source))
    labels=(SimpleNamespace(close_us=300,held_index=np.empty(0,int),soft_tokens=(0,1)),)
    normal=dict(version=VERSION,scope='train_only',mean=[0.]*INPUT_WIDTH,std=[1.]*INPUT_WIDTH)
    x,present=prepare_windows(session,labels,normal)
    assert present.sum()==2 and present[0,-2:].all()
    assert np.all(x[0,:-2]==0)
    source.scalar[2]=float('nan')
    changed,_=prepare_windows(session,labels,normal)
    assert np.array_equal(x,changed)
    with pytest.raises(ValueError,match='budget'):
        prepare_windows(session,labels,normal,max_bytes=1)
