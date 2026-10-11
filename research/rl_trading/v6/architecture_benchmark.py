"""Matched scalable causal-window teachers; not the full-market policy.

All candidates retain the same own-channel adapter and hierarchical heads.
Width controls capacity; architecture-specific default temporal depth is fixed.
No target/episode identity enters the forward path.
"""
from dataclasses import dataclass
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F
from research.rl_trading.v6.channel_features import ChannelProjection
from research.rl_trading.v6.temporal_encoders import TemporalCandleEncoder
from research.rl_trading.v6.hierarchical_heads import HierarchicalTickerHeads, HierarchicalForecast, sequence_losses

VERSION = 'rl-v6-matched-own-channel-window-benchmark-v1'
ARCHITECTURES = ('tcn', 'gru', 'transformer')


@dataclass(frozen=True)
class Candidate:
    architecture: str
    width: int

    def __post_init__(self):
        if self.architecture not in ARCHITECTURES or type(self.width) is not int or self.width < 32 or self.width % 16:
            raise ValueError('Known architecture and scalable multiple-of-16 width required')


class WindowTeacher(nn.Module):
    def __init__(self, candidate, base, extra):
        super().__init__()
        width = candidate.width
        self.encoder = TemporalCandleEncoder(width, architecture=candidate.architecture, structured=True)
        self.encoder.project = ChannelProjection(width, base, extra)
        self.holding = nn.Sequential(nn.Linear(width + 11, width), nn.GELU(), nn.LayerNorm(width))
        self.context = nn.Sequential(nn.Linear(width, width), nn.GELU(), nn.LayerNorm(width))
        self.heads = HierarchicalTickerHeads(width)
        self.size = nn.Linear(width, 1)
        self.forecast = HierarchicalForecast(width, HierarchicalTickerHeads(width))
        self.forecast_gru = nn.GRUCell(width * 2 + 3, width)

    def representation(self, raw, present, held):
        local = self.encoder.encode_history(self.encoder.project.history(raw, present), present)
        values = held.clone()
        values[:, :2] = torch.log1p(values[:, :2].clamp_min(0))
        values[:, 2] = torch.log1p(values[:, 2].clamp_min(0)) / 10
        values[:, 3:6] *= 10
        return self.context(torch.where((held[:, 0] > 0)[:, None],
            self.holding(torch.cat((local, values), -1)), local))

    def head_parameters(self):
        for parameter in self.parameters():
            parameter.requires_grad_(False)
        for module in (self.heads, self.size, self.forecast, self.forecast_gru):
            for parameter in module.parameters():
                parameter.requires_grad_(True)
        return [parameter for parameter in self.parameters() if parameter.requires_grad]


def matched_sizes(base, extra, target=1_000_000):
    """Choose capacity without inspecting labels or validation performance."""
    result = []
    for architecture in ARCHITECTURES:
        candidates = []
        for width in range(64, 257, 16):
            spec = Candidate(architecture, width)
            model = WindowTeacher(spec, base, extra)
            count = sum(p.numel() for p in model.parameters())
            candidates.append((abs(count - target), width, count))
        _, width, count = min(candidates)
        result.append(dict(architecture=architecture, width=width, parameters=count))
    if max(r['parameters'] for r in result) / min(r['parameters'] for r in result) > 1.15:
        raise ValueError('Candidate parameter counts differ by more than 15%')
    return result


def loss(model, context, data, weights):
    """Equal current rows; selected allocation/quality; forced + free forecasts."""
    action = data['action']
    output = model.heads(context, action >= 2)
    current = F.cross_entropy(output.logits, action)
    selected = (action == 0) | (action == 3)
    prediction = output.quality.gather(1, (action == 3).long()[:, None]).flatten()
    quality = F.smooth_l1_loss(prediction[selected], data['quality'][selected], beta=.1) if selected.any() else context.sum() * 0
    valid = torch.isfinite(data['ratio'])
    allocation = F.smooth_l1_loss(model.size(context).flatten().sigmoid()[valid], data['ratio'][valid], beta=.1) if valid.any() else context.sum() * 0
    future_valid = data['future'] >= 0
    hard = data['future'].clamp_min(0)
    future = context.sum() * 0
    for forcing in (F.one_hot(hard, 4).to(context.dtype), None):
        logits = model.forecast(context, model.forecast_gru, steps=5, previous_targets=forcing, validated=True)
        action_loss, quality_loss, *_ = sequence_losses(logits, model.forecast.quality_predictions,
            hard, data['future_soft'], weights)
        future = future + ((action_loss + quality_loss)[future_valid].mean() if future_valid.any() else context.sum() * 0) / 2
    return current + quality + allocation + future


def class_metrics(actual, predicted, probability):
    result = {}
    from sklearn.metrics import average_precision_score
    for index, name in enumerate(('ENTRY', 'WAIT', 'HOLD', 'EXIT')):
        truth, chosen = actual == index, predicted == index
        tp = int((truth & chosen).sum()); count = int(truth.sum()); pred = int(chosen.sum())
        result[name] = dict(count=count, predicted=pred,
            precision=tp / pred if pred else 0., recall=tp / count if count else 0.,
            f1=2 * tp / (count + pred) if count + pred else 0.,
            average_precision=float(average_precision_score(truth, probability[:, index])) if count else None)
    return result


def report(data, current, quality, ratio, future, future_quality):
    action = data['action'].numpy()
    result = dict(current=class_metrics(action, current.argmax(-1), current), quality={}, forecast=[], forecast_quality=[])
    for index, name, branch in ((0, 'ENTRY', 0), (3, 'EXIT', 1)):
        mask = action == index
        result['quality'][name] = float(np.abs(quality[mask, branch] - data['quality'].numpy()[mask]).mean()) if mask.any() else None
    allocation = data['ratio'].numpy(); valid = np.isfinite(allocation)
    result['allocation_count'] = int(valid.sum())
    result['allocation_mae'] = float(np.abs(ratio[valid] - allocation[valid]).mean()) if valid.any() else None
    hard = data['future'].numpy(); soft = data['future_soft'].numpy()
    for step in range(5):
        valid = hard[:, step] >= 0
        result['forecast'].append(class_metrics(hard[valid, step], future[valid, step].argmax(-1), future[valid, step]))
        values = {}
        for index, name, branch in ((0, 'ENTRY', 0), (3, 'EXIT', 1)):
            selected = hard[:, step] == index
            values[name] = float(np.abs(future_quality[selected, step, branch] - soft[selected, step, index]).mean()) if selected.any() else None
        result['forecast_quality'].append(values)
    return result


def passes(report):
    return (all(value['count'] > 0 and value['f1'] >= .95 for value in report['current'].values())
        and len(report['forecast']) == 5
        and all(value['count'] > 0 and value['f1'] >= .95 for step in report['forecast'] for value in step.values())
        and report['allocation_count'] > 0 and report['allocation_mae'] <= .02
        and all(value is not None and np.isfinite(value) and value <= .02 for value in report['quality'].values())
        and len(report['forecast_quality']) == 5
        and all(value is not None and np.isfinite(value) and value <= .02 for step in report['forecast_quality'] for value in step.values()))
