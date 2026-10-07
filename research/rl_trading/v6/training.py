"""Chronological V6 training core over packed actual-candle events.

This core deliberately accepts certified, already reconstructed price-action
teacher snapshots. Teacher order outcomes are idealized candle-path events;
the separate quote-aware environment supplies later interactive learning and
model replay. Old V5 clock-second orders cannot satisfy this contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby
import math

import numpy as np
import torch

from research.rl_trading.v6.candle_stream import (SparseCandleState,
                                                   seed_previous_session)
from research.rl_trading.v6.model import BracketPolicy, HELD_FEATURE_WIDTH
from research.rl_trading.v6.objective import bracket_loss
from research.rl_trading.v6.label_metrics import classification_metrics
from research.rl_trading.v6.session_data import PackedSession
from research.rl_trading.v6.action_contract import ActionAxes, ACTION_NAMES as SIX_ACTION_NAMES


ACTION_NAMES = ('hold', 'enter_long', 'exit_long', 'set_stop', 'set_target')


def _event_chunks(events, size, learning_start_us=None):
    """Preserve every event; start a fresh BPTT chunk at the learning fence.

    Warmup events retain their causal state but cannot share an optimizer
    chunk with the requested learning interval. With no fence, batching is
    identical to the original chronological trainer.
    """
    pending = []
    crossed = learning_start_us is None
    for event in events:
        if not crossed and event.close_us >= learning_start_us:
            if pending:
                yield tuple(pending)
                pending = []
            crossed = True
        pending.append(event)
        if len(pending) == size:
            yield tuple(pending)
            pending = []
    if pending:
        yield tuple(pending)


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
    label_version: str | None = None
    raw_entry_gain: float | None = None
    raw_exit_gain: float | None = None
    # Retrospective audit targets only; never observation features or losses.
    target_close_us: int | None = None
    target_horizon_seconds: float | None = None
    execution_indices: tuple[int,...] = ()
    execution_features: np.ndarray | None = None  # Sparse causal [K,11], never future scores.
    opportunity_value_bps: float | None = None
    entry_stop_bps: float | None = None
    entry_target_bps: float | None = None
    allocation_ratio_target: float | None = None  # Saved 1b target, never an observation.
    forecast_probabilities: np.ndarray | None = None  # [<=5,4], retrospective labels.
    forecast_close_us: np.ndarray | None = None  # Actual clocks, same listing/session.
    forecast_actions: np.ndarray | None = None  # Explicit copied 1b actions, not soft argmax.


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
    ticker_regression_mae_bps: dict[str,float | None] | None = None
    ticker_regression_counts: dict[str,int] | None = None
    allocation_ratio_mae: float | None = None
    allocation_targets: int = 0
    forecast_cross_entropy: tuple[float | None, ...] = ()
    forecast_targets: tuple[int, ...] = ()
    forecast_label_metrics: tuple[dict, ...] = ()
    action_quality_mae: dict[str, float | None] | None = None
    action_quality_counts: dict[str, int] | None = None
    forecast_quality_mae: tuple[dict, ...] = ()
    forecast_quality_counts: tuple[dict, ...] = ()


def teacher_loss_balance(decisions, listings, close_us, clocks_per_chunk, *, wait_hold=False, mode='balanced-v2'):
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
    if mode=='branch-balanced-v3':
        if not wait_hold or not np.isin(classes,[0,1,2,5]).all():
            raise ValueError('Conditional branch balance requires WAIT/ENTRY/HOLD/EXIT teacher actions')
        from research.rl_trading.v6.bias_models import balance_weights
        mapping=np.array([1,0,3,-1,-1,2]);local=mapping[classes]
        conditional=balance_weights(local,sample_weights,mode='branch')
        weights[[0,1,2,5]]=conditional[[1,0,3,2]]
    elif mode=='balanced-v2':
        present = counts > 0
        weights[present] = 1 / np.sqrt(counts[present])
        weights /= np.dot(weights, counts) / sample_weights.sum()
    else:raise ValueError('Unknown teacher class balance')
    span = (int(np.max(close_us)) - int(np.min(close_us))) // 1_000_000 + 1
    blocks = max(1, math.ceil(span / clocks_per_chunk))
    return weights, max(1., sample_weights.sum() / blocks)


def _validate(decisions: tuple[TeacherDecision, ...],
              outcomes: tuple[ExecutionOutcome, ...],
              listings: int, *, wait_hold=False, ticker_heads=False) -> None:
    previous_key = None
    by_key = {}
    for item in decisions:
        key = (item.close_us, item.order_index)
        held = len(item.held_index)
        if item.allocation_ratio_target is not None and (
                held or not 1 <= item.token <= listings or
                not math.isfinite(item.allocation_ratio_target) or
                not 0 <= item.allocation_ratio_target <= 1):
            raise ValueError('Allocation supervision requires a selected flat ENTRY')
        if (item.forecast_probabilities is None) != (item.forecast_close_us is None):
            raise ValueError('Forecast probabilities/clocks must be paired')
        if item.forecast_probabilities is not None:
            p, clocks = item.forecast_probabilities, item.forecast_close_us
            if (p.ndim != 2 or not 1 <= len(p) <= 5 or p.shape[1] != 4 or
                    clocks.shape != (len(p),) or clocks.dtype != np.int64 or
                    clocks[0] != item.close_us or np.any(np.diff(clocks) <= 0) or
                    not np.isfinite(p).all() or (p < 0).any() or
                    not np.allclose(p.sum(1), 1)):
                raise ValueError('Invalid same-listing five-candle forecast target')
        if item.forecast_actions is not None:
            if (item.forecast_probabilities is None or item.forecast_actions.shape != (len(item.forecast_probabilities),) or
                    item.forecast_actions.dtype != np.int64 or (item.forecast_actions < 0).any() or (item.forecast_actions > 3).any()):
                raise ValueError('Invalid explicit saved forecast actions')
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
        if ticker_heads:
            if not item.soft_tokens or item.size_fraction is not None or item.oracle_log_distance is not None or bracket_action or held>1:
                raise ValueError('Ticker supervision requires local labels without sizing/SET actions')
        elif (enters != (item.size_fraction is not None) or
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
                  teacher_loss: str = 'legacy',
                  learning_start_us: int | None = None,
                  evaluate_train: bool = False, auxiliary_weights=None, regression_weights=(1.,1.),
                  regression_evidence: dict | None = None) -> TrainingMetrics:
    """Train with 120 actual-candle histories and bounded chronological BPTT.

    Current opportunity targets are stamped at candle close. Their features
    exclude that candle: the encoder/ranker advances only after supervision.
    Historical label versions retain their original audit alignment.
    Outcomes after a decision are applied only on a later clock. The encoder
    and action GRU state detach after each optimizer chunk, never mid-order.
    Evaluation accepts development sessions by default; evaluate_train also
    permits authentic train sessions for fixed-checkpoint fit audits. It builds no autograd graph,
    and never reads or mutates an optimizer.
    An optional learning start retains all earlier candle events as warmup;
    callers must supply only labels at or after that completed-clock fence.
    """
    auxiliary=dict(ratio=1.,forecast=1.,quality=1.,future_quality=1.)
    if len(regression_weights)!=2 or not all(isinstance(w,(int,float)) and math.isfinite(w) and 0<=w<=10 for w in regression_weights):
        raise ValueError('Finite value/bracket regression coefficients required')
    if auxiliary_weights is not None:
        if set(auxiliary_weights)!=set(auxiliary) or not all(isinstance(v,(int,float)) and math.isfinite(v) and 0<=v<=10 for v in auxiliary_weights.values()):
            raise ValueError('Explicit finite four-task auxiliary coefficients required')
        auxiliary.update(auxiliary_weights)
    if evaluate_train and not evaluation:
        raise ValueError('Training-set evaluation requires evaluation mode')
    evaluation_roles = ('development', 'train') if evaluate_train else ('development',)
    if (session.role not in (evaluation_roles if evaluation else ('train',)) or clocks_per_chunk < 1 or grad_clip <= 0 or
            not decisions or teacher_loss not in ('legacy', 'balanced-v2','branch-balanced-v3')):
        raise ValueError('V6 trainer requires a train session and labels')
    listings = len(session.listings)
    from research.rl_trading.v6.price_action_opportunities import VERSION as OPPORTUNITY_VERSION
    prior_only = any(item.label_version == OPPORTUNITY_VERSION for item in decisions)
    if prior_only and not all(item.label_version == OPPORTUNITY_VERSION for item in decisions):
        raise ValueError('Cannot mix teacher feature timing contracts')
    if learning_start_us is not None and any(
            item.close_us < learning_start_us for item in decisions):
        raise ValueError('Learning fence cannot discard supplied teacher labels')
    ranked = hasattr(policy, 'observe_market')
    if ranked:
        policy.reset_market(listings)
    pending_entries = {}
    wait_hold = policy.decoder.wait_hold
    if teacher_loss=='branch-balanced-v3' and not (wait_hold and getattr(policy,'hierarchical_teacher',False)):
        raise ValueError('Branch-balanced-v3 requires hierarchical WAIT/ENTRY/HOLD/EXIT heads')
    action_names = SIX_ACTION_NAMES if wait_hold else ACTION_NAMES
    _validate(decisions, outcomes, listings, wait_hold=wait_hold,ticker_heads=hasattr(policy.decoder,'ticker_outputs'))
    balance, loss_denominator = (teacher_loss_balance(decisions, listings,
        session.bank.close_us if learning_start_us is None else
        session.bank.close_us[session.bank.close_us >= learning_start_us],
        clocks_per_chunk, wait_hold=wait_hold,mode=teacher_loss)
        if teacher_loss != 'legacy' and not evaluation else (None, None))
    state = SparseCandleState.empty(policy.encoder, listings, device=device,
                                    dtype=torch.float32, refreshable=wait_hold)
    seed_previous_session(state, policy.encoder, session.listings,
                          session.previous)
    if prior_only and ranked:
        policy.seed_market(state, (int(session.bank.close_us.min())//1_000_000-1)*1_000_000)
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
    ticker_sum=np.zeros(3);ticker_count=np.zeros(3,dtype=np.int64)
    sequence = hasattr(policy, 'teacher_forecast')
    if sequence and (not prior_only or not hasattr(policy.decoder, 'supervision_context') or
                     not any(d.forecast_probabilities is not None for d in decisions)):
        raise ValueError('Autoregressive teacher requires copied 1b strict-prior targets')
    # Fixed task denominators prevent WAIT/missing-target rows diluting sizing.
    span = (int(session.bank.close_us.max())-int(session.bank.close_us.min()))//1_000_000+1
    blocks = max(1, math.ceil(span/clocks_per_chunk))
    ratio_mass = sum(d.sample_weight for d in decisions if d.allocation_ratio_target is not None)
    forecast_mass = sum(d.sample_weight for d in decisions if d.forecast_probabilities is not None)
    ratio_sum=ratio_weight=0.; ratio_count=0
    forecast_sum=np.zeros(5);forecast_weight=np.zeros(5);forecast_count=np.zeros(5,np.int64)
    forecast_confusion=np.zeros((5,4,4),np.int64)
    hierarchy=getattr(policy,'hierarchical_teacher',False)
    if hierarchy and any(d.forecast_probabilities is not None and d.forecast_actions is None for d in decisions):
        raise ValueError('Hierarchy requires explicit saved 1b forecast actions')
    quality_sum=np.zeros(2);quality_weight=np.zeros(2);quality_count=np.zeros(2,np.int64)
    future_quality_sum=np.zeros((5,2));future_quality_weight=np.zeros((5,2));future_quality_count=np.zeros((5,2),np.int64)
    quality_mass=sum(d.sample_weight for d in decisions if (1<=d.token<=listings or len(d.held_index) and d.token==1+listings))
    future_quality_mass=sum(d.sample_weight*np.isin(d.forecast_actions,[0,3]).mean() for d in decisions if d.forecast_actions is not None)
    if hierarchy:
        from research.rl_trading.v6.hierarchical_heads import forecast_class_weights
        future_class_weights=torch.tensor(np.ones(4) if evaluation else forecast_class_weights(decisions),device=device,dtype=torch.float32)
    policy.train(not evaluation)
    if not evaluation:
        optimizer.zero_grad(set_to_none=True)
    for chunk in _event_chunks(session.candle_events(), clocks_per_chunk,
                               learning_start_us):
        resource_pacer = getattr(policy, 'resource_pacer', None)
        reserve_check = getattr(resource_pacer, 'check_reserve', None)
        if reserve_check is not None:
            reserve_check()
        labeled = any(event.close_us in decision_groups for event in chunk)
        pending_losses = []
        pending_objectives = []
        pending_weights = []
        pending_correct = []
        pending_predictions = []
        pending_conditional = ([], [], [])
        pending_ticker=([],[],[])
        pending_ratio=[];pending_forecast=[];pending_quality=[];pending_future_quality=[]
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
                if not prior_only:
                    state.advance(policy.encoder, index, scalar, levels)
                    if ranked:
                        policy.observe_market(state, event.close_us,
                            event.listing_index, scalar_cpu)
                for item in decision_groups.pop(event.close_us, ()):
                    if hasattr(policy,'execution_projection'):
                        if item.execution_features is None:raise ValueError('Causal cost observation missing')
                        policy.set_execution_features(torch.as_tensor(item.execution_indices,device=device,dtype=torch.long),
                            torch.as_tensor(item.execution_features,device=device,dtype=torch.float32))
                    if ranked:
                        policy.set_pending(pending_entries.values())
                    def tensor(values, dtype=None):
                        if isinstance(values,np.ndarray) and not values.flags.writeable:
                            values=values.copy()
                        return torch.as_tensor(values, dtype=dtype,
                                               device=device)
                    if hasattr(policy.decoder, 'supervision_index'):
                        policy.decoder.supervision_index = (int(item.held_index[0])
                            if len(item.held_index) else item.soft_tokens[1]-1)
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
                    if hasattr(policy.decoder,'ticker_outputs'):
                        from research.rl_trading.v6.ticker_heads import TickerOutputs,supervised_loss
                        decoded=policy.decoder.ticker_outputs
                        identity=0 if policy.decoder.supervision_index is not None else (int(item.held_index[0]) if len(item.held_index) else item.soft_tokens[1]-1)
                        local=TickerOutputs(*(getattr(decoded,k)[identity:identity+1] for k in
                            ('logits','value_bps','stop_bps','target_bps')))
                        p=torch.zeros((1,4),device=device)
                        p[0,2 if len(item.held_index) else 1]=item.soft_probabilities[1 if len(item.held_index) else 0]
                        p[0,3 if len(item.held_index) else 0]=item.soft_probabilities[0 if len(item.held_index) else 1]
                        if hierarchy:
                            actual_action={'wait':1,'enter_long':0,'hold':2,'exit_long':3}[action_names[target_class]]
                            if actual_action in (0,3):
                                branch=actual_action==3
                                quality_prediction=decoded.quality[identity,int(branch)]
                                quality_target=p[0,actual_action].detach()
                                quality_objective=torch.nn.functional.smooth_l1_loss(quality_prediction,quality_target,beta=.1)
                                pending_quality.append(quality_objective*item.sample_weight)
                                quality_sum[int(branch)]+=float((quality_prediction-quality_target).abs().detach())*item.sample_weight
                                quality_weight[int(branch)]+=item.sample_weight;quality_count[int(branch)]+=1
                            p=torch.nn.functional.one_hot(torch.tensor([actual_action],device=device),4).to(p.dtype)
                        value_valid=torch.tensor([item.opportunity_value_bps is not None],device=device)
                        bracket_valid=torch.tensor([item.entry_stop_bps is not None and item.entry_target_bps is not None],device=device)
                        target=lambda value:logits.new_tensor([float('nan') if value is None else value])
                        objective,aux=supervised_loss(local,p,value_bps=target(item.opportunity_value_bps),
                            value_valid=value_valid,stop_bps=target(item.entry_stop_bps),target_bps=target(item.entry_target_bps),
                            bracket_valid=bracket_valid,
                            value_weight=regression_weights[0],bracket_weight=regression_weights[1],
                            action_weight=float(balance[target_class]) if balance is not None else 1.)
                        for slot,name in enumerate(('value','stop','target')):
                            if torch.isfinite(aux[name+'_mae_bps']):pending_ticker[slot].append(aux[name+'_mae_bps'])
                        metrics={'action_loss':aux['action_loss'],'size_loss':logits.new_zeros(()),
                            'value_loss':aux['value_loss'],
                            'bracket_loss':aux['stop_loss']+aux['target_loss'],
                            'action_correct':(local.logits.argmax(-1)==p.argmax(-1)).float().mean(),
                            'size_absolute_error':logits.new_zeros(()),'bracket_absolute_error':logits.new_zeros(())}
                        if sequence and item.allocation_ratio_target is not None:
                            from research.rl_trading.v6.teacher_forecast import allocation_loss
                            ratio, error = allocation_loss(policy.decoder.supervision_allocation[0],
                                                           item.allocation_ratio_target)
                            pending_ratio.append(ratio*item.sample_weight)
                            ratio_sum+=float(error.detach())*item.sample_weight
                            ratio_weight+=item.sample_weight;ratio_count+=1
                        if sequence and item.forecast_probabilities is not None:
                            from research.rl_trading.v6.teacher_forecast import forecast_loss
                            probabilities=torch.tensor(np.array(item.forecast_probabilities,copy=True),device=device)[None]
                            if hierarchy:
                                from research.rl_trading.v6.hierarchical_heads import sequence_losses
                                actions=torch.tensor(np.array(item.forecast_actions,copy=True),device=device)[None]
                                hard_targets=torch.nn.functional.one_hot(actions,4).to(probabilities.dtype)
                                # Evaluation is always free-running; the two training
                                # trajectories share parameters and causal context.
                                variants=(None,) if evaluation else (hard_targets,None)
                                objectives=[];quality_objectives=[]
                                for forcing in variants:
                                    forecast=policy.teacher_forecast(policy.decoder.supervision_context,policy.action_gru,
                                        steps=probabilities.shape[1],previous_targets=forcing,validated=True)
                                    action_loss,quality_loss,ce,quality_error,selected=sequence_losses(
                                        forecast,policy.teacher_forecast.quality_predictions,actions,probabilities,future_class_weights)
                                    objectives.append(action_loss[0].mean());quality_objectives.append(quality_loss[0].mean())
                                pending_forecast.append(torch.stack(objectives).mean()*item.sample_weight)
                                if selected.any():pending_future_quality.append(torch.stack(quality_objectives).mean()*item.sample_weight)
                                losses=ce[0]  # Unweighted free-running CE, comparable across runs only within this target version.
                                for branch,label in enumerate((0,3)):
                                    mask=(item.forecast_actions==label)
                                    future_quality_sum[:len(mask),branch]+=quality_error[0].detach().cpu().numpy()*mask*item.sample_weight
                                    future_quality_weight[:len(mask),branch]+=mask*item.sample_weight
                                    future_quality_count[:len(mask),branch]+=mask
                            else:
                                forecast=policy.teacher_forecast(policy.decoder.supervision_context,policy.action_gru,
                                    steps=probabilities.shape[1],previous_targets=None if evaluation else probabilities,validated=True)
                                losses=forecast_loss(forecast,probabilities,validated=True)[0]
                                pending_forecast.append(losses.mean()*item.sample_weight)
                            length=len(losses)
                            forecast_sum[:length]+=losses.detach().cpu().numpy()*item.sample_weight
                            forecast_weight[:length]+=item.sample_weight
                            forecast_count[:length]+=1
                            predicted=forecast.detach().argmax(-1)[0].cpu().numpy()
                            actual=item.forecast_actions if item.forecast_actions is not None else item.forecast_probabilities.argmax(-1)
                            forecast_confusion[np.arange(length),actual,predicted]+=1
                    else:
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
                            metrics['bracket_loss']+metrics.get('value_loss',0))
                    pending_losses.append(loss)
                    pending_correct.append(metrics['action_correct'])
                    if hasattr(policy.decoder,'ticker_outputs'):
                        prediction=local.logits.detach().argmax(-1).squeeze(0)
                        first,second=item.soft_tokens
                        first_chosen=prediction==(3 if len(item.held_index) else 1)
                        selected=torch.where(first_chosen,torch.tensor(first,device=device),torch.tensor(second,device=device))
                    elif item.soft_tokens:
                        alternatives=torch.as_tensor(item.soft_tokens,device=device)
                        selected=alternatives[logits.detach()[alternatives].argmax()]
                    else:
                        selected=logits.detach().argmax()
                    pending_predictions.append((selected,
                        item.token, len(item.held_index)))
                    pending_objectives.append(objective)
                    pending_weights.append(item.sample_weight)
                    if target_class in (1, 3, 4) and not hasattr(policy.decoder,'ticker_outputs'):
                        slot = {1: 0, 3: 1, 4: 2}[target_class]
                        pending_conditional[slot].append(
                            metrics['size_absolute_error'] if slot == 0 else
                            metrics['bracket_absolute_error'])
                    observed_decisions += 1
                    if 1 <= item.token <= listings and not item.soft_tokens:
                        pending_entries[(item.close_us, item.order_index)] = item.token-1
                if prior_only:
                    # All targets at t see the same strictly earlier market state.
                    state.advance(policy.encoder, index, scalar, levels)
                    if ranked:
                        policy.observe_market(state, event.close_us,
                            event.listing_index, scalar_cpu)
        if pending_losses:
            mean = (torch.stack(pending_objectives).sum() / loss_denominator
                    if balance is not None else torch.stack(pending_objectives).sum()/sum(pending_weights))
            if pending_ratio:
                mean=mean+auxiliary['ratio']*torch.stack(pending_ratio).sum()/(ratio_mass/blocks)
            if pending_forecast:
                mean=mean+auxiliary['forecast']*torch.stack(pending_forecast).sum()/(forecast_mass/blocks)
            if pending_quality:
                mean=mean+auxiliary['quality']*torch.stack(pending_quality).sum()/(quality_mass/blocks)
            if pending_future_quality:
                mean=mean+auxiliary['future_quality']*torch.stack(pending_future_quality).sum()/(future_quality_mass/blocks)
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
            for slot,values in enumerate(pending_ticker):
                if values:
                    ticker_sum[slot]+=float(torch.stack(values).sum());ticker_count[slot]+=len(values)
        state.detach()
        if pending_losses and not evaluation and wait_hold:
            state.refresh_projection(policy.encoder)
        action_state = action_state.detach()
        # Resource pacing also covers evaluation and empty chronological chunks.
        # It runs after detachment/refresh and never changes optimizer boundaries.
        resource_pacer = getattr(policy, 'resource_pacer', None)
        if resource_pacer is not None:
            resource_pacer()
        if progress_callback:
            progress_callback({'close_us':chunk[-1].close_us,
                'observed_decisions':observed_decisions,'optimizer_updates':updates,
                'loss':loss_sum/observed_decisions if observed_decisions else None})
    if decision_groups or observed_decisions != len(decisions):
        raise ValueError('Teacher decision clock absent from certified candles')
    if next_outcome != len(outcomes):
        raise ValueError('Execution outcome occurs after last certified candle')
    if regression_evidence is not None:
        regression_evidence.update(
            allocation_weight=float(ratio_weight), allocation_error_sum=float(ratio_sum),
            action_quality_weights={name:float(quality_weight[i]) for i,name in enumerate(('ENTRY','EXIT'))},
            action_quality_error_sums={name:float(quality_sum[i]) for i,name in enumerate(('ENTRY','EXIT'))},
            forecast_quality_weights=[{name:float(future_quality_weight[h,i]) for i,name in enumerate(('ENTRY','EXIT'))} for h in range(5)],
            forecast_quality_error_sums=[{name:float(future_quality_sum[h,i]) for i,name in enumerate(('ENTRY','EXIT'))} for h in range(5)])
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
    if hasattr(policy.decoder, 'supervision_index'):
        policy.decoder.supervision_index = None
    return TrainingMetrics(observed_decisions, next_outcome, updates,
                           loss_sum / observed_decisions,
                           correct_sum / observed_decisions,
                           counts, precision, recall, f1,
                           *conditional_mae,
                           entry_correct / counts['enter_long']
                           if counts['enter_long'] else None,
                           hold_correct / counts['hold']
                           if wait_hold and counts['hold'] else None,
                           {name:int(confusion[:, index].sum()) for index,name in enumerate(action_names)},
                           {name:float(ticker_sum[i]/ticker_count[i]) if ticker_count[i] else None for i,name in enumerate(('value','stop','target'))},
                           {name:int(ticker_count[i]) for i,name in enumerate(('value','stop','target'))},
                           ratio_sum/ratio_weight if ratio_weight else None,ratio_count,
                           tuple(float(forecast_sum[i]/forecast_weight[i]) if forecast_weight[i] else None for i in range(5)) if sequence else (),
                           tuple(map(int,forecast_count)) if sequence else (),
                           tuple(classification_metrics(c)
                                 for c in forecast_confusion) if sequence else (),
                           {name:float(quality_sum[i]/quality_weight[i]) if quality_weight[i] else None for i,name in enumerate(('ENTRY','EXIT'))} if hierarchy else None,
                           {name:int(quality_count[i]) for i,name in enumerate(('ENTRY','EXIT'))} if hierarchy else None,
                           tuple({name:float(future_quality_sum[h,i]/future_quality_weight[h,i]) if future_quality_weight[h,i] else None for i,name in enumerate(('ENTRY','EXIT'))} for h in range(5)) if hierarchy else (),
                           tuple({name:int(future_quality_count[h,i]) for i,name in enumerate(('ENTRY','EXIT'))} for h in range(5)) if hierarchy else ())
