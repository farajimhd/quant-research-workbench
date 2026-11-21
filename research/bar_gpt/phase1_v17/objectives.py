import torch
from ..phase1_v16.model import direction_objective

ACTIVITY = (4, 5, 10, 11, 16, 17)
ARMS = ('control', 'activity', 'activity_delta', 'activity_tail', 'activity_visible', 'activity_preserve')


def activity_terms(logs, raw, source_valid, known, hidden_weight=.1):
    """Targets only in the objective; zero activity is valid. No clipping."""
    mask = source_valid[..., ACTIVITY]
    target = torch.where(mask, raw[..., ACTIVITY].double(), 0).log1p().float()
    predicted = logs[..., ACTIVITY]
    weights = torch.where(known, 1., hidden_weight)[..., None] * mask
    error = predicted - target
    level = (torch.where(mask, error.square(), 0)*weights).sum((0, 1)) / weights.sum((0, 1)).clamp_min(1)
    pair = mask[:, 1:] & mask[:, :-1]
    pair_weights = torch.where(known[:, 1:] & known[:, :-1], 1., hidden_weight)[..., None]*pair
    delta_error = error[:, 1:] - error[:, :-1]
    delta = (torch.where(pair, delta_error.square(), 0)*pair_weights).sum((0, 1))/pair_weights.sum((0, 1)).clamp_min(1)
    # Fourth power is a smooth tail penalty in log units, not an output clamp.
    tail = (torch.where(mask, error.pow(4), 0)*weights).sum((0, 1))/weights.sum((0, 1)).clamp_min(1)
    return level.mean(), delta.mean(), tail.mean()


def objective(model, teacher, pred, latent, valid, pos, raw, source_valid, arm):
    if arm not in ARMS:
        raise ValueError(arm)
    loss, body = direction_objective(model, teacher, pred, latent, valid, pos, raw, source_valid)
    logs = teacher.decode(model.denormalize(pred))['logs']
    known = torch.zeros_like(valid).scatter(1, pos, True)
    level, delta, tail = activity_terms(logs, raw, source_valid, known, 0. if arm in ('activity_visible','activity_preserve') else .1)
    if arm != 'control':
        loss = loss + level
    if arm in ('activity_delta', 'activity_tail', 'activity_visible', 'activity_preserve'):
        loss = loss + delta
    if arm == 'activity_preserve':
        loss = loss + .4*body
    if arm == 'activity_tail':
        loss = loss + .1*tail
    return loss, dict(body=body, activity_log_mse=level, activity_delta_mse=delta, activity_tail=tail)
