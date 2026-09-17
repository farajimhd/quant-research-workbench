"""Causal long approximation of completed-1s MACD hindsight price swings.

No label or future price is an input. One acquisition attempt per observed bullish
episode; initial broker protection plus a strategy-owned executable-bid drawdown.
"""
from copy import deepcopy
from datetime import datetime, time
from math import floor, isfinite
from uuid import uuid4
from zoneinfo import ZoneInfo

from .execution_policies import ExecutionEnvelope, ExecutionPolicy, ExecutionPolicyName
from .signals import StrategyEvaluation, StrategyIntent, StrategySignal

CONTRACT = "hindsight-long-1s-v1"
DEFAULTS = dict(
    quantity=100., lookback_seconds=2., entry_window_ms=1000.,
    entry_deadline_ms=500., maximum_spread_bps=100., quote_age_ms=1000.,
    macd_age_ms=2000., tick_size=.01, maximum_initial_risk_bps=300.,
    trail_activation_bps=100., minimum_trail_bps=50., profit_giveback_fraction=.25,
    spread_multiple=2.,
)
NY = ZoneInfo("America/New_York")


def settings(parameters):
    raw = parameters.get("hindsight_long", {})
    if parameters.get("hindsight_long_contract") != CONTRACT:
        raise ValueError("Unknown hindsight long contract")
    if not isinstance(raw, dict) or set(raw) - set(DEFAULTS):
        raise ValueError("Unknown hindsight long parameter")
    config = {**DEFAULTS, **raw}
    if any(type(v) not in (int, float) or not isfinite(v) or v <= 0 for v in config.values()):
        raise ValueError("Hindsight long settings must be positive finite numbers")
    if not .1 <= config['lookback_seconds'] <= 30:
        raise ValueError("Hindsight lookback must be between 0.1 and 30 seconds")
    if config['profit_giveback_fraction'] >= 1 or config['maximum_initial_risk_bps'] >= 10000:
        raise ValueError("Risk and giveback fractions must be below one")
    if config['entry_window_ms'] > config['macd_age_ms']:
        raise ValueError("Entry window cannot exceed MACD freshness")
    if config['entry_deadline_ms'] > config['entry_window_ms']:
        raise ValueError("Acquisition deadline cannot exceed the entry window")
    if config['quantity'] != int(config['quantity']):
        raise ValueError("Quantity must be whole shares")
    return config


def _quote(observation, config):
    record = observation.source_values.get('market.spread_bps', {})
    try:
        stamp = datetime.fromisoformat(str(record['observed_at']).replace('Z', '+00:00'))
        age = (observation.observed_at - stamp).total_seconds() * 1000
        bid, ask = observation.bid, observation.ask
        if not (0 <= age <= config['quote_age_ms'] and isfinite(bid) and isfinite(ask) and 0 < bid <= ask):
            return None
        return dict(bid=bid, ask=ask, spread=ask-bid, at=stamp.isoformat(),
                    spread_bps=(ask-bid) / ((ask+bid)/2) * 10000)
    except (KeyError, TypeError, ValueError):
        return None


def evaluate(assignment, observation):
    from .strategy_engine import AssignmentStatus as Status, StrategyEngineResult
    config = settings(assignment.parameters)
    state = deepcopy(assignment.state)
    track = state.setdefault('hindsight_long', {})
    now = observation.observed_at.timestamp()
    quote = _quote(observation, config)
    held = observation.position_quantity > 0
    status = assignment.status

    def emit(action, reason, next_status, quantity=0.):
        identity = str(uuid4())
        entry = action == 'enter_long'
        exiting = action == 'exit'
        metadata = dict(
            assignment_id=assignment.assignment_id, contract=CONTRACT, reason_code=reason,
            status=next_status.value, session_routing='smart',
            eligible_sessions=['premarket', 'regular', 'after_hours'],
            bid=observation.bid, ask=observation.ask, tick_size=config['tick_size'],
            reference_price=quote['ask'] if entry else quote['bid'] if quote else observation.price,
            quote_observed_at=quote['at'] if quote else None,
            episode_open=track.get('episode_open'), completed_macd_at=track.get('macd_at'),
            high_bid=track.get('high_bid'), trailing_boundary=track.get('trailing_boundary'),
            initial_stop=track.get('stop'), entry_completion_quote='ask',
            cancel_entry_acquisition=exiting, position_fraction=1. if exiting else 0.,
            reentry_after_fill=exiting and reason != 'session_end',
        )
        signal = StrategySignal(identity, CONTRACT, observation.ticker, observation.observed_at,
            action, 'bullish' if entry else 'bearish' if exiting else 'neutral',
            1. if entry else 0., 1., reason, observation.source_signal_ids, '1s', metadata=metadata)
        intents = ()
        if action in ('enter_long', 'exit', 'cancel_entry'):
            intents = (StrategyIntent(identity, observation.ticker, observation.observed_at,
                action, quantity, quote['ask'] if entry else quote['bid'] if quote else observation.price,
                invalidation_price=track.get('stop') if entry else None,
                execution_policy=ExecutionPolicy(policy_id=CONTRACT,
                    name=ExecutionPolicyName.ADAPTIVE_URGENT,
                    envelope=ExecutionEnvelope(
                        maximum_buy_price=quote['ask'] if entry else None,
                        deadline_ms=max(1, int(min(config['entry_deadline_ms'],
                            config['entry_window_ms']-(now-track['episode_open'])*1000))) if entry else 750,
                        persist_until_cancelled=exiting)),
                urgency='urgent', time_in_force='', reason=reason, metadata=metadata),)
        return StrategyEngineResult(StrategyEvaluation(signals=(signal,), intents=intents),
                                    state, next_status, signal.payload())

    if now < track.get('observed_at', now):
        return emit('wait', 'out_of_order_observation', status)
    track['observed_at'] = now
    if status in (Status.DISABLED, Status.COMPLETED, Status.ERROR, Status.PAUSED) or not assignment.permissions.observe:
        return emit('wait', 'assignment_not_active', status)

    session = observation.observed_at.astimezone(NY)
    session_key = session.date().isoformat()
    if track.get('session') != session_key:
        # Preserve protection/acquisition for an outstanding position, but never
        # carry an earlier session's entry or MACD permission into the next day.
        for key in ('macd_at', 'bullish', 'episode_open', 'used_episode', 'lows'):
            track.pop(key, None)
        track['session'] = session_key
    closed = 'bar_close' in observation.evaluation_events
    if closed and observation.source_timeframe == '100ms' and now > track.get('low_at', -1):
        track['low_at'] = now
        lows = [row for row in track.get('lows', []) if row[0] > now-config['lookback_seconds']]
        low = observation.bar_low
        if low is not None and isfinite(low) and low > 0:
            lows.append([now, low])
        track['lows'] = lows[-301:]
    if closed and observation.source_timeframe == '1s' and now > track.get('macd_at', -1):
        valid = all(v is not None and isfinite(v) for v in (observation.macd_line, observation.macd_signal))
        bullish = valid and observation.macd_line > observation.macd_signal
        if bullish and not track.get('bullish', False):
            track['episode_open'] = now
            lows = [low for at, low in track.get('lows', []) if now-config['lookback_seconds'] < at <= now]
            # The completed opening candle is a causal fallback when no finer
            # bars have arrived (e.g. a newly admitted ticker).
            low = observation.bar_low
            track['opening_low'] = min(lows) if lows else low
        track.update(macd_at=now, bullish=bullish)

    pending = bool(track.get('acquisition_open')) or status == Status.ENTRY_PENDING
    session_end = session.time().replace(tzinfo=None) >= time(20) or track.get('position_session', session_key) != session_key
    manual_exit = bool(state.get('manual_exit_requested'))
    exit_reason = ('session_end' if session_end else 'manual_exit' if manual_exit else
                   'macd_episode_closed' if track.get('bullish') is False else None)
    if held:
        track['ever_filled'] = True
        basis = observation.average_price
        if quote:
            if not track.get('holding_seen'):
                track['high_bid'] = quote['bid']
                track['holding_seen'] = True
            track['high_bid'] = max(track.get('high_bid', quote['bid']), quote['bid'])
            high = track['high_bid']
            if basis > 0 and high >= basis*(1+config['trail_activation_bps']/10000):
                distance = max(high*config['minimum_trail_bps']/10000,
                               (high-basis)*config['profit_giveback_fraction'],
                               config['spread_multiple']*track.get('entry_spread', quote['spread']))
                track['trailing_boundary'] = max(track.get('trailing_boundary', 0), high-distance)
            if quote['bid'] <= track.get('stop', 0):
                exit_reason = 'initial_stop_breached'
            elif quote['bid'] <= track.get('trailing_boundary', 0):
                exit_reason = 'peak_bid_pullback'
        if status == Status.EXIT_PENDING:
            exit_reason = state.get('last_exit_reason', 'exit_pending')
        if exit_reason:
            if not assignment.permissions.exit:
                return emit('hold', 'exit_not_authorized', status)
            if observation.pending_exit_quantity >= observation.position_quantity:
                return emit('hold', 'exit_fill_pending', Status.EXIT_PENDING)
            state.update(last_exit_reason=exit_reason, entry_acquisition_exit_latched=True)
            state.pop('manual_exit_requested', None)
            return emit('exit', exit_reason, Status.EXIT_PENDING,
                        max(0., observation.position_quantity-observation.pending_exit_quantity))
    if pending:
        invalid = (exit_reason or not quote or quote['spread_bps'] > config['maximum_spread_bps']
                   or now-track.get('requested_at', now) >= config['entry_deadline_ms']/1000
                   or now-track.get('macd_at', 0) > config['macd_age_ms']/1000)
        if invalid and not track.get('cancel_requested'):
            track['cancel_requested'] = True
            # The runtime awaits OMS cancellation before returning. An unfilled
            # order remains ENTRY_PENDING until its callback, while a partially
            # filled position must not retain a phantom acquisition after exit.
            track['acquisition_open'] = False
            return emit('cancel_entry', 'acquisition_expired_or_invalid',
                        Status.MANAGING if held else Status.ENTRY_PENDING)
        return emit('hold' if held else 'wait', 'entry_fill_pending',
                    Status.MANAGING if held else Status.ENTRY_PENDING)
    if held:
        return emit('hold', 'bullish_episode_held', Status.MANAGING)
    if status == Status.EXIT_PENDING or observation.pending_exit_quantity > 0:
        return emit('wait', 'exit_fill_pending', Status.EXIT_PENDING)
    if manual_exit:
        state.pop('manual_exit_requested', None)
        return emit('wait', 'manual_exit_already_flat', status)
    if not observation.market_open or not time(4) <= session.time().replace(tzinfo=None) < time(20):
        return emit('wait', 'outside_session', status)
    allowed = assignment.permissions.reenter if track.get('ever_filled') else assignment.permissions.enter
    if not allowed or state.get('disable_after_exit'):
        return emit('wait', 'entry_not_authorized', status)
    if not track.get('bullish'):
        return emit('wait', 'waiting_for_bullish_episode', status)
    opened = track['episode_open']
    if opened == track.get('used_episode'):
        return emit('wait', 'episode_already_attempted', status)
    if now-opened >= config['entry_window_ms']/1000 or now-track['macd_at'] > config['macd_age_ms']/1000:
        return emit('wait', 'entry_window_expired', status)
    if not quote or quote['spread_bps'] > config['maximum_spread_bps']:
        return emit('wait', 'fresh_executable_quote_required', status)
    low = track.get('opening_low')
    if low is None or not isfinite(low) or low <= 0:
        return emit('wait', 'opening_low_unavailable', status)
    stop = floor((low-config['tick_size'])/config['tick_size']+1e-9)*config['tick_size']
    risk = (quote['ask']-stop)/quote['ask']*10000
    if not 0 < stop < quote['bid'] or not 0 < risk <= config['maximum_initial_risk_bps']:
        return emit('wait', 'initial_risk_out_of_bounds', status)
    track.update(used_episode=opened, stop=stop, high_bid=quote['bid'],
                 trailing_boundary=0., entry_spread=quote['spread'], requested_at=now,
                 acquisition_open=True, cancel_requested=False, holding_seen=False,
                 position_session=session_key)
    state.update(entries=int(state.get('entries', 0))+1, initial_stop=stop, active_stop=stop,
                 entry_reference_price=quote['ask'], entry_at=observation.observed_at.isoformat(),
                 entry_acquisition_exit_latched=False)
    state.pop('last_exit_reason', None)
    state.pop('liquidation_origin_fill_role', None)
    state.pop('liquidation_origin_reentry_after_fill', None)
    return emit('enter_long', 'bullish_macd_episode_open', Status.ENTRY_PENDING, config['quantity'])


def acquisition_update(state, *, terminal, filled=False):
    """Broker/Portfolio acknowledgement, never inferred from a requested order."""
    track = deepcopy(state.get('hindsight_long', {}))
    if terminal:
        track['acquisition_open'] = False
    if filled:
        track['ever_filled'] = True
    state['hindsight_long'] = track
