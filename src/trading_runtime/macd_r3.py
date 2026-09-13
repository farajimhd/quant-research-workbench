"""Causal 100ms MACD entries with HOD-ranked V6 resistance protection."""
from copy import deepcopy
from math import floor, isfinite
from uuid import uuid4

from .execution_policies import ExecutionEnvelope, ExecutionPolicy, ExecutionPolicyName
from .macd_threshold import DEFAULTS, quality
from .signals import StrategyEvaluation, StrategyIntent, StrategySignal

CONTRACT = 'macd-r3-100ms-1'
BOOK_VERSION = 'causal-swing-closing-book-6'
BOOK_VERSIONS = (BOOK_VERSION, 'causal-level-book-v7-mle-1')


def references(observation):
    """Count distinct confirmed resistance zones down from the as-of HOD."""
    now_ms = observation.observed_at.timestamp() * 1000
    rows = []
    for raw in observation.structural_resistance_levels:
        values = [raw.get(k) for k in ('lower', 'upper', 'confirmed_at_ms')]
        if (raw.get('book_version') not in BOOK_VERSIONS
                or raw.get('side') not in (-1, 'resistance')
                or any(not isinstance(v, (int, float)) or not isfinite(v) for v in values)):
            continue
        if 0 < values[0] <= values[1] and values[2] <= now_ms:
            rows.append(deepcopy(raw))
    merged = []
    for row in sorted(rows, key=lambda r: r['lower']):
        if merged and row['lower'] <= merged[-1]['upper']:
            merged[-1]['upper'] = max(merged[-1]['upper'], row['upper'])
        else:
            merged.append(row)
    hod = observation.structural_session_high
    if hod is None or not isfinite(hod) or hod <= 0:
        return [], None
    selected = sorted((r for r in merged if r['upper'] <= hod),
                      key=lambda r: r['upper'], reverse=True)[:3]
    target = next((r for r in merged if selected and r['lower'] > selected[0]['upper']), None)
    return selected, target


def evaluate(assignment, observation):
    from .strategy_engine import AssignmentStatus as Status, StrategyEngineResult
    state = deepcopy(assignment.state)
    config = {**DEFAULTS, **assignment.parameters.get('macd_r3', {})}
    now = observation.observed_at.timestamp()
    tick = assignment.parameters['execution']['tick_size']
    closed = ('bar_close' in observation.evaluation_events and observation.source_timeframe == '100ms'
              and now > state.get('r3_closed_at', 0))
    gap = None
    if closed:
        state['r3_closed_at'] = now
        values = (observation.macd_line, observation.macd_signal, observation.price)
        if all(v is not None and isfinite(v) for v in values) and observation.price > 0:
            gap = 10000 * (values[0] - values[1]) / values[2]
    refs, target_level = references(observation)
    gates = quality(observation, config)
    vwap = observation.execution_vwap
    above_vwap = vwap is not None and isfinite(vwap) and vwap > 0 and observation.price > vwap
    acquired = observation.position_quantity > 0
    if acquired:
        state['r3_ever_filled'] = True
    reentry = bool(state.get('r3_ever_filled'))
    threshold = 0. if reentry else -config['gap_bps'] / 2
    snapshot = dict(levels=[dict(r, entry_boundary=r['upper']) for r in refs],
                    session_high=observation.structural_session_high,
                    selected_at=observation.observed_at.isoformat(), frozen_at_entry=True)
    stop = float(state.get('active_stop') or 0)
    target = float(state.get('r3_target') or 0)
    proposed_stop = round(floor((refs[2]['lower'] - tick) / tick + 1e-9) * tick, 8) if len(refs) == 3 else 0.
    proposed_target = round(floor(target_level['lower'] / tick + 1e-9) * tick, 8) if target_level else 0.
    metadata = dict(assignment_id=assignment.assignment_id, contract=CONTRACT,
        session_routing='smart', eligible_sessions=['premarket', 'regular', 'after_hours'],
        bid=observation.bid, ask=observation.ask, reference_price=observation.price,
        liquidity_admission=gates, vwap=vwap,
        macd=dict(timeframe='100ms', gap_bps=gap, entry_above_bps=threshold),
        unified_structural_trigger=dict(current_snapshot=snapshot),
        current_resistance_snapshot=snapshot, target_level=target_level,
        initial_stop=state.get('initial_stop'), active_stop=stop,
        profit_targets=[target] if target else [], mandatory_broker_target=True)

    def emit(action, reason, status, quantity=0.):
        identity = str(uuid4())
        details = {**metadata, 'reason_code': reason, 'status': status.value,
                   'initial_stop': state.get('initial_stop'),
                   'active_stop': stop, 'profit_targets': [target] if target else [],
                   'decision_values': dict(initial_stop=state.get('initial_stop'), active_stop=stop,
                                           profit_target=target, profit_targets=[target] if target else [])}
        signal = StrategySignal(identity, CONTRACT, observation.ticker, observation.observed_at, action,
            'bullish' if action == 'enter_long' else 'bearish' if action == 'exit' else 'neutral',
            1. if action == 'enter_long' else 0., 1., reason, observation.source_signal_ids, '100ms', metadata=details)
        intents = ()
        if action in ('enter_long', 'exit', 'cancel_entry', 'replace_protective_stop', 'replace_profit_target'):
            exiting = action == 'exit'
            intents = (StrategyIntent(identity, observation.ticker, observation.observed_at, action, quantity,
                observation.ask if action == 'enter_long' else observation.price,
                invalidation_price=stop or None, profit_target_price=target or None,
                execution_policy=ExecutionPolicy(policy_id='macd-r3-execution', name=ExecutionPolicyName.ADAPTIVE_URGENT,
                    envelope=ExecutionEnvelope(deadline_ms=750 if exiting else 100, persist_until_cancelled=exiting)),
                urgency='urgent', time_in_force='', outside_rth=False, reason=reason,
                metadata={**details, 'reentry_after_fill': True, 'cancel_entry_acquisition': exiting,
                          'position_fraction': 1. if exiting else 0.}),)
        return StrategyEngineResult(StrategyEvaluation(signals=(signal,), intents=intents), state, status, signal.payload())

    if assignment.status == Status.EXIT_PENDING or observation.pending_exit_quantity > 0:
        return emit('hold' if acquired else 'wait', 'exit_fill_pending', Status.EXIT_PENDING)
    if acquired:
        # Existing protection wins over a same-candle reference/target advance.
        reason = ('operator_exit' if state.get('manual_exit_requested') else
                  'r3_stop_reached' if stop and observation.price <= stop else
                  'r1_target_reached' if target and observation.price >= target else '')
        if reason:
            return emit('exit', reason, Status.EXIT_PENDING, observation.position_quantity)
        if closed and proposed_stop > stop and proposed_stop < observation.bid:
            stop = proposed_stop
            state['active_stop'] = stop
            return emit('replace_protective_stop', 'r3_stop_advanced', Status.MANAGING, observation.position_quantity)
        if closed and proposed_target > target and proposed_target > observation.ask:
            target = proposed_target
            state.update(r3_target=target, structural_profit_targets=[target])
            return emit('replace_profit_target', 'r1_target_advanced', Status.MANAGING, observation.position_quantity)
        return emit('hold', 'hold_to_structural_protection', Status.MANAGING)
    if assignment.status == Status.ENTRY_PENDING:
        if (now - state.get('entry_requested_at', 0) >= .1 or gates['failed'] or not above_vwap
                or len(refs) != 3 or observation.price <= refs[2]['upper']
                or (closed and (gap is None or gap <= state.get('r3_request_threshold', threshold)))):
            return emit('cancel_entry', 'acquisition_expired_or_invalid', Status.WATCHING)
        return emit('wait', 'entry_fill_pending', Status.ENTRY_PENDING)
    if assignment.status in (Status.DISABLED, Status.PAUSED, Status.COMPLETED, Status.ERROR) or not assignment.permissions.observe:
        return emit('wait', 'assignment_not_active', assignment.status)
    if not (assignment.permissions.reenter if reentry else assignment.permissions.enter):
        return emit('wait', 'entry_not_authorized', assignment.status)
    if not closed or gap is None or gap <= threshold:
        return emit('wait', 'waiting_for_100ms_macd', assignment.status)
    if gates['failed'] or not above_vwap:
        return emit('wait', 'entry_quality_or_vwap_failed', assignment.status)
    if len(refs) != 3 or observation.price <= refs[2]['upper']:
        return emit('wait', 'waiting_for_price_above_r3', assignment.status)
    if not 0 < proposed_stop < observation.bid or proposed_target <= observation.ask:
        return emit('wait', 'stop_or_target_unavailable', assignment.status)
    stop, target = proposed_stop, proposed_target
    state.update(entry_requested_at=now, entry_acquisition_exit_latched=False,
                 initial_stop=stop, active_stop=stop, r3_target=target, structural_profit_targets=[target],
                 r3_request_threshold=threshold, r3_entry_snapshot=snapshot)
    for key in ('liquidation_origin_fill_role', 'liquidation_origin_reentry_after_fill', 'last_exit_reason'):
        state.pop(key, None)
    return emit('enter_long', 'macd_above_r3_and_vwap', Status.ENTRY_PENDING, config['quantity'])
