"""Chronological, bounded-memory V5 teacher-forced session training core.

This module is deliberately not a campaign launcher. A launcher must first
verify the split, bind every certified feature bank and teacher ledger, and
provide checkpoint, validation replay, and W&B lifecycle management.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl
import torch

from research.rl_trading.v1.features import FEATURE_NAMES
from research.rl_trading.v1.model_v5 import DynamicMarketPolicy, PolicyState
from research.rl_trading.v1.objective_v5 import teacher_loss
from research.rl_trading.v1.v5_feature_binding import FeatureBinding, feature_chunks
from research.rl_trading.v1.v5_order_adapter import (
    OrderSecond, empty_stop_seconds, order_seconds, stop_second_indices)


@dataclass(frozen=True)
class SessionMetrics:
    optimization_steps: int
    active_seconds: int
    sampled_empty_seconds: int
    teacher_orders: int
    loss: float
    action_loss: float
    size_loss: float
    action_accuracy: float
    buy_size_mae: float


def selected_seconds(binding: FeatureBinding, orders: pl.DataFrame,
                     trajectory: pl.DataFrame, positions: pl.DataFrame,
                     bank: np.ndarray, *, initial_cash: float,
                     stop_radius: int = 15, background_stride: int = 60):
    """Merge sparse teacher orders and sampled STOP targets in time order."""
    first = int(trajectory['time_us'][0])
    active = np.asarray([(int(at)-first)//1_000_000
                         for at in orders['time_us'].unique().to_list()], dtype=np.int64)
    stops = stop_second_indices(active, binding.decision_seconds,
                                radius=stop_radius,
                                background_stride=background_stride)
    actions = iter(order_seconds(orders, trajectory, positions, bank,
                                 binding.tickers, initial_cash=initial_cash))
    empties = iter(empty_stop_seconds(stops, trajectory, positions, bank,
                                      binding.tickers))
    action = next(actions, None)
    empty = next(empties, None)
    while action is not None or empty is not None:
        if empty is None or action is not None and action.second < empty.second:
            yield action
            action = next(actions, None)
        elif action is None or empty.second < action.second:
            yield empty
            empty = next(empties, None)
        else:
            raise ValueError('A teacher action was labeled as an empty second')


def _on_device(step: OrderSecond, device: torch.device):
    """Add a batch axis to one variable-length, within-second order sequence."""
    def tensor(values):
        return torch.from_numpy(values).unsqueeze(0).to(device, non_blocking=True)
    return dict(held_index_by_order=tensor(step.held_index),
                held_valid_by_order=tensor(step.held_valid),
                held_features_by_order=tensor(step.held_features),
                account_by_order=tensor(step.account),
                action_mask=tensor(step.action_mask),
                teacher_tokens=tensor(step.token),
                teacher_sizes=tensor(step.size),
                order_valid=torch.ones(1, len(step.token), dtype=torch.bool,
                                       device=device))


def train_chronological_session(model: DynamicMarketPolicy, optimizer: torch.optim.Optimizer,
                                binding: FeatureBinding, orders: pl.DataFrame,
                                trajectory: pl.DataFrame, positions: pl.DataFrame,
                                ticker_ids: np.ndarray, *, initial_cash: float,
                                device: torch.device, seconds_per_chunk: int = 8,
                                stop_radius: int = 15,
                                background_stride: int = 60,
                                empty_loss_weight: float = .1,
                                grad_clip: float = 1.) -> SessionMetrics:
    """Train one session with exact chronological state and bounded BPTT.

    Features are projected once per second, including seconds with no target.
    An optimizer step occurs at each labeled chunk; the 120-second cache and
    cross-second action GRU are detached only at chunk boundaries. This avoids
    retaining a graph for the full 57,481-second session. It must be called
    with a fresh model state for each session and never with sealed test data.
    """
    if (binding.decision_seconds != trajectory.height or
            ticker_ids.shape != (len(binding.tickers),) or
            np.any(ticker_ids < 1) or np.any(ticker_ids > model.unknown_ticker_id) or
            seconds_per_chunk < 1 or not 0 < empty_loss_weight <= 1 or
            grad_clip <= 0):
        raise ValueError('Invalid V5 training session contract')
    bank = np.load(binding.features, mmap_mode='r', allow_pickle=False)
    if bank.shape != (len(binding.tickers), 57_601, len(FEATURE_NAMES)):
        raise ValueError('Certified feature bank shape changed')
    device_ids = torch.from_numpy(np.asarray(ticker_ids, dtype=np.int64).copy())[
        None].to(device)
    state = model.initial_state(1, len(binding.tickers), device=device,
                                dtype=torch.float32)
    selected = iter(selected_seconds(binding, orders, trajectory, positions, bank,
                                      initial_cash=initial_cash,
                                      stop_radius=stop_radius,
                                      background_stride=background_stride))
    next_step = next(selected, None)
    total = {name:torch.zeros((), device=device) for name in
             ('loss', 'action_loss', 'size_loss', 'action_accuracy', 'buy_size_mae')}
    examples = active_seconds = empty_seconds = teacher_orders = updates = 0
    model.train()
    try:
        for start, chunk in feature_chunks(binding, seconds_per_chunk=seconds_per_chunk,
                                           device=device):
            stop = start+chunk.shape[1]
            labeled = next_step is not None and next_step.second < stop
            with (nullcontext() if labeled else torch.no_grad()):
                encoded, chunk_state = model.encode_chunk(chunk, state)
            history = state.actions
            losses = []
            weights = []
            while next_step is not None and next_step.second < stop:
                step = next_step
                if step.second < start:
                    raise ValueError('Selected V5 seconds are not strictly ordered')
                valid_cpu = np.asarray(bank[:, step.second,
                                            FEATURE_NAMES.index('price_available')] > .5)
                valid = torch.from_numpy(valid_cpu)[None].to(device)
                tensors = _on_device(step, device)
                logits, sizes, updated = model.teacher_forced_encoded_second(
                    encoded[:, step.second-start],
                    PolicyState(chunk_state.temporal, history),
                    ticker_id=device_ids, valid=valid, **tensors)
                history = updated.actions
                loss, metrics = teacher_loss(logits, sizes,
                    tensors['teacher_tokens'], tensors['teacher_sizes'],
                    tensors['order_valid'])
                is_empty = len(step.token) == 1 and step.token[0] == 0
                weight = empty_loss_weight if is_empty else 1.
                losses.append(loss*weight)
                weights.append(weight)
                for name in total:
                    key = 'order_accuracy' if name == 'action_accuracy' else name
                    value = loss if name == 'loss' else metrics[key]
                    total[name] += value.detach()*weight
                examples += 1
                if is_empty:
                    empty_seconds += 1
                else:
                    active_seconds += 1
                    teacher_orders += len(step.token)-1
                next_step = next(selected, None)
            if losses:
                optimizer.zero_grad(set_to_none=True)
                combined = torch.stack(losses).sum()/sum(weights)
                if not torch.isfinite(combined):
                    raise FloatingPointError('Nonfinite V5 teacher loss')
                combined.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), grad_clip,
                                               error_if_nonfinite=True)
                optimizer.step()
                updates += 1
            state = PolicyState(chunk_state.temporal, history).detach()
        if next_step is not None or examples == 0:
            raise ValueError('V5 selected seconds extend past the feature bank')
        denominator = (active_seconds + empty_loss_weight*empty_seconds)
        return SessionMetrics(updates, active_seconds, empty_seconds, teacher_orders,
            *(float((total[name]/denominator).cpu()) for name in total))
    finally:
        del bank
