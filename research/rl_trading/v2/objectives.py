"""On-policy PPO and finite-session undiscounted advantage targets."""
import numpy as np
import torch


def advantages(rewards, values, dones, bootstrap, *, gamma=1., gae_lambda=.95):
    result = np.zeros(len(rewards),dtype=np.float32)
    tail = 0.
    for t in reversed(range(len(rewards))):
        continuation = 0. if dones[t] else 1.
        next_value = bootstrap if t == len(rewards)-1 else values[t+1]
        delta = rewards[t]+gamma*continuation*next_value-values[t]
        tail = delta+gamma*gae_lambda*continuation*tail
        result[t] = tail
    return result, result+np.asarray(values,dtype=np.float32)


def ppo_loss(logprob, old_logprob, advantage, value, returns, entropy,
             *, clip=.2, value_weight=.5, entropy_weight=.001):
    logratio = logprob-old_logprob
    ratio = torch.exp(logratio)
    policy = -torch.minimum(ratio*advantage,ratio.clamp(1-clip,1+clip)*advantage).mean()
    critic = (value-returns).square().mean()
    loss = policy+value_weight*critic-entropy_weight*entropy.mean()
    if not torch.isfinite(loss):
        raise ValueError('Nonfinite PPO objective')
    kl = ((ratio-1)-logratio).mean().detach()
    return loss, dict(policy_loss=float(policy.detach()),value_loss=float(critic.detach()),
                       entropy=float(entropy.mean().detach()),approx_kl=float(kl))
