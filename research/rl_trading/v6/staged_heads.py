"""TRAIN-only frozen-context head fitting; acceptance still requires causal replay.

This objective assigns equal mass to each selected current row. Forecast loss
averages each row's available horizons and teacher-forced/free trajectories.
It does not replace the population-weighted reporting or dataset contract.
"""
import torch
from torch.nn import functional as F

from research.rl_trading.v6.hierarchical_heads import sequence_losses

VERSION = 'rl-v6-frozen-context-equal-row-head-fit-v1'


def capture(policy, session, targets, device):
    """Capture ordered decoder inputs through the real TRAIN causal evaluator.

    The caller must first authenticate cache/selection/source receipts. This
    function rejects non-TRAIN sessions and records identity alongside tensors,
    so a later loader can verify more than row counts or class frequencies.
    """
    from research.rl_trading.v6.run_ranked_teacher_generalization import evaluate_probabilities
    if session.role != 'train' or not targets:
        raise ValueError('Frozen contexts require nonempty TRAIN targets')
    rows = []
    handle = policy.decoder.heads.register_forward_pre_hook(
        lambda module, args: rows.append(args[0].detach().cpu().clone()))
    evidence = {}
    try:
        metrics, probabilities, arrays = evaluate_probabilities(
            policy, session, targets, device, regression_evidence=evidence)
    finally:
        handle.remove()
    if len(rows) != len(targets) or any(row.ndim != 2 or len(row) != 1 for row in rows):
        raise ValueError('Decoder context/target correspondence changed')
    context = torch.cat(rows)
    if not torch.isfinite(context).all():
        raise ValueError('Nonfinite causal decoder context')
    metrics.update(evidence)
    packet = dict(version=VERSION, day=session.day.isoformat(), role='train',
        context=context, action=arrays['action'], close_us=arrays['close_us'],
        episode_uid=arrays['episode_uid'], listing_index=arrays['listing_index'],
        weights=[target.sample_weight for target in targets])
    return packet, metrics, probabilities


def configure(policy):
    """Freeze causal representation parameters; return unique head parameters."""
    modules = (policy.decoder.heads.entry, policy.decoder.heads.exit,
               policy.decoder.heads.quality, policy.decoder.size_head,
               policy.teacher_forecast, policy.action_gru)
    for parameter in policy.parameters():
        parameter.requires_grad_(False)
    for module in modules:
        for parameter in module.parameters():
            parameter.requires_grad_(True)
    return [parameter for parameter in policy.parameters() if parameter.requires_grad]


def targets_from_packet(session, targets, packet):
    """Rebind captured rows to authoritative targets before fitting any head."""
    import numpy as np
    if (session.role != 'train' or packet.get('role') != 'train' or
            packet.get('version') != VERSION or packet.get('day') != session.day.isoformat() or
            len(targets) != len(packet['context'])):
        raise ValueError('Captured TRAIN session binding changed')
    actions = [(3 if item.token == 1 + len(session.listings) else 2)
               if len(item.held_index) else (0 if item.token else 1) for item in targets]
    expected = dict(action=actions, close_us=[item.close_us for item in targets],
        episode_uid=[item.episode_uid for item in targets],
        listing_index=[int(item.held_index[0]) if len(item.held_index)
                       else item.soft_tokens[1] - 1 for item in targets],
        weights=[item.sample_weight for item in targets])
    if any(not np.array_equal(packet[name], value) for name, value in expected.items()):
        raise ValueError('Captured row identity/action/weight changed')
    quality = torch.tensor([item.soft_probabilities[0 if len(item.held_index) else 1]
                            for item in targets], dtype=torch.float32)
    allocation = torch.tensor([float('nan') if item.allocation_ratio_target is None
                               else item.allocation_ratio_target for item in targets])
    if any(item.forecast_actions is None or item.forecast_probabilities is None for item in targets):
        raise ValueError('Missing saved forecast authority')
    forecasts = []
    for length in sorted({len(item.forecast_actions) for item in targets}):
        indices = [i for i, item in enumerate(targets) if len(item.forecast_actions) == length]
        forecasts.append((torch.tensor(indices, dtype=torch.long),
            torch.tensor(np.stack([targets[i].forecast_actions for i in indices])),
            torch.tensor(np.stack([targets[i].forecast_probabilities for i in indices]), dtype=torch.float32)))
    return packet['context'], torch.tensor(actions, dtype=torch.long), quality, allocation, forecasts


def objective(policy, context, actions, quality, allocation, forecasts, class_weights):
    """Fit all supervised heads against a detached, authenticated TRAIN packet.

    Forecasts are (row indices, hard actions, soft probabilities) grouped by
    available length. Every row must occur exactly once. Authentication and
    causality belong to the capturing launcher, not this tensor objective.
    NaN allocation means no supervised allocation target, never zero.
    """
    n = len(context)
    if (context.ndim != 2 or not n or context.requires_grad or
            not torch.isfinite(context).all() or actions.shape != (n,) or
            actions.dtype != torch.long or ((actions < 0) | (actions > 3)).any() or
            quality.shape != (n,) or allocation.shape != (n,) or
            not torch.isfinite(quality).all() or ((quality < 0) | (quality > 1)).any()):
        raise ValueError('Invalid frozen-context current targets')
    valid = torch.isfinite(allocation)
    if (torch.isinf(allocation).any() or not valid.any() or
            ((allocation[valid] < 0) | (allocation[valid] > 1)).any() or
            (actions[valid] != 0).any()):
        raise ValueError('Allocation requires valid selected ENTRY targets')
    indices = torch.cat([indices for indices, _, _ in forecasts]) if forecasts else actions[:0]
    if indices.dtype != torch.long or not torch.equal(indices.sort().values, torch.arange(n, device=indices.device)):
        raise ValueError('Forecast rows must partition current rows exactly')
    for indices, hard, soft in forecasts:
        if (hard.ndim != 2 or not 1 <= hard.shape[1] <= 5 or
                hard.shape[0] != len(indices) or hard.dtype != torch.long or
                ((hard < 0) | (hard > 3)).any() or
                soft.shape != (*hard.shape, 4) or not torch.isfinite(soft).all() or
                (soft < 0).any() or not torch.allclose(soft.sum(-1), torch.ones_like(soft[..., 0]))):
            raise ValueError('Invalid forecast targets')
    if class_weights.shape != (4,) or not torch.isfinite(class_weights).all() or (class_weights < 0).any():
        raise ValueError('Invalid TRAIN forecast class weights')
    output = policy.decoder.heads(context, actions >= 2)
    current = F.cross_entropy(output.logits, actions)
    selected = (actions == 0) | (actions == 3)
    if not selected.any():
        raise ValueError('Conditional quality targets required')
    prediction = output.quality.gather(1, (actions == 3).long()[:, None]).flatten()
    current_quality = F.smooth_l1_loss(prediction[selected], quality[selected], beta=.1)
    ratio = F.smooth_l1_loss(policy.decoder.size_head(context).flatten().sigmoid()[valid], allocation[valid], beta=.1)
    future = context.new_zeros(())
    for indices, hard, soft in forecasts:
        for forcing in (F.one_hot(hard, 4).to(context.dtype), None):
            logits = policy.teacher_forecast(context[indices], policy.action_gru,
                steps=hard.shape[1], previous_targets=forcing, validated=True)
            action_loss, quality_loss, *_ = sequence_losses(logits,
                policy.teacher_forecast.quality_predictions, hard, soft, class_weights)
            future = future + (action_loss + quality_loss).mean() * len(indices) / n / 2
    return current + current_quality + ratio + future
