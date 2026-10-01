"""V6 hybrid actor-critic. Oracle labels are absent from its observation API.

Continuous actions retain their latent samples for exact PPO likelihoods;
OMS rounding/partial fills are environmental outcomes, not policy samples.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
import math
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

    @cached_property
    def categorical(self):
        """Normalize this decision's immutable logits once for sample/loss/entropy.

        The object lives for one decision and retains its autograd graph; it is
        never reused after a policy update or across different observations.
        """
        return Categorical(logits=self.logits)

    def parameter_kind(self, token: int) -> int:
        if not 0 <= token < self.logits.numel():
            raise ValueError('Token outside action axis')
        if 1 <= token <= self.listings:
            return 1
        if 1 + self.listings + self.holdings <= token < 1 + self.listings + 3*self.holdings:
            return 2
        return 0

    def log_prob(self, token: int, latent: torch.Tensor) -> torch.Tensor:
        kind = self.parameter_kind(token)
        result = self.categorical.log_prob(
            self.logits.new_tensor(token, dtype=torch.long))
        if kind:
            result = result + Normal(self.locations[token], self.scales[token]).log_prob(latent)
            # Density of transformed proposal, including its Jacobian.
            jacobian = (F.logsigmoid(latent) + F.logsigmoid(-latent)
                        if kind == 1 else F.logsigmoid(latent))
            result = result - jacobian
        return result

    def sample(self) -> tuple[int, torch.Tensor, torch.Tensor, torch.Tensor]:
        token = int(self.categorical.sample())
        kind = self.parameter_kind(token)
        latent = (Normal(self.locations[token], self.scales[token]).sample()
                  if kind else self.logits.new_zeros(()))
        parameter = (latent.sigmoid() if kind == 1 else
                     F.softplus(latent) if kind == 2 else latent)
        return token, latent, parameter, self.log_prob(token, latent)

    def tensor_log_prob(self,token,latent):
        """Scalar token/latent stay on device; same joint density as log_prob."""
        kind=torch.where((token>0)&(token<=self.listings),1,
            torch.where((token>=1+self.listings+self.holdings)&(token<1+self.listings+3*self.holdings),2,0))
        location=self.locations.gather(0,token.reshape(1)).squeeze(0)
        scale=self.scales.gather(0,token.reshape(1)).squeeze(0)
        normal=-.5*((latent-location)/scale).square()-scale.log()-.5*math.log(2*math.pi)
        jac=torch.where(kind==1,F.logsigmoid(latent)+F.logsigmoid(-latent),F.logsigmoid(latent))
        return self.logits.log_softmax(0).gather(0,token.reshape(1)).squeeze(0)+torch.where(kind>0,normal-jac,0.)

    def sample_tensor(self):
        """Avoid the reference sample's int(CUDA token) synchronization."""
        token=torch.multinomial(self.logits.softmax(0),1).squeeze(0)
        kind=torch.where((token>0)&(token<=self.listings),1,
            torch.where((token>=1+self.listings+self.holdings)&(token<1+self.listings+3*self.holdings),2,0))
        location=self.locations.gather(0,token.reshape(1)).squeeze(0)
        scale=self.scales.gather(0,token.reshape(1)).squeeze(0)
        latent=torch.where(kind>0,location+scale*torch.randn_like(location),0.)
        parameter=torch.where(kind==1,latent.sigmoid(),torch.where(kind==2,F.softplus(latent),0.))
        return token,latent,parameter,self.tensor_log_prob(token,latent)


def tensor_batch_statistics(decoded,tokens,latents):
    """Independent [B,A] density/entropy lanes; no time-axis attention.

    Packets have equal action axes. All sampled tokens and Gaussian latents
    remain on device; reductions operate on each packet's action dimension.
    """
    distributions,values=zip(*decoded)
    first=distributions[0]
    if any((d.listings,d.holdings)!=(first.listings,first.holdings) for d in distributions):
        raise ValueError('Unequal batched hybrid action axes')
    logits=torch.stack([d.logits for d in distributions])
    location=torch.stack([d.locations for d in distributions]).gather(1,tokens[:,None]).squeeze(1)
    scale=torch.stack([d.scales for d in distributions]).gather(1,tokens[:,None]).squeeze(1)
    kind=torch.where((tokens>0)&(tokens<=first.listings),1,
        torch.where((tokens>=1+first.listings+first.holdings)&(tokens<1+first.listings+3*first.holdings),2,0))
    normal=-.5*((latents-location)/scale).square()-scale.log()-.5*math.log(2*math.pi)
    jac=torch.where(kind==1,F.logsigmoid(latents)+F.logsigmoid(-latents),F.logsigmoid(latents))
    probability=logits.log_softmax(1).gather(1,tokens[:,None]).squeeze(1)+torch.where(kind>0,normal-jac,0.)
    return probability,torch.stack(values),Categorical(logits=logits).entropy()


class BracketActorCritic(BracketPolicy):
    """Causal candle/action encoder plus stochastic actor and separate critic.

    No temporal attention or future-return input. Critic gradients do not
    alter actor representations; its targets must come from policy rollouts.
    """
    def __init__(self, width: int = 128, *, wait_hold: bool = False):
        super().__init__(width, wait_hold=wait_hold)
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
        if self.decoder.wait_hold:
            locations = torch.cat((locations, logits.new_zeros(h)))
            scales = torch.cat((scales, scale[0].expand(h)))
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

    def prepare_market(self, listings, held_index, masks):
        return listings, masks

    def decode_batch(self, packets):
        """Decode a bounded group of causal packets with equal listing/held axes.

        Each packet contains captured market [N,D], account [7], holdings [H],
        features [H,11], action memory [D], and masks. Sequential state was
        already advanced by the caller; batching does not advance it again.
        """
        markets, accounts, indices, features, memories, masks = zip(*packets)
        market = torch.stack(markets)
        account = torch.stack(accounts)
        held_index = torch.stack(indices)
        held_features = torch.stack(features)
        memory = torch.stack(memories)
        logits, sizes, stops, targets = self.decoder.forward_batch(
            market + memory[:, None], account, held_index, held_features,
            **{name: torch.stack([m[name] for m in masks]) for name in masks[0]})
        b, n = sizes.shape
        h = stops.shape[1]
        locations = torch.cat((logits.new_zeros(b, 1), torch.logit(sizes.clamp(1e-6, 1-1e-6)),
                               logits.new_zeros(b, h), stops, targets), dim=1)
        scale = self.log_scale.clamp(-5, 2).exp()
        scales = torch.cat((scale[0].expand(1+n+h), scale[1].expand(h), scale[2].expand(h)))
        if self.decoder.wait_hold:
            locations = torch.cat((locations, logits.new_zeros(b, h)), dim=1)
            scales = torch.cat((scales, scale[0].expand(h)))
        risk = account.new_zeros(b, 2)
        if h and held_features.shape[2] >= 11:
            weights = held_features[:, :, 0]*held_features[:, :, 1]/account[:, 1:2].clamp_min(1)
            risk = (held_features[:, :, 9:11]*weights[:, :, None]).sum(1)
        critic_input = torch.cat((market.mean(1).detach(), memory.detach(),
            (account.sign()*torch.log1p(account.abs())).detach(), risk.detach()), dim=1)
        values = self.critic(critic_input).squeeze(-1)
        return [(HybridDistribution(logits[i], locations[i], scales, n, h), values[i])
                for i in range(b)]


def elapsed_gae(rewards, values, terminated, elapsed_seconds, *, bootstrap,
                gamma: float = .999, trace_decay: float = .95,parallel_scan=False):
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
        if parallel_scan:
            # Reverse affine recurrence y[t]=delta[t]+coefficient[t]*y[t+1].
            # [T] lanes compose at doubling offsets: O(log T) launches and
            # O(T) memory, preserving zero-time and terminal transitions.
            continuation=(~terminated).to(rewards.dtype)
            discount=gamma**elapsed_seconds
            following=torch.cat((values[1:],bootstrap.reshape(1)))
            delta=rewards+continuation*discount*following-values
            result=delta.flip(0)
            coefficient=(continuation*discount*trace_decay**elapsed_seconds).flip(0)
            offset=1
            while offset<rewards.numel():
                result=torch.cat((result[:offset],result[offset:]+coefficient[offset:]*result[:-offset]))
                coefficient=torch.cat((coefficient[:offset],coefficient[offset:]*coefficient[:-offset]))
                offset*=2
            advantages=result.flip(0)
            return advantages,advantages+values.detach()
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
