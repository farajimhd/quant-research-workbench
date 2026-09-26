import torch

from research.rl_trading.v1.model import MarketPolicy
from research.rl_trading.v1.objectives import teacher_loss


def test_policy_scores_identity_preserving_market_and_teacher_orders():
    n,l,k,b,t,f = 3,2,2,2,5,25
    model = MarketPolicy(features=f,tickers=5,top_n=n,max_lots=l,max_orders=k,
        d_model=32,layers=1,heads=4)
    batch = dict(market=torch.randn(b,n,t,f),valid=torch.ones(b,n,dtype=torch.bool),
        ticker_id=torch.tensor([[1,2,3],[3,1,2]]),rank=torch.zeros(b,n),
        held=torch.zeros(b,n),lots=torch.zeros(b,l,3),
        lot_slots=torch.full((b,l),-1),account=torch.ones(b,3),
        actions=torch.tensor([[1,0],[0,0]]),return_to_go=torch.zeros(b))
    mask = torch.zeros(b,k,1+n+l,dtype=torch.bool)
    mask[:,:,0] = True
    mask[:,:,1] = True
    batch['action_mask'] = mask
    logits,value = model(batch,teacher_actions=batch['actions'])
    loss,metrics = teacher_loss(logits,value,batch)
    assert logits.shape == (b,k,1+n+l)
    assert value.shape == (b,)
    assert torch.isfinite(loss)
    loss.backward()
    assert model.temporal[0].weight.grad is not None
    assert 'trade_recall' in metrics


def test_temporal_encoder_uses_older_history_and_unknown_identity():
    model = MarketPolicy(features=3,tickers=2,top_n=1,max_lots=1,max_orders=1,
        d_model=16,layers=1,heads=4).eval()
    market = torch.zeros(1,1,120,3)
    batch = dict(market=market,valid=torch.ones(1,1,dtype=torch.bool),
        ticker_id=torch.tensor([[model.unknown_ticker_id]]),rank=torch.zeros(1,1),
        held=torch.zeros(1,1),lots=torch.zeros(1,1,3),
        lot_slots=torch.full((1,1),-1),account=torch.ones(1,3))
    with torch.no_grad():
        before = model.encode(batch)[0].clone()
        batch['market'][0,0,10,0] = 100.
        after = model.encode(batch)[0]
    assert not torch.allclose(before,after)
    assert model.identity.weight[model.unknown_ticker_id].requires_grad
