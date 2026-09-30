"""Chronological V6 training core over packed actual-candle events.

This core deliberately accepts certified, already reconstructed price-action
teacher snapshots. Teacher order outcomes are idealized candle-path events;
the separate quote-aware environment supplies later interactive learning and
model replay. Old V5 clock-second orders cannot satisfy this contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby, islice
import math

import numpy as np
import torch

from research.rl_trading.v6.candle_stream import (SparseCandleState,
                                                   seed_previous_session)
from research.rl_trading.v6.model import BracketPolicy, HELD_FEATURE_WIDTH
from research.rl_trading.v6.objective import bracket_loss
from research.rl_trading.v6.session_data import PackedSession


ACTION_NAMES = ('hold', 'enter_long', 'exit_long', 'set_stop', 'set_target')


def _action_class(token: int, listings: int, holdings: int) -> int:
    """Map a variable-width identity token to one of five stable classes."""
    if token == 0:
        return 0
    if token <= listings:
        return 1
    return 2 + (token - 1 - listings) // holdings


@dataclass(frozen=True)
class TeacherDecision:
    """One action at a completed candle close; state is strictly pre-action."""

    close_us: int
    order_index: int
    token: int  # 0 HOLD, 1..N enter, then H exit/stop/target slots.
    account: np.ndarray  # [7] causal cash/equity/P&L/exposure/age/pending.
    held_index: np.ndarray  # [H] identity-ordered listing indices.
    held_features: np.ndarray  # [H,9], includes causal armed bracket state.
    enter_allowed: np.ndarray  # [N] causal fresh/listing/cash eligibility.
    exit_allowed: np.ndarray  # [H].
    stop_allowed: np.ndarray  # [H], false until confirmed entry fill.
    target_allowed: np.ndarray  # [H], false until confirmed entry fill.
    size_fraction: float | None = None
    oracle_log_distance: float | None = None


@dataclass(frozen=True)
class ExecutionOutcome:
    """Later hypothetical teacher result; never visible at the decision."""

    source_close_us: int
    source_order_index: int
    bucket_end_us: int
    action: int  # 1 ENTER, 2 EXIT, 3 SET_STOP, 4 SET_TARGET.
    listing_index: int
    requested_fraction: float
    filled_fraction: float
    realized_net_over_equity: float


@dataclass(frozen=True)
class TrainingMetrics:
    decisions: int
    execution_outcomes: int
    optimizer_steps: int
    mean_loss: float
    action_accuracy: float
    action_class_counts: dict[str, int]
    action_class_precision: dict[str, float]
    action_class_recall: dict[str, float]
    action_class_f1: dict[str, float]
    buy_size_mae: float | None
    stop_log_distance_mae: float | None
    target_log_distance_mae: float | None


def _validate(decisions: tuple[TeacherDecision, ...],
              outcomes: tuple[ExecutionOutcome, ...],
              listings: int) -> None:
    previous_key = None
    by_key = {}
    for item in decisions:
        key = (item.close_us, item.order_index)
        held = len(item.held_index)
        if (previous_key is not None and key <= previous_key or
                item.close_us <= 0 or item.order_index < 0 or
                item.account.shape != (7,) or
                item.held_index.shape != (held,) or
                item.held_features.shape != (held, HELD_FEATURE_WIDTH) or
                item.enter_allowed.shape != (listings,) or
                any(mask.shape != (held,) for mask in
                    (item.exit_allowed, item.stop_allowed,
                     item.target_allowed)) or
                any(mask.dtype != np.bool_ for mask in
                    (item.enter_allowed, item.exit_allowed,
                     item.stop_allowed, item.target_allowed)) or
                np.any(item.held_index < 0) or
                np.any(item.held_index >= listings) or
                len(np.unique(item.held_index)) != held or
                not np.isfinite(item.account).all() or
                not np.isfinite(item.held_features).all()):
            raise ValueError('Malformed or unsorted causal teacher decision')
        permitted = np.concatenate((np.ones(1, dtype=np.bool_),
            item.enter_allowed, item.exit_allowed, item.stop_allowed,
            item.target_allowed))
        if not 0 <= item.token < len(permitted) or not permitted[item.token]:
            raise ValueError('Teacher selected a masked bracket action')
        enters = 1 <= item.token <= listings
        bracket_base = 1 + listings + held
        bracket_action = bracket_base <= item.token < bracket_base + 2*held
        if (enters != (item.size_fraction is not None) or
                bracket_action != (item.oracle_log_distance is not None)):
            raise ValueError('Conditional label does not match teacher action')
        previous_key = key
        by_key[key] = item
    previous_clock = 0
    for item in outcomes:
        source = by_key.get((item.source_close_us, item.source_order_index))
        if source is None:
            raise ValueError('Outcome has no earlier teacher decision')
        held = len(source.held_index)
        base = 1 + listings
        expected_action = (1 if 1 <= source.token <= listings else
                           2 if base <= source.token < base + held else
                           3 if base + held <= source.token < base + 2*held else
                           4 if base + 2*held <= source.token < base + 3*held
                           else 0)
        slot = (source.token - 1 if expected_action == 1 else
                source.token - base - (expected_action - 2) * held
                if expected_action else -1)
        expected_listing = (slot if expected_action == 1 else
                            int(source.held_index[slot]) if expected_action else -1)
        if (item.bucket_end_us < previous_clock or
                item.bucket_end_us < item.source_close_us or
                (item.action in (1, 2) and
                 item.bucket_end_us == item.source_close_us) or
                item.action != expected_action or
                item.listing_index != expected_listing or
                not 0 <= item.listing_index < listings or
                not all(math.isfinite(value) for value in
                    (item.requested_fraction, item.filled_fraction,
                     item.realized_net_over_equity)) or
                not 0 <= item.requested_fraction <= 1 or
                not 0 <= item.filled_fraction <= 1):
            raise ValueError('Malformed or unsorted later execution outcome')
        previous_clock = item.bucket_end_us


def train_session(policy: BracketPolicy, optimizer: torch.optim.Optimizer,
                  session: PackedSession,
                  decisions: tuple[TeacherDecision, ...],
                  outcomes: tuple[ExecutionOutcome, ...], *,
                  device: torch.device, clocks_per_chunk: int = 32,
                  grad_clip: float = 1., progress_callback=None) -> TrainingMetrics:
    """Train with 120 actual-candle histories and bounded chronological BPTT.

    Decisions use current completed candles and outcomes up to that close.
    Outcomes after a decision are applied only on a later clock. The encoder
    and action GRU state detach after each optimizer chunk, never mid-order.
    """
    if (session.role != 'train' or clocks_per_chunk < 1 or grad_clip <= 0 or
            not decisions):
        raise ValueError('V6 trainer requires a train session and labels')
    listings = len(session.listings)
    ranked = hasattr(policy, 'observe_market')
    if ranked:
        policy.reset_market(listings)
    pending_entries = {}
    _validate(decisions, outcomes, listings)
    state = SparseCandleState.empty(policy.encoder, listings, device=device,
                                    dtype=torch.float32)
    seed_previous_session(state, policy.encoder, session.listings,
                          session.previous)
    action_state = policy.initial_action_state(device=device,
                                                dtype=torch.float32)
    decision_groups = {clock:tuple(group) for clock, group in
                       groupby(decisions, key=lambda item:item.close_us)}
    next_outcome = 0
    observed_decisions = 0
    updates = 0
    loss_sum = correct_sum = 0.
    confusion = np.zeros((5, 5), dtype=np.int64)
    conditional_sum = np.zeros(3, dtype=np.float64)
    conditional_count = np.zeros(3, dtype=np.int64)
    policy.train()
    optimizer.zero_grad(set_to_none=True)
    event_iter = iter(session.candle_events())
    while chunk := tuple(islice(event_iter, clocks_per_chunk)):
        labeled = any(event.close_us in decision_groups for event in chunk)
        pending_losses = []
        pending_correct = []
        pending_predictions = []
        pending_conditional = ([], [], [])
        # Empty chunks still advance every observed candle and actual order
        # outcome, but do not build a useless autograd graph.
        with torch.set_grad_enabled(labeled):
            for event in chunk:
                # An execution bucket can close before this candle does.
                # Apply it using the *previous* completed-candle state, even
                # when its boundary equals this candle close.
                while (next_outcome < len(outcomes) and
                       outcomes[next_outcome].bucket_end_us <= event.close_us):
                    outcome = outcomes[next_outcome]
                    pending_entries.pop((outcome.source_close_us,
                                         outcome.source_order_index), None)
                    embedding = state.embeddings()[outcome.listing_index]
                    action_state = policy.remember_execution(action_state,
                        embedding, action=outcome.action,
                        requested_fraction=embedding.new_tensor(
                            outcome.requested_fraction),
                        filled_fraction=embedding.new_tensor(
                            outcome.filled_fraction),
                        realized_net_over_equity=embedding.new_tensor(
                            outcome.realized_net_over_equity))
                    next_outcome += 1
                index = torch.from_numpy(
                    event.listing_index.astype(np.int64)).to(device)
                rows = event.bank_row
                scalar = torch.from_numpy(np.asarray(
                    session.bank.scalar[rows]).copy()).to(device)
                levels = torch.from_numpy(np.asarray(
                    session.bank.levels[rows]).copy()).to(device)
                state.advance(policy.encoder, index, scalar, levels)
                if ranked:
                    policy.observe_market(state, event.close_us,
                        event.listing_index, scalar.detach().cpu().numpy())
                for item in decision_groups.pop(event.close_us, ()):
                    if ranked:
                        policy.set_pending(pending_entries.values())
                    def tensor(values, dtype=None):
                        return torch.as_tensor(values, dtype=dtype,
                                               device=device)
                    logits, sizes, stops, targets = policy.decide(
                        state.embeddings(),
                        tensor(item.account, torch.float32),
                        tensor(item.held_index, torch.long),
                        tensor(item.held_features, torch.float32),
                        action_state,
                        enter_allowed=tensor(item.enter_allowed, torch.bool),
                        exit_allowed=tensor(item.exit_allowed, torch.bool),
                        stop_allowed=tensor(item.stop_allowed, torch.bool),
                        target_allowed=tensor(item.target_allowed, torch.bool))
                    if ranked and logits[item.token] == torch.finfo(logits.dtype).min:
                        raise ValueError('Teacher action outside causal ranked universe; '
                                         'audit top_r/sort_secs coverage before training')
                    loss, metrics = bracket_loss(logits, sizes, stops,
                        targets, token=item.token,
                        size_fraction=item.size_fraction,
                        oracle_log_distance=item.oracle_log_distance)
                    pending_losses.append(loss)
                    pending_correct.append(metrics['action_correct'])
                    pending_predictions.append((logits.detach().argmax(),
                        item.token, len(item.held_index)))
                    target_class = _action_class(item.token, listings,
                                                 len(item.held_index))
                    if target_class in (1, 3, 4):
                        slot = {1: 0, 3: 1, 4: 2}[target_class]
                        pending_conditional[slot].append(
                            metrics['size_absolute_error'] if slot == 0 else
                            metrics['bracket_absolute_error'])
                    observed_decisions += 1
                    if 1 <= item.token <= listings:
                        pending_entries[(item.close_us, item.order_index)] = item.token-1
        if pending_losses:
            mean = torch.stack(pending_losses).mean()
            mean.backward()
            torch.nn.utils.clip_grad_norm_(policy.parameters(), grad_clip)
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            updates += 1
            loss_sum += float(torch.stack([value.detach() for value in
                                           pending_losses]).sum())
            correct_sum += float(torch.stack(pending_correct).sum())
            predicted = torch.stack([item[0] for item in
                                     pending_predictions]).cpu().tolist()
            for selected, (_, target, held) in zip(predicted,
                                                    pending_predictions):
                confusion[_action_class(target, listings, held),
                          _action_class(selected, listings, held)] += 1
            for slot, values in enumerate(pending_conditional):
                if values:
                    conditional_sum[slot] += float(torch.stack(values).sum())
                    conditional_count[slot] += len(values)
        state.detach()
        action_state = action_state.detach()
        if progress_callback:
            progress_callback({'close_us':chunk[-1].close_us,
                'observed_decisions':observed_decisions,'optimizer_updates':updates,
                'loss':loss_sum/observed_decisions if observed_decisions else None})
    if decision_groups or observed_decisions != len(decisions):
        raise ValueError('Teacher decision clock absent from certified candles')
    if next_outcome != len(outcomes):
        raise ValueError('Execution outcome occurs after last certified candle')
    counts = {name: int(confusion[index].sum()) for index, name in
              enumerate(ACTION_NAMES)}
    precision, recall, f1 = {}, {}, {}
    for index, name in enumerate(ACTION_NAMES):
        correct = int(confusion[index, index])
        predicted = int(confusion[:, index].sum())
        actual = counts[name]
        precision[name] = correct / predicted if predicted else 0.
        recall[name] = correct / actual if actual else 0.
        denominator = precision[name] + recall[name]
        f1[name] = (2*precision[name]*recall[name]/denominator
                    if denominator else 0.)
    conditional_mae = [float(conditional_sum[index]/conditional_count[index])
                       if conditional_count[index] else None
                       for index in range(3)]
    return TrainingMetrics(observed_decisions, next_outcome, updates,
                           loss_sum / observed_decisions,
                           correct_sum / observed_decisions,
                           counts, precision, recall, f1,
                           *conditional_mae)
