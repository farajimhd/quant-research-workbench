"""On-policy V6 update contract, separate from teacher initialization.

Environment collectors must preserve latent proposals and actual execution
outcomes. Re-evaluation reconstructs the chronological candle/action states
with current weights; cached old hidden states are not valid PPO inputs.
"""
from dataclasses import dataclass
from collections.abc import Callable
import torch
from research.rl_trading.v6.actor_critic import elapsed_gae, clipped_ppo_loss


@dataclass(frozen=True)
class PolicyRollout:
    old_log_prob: torch.Tensor  # [T], sampled joint proposal likelihood.
    old_values: torch.Tensor  # [T], before action.
    rewards: torch.Tensor  # [T], actual net-equity change / initial equity.
    terminated: torch.Tensor  # [T], environment terminal (not truncation).
    elapsed_seconds: torch.Tensor  # [T], actual environment clock advances.
    bootstrap: torch.Tensor  # scalar, actual next causal state value.

    def targets(self, gamma=.999, trace_decay=.95):
        if any(x.requires_grad for x in (self.old_log_prob, self.old_values,
                                        self.rewards, self.bootstrap)):
            raise ValueError('Rollout evidence must be detached')
        return elapsed_gae(self.rewards, self.old_values, self.terminated,
                           self.elapsed_seconds, bootstrap=self.bootstrap,
                           gamma=gamma, trace_decay=trace_decay)


def update_on_policy(model, optimizer, rollout: PolicyRollout,
                     reconstruct: Callable, *, epochs=4, gamma=.999,
                     trace_decay=.95, clip=.2, max_grad_norm=1., target_kl=.02):
    """Update one ordered rollout using fresh chronological reconstruction.

    `reconstruct(model)` returns [T] joint log probabilities and values for
    the *stored* tokens/latents, after replaying completed observations and
    actual outcomes from a session reset or a causally reconstructed burn-in.
    No sampling, teacher actions, or independent timestep shuffle is allowed.
    This is an update core, not a substitute for an audited OMS collector.
    """
    if epochs < 1 or max_grad_norm <= 0 or target_kl <= 0:
        raise ValueError('Invalid PPO update bounds')
    advantages, returns = rollout.targets(gamma, trace_decay)
    metrics = {}
    for epoch in range(epochs):
        log_prob, values = reconstruct(model)
        loss, metrics = clipped_ppo_loss(log_prob, rollout.old_log_prob,
            values, returns, advantages, clip=clip)
        log_ratio = log_prob.detach()-rollout.old_log_prob
        approximate_kl = ((log_ratio.exp()-1)-log_ratio).mean()
        if not torch.isfinite(approximate_kl):
            raise ValueError('Nonfinite on-policy likelihood ratio')
        # A changed likelihood before the first update means collection and
        # reconstruction disagree; fail rather than optimizing stale states.
        if epoch == 0 and not torch.allclose(log_prob.detach(),
                rollout.old_log_prob, atol=1e-5, rtol=1e-5):
            raise ValueError('Rollout/reconstruction policy likelihood differs')
        if approximate_kl > target_kl:
            metrics['kl_stop'] = True
            break
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        norm = torch.nn.utils.clip_grad_norm_(model.parameters(), max_grad_norm,
                                             error_if_nonfinite=True)
        optimizer.step()
        metrics.update(update_epochs=epoch+1, gradient_norm=float(norm),
                       approximate_kl=float(approximate_kl), kl_stop=False)
    return metrics
