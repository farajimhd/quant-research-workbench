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
from research.rl_trading.v6.action_contract import ActionAxes, ACTION_NAMES as SIX_ACTION_NAMES


ACTION_NAMES = ('hold', 'enter_long', 'exit_long', 'set_stop', 'set_target')


def _action_class(token: int, listings: int, holdings: int, *, wait_hold=False) -> int:
    """Map a variable-width identity token to one of five stable classes."""
    if wait_hold:
        return ActionAxes(listings, holdings).action_class(token)
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
    sample_weight: float = 1.  # Expanded simultaneous HOLDs share one old row's weight.
    soft_tokens: tuple[int, ...] = ()  # Local alternative actions, not portfolio negatives.
    soft_probabilities: tuple[float, ...] = ()
    episode_uid: str | None = None


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
    entry_token_accuracy: float | None = None
    hold_token_accuracy: float | None = None
    action_predicted_class_counts: dict[str, int] | None = None


def teacher_loss_balance(decisions, listings, close_us, clocks_per_chunk, *, wait_hold=False):
    """Fixed per-session normalization; no per-block density reweighting.

    Inverse-square-root class weights have empirical mean one. Only the
    action CE is balanced: conditional size/bracket regressions retain their
    original weight. All labels in a session share one denominator, including
    the last partial block. Coordinates are existing certified bank data.
    """
    classes = np.asarray([_action_class(d.token, listings, len(d.held_index), wait_hold=wait_hold)
                          for d in decisions], dtype=np.int64)
    sample_weights = np.asarray([getattr(d, 'sample_weight', 1.) for d in decisions])
    counts = np.bincount(classes, weights=sample_weights, minlength=6 if wait_hold else 5)
    weights = np.zeros(len(counts), dtype=np.float64)
    present = counts > 0
    weights[present] = 1 / np.sqrt(counts[present])
    weights /= np.dot(weights, counts) / sample_weights.sum()
    span = (int(np.max(close_us)) - int(np.min(close_us))) // 1_000_000 + 1
    blocks = max(1, math.ceil(span / clocks_per_chunk))
    return weights, max(1., sample_weights.sum() / blocks)


def _validate(decisions: tuple[TeacherDecision, ...],
              outcomes: tuple[ExecutionOutcome, ...],
              listings: int, *, wait_hold=False) -> None:
    previous_key = None
    by_key = {}
    for item in decisions:
        key = (item.close_us, item.order_index)
        held = len(item.held_index)
        if (previous_key is not None and key <= previous_key or
                item.close_us <= 0 or item.order_index < 0 or
                item.account.shape != (7,) or
                item.held_index.shape != (held,) or
                item.held_features.shape not in ((held, 9), (held, HELD_FEATURE_WIDTH)) or
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
                not np.isfinite(item.held_features).all() or
                not math.isfinite(item.sample_weight) or not 0 < item.sample_weight <= 1):
            raise ValueError('Malformed or unsorted causal teacher decision')
        permitted = np.concatenate((np.ones(1, dtype=np.bool_),
            item.enter_allowed, item.exit_allowed, item.stop_allowed,
            item.target_allowed))
        if wait_hold:
            permitted = np.concatenate((permitted, np.ones(held, dtype=np.bool_)))
        if not 0 <= item.token < len(permitted) or not permitted[item.token]:
            raise ValueError('Teacher selected a masked bracket action')
        if item.soft_tokens or item.soft_probabilities:
            if (not item.episode_uid or len(item.soft_tokens)!=len(item.soft_probabilities) or
                len(set(item.soft_tokens))!=len(item.soft_tokens) or
                any(not 0<=token<len(permitted) or not permitted[token] for token in item.soft_tokens) or
                any(not math.isfinite(p) or not 0<=p<=1 for p in item.soft_probabilities) or
                not math.isclose(sum(item.soft_probabilities),1.,abs_tol=1e-9) or item.token not in item.soft_tokens):
                raise ValueError('Invalid independent episode soft target')
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
                  grad_clip: float = 1., progress_callback=None,
                  evaluation: bool = False, learning_rate_for_clock=None,
                  teacher_loss: str = 'legacy') -> TrainingMetrics:
    """Train with 120 actual-candle histories and bounded chronological BPTT.

    Decisions use current completed candles and outcomes up to that close.
    Outcomes after a decision are applied only on a later clock. The encoder
    and action GRU state detach after each optimizer chunk, never mid-order.
    Evaluation accepts development sessions only, builds no autograd graph,
    and never reads or mutates an optimizer.
    """
    if (session.role not in (('development',) if evaluation else ('train',)) or clocks_per_chunk < 1 or grad_clip <= 0 or
            not decisions or teacher_loss not in ('legacy', 'balanced-v2')):
        raise ValueError('V6 trainer requires a train session and labels')
    listings = len(session.listings)
    ranked = hasattr(policy, 'observe_market')
    if ranked:
        policy.reset_market(listings)
    pending_entries = {}
    wait_hold = policy.decoder.wait_hold
    action_names = SIX_ACTION_NAMES if wait_hold else ACTION_NAMES
    _validate(decisions, outcomes, listings, wait_hold=wait_hold)
    balance, loss_denominator = (teacher_loss_balance(decisions, listings,
        session.bank.close_us, clocks_per_chunk, wait_hold=wait_hold)
        if teacher_loss == 'balanced-v2' and not evaluation else (None, None))
    state = SparseCandleState.empty(policy.encoder, listings, device=device,
                                    dtype=torch.float32, refreshable=wait_hold)
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
    entry_correct = 0
    hold_correct = 0
    confusion = np.zeros((len(action_names), len(action_names)), dtype=np.int64)
    conditional_sum = np.zeros(3, dtype=np.float64)
    conditional_count = np.zeros(3, dtype=np.int64)
    policy.train(not evaluation)
    if not evaluation:
        optimizer.zero_grad(set_to_none=True)
    event_iter = iter(session.candle_events())
    while chunk := tuple(islice(event_iter, clocks_per_chunk)):
        labeled = any(event.close_us in decision_groups for event in chunk)
        pending_losses = []
        pending_objectives = []
        pending_weights = []
        pending_correct = []
        pending_predictions = []
        pending_conditional = ([], [], [])
        # Empty chunks still advance every observed candle and actual order
        # outcome, but do not build a useless autograd graph.
        with torch.set_grad_enabled(labeled and not evaluation):
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
                scalar_cpu = np.asarray(session.bank.scalar[rows]).copy()
                scalar = torch.from_numpy(scalar_cpu).to(device)
                levels = torch.from_numpy(np.asarray(
                    session.bank.levels[rows]).copy()).to(device)
                state.advance(policy.encoder, index, scalar, levels)
                if ranked:
                    policy.observe_market(state, event.close_us,
                        event.listing_index, scalar_cpu)
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
                    target_class = _action_class(item.token, listings,
                                                 len(item.held_index), wait_hold=wait_hold)
                    objective, metrics = bracket_loss(logits, sizes, stops,
                        targets, token=item.token,
                        size_fraction=item.size_fraction,
                        oracle_log_distance=item.oracle_log_distance,
                        action_weight=float(balance[target_class])
                        if balance is not None else 1., wait_hold=wait_hold,
                        soft_tokens=item.soft_tokens, soft_probabilities=item.soft_probabilities)
                    objective = objective * item.sample_weight
                    # Legacy monitoring remains unweighted. Episode-mode loss
                    # is local soft CE and has a separate manifest metric scope;
                    # it is not comparable to portfolio-token CE or recall.
                    loss = (metrics['action_loss'] + metrics['size_loss'] +
                            metrics['bracket_loss'])
                    pending_losses.append(loss)
                    pending_correct.append(metrics['action_correct'])
                    if item.soft_tokens:
                        alternatives=torch.as_tensor(item.soft_tokens,device=device)
                        selected=alternatives[logits.detach()[alternatives].argmax()]
                    else:
                        selected=logits.detach().argmax()
                    pending_predictions.append((selected,
                        item.token, len(item.held_index)))
                    pending_objectives.append(objective)
                    pending_weights.append(item.sample_weight)
                    if target_class in (1, 3, 4):
                        slot = {1: 0, 3: 1, 4: 2}[target_class]
                        pending_conditional[slot].append(
                            metrics['size_absolute_error'] if slot == 0 else
                            metrics['bracket_absolute_error'])
                    observed_decisions += 1
                    if 1 <= item.token <= listings and not item.soft_tokens:
                        pending_entries[(item.close_us, item.order_index)] = item.token-1
        if pending_losses:
            mean = (torch.stack(pending_objectives).sum() / loss_denominator
                    if balance is not None else torch.stack(pending_objectives).sum()/sum(pending_weights))
            if not torch.isfinite(mean):
                raise ValueError('Nonfinite teacher objective')
            if not evaluation:
                mean.backward()
                torch.nn.utils.clip_grad_norm_(policy.parameters(), grad_clip)
                if learning_rate_for_clock is not None:
                    rate=learning_rate_for_clock(chunk[-1].close_us)
                    for group in optimizer.param_groups:
                        group['lr']=rate
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
                if 1 <= target <= listings:
                    entry_correct += int(selected == target)
                target_class = _action_class(target, listings, held, wait_hold=wait_hold)
                if target_class == 5:
                    hold_correct += int(selected == target)
                confusion[target_class,
                          _action_class(selected, listings, held, wait_hold=wait_hold)] += 1
            for slot, values in enumerate(pending_conditional):
                if values:
                    conditional_sum[slot] += float(torch.stack(values).sum())
                    conditional_count[slot] += len(values)
        state.detach()
        if pending_losses and not evaluation and wait_hold:
            state.refresh_projection(policy.encoder)
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
              enumerate(action_names)}
    precision, recall, f1 = {}, {}, {}
    for index, name in enumerate(action_names):
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
                           *conditional_mae,
                           entry_correct / counts['enter_long']
                           if counts['enter_long'] else None,
                           hold_correct / counts['hold']
                           if wait_hold and counts['hold'] else None,
                           {name:int(confusion[:, index].sum()) for index,name in enumerate(action_names)})
