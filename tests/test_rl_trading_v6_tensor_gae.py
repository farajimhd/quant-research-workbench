import pytest
import torch
from research.rl_trading.v6.actor_critic import elapsed_gae,HybridDistribution,tensor_batch_statistics


@pytest.mark.parametrize('device',['cpu']+(['cuda'] if torch.cuda.is_available() else []))
def test_parallel_affine_gae_matches_chronological_recurrence(device):
    torch.manual_seed(16)
    rewards=torch.randn(2049,device=device)*.01
    values=torch.randn_like(rewards)*.1
    terminal=torch.rand(2049,device=device)<.02
    elapsed=torch.randint(0,4,(2049,),device=device).float()
    bootstrap=rewards.new_tensor(.23)
    reference=elapsed_gae(rewards,values,terminal,elapsed,bootstrap=bootstrap)
    vectorized=elapsed_gae(rewards,values,terminal,elapsed,bootstrap=bootstrap,parallel_scan=True)
    for a,b in zip(reference,vectorized):
        assert torch.allclose(a,b,atol=2e-6,rtol=2e-6)


@pytest.mark.parametrize('device',['cpu']+(['cuda'] if torch.cuda.is_available() else []))
def test_batched_hybrid_density_entropy_and_gradients(device):
    logits=torch.randn(4,10,device=device,requires_grad=True)
    location=torch.randn_like(logits,requires_grad=True)
    scales=torch.ones_like(logits)*.5
    tokens=torch.tensor([0,2,5,9],device=device);latents=torch.randn(4,device=device)
    decoded=[(HybridDistribution(logits[i],location[i],scales[i],3,2),logits[i].sum()) for i in range(4)]
    expected=torch.stack([d.tensor_log_prob(tokens[i],latents[i]) for i,(d,_) in enumerate(decoded)])
    entropy=torch.stack([d.categorical.entropy() for d,_ in decoded])
    (expected.sum()+entropy.sum()).backward(retain_graph=True);gradient=logits.grad.clone();logits.grad=None
    result,_,batched_entropy=tensor_batch_statistics(decoded,tokens,latents)
    (result.sum()+batched_entropy.sum()).backward()
    assert torch.allclose(result,expected,atol=1e-6,rtol=1e-6)
    assert torch.allclose(batched_entropy,entropy,atol=1e-6,rtol=1e-6)
    assert torch.allclose(logits.grad,gradient,atol=1e-6,rtol=1e-6)
