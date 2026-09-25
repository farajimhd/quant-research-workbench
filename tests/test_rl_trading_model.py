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
