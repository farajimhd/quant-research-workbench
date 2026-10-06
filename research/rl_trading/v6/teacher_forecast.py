"""Versioned five-observed-candle 1b label supervision, never observations.

The shared execution GRU is pretrained as a label decoder. This is not a
teacher broker: fill/P&L channels are zero and no holding is manufactured.
Evaluation rolls out predictions; training may use only the preceding label.
"""
from dataclasses import dataclass

import numpy as np
import polars as pl
import torch
from torch import nn
from torch.nn import functional as F

VERSION = 'rl-v6-1b-autoregressive-five-candle-v2'
STEPS = 5
CONTRACT = dict(version=VERSION, steps=STEPS,
    axis='current_and_next_four_observed_price_candles_same_listing_same_session',
    feature_cutoff='close_us_strictly_before_current_target',
    targets='copied_1b_teacher_probabilities',
    training='previous_label_teacher_forcing', evaluation='free_running',
    sizing='masked_selected_ENTRY_allocation_ratio_not_session_softmax',
    memory='shared_GRU_label_pretraining_no_synthetic_execution_outcomes',
    ranking='bounded_attention_full_certified_action_population',
    held_forecast='saved conditional HOLD_EXIT quality; same listing/pair; stop at absent exit coverage')


def configure(policy, *, hierarchical=False, shared_heads=True):
    """One architecture factory shared by the launcher and laptop probe."""
    from research.rl_trading.v6.ticker_heads import TickerDecoder
    parameter=next(policy.parameters())
    policy.decoder=TickerDecoder(policy.encoder.width,teacher_sequence=True).to(parameter.device)
    if hierarchical:
        from research.rl_trading.v6.hierarchical_heads import HierarchicalTickerHeads, HierarchicalForecast, VERSION as HIERARCHICAL_VERSION,SEPARATE_VERSION
        policy.decoder.heads=HierarchicalTickerHeads(policy.encoder.width).to(parameter.device)
        future_heads=policy.decoder.heads if shared_heads else HierarchicalTickerHeads(policy.encoder.width).to(parameter.device)
        policy.teacher_forecast=HierarchicalForecast(policy.encoder.width,future_heads).to(parameter.device)
        policy.decoder.action_version=HIERARCHICAL_VERSION if shared_heads else SEPARATE_VERSION
    else:
        if not shared_heads:raise ValueError('Independent forecast branches require hierarchical heads')
        policy.teacher_forecast=LabelForecast(policy.encoder.width).to(parameter.device)
    policy.hierarchical_teacher=hierarchical
    policy.full_market_actions=True
    return policy


@dataclass
class ForecastWindows:
    """Shared arrays; each decision retains a view, not five copied rows."""
    probabilities: np.ndarray
    clocks: np.ndarray
    ends: np.ndarray
    actions: np.ndarray | None = None

    @classmethod
    def from_frame(cls, frame):
        disorder=frame.select(((pl.col('listing_id')<pl.col('listing_id').shift(1)) |
            ((pl.col('listing_id')==pl.col('listing_id').shift(1)) &
             (pl.col('time_us')<pl.col('time_us').shift(1)))).any()).item()
        if disorder:
            raise ValueError('Forecast rows must be sorted by identity and clock')
        if frame.select(pl.struct('listing_id', 'time_us').n_unique()).item() != frame.height:
            raise ValueError('Duplicate forecast candle identity')
        p = frame['teacher_probabilities'].list.to_array(4).to_numpy().astype(np.float32)
        clocks = frame['time_us'].to_numpy().astype(np.int64)
        counts = frame.group_by('listing_id', maintain_order=True).len()['len'].to_numpy().astype(np.int64)
        ends = np.repeat(np.cumsum(counts), counts)
        if not np.isfinite(p).all() or (p < 0).any() or not np.allclose(p.sum(1), 1):
            raise ValueError('Invalid copied 1b forecast probabilities')
        for a in (p, clocks, ends):
            a.setflags(write=False)
        actions=None
        if 'action' in frame.columns:
            names={'ENTRY':0,'WAIT':1,'HOLD':2,'EXIT':3}
            if frame['action'].null_count() or not frame['action'].is_in(list(names)).all():raise ValueError('Invalid saved forecast action')
            actions=frame['action'].replace_strict(names,return_dtype=pl.Int64).to_numpy()
            actions.setflags(write=False)
        return cls(p, clocks, ends, actions)

    def action_window(self, index):
        if self.actions is None:return None
        return self.actions[index:min(index+STEPS,int(self.ends[index]))]

    @classmethod
    def from_reference_frame(cls, frame):
        """Conditional held targets from saved fields, without simulated fills."""
        base=cls.from_frame(frame)
        gain=frame['exit_gain'].to_numpy()
        quality=frame['exit_quality'].to_numpy().astype(np.float32)
        valid=np.isfinite(gain) & np.isfinite(quality)
        if ((quality[valid]<0)|(quality[valid]>1)).any():raise ValueError('Invalid saved held quality')
        names=frame['reference_action'].to_numpy()
        if not frame['reference_action'].is_in(['ENTRY','WAIT','HOLD','EXIT']).all():raise ValueError('Invalid saved held action')
        actions=np.where(names=='EXIT',3,2).astype(np.int64)
        probabilities=np.zeros((frame.height,4),np.float32)
        probabilities[valid,2]=1-quality[valid];probabilities[valid,3]=quality[valid]
        # Contiguous pair identity and coverage bound every conditional window.
        pairs=frame['pair_id'].to_numpy();ids=frame['listing_id'].to_numpy();indices=np.arange(frame.height,dtype=np.int64)
        continuation=valid[:-1]&valid[1:]&(ids[:-1]==ids[1:])&(pairs[:-1]==pairs[1:])
        boundaries=np.append(np.flatnonzero(~continuation)+1,frame.height)
        ends=np.where(valid,boundaries[np.searchsorted(boundaries,indices,side='right')],indices)
        for array in (probabilities,actions,ends):array.setflags(write=False)
        return cls(probabilities,base.clocks,ends,actions)

    def window(self, index):
        end = min(index + STEPS, int(self.ends[index]))
        return self.probabilities[index:end], self.clocks[index:end]


class LabelForecast(nn.Module):
    def __init__(self, width):
        super().__init__()
        self.previous_label = nn.Linear(4, width, bias=False)
        self.action = nn.Linear(width, 4)

    def forward(self, context, gru, *, steps=STEPS, previous_targets=None, validated=False):
        if context.ndim != 2 or not 1 <= steps <= STEPS:
            raise ValueError('Invalid forecast shape/horizon')
        if previous_targets is not None and not validated:
            if (previous_targets.shape != (len(context), steps, 4) or
                    not torch.isfinite(previous_targets).all() or
                    (previous_targets < 0).any() or
                    not torch.allclose(previous_targets.sum(-1), context.new_ones(len(context), steps))):
                raise ValueError('Invalid teacher-forcing probabilities')
        state = context
        logits = []
        for step in range(steps):
            if step:
                previous = (previous_targets[:, step-1] if previous_targets is not None
                            else logits[-1].softmax(-1))
                recurrent_input = torch.cat((context, self.previous_label(previous),
                                            context.new_zeros(len(context), 3)), -1)
                state = gru(recurrent_input, state)
            logits.append(self.action(F.layer_norm(state, (state.shape[-1],))))
        return torch.stack(logits, 1)


def forecast_loss(logits, probabilities, *, validated=False):
    if logits.shape != probabilities.shape:
        raise ValueError('Invalid forecast output shape')
    if not validated and (not torch.isfinite(logits).all() or
            not torch.isfinite(probabilities).all() or (probabilities < 0).any() or
            not torch.allclose(probabilities.sum(-1), torch.ones_like(probabilities[...,0]))):
        raise ValueError('Invalid forecast outputs/targets')
    return -(probabilities * logits.log_softmax(-1)).sum(-1)


def allocation_loss(prediction, target):
    if not 0 <= target <= 1 or not np.isfinite(target):
        raise ValueError('Invalid 1b allocation ratio')
    expected = prediction.new_tensor(target)
    return F.smooth_l1_loss(prediction, expected, beta=.1), (prediction-expected).abs()
