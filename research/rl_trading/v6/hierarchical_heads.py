"""Versioned hard-action hierarchy with separate soft quality supervision.

Known broker position gates current decisions. Future reference-position state
is an auxiliary prediction, never an assumed execution or observation.
"""
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from research.rl_trading.v6.ticker_heads import TickerHeads, TickerOutputs
from research.rl_trading.v6.teacher_forecast import CONTRACT as BASE_CONTRACT

VERSION = 'rl-v6-1b-hierarchical-five-candle-v2'
CONTRACT = dict(BASE_CONTRACT, version=VERSION,
    targets='saved_1b_hard_action_plus_separate_quality',
    hierarchy='known_current_position_predicted_future_reference_state',
    training='equal_teacher_forced_and_free_running_losses',
    head_sharing='current_and_all_future_ENTRY_EXIT_quality_branches',
    forecast_balance='training_only_inverse_sqrt_weighted_class_mass')


class HierarchicalTickerHeads(TickerHeads):
    def __init__(self, width):
        super().__init__(width)
        del self.action
        self.entry = nn.Linear(width, 1)
        self.exit = nn.Linear(width, 1)
        self.quality = nn.Linear(width, 2)  # Entry / exit quality, not action odds.

    def forward(self, representation, held):
        if representation.shape[-1] != self.width or held.shape != representation.shape[:-1] or held.dtype != torch.bool:
            raise ValueError('Ticker representation/position shape mismatch')
        features = F.layer_norm(representation, (self.width,))
        entry, exit_ = self.entry(features).squeeze(-1), self.exit(features).squeeze(-1)
        zero = torch.zeros_like(entry)
        allowed = torch.stack((~held, ~held, held, held), -1)
        logits = torch.stack((entry, zero, zero, exit_), -1).masked_fill(~allowed, -torch.inf)
        return TickerOutputs(logits, self.value(features).squeeze(-1),
            self.stop(features).squeeze(-1).sigmoid()*9999.,
            F.softplus(self.target(features).squeeze(-1)), self.quality(features).sigmoid())


class HierarchicalForecast(nn.Module):
    def __init__(self, width, heads):
        super().__init__()
        self.previous_label = nn.Linear(4, width, bias=False)
        self.reference_held = nn.Linear(width, 1)
        # Deliberately tied modules: one branch authority, one optimizer slot.
        self.entry, self.exit, self.quality = heads.entry, heads.exit, heads.quality
        self.quality_predictions = None

    def forward(self, context, gru, *, steps=5, previous_targets=None, validated=False):
        if context.ndim != 2 or not 1 <= steps <= 5:
            raise ValueError('Invalid forecast shape/horizon')
        if previous_targets is not None and not validated:
            if previous_targets.shape != (len(context), steps, 4) or not torch.isfinite(previous_targets).all() or (previous_targets < 0).any() or not torch.allclose(previous_targets.sum(-1), context.new_ones(len(context), steps)):
                raise ValueError('Invalid hierarchy teacher targets')
        state = context
        logits, qualities = [], []
        for step in range(steps):
            if step:
                previous = previous_targets[:, step-1] if previous_targets is not None else logits[-1].exp()
                state = gru(torch.cat((context, self.previous_label(previous), context.new_zeros(len(context), 3)), -1), state)
            features = F.layer_norm(state, (state.shape[-1],))
            held = self.reference_held(features).squeeze(-1)
            entry, exit_ = self.entry(features).squeeze(-1), self.exit(features).squeeze(-1)
            # Joint log probabilities: state gate × conditional binary action.
            flat_log, held_log = F.logsigmoid(-held), F.logsigmoid(held)
            logits.append(torch.stack((flat_log+F.logsigmoid(entry), flat_log+F.logsigmoid(-entry),
                                       held_log+F.logsigmoid(-exit_), held_log+F.logsigmoid(exit_)), -1))
            qualities.append(self.quality(features).sigmoid())
        self.quality_predictions = torch.stack(qualities, 1)
        return torch.stack(logits, 1)


def forecast_class_weights(decisions):
    """Training targets only; never estimate loss balance from development."""
    mass = np.zeros(4)
    for item in decisions:
        if item.forecast_actions is None:
            continue
        for action in item.forecast_actions:
            mass[int(action)] += item.sample_weight / len(item.forecast_actions)
    present = mass > 0
    if not present.any(): raise ValueError('Hierarchy needs explicit saved forecast actions')
    weights = np.zeros(4)
    weights[present] = np.sqrt(mass.sum()/mass[present])
    weights /= (weights*mass).sum()/mass.sum()
    return weights


def sequence_losses(logits, qualities, actions, soft_targets, class_weights):
    """Hard actions and their saved probability mass are different targets."""
    hard = F.one_hot(actions, 4).to(logits.dtype)
    ce = -(hard*logits).sum(-1)
    action_loss = ce * class_weights[actions]
    quality_target = soft_targets.gather(-1, actions[..., None]).squeeze(-1)
    branch = (actions == 3).long()
    quality_prediction = qualities.gather(-1, branch[..., None]).squeeze(-1)
    selected = (actions == 0) | (actions == 3)
    quality_loss = F.smooth_l1_loss(quality_prediction, quality_target, beta=.1, reduction='none')
    quality_loss = quality_loss.masked_fill(~selected, 0.)
    quality_error = (quality_prediction-quality_target).abs().masked_fill(~selected, 0.)
    return action_loss, quality_loss, ce, quality_error, selected
