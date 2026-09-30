"""V6 hybrid actor-critic. Oracle labels are absent from its observation API.

Continuous actions retain their latent samples for exact PPO likelihoods;
OMS rounding/partial fills are environmental outcomes, not policy samples.
"""
from __future__ import annotations

from dataclasses import dataclass
import torch
from torch import nn
from torch.distributions import Categorical, Normal
from torch.nn import functional as F
from research.rl_trading.v6.model import BracketPolicy


@dataclass
class HybridDistribution:
    logits: torch.Tensor  # [1+N+3H], masked discrete proposals.
    locations: torch.Tensor  # [1+N+3H], conditional latent means.
    scales: torch.Tensor  # same axis; only ENTER/STOP/TARGET use them.
    listings: int
    holdings: int

    def parameter_kind(self, token: int) -> int:
        if not 0 <= token < self.logits.numel():
            raise ValueError('Token outside action axis')
        if 1 <= token <= self.listings:
            return 1
        if token >= 1 + self.listings + self.holdings:
            return 2
        return 0

    def log_prob(self, token: int, latent: torch.Tensor) -> torch.Tensor:
        kind = self.parameter_kind(token)
        result = Categorical(logits=self.logits).log_prob(
            self.logits.new_tensor(token, dtype=torch.long))
        if kind:
            result = result + Normal(self.locations[token], self.scales[token]).log_prob(latent)
            # Density of transformed proposal, including its Jacobian.
            jacobian = (F.logsigmoid(latent) + F.logsigmoid(-latent)
                        if kind == 1 else F.logsigmoid(latent))
            result = result - jacobian
        return result

    def sample(self) -> tuple[int, torch.Tensor, torch.Tensor, torch.Tensor]:
        token = int(Categorical(logits=self.logits).sample())
        kind = self.parameter_kind(token)
        latent = (Normal(self.locations[token], self.scales[token]).sample()
                  if kind else self.logits.new_zeros(()))
        parameter = (latent.sigmoid() if kind == 1 else
                     F.softplus(latent) if kind == 2 else latent)
        return token, latent, parameter, self.log_prob(token, latent)


class BracketActorCritic(BracketPolicy):
    """Causal candle/action encoder plus stochastic actor and separate critic.

    No temporal attention or future-return input. Critic gradients do not
    alter actor representations; its targets must come from policy rollouts.
    """
    def __init__(self, width: int = 128):
        super().__init__(width)
        self.log_scale = nn.Parameter(torch.full((3,), -1.0))
        self.critic = nn.Sequential(nn.Linear(2 * width + 9, width), nn.Tanh(), nn.Linear(width, 1))

    def distribution_and_value(self, listing_embeddings, account, held_index,
                               held_features, action_state, **masks):
        logits, sizes, stops, targets = self.decide(
            listing_embeddings, account, held_index, held_features, action_state, **masks)
        n, h = sizes.numel(), stops.numel()
        # Convert existing sigmoid size head back to its Gaussian latent.
        locations = torch.cat((logits.new_zeros(1), torch.logit(sizes.clamp(1e-6, 1-1e-6)),
                               logits.new_zeros(h), stops, targets))
        scale = self.log_scale.clamp(-5, 2).exp()
        scales = torch.cat((scale[0].expand(1+n+h), scale[1].expand(h), scale[2].expand(h)))
        critic_market = self.critic_market_embeddings(listing_embeddings)
        risk = account.new_zeros(2)
        if len(held_index) and held_features.shape[1] >= 11:
            weights = held_features[:,0]*held_features[:,1]/account[1].clamp_min(1)
            risk = (held_features[:,9:11]*weights[:,None]).sum(0)
        critic_input = torch.cat((critic_market.mean(0).detach(),
                                  action_state.memory.detach(),
                                  (account.sign()*torch.log1p(account.abs())).detach(),risk.detach()))
        value = self.critic(critic_input).squeeze(-1)
        return HybridDistribution(logits, locations, scales, n, h), value

    def critic_market_embeddings(self, listings):
        return listings


def elapsed_gae(rewards, values, terminated, elapsed_seconds, *, bootstrap,
                gamma: float = .999, trace_decay: float = .95):
    """Detached [T] advantages; same-clock orders incur no time discount.

    Terminal clears continuation. Truncation must supply a causal bootstrap
    from the actual next account state, rather than teacher terminal profit.
    """
    if (rewards.ndim != 1 or any(x.shape != rewards.shape for x in
            (values, terminated, elapsed_seconds)) or terminated.dtype != torch.bool
            or not 0 < gamma <= 1 or not 0 < trace_decay <= 1
            or (elapsed_seconds < 0).any() or not all(torch.isfinite(x).all()
            for x in (rewards, values, elapsed_seconds, bootstrap))):
        raise ValueError('Invalid chronological rollout')
    with torch.no_grad():
        advantages = torch.zeros_like(rewards)
        carry = rewards.new_zeros(())
        following = bootstrap
        for t in range(rewards.numel()-1, -1, -1):
            continuation = (~terminated[t]).to(rewards.dtype)
            discount = gamma ** elapsed_seconds[t]
            delta = rewards[t] + continuation * discount * following - values[t]
            carry = delta + continuation * discount * trace_decay ** elapsed_seconds[t] * carry
            advantages[t] = carry
            following = values[t]
        return advantages, advantages + values.detach()


def clipped_ppo_loss(log_prob, old_log_prob, values, returns, advantages, *, clip=.2):
    """[T] policy samples evaluated with reconstructed chronological state.

    Never shuffle recurrent events independently. Old likelihoods and targets
    are fixed rollout evidence, not differentiable teacher supervision.
    """
    if not 0 < clip < 1 or any(x.shape != log_prob.shape for x in
            (old_log_prob, values, returns, advantages)) or not all(
            torch.isfinite(x).all() for x in (log_prob, old_log_prob, values, returns, advantages)):
        raise ValueError('Invalid PPO tensors')
    ratio = (log_prob-old_log_prob.detach()).exp()
    advantage = advantages.detach()
    policy = -torch.minimum(ratio*advantage, ratio.clamp(1-clip, 1+clip)*advantage).mean()
    critic = .5 * (values-returns.detach()).square().mean()
    return policy + critic, {'policy_loss': policy.detach(), 'value_loss': critic.detach()}
