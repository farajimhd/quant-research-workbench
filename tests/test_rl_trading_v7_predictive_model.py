import torch
from research.rl_trading.v7.experiments.predictive_model import PredictivePolicy


def test_forecast_inputs_are_current_state_only_and_permutation_equivariant():
    torch.set_num_threads(1);torch.manual_seed(1)
    model=PredictivePolicy(width=16,heads=4)
    with torch.no_grad():model.forecast.weight.normal_(std=.01)
    x=dict(features=torch.randn(2,5,112),account=torch.zeros(2,8),holding=torch.zeros(2,8),
           candidate_valid=torch.ones(2,5,dtype=torch.bool))
    predicted=model.predict(**x)
    p=torch.tensor([2,1,4,0,3])
    changed=dict(x,features=x['features'][:,p],candidate_valid=x['candidate_valid'][:,p])
    assert predicted.shape==(2,5,3)
    assert torch.allclose(predicted[:,p],model.predict(**changed),atol=1e-6)
    labels=torch.randn_like(predicted)
    loss=torch.nn.functional.smooth_l1_loss(predicted,labels)
    loss.backward()
    assert model.forecast.weight.grad.abs().sum()>0
    assert model.time_attention.in_proj_weight.grad.abs().sum()>0
    assert all(torch.isfinite(v.grad).all() for v in model.parameters() if v.grad is not None)


def test_cost_hurdle_and_action_mask():
    torch.set_num_threads(1)
    model=PredictivePolicy(width=16,heads=4)
    with torch.no_grad():
        model.buy.weight.zero_();model.buy.bias.zero_()
        model.control.weight.zero_()
    features=torch.zeros(1,5,112)
    features[0,1,-12]=1. # Candidate1 entryfee one percentage point higher.
    mask=torch.tensor([[True,True,True,False,False,False,False]])
    dist,value=model(features,torch.zeros(1,8),torch.zeros(1,8),torch.ones(1,5,dtype=torch.bool),mask)
    assert dist.probs[0,1]>dist.probs[0,2]*1000
    assert torch.all(dist.probs[0,3:]==0)
