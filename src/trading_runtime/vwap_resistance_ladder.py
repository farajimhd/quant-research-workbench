"""VWAP midpoint entry, native MACD episodes and unreserved resistance additions."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, time
from math import floor, isfinite

from . import historical_hod as H
from .signals import CapitalRequest, StrategyIntent
from src.market_engine.derived_trade_policy import POLICY

CONTRACT = 'vwap-midpoint-resistance-ladder-v1'
DEFAULTS = dict(cash_fraction=1., late_entry_breaks=6, stop_offset_ticks=1.)
RETEST_DEFAULTS = dict(group_resistances=0, require_late_retest=0, allow_retest_stop_fallback=0)
POST_MOVE_DEFAULTS = dict(post_move_entries=0, pullback_independent_episode=0,
                          pullback_above_vwap_only=0, breakout_offset_ticks=1., pullback_min_rise_pct=0.)
TIMEFRAMES = {'100ms': .1, '1s': 1., '5s': 5., '10s': 10., '30s': 30.}


def configure(p):
    if p.get('vwap_ladder_contract') != CONTRACT:
        raise ValueError('Unknown VWAP resistance ladder contract')
    if any(p.get(k) for k in ('r1_ladder_contract', 'pullback_hod_contract')):
        raise ValueError('VWAP ladder cannot compose another entry policy')
    raw = p.get('vwap_ladder', {})
    if set(raw) - set(DEFAULTS) - set(RETEST_DEFAULTS) - set(POST_MOVE_DEFAULTS):
        raise ValueError('Unknown VWAP ladder setting')
    s = {**DEFAULTS, **RETEST_DEFAULTS, **POST_MOVE_DEFAULTS, **raw}
    if (any(type(v) not in (int, float) or not isfinite(v) for v in s.values())
            or not 0 < s['cash_fraction'] <= 1 or s['late_entry_breaks'] not in (4, 6)
            or s['stop_offset_ticks'] < 1 or any(s[k] not in (0, 1) for k in RETEST_DEFAULTS)
            or s['pullback_min_rise_pct'] < 0
            or s['pullback_min_rise_pct'] and not (s['group_resistances'] and s['post_move_entries'])
            or any(s[k] not in (0, 1) for k in ('post_move_entries','pullback_independent_episode','pullback_above_vwap_only'))
            or s['breakout_offset_ticks'] < 1 or s['breakout_offset_ticks'] != int(s['breakout_offset_ticks'])):
        raise ValueError('Invalid VWAP ladder settings')
    p['vwap_ladder'] = s


def macd(o, timeframe):
    """Matching native completed samples, never a fallback or future sample."""
    values, stamps = [], []
    period = TIMEFRAMES[timeframe]
    for name in ('line', 'signal'):
        key = f'indicator.macd.{name}@{timeframe}'
        sample = o.source_values.get(key + ':completed', o.source_values.get(key, {}))
        value = sample.get('value')
        try:
            at = datetime.fromisoformat(str(sample['observed_at']).replace('Z', '+00:00'))
        except (KeyError, ValueError, TypeError):
            return None
        if (sample.get('sample_kind') == 'forming'
                or at.tzinfo is None or type(value) not in (int, float) or not isfinite(value)
                or not 0 <= (o.observed_at-at).total_seconds() < period
                or abs(at.timestamp()/period-round(at.timestamp()/period)) > 1e-4):
            return None
        values.append(value); stamps.append(at.timestamp())
    return dict(line=values[0], signal=values[1], at=stamps[0]) if stamps[0] == stamps[1] else None


def levels(o, *, typed_persistence=False):
    now = o.observed_at.timestamp()*1000
    result = {}
    for r in (*o.structural_support_levels, *o.structural_resistance_levels, *o.structural_transition_levels):
        r = dict(r, lower=r.get('band_lower', r.get('lower')), upper=r.get('band_upper', r.get('upper')))
        identity = r.get('unified_level_id')
        if (not identity or r.get('book_version') != 'causal-level-book-v7-mle-1'
                or any(type(r.get(k)) not in (int, float) or not isfinite(r[k])
                       for k in ('lower', 'upper', 'confirmed_at_ms'))
                or not 0 < r['lower'] <= r['upper'] or r['confirmed_at_ms'] > now):
            continue
        projected = {k: r[k] for k in ('unified_level_id', 'lower', 'upper', 'price',
            'side', 'role', 'confirmed_at_ms', 'book_version', 'input_policy', 'seed_input_policy') if k in r}
        if typed_persistence:
            from .typed_assignment_input import validate_grouped_resistance_level
            projected = validate_grouped_resistance_level(projected)
        result[str(identity)] = projected
    return result


def fresh_quote(o):
    try:
        at = datetime.fromisoformat(str(o.source_values['market.spread_bps']['observed_at']).replace('Z', '+00:00'))
        return (at.tzinfo is not None and 0 <= (o.observed_at-at).total_seconds() <= 1
                and all(isfinite(v) for v in (o.bid, o.ask)) and 0 < o.bid <= o.ask)
    except (KeyError, TypeError, ValueError):
        return False


def release_unfilled_episode(state):
    active = state.get('vwap_ladder_entry', {})
    episode = state.get('vwap_ladder_episode', {})
    if not active.get('first_fill_at') and episode.get('started_at') == active.get('episode_id'):
        state['vwap_ladder_episode'] = dict(episode, used=bool(active.get('prior_episode_used')))
        if active.get('entry_kind') == 'post_move_pullback':
            consumed = state.get('post_move_entry_clock', {}).get('consumed_pullbacks', [])
            pivot = active['retest_anchor']['pivot_at']
            if pivot in consumed:
                consumed.remove(pivot)
            move = active.get('pullback_move')
            consumed_moves = state.get('post_move_entry_clock', {}).get('consumed_moves', [])
            if move and move['id'] in consumed_moves:
                consumed_moves.remove(move['id'])


def observe_market(o, state, settings=None, *, typed_persistence=False):
    """Remember physical resistance IDs before role flips; recrosses count once."""
    if (settings or {}).get('group_resistances'):
        from .resistance_zones import observe
        crossed = observe(o, state, levels(o, typed_persistence=typed_persistence))
        if settings.get('pullback_min_rise_pct'):
            from .pullback_impulse import observe as observe_impulse
            observe_impulse(state.setdefault('pullback_impulse', {}), o.observed_at.timestamp(),
                            o.price, settings['pullback_min_rise_pct'])
        return crossed
    session = o.observed_at.astimezone(H.NY).date().isoformat()
    if state.get('session') != session:
        state.clear(); state.update(session=session, known={}, broken=[])
    rows = levels(o, typed_persistence=typed_persistence)
    previous = state.get('price')
    crossed = []
    for key, row in state['known'].items():
        if previous is not None and previous <= row['upper'] < o.price and key not in state['broken']:
            crossed.append((key, row))
    for key, row in sorted(crossed, key=lambda item: item[1]['upper']):
        state['broken'].append(key)
        state.setdefault('break_rows', {})[key] = dict(deepcopy(row), broken_at=o.observed_at.timestamp())
    for key, row in rows.items():
        if row.get('side') in (-1, 'resistance') or row.get('role') == 'resistance':
            state['known'][key] = row
    state['price'] = o.price
    state['at'] = o.observed_at.timestamp()
    return crossed


def support_swing(o, rows, cutoff, *, typed_persistence=False):
    row = (o.structural_detector_state or {}).get('row', {})
    supports = [r for r in rows.values() if r.get('side') in (1, 'support')]
    def recovered(swing):
        return next((w for w in row.get('vwap_support_bounces', [])
            if w['pivot_at'] == swing['pivot_at'] and w['pivot_price'] == swing['price']
            and w.get('recovered_at') is not None and w['recovered_at'] <= o.observed_at.timestamp()
            and o.price > w['support']['lower']), None)
    def supported(swing):
        if any(r['confirmed_at_ms']/1000 <= swing['pivot_at']
               and r['lower'] <= swing['price'] <= r['upper'] for r in supports):
            return True
        return recovered(swing) is not None
    candidate = H.initial_swing_low(row, dict(lower=o.price), o.observed_at.timestamp(),
        price_only=True, pivot_not_before=cutoff, eligible=supported)
    if candidate and recovered(candidate):
        candidate['support_bounce'] = deepcopy(recovered(candidate))
    if candidate and typed_persistence:
        from .arte_assignment_vwap_swing import validate_typed_entry_swing
        candidate = validate_typed_entry_swing(candidate)
    return candidate


def observe_support_bounces(market, previous, bar, level_rows):
    """Retain causal support contacts for active/developing swing lows only."""
    from types import SimpleNamespace
    from datetime import timezone
    row = market['row']
    pivots = {s['pivot_at'] for s in row.get('local_swings', []) + row.get('confirmed_swings', [])
              if s.get('side') in (1, 'support')}
    developing = (row.get('developing_swings') or {}).get('low') or {}
    if developing.get('pivot_at') is not None:
        pivots.add(developing['pivot_at'])
    pivots.add(bar['end'])
    witnesses = [] if market.get('reset') else deepcopy(previous.get('vwap_support_bounces', []))
    witnesses = [w for w in witnesses if w['pivot_at'] in pivots]
    current = levels(SimpleNamespace(observed_at=datetime.fromtimestamp(bar['end'], timezone.utc),
        structural_support_levels=level_rows, structural_resistance_levels=(), structural_transition_levels=()))
    # A touch may flip/refit the band at this close. Use the preceding known
    # geometry for that candle, not the changed classification caused by it.
    known = dict(current)
    if not market.get('reset'):
        known.update(previous.get('vwap_support_bands', {}))
    for r in known.values():
        if (r.get('side') in (1, 'support') and r['confirmed_at_ms']/1000 <= bar['time']
                and bar['low'] < r['lower'] <= bar['high']):
            witnesses.append(dict(pivot_at=bar['end'], pivot_price=bar['low'], support=deepcopy(r)))
    for w in witnesses:
        if w.get('recovered_at') is None and bar['close'] > w['support']['lower']:
            w['recovered_at'] = bar['end']
    market['vwap_support_bounces'] = witnesses
    market['vwap_support_bands'] = current
    row['vwap_support_bounces'] = deepcopy(witnesses)


def evaluate(host, a, o, p, old_state, *, typed_persistence=False):
    from .strategy_engine import AssignmentStatus as Status, _at_or_after_session_time
    state = deepcopy(old_state)
    settings = p['vwap_ladder']; tick = p['execution']['tick_size']
    now = o.observed_at.timestamp()
    local = o.observed_at.astimezone(H.NY)
    cutoff = datetime.combine(local.date(), time(4, 5), H.NY).timestamp()
    market = state.setdefault('vwap_ladder_market', {})
    def geometry(book):
        return tuple(sorted((k, r['lower'], r['upper'], tuple(r.get('members', ())))
                            for k, r in book.get('known', {}).items()))
    prior_geometry = geometry(market)
    passive = (o.structural_detector_state or {}).get('historical_hod_observation', {}).get('vwap_ladder_market')
    prior_broken = set(market.get('broken', []))
    if passive and market.get('at', 0) < passive.get('at', 0) <= now:
        previous_market = market
        market = deepcopy(passive)
        if not settings.get('group_resistances') and previous_market.get('session') == market.get('session'):
            # A completed candle can miss an intrabar break already seen in the
            # trade stream. Passive history may extend, never erase that evidence.
            market['broken'] = list(dict.fromkeys((*previous_market.get('broken', []), *market['broken'])))
            market['break_rows'] = {**market.get('break_rows', {}), **previous_market.get('break_rows', {})}
        state['vwap_ladder_market'] = market
    active = state.get('vwap_ladder_entry', {})
    acquired = o.position_quantity > 0
    stop = float(state.get('active_stop') or 0)
    target = float((state.get('structural_profit_targets') or [0])[0])
    samples = {tf: macd(o, tf) for tf in TIMEFRAMES}
    episode = state.setdefault('vwap_ladder_episode', {})
    if episode.get('session') != local.date().isoformat():
        episode.clear(); episode['session'] = local.date().isoformat()
    ten = samples['10s']
    if ten and ten['at'] >= episode.get('sample_at', 0):
        episode['sample_at'] = ten['at']
        if ten['line'] < ten['signal']:
            episode['bullish'] = False
        elif ten['line'] > ten['signal'] and not episode.get('bullish'):
            episode.update(bullish=True, used=False, started_at=ten['at'])
    bullish = bool(ten and ten['line'] > ten['signal'])
    all_macd = all(s and s['line'] > s['signal'] for s in samples.values())
    is_trade = ('market.last_price' in o.changed_source_ids
                or ('bar_close' in o.evaluation_events and o.source_timeframe == '100ms'))
    entry_clock = state.setdefault('post_move_entry_clock', {})
    if entry_clock.get('session') != local.date().isoformat():
        entry_clock.clear()
        entry_clock.update(session=local.date().isoformat(), consumed_pullbacks=[])
    previous_entry_price = entry_clock.get('price')
    if is_trade:
        entry_clock['price'] = o.price
    crossed = []
    if passive and market is not None:
        crossed = [(key, market['break_rows'][key]) for key in market.get('broken', [])
                   if key not in prior_broken]
    group_clock = not settings.get('group_resistances') or ('bar_close' in o.evaluation_events and o.source_timeframe == '100ms')
    if now >= cutoff and is_trade and group_clock and now > market.get('at', 0):
        crossed.extend(observe_market(o, market, settings,
                                      typed_persistence=typed_persistence))
    rows = levels(o, typed_persistence=typed_persistence)
    count = len(market.get('broken', []))
    evidence = dict(contract=CONTRACT, macd_samples=samples, session_resistance_breaks=count,
                    episode=deepcopy(episode), initial_stop=state.get('initial_stop'),
                    active_stop=stop, profit_targets=[target] if target else [])

    def emit(action, reason, status=None, **kw):
        metadata = {**evidence, 'active_stop': state.get('active_stop'),
                    'profit_targets': list(state.get('structural_profit_targets') or []),
                    **kw.pop('metadata', {})}
        if action == 'exit':
            state.update(last_exit_reason=reason, entry_acquisition_exit_latched=True)
            metadata.update(position_fraction=1., cancel_entry_acquisition=True, reentry_after_fill=True)
        result = host._result(a, o, action, reason, float(action in ('enter_long', 'add_long')), 1.,
            state, status or a.status, metadata=metadata,
            order_intent={'execution_policy': 'adaptive_urgent', 'protection_profile': 'structural-single-target'}, **kw)
        if action in ('enter_long', 'add_long'):
            result = replace(result, evaluation=replace(result.evaluation, intents=tuple(
                replace(i, reference_price=o.ask, execution_policy=replace(i.resolved_execution_policy(),
                    envelope=replace(i.resolved_execution_policy().envelope, deadline_ms=100,
                        persist_until_cancelled=False, maximum_buy_price=o.ask)),
                    metadata={**i.metadata, 'mandatory_broker_target': True, 'wait_for_capital': False})
                for i in result.evaluation.intents)))
        return result

    if a.status == Status.EXIT_PENDING or o.pending_exit_quantity > 0:
        return emit('hold' if acquired else 'wait', 'exit_fill_pending', Status.EXIT_PENDING)
    if acquired:
        reason = ('manual_exit' if state.get('manual_exit_requested') else
            'session_flatten' if _at_or_after_session_time(o.observed_at,
                p.get('strategy_behavior', {}).get('flatten_time', '15:55:00')) else
            'protective_stop' if stop and o.price <= stop else
            'profit_target' if target and o.price >= target else '')
        if reason:
            return emit('exit', reason, Status.EXIT_PENDING, quantity=o.position_quantity)
        if not active:
            return emit('hold', 'position_entry_state_unavailable', Status.MANAGING)
        if active.get('entry_kind') == 'post_move_pullback':
            # Session counts deduplicate breaks; a new position must still see
            # its own recrosses of historically broken, overhead resistance.
            crossed = []
            if market.get('at', 0) > active.get('cross_at', active['requested_at']):
                previous = active.get('cross_price', active['entry_price'])
                crossed = sorted(((k, r) for k, r in market.get('known', {}).items()
                    if k in active.get('cross_known', market['known'])
                    and previous <= r['upper'] < market['price'] and k not in active['broken']),
                    key=lambda item: item[1]['upper'])
                active.update(cross_at=market['at'], cross_price=market['price'], cross_known=list(market['known']))
        for key, row in crossed:
            if row['upper'] > active['entry_price'] and key not in active['broken']:
                active['broken'].append(key)
        if typed_persistence:
            from .arte_assignment_vwap_entry_ids import validate_typed_entry_id_lists
            validate_typed_entry_id_lists({name: active[name]
                                           for name in ('broken', 'cross_known')
                                           if name in active})
        position_breaks = len(active['broken'])
        # Two physical levels behind, including prior session breaks for a late entry.
        eligible_stop = position_breaks >= (2 if active['late'] else 3)
        if eligible_stop and (count >= 3 or active.get('entry_kind') == 'post_move_pullback'):
            if active.get('entry_kind') == 'post_move_pullback':
                trail = [active['retest_anchor']['anchor']]+[market['known'][k] for k in active['broken']]
                reference = trail[-3]
            else:
                reference = market['break_rows'][market['broken'][-3]]
            proposal = floor((reference['lower']-settings['stop_offset_ticks']*tick+1e-9)/tick)*tick
        else:
            proposal = stop
        results = []
        if stop < proposal < o.bid:
            state['active_stop'] = proposal
            results.append(emit('replace_protective_stop', 'two_resistance_stop_advance', Status.MANAGING,
                quantity=o.position_quantity, invalidation_price=proposal,
                metadata=dict(previous_stop=stop, active_stop=proposal)))
            stop = proposal
        # Never reduce an existing target when the target-distance regime tightens.
        distance = 1 if active['late'] or count >= 6 else 2 if count >= 4 else 3
        overhead = sorted((r for k, r in market.get('known', {}).items()
            if (active.get('entry_kind') == 'post_move_pullback' or k not in market.get('broken', []))
            and r['upper'] > o.ask), key=lambda r: r['lower'])
        regrouped = bool(settings.get('group_resistances') and prior_geometry != geometry(market))
        if (crossed or regrouped) and len(overhead) >= distance and (not active['late'] or active['target_moves'] < 2):
            proposal = (floor((overhead[distance-1]['upper']+1e-9)/tick)+1)*tick
            if active.get('entry_kind') == 'post_move_breakout':
                from .post_move_entries import midpoint_target
                reference = max((market['break_rows'][k] for k in market.get('broken', [])),
                                key=lambda r:r['upper'], default=active['breakout_setup']['anchor'])
                selection = midpoint_target(market, reference, tick)
                proposal = selection['price'] if selection else target
            pending = active.get('pending_target', {})
            if proposal > max(target, o.ask, pending.get('price', 0)):
                active['pending_target'] = dict(price=proposal,
                    moves=max(pending.get('moves', 0), active['target_moves']+int(bool(crossed))))
        pending_target = active.get('pending_target')
        if pending_target and pending_target['price'] > max(target, o.ask):
            proposal = pending_target['price']
            previous_moves = active['target_moves']
            state['structural_profit_targets'] = [proposal]
            active['target_moves'] = pending_target['moves']
            results.append(emit('replace_profit_target', 'resistance_target_advance', Status.MANAGING,
                quantity=o.position_quantity, profit_target_price=proposal,
                metadata=dict(previous_profit_target=target, profit_targets=[proposal],
                    vwap_ladder_previous_target_moves=previous_moves)))
            target = proposal
        # Consume each break's opportunity once, even when cash/permission is absent.
        available_adds = min(position_breaks, 2)
        for index in range(active['add_opportunities'], available_adds):
            active['add_opportunities'] = index+1
            cap = active.get('slice_notional', 0)
            if a.permissions.add and bullish and fresh_quote(o) and cap > 0 and 0 < stop < o.bid <= o.ask < target:
                results.append(emit('add_long', 'resistance_cash_addition', Status.MANAGING,
                    invalidation_price=stop, profit_target_price=target,
                    capital_request=CapitalRequest(mode='fixed_notional', value=cap),
                    metadata=dict(addition_index=index+1, slice_notional=cap)))
        if results:
            final = results[-1]
            return replace(final, state=state, evaluation=replace(final.evaluation,
                signals=tuple(s for r in results for s in r.evaluation.signals),
                intents=tuple(i for r in results for i in r.evaluation.intents)))
        return emit('hold', 'manage_resistance_ladder', Status.MANAGING)
    if a.status == Status.ENTRY_PENDING:
        acquisition_bullish = bullish
        if active.get('entry_kind') == 'post_move_pullback':
            acquisition_bullish = bool(samples['1s'] and samples['1s']['line'] > samples['1s']['signal'])
        if not acquisition_bullish or now-active.get('requested_at', 0) >= .1:
            result = emit('wait', 'entry_acquisition_expired', Status.WATCHING)
            cancel = StrategyIntent(intent_id=result.evaluation.signals[0].signal_id+'-cancel',
                ticker=o.ticker, event_time=o.observed_at, action='cancel_entry', quantity=0,
                reference_price=o.price, metadata={'assignment_id': a.assignment_id})
            return replace(result, evaluation=replace(result.evaluation, intents=(cancel,)))
        return emit('wait', 'entry_fill_pending', Status.ENTRY_PENDING)
    if a.status in (Status.DISABLED, Status.PAUSED, Status.COMPLETED, Status.ERROR) or not a.permissions.observe:
        return emit('wait', 'assignment_not_active')
    if not (a.permissions.reenter if state.get('entries', 0) else a.permissions.enter):
        return emit('wait', 'entry_not_authorized')
    late = count >= settings['late_entry_breaks']
    impulse_required = bool(settings.get('pullback_min_rise_pct'))
    post_move = bool(settings.get('post_move_entries') and
                     (late or impulse_required and state.get('entries', 0)))
    anchor = None
    breakout = None
    entry_kind = 'initial'
    target_selection = None
    pullback_move = None
    if post_move:
        from .resistance_zones import entry_anchor
        from .post_move_entries import pending_breakout, midpoint_target
        witnessed_breakout = pending_breakout(o, market, entry_clock, previous_entry_price,
            tick, settings['breakout_offset_ticks'], episode, is_trade and late,
            typed_persistence=typed_persistence)
        one = samples['1s']
        if (one and one['line'] > one['signal']
                and (settings.get('pullback_independent_episode') or not episode.get('used'))):
            def eligible(candidate):
                if candidate['pivot_at'] in entry_clock['consumed_pullbacks']:
                    return False
                if impulse_required:
                    from .pullback_impulse import qualify
                    return bool(qualify(market, candidate, entry_clock.get('consumed_moves', []),
                                        typed_persistence=typed_persistence))
                return True
            anchor = entry_anchor(o, market, not impulse_required, settings['late_entry_breaks'],
                                  fresh=not impulse_required, eligible=eligible,
                                  typed_persistence=typed_persistence)
            if anchor and impulse_required:
                from .pullback_impulse import qualify
                pullback_move = qualify(market, anchor, entry_clock.get('consumed_moves', []),
                                        typed_persistence=typed_persistence)
        if anchor:
            entry_kind = 'post_move_pullback'
        elif late and not episode.get('used') and all(samples[tf] and samples[tf]['line'] > samples[tf]['signal']
                                           for tf in ('100ms','1s','5s','10s')):
            breakout = witnessed_breakout
            if breakout:
                entry_kind = 'post_move_breakout'
                target_selection = midpoint_target(market, breakout['anchor'], tick)
        if not anchor and not breakout:
            return emit('wait', 'post_move_fresh_pullback_or_breakout_required')
    if now < cutoff or not is_trade or (not post_move and (not all_macd or episode.get('used'))):
        return emit('wait', 'waiting_for_unused_bullish_episode')
    if not fresh_quote(o):
        return emit('wait', 'fresh_executable_quote_required')
    if not rows or any(r.get('input_policy') != POLICY or r.get('seed_input_policy') != POLICY for r in rows.values()):
        return emit('wait', 'filtered_v7_seed_rebuild_required')
    vwap, hod = o.execution_vwap, o.structural_session_high
    above_vwap_only = entry_kind == 'post_move_pullback' and settings.get('pullback_above_vwap_only')
    if (not vwap or not hod or not isfinite(vwap) or not isfinite(hod)
            or hod <= vwap or o.price <= vwap or (not above_vwap_only and o.price < vwap+.5*(hod-vwap))):
        return emit('wait', 'above_vwap_required' if above_vwap_only else 'below_vwap_hod_midpoint')
    swing = None if breakout else support_swing(
        o, rows, cutoff, typed_persistence=typed_persistence)
    if not post_move and (late and settings.get('require_late_retest') or not swing and settings.get('allow_retest_stop_fallback')):
        from .resistance_zones import entry_anchor
        anchor = entry_anchor(o, market, late, settings['late_entry_breaks'],
                              typed_persistence=typed_persistence)
        if anchor is None:
            return emit('wait', 'late_pullback_retest_required' if late else 'level_retest_stop_required')
    if anchor:
        swing = anchor['swing']
        if typed_persistence:
            from .arte_assignment_vwap_swing import validate_typed_entry_swing
            swing = validate_typed_entry_swing(swing)
    overhead = sorted((r for k, r in market.get('known', {}).items()
        if (entry_kind == 'post_move_pullback' or k not in market.get('broken', []))
        and r['upper'] > o.ask), key=lambda r: r['lower'])
    distance = 1 if late or entry_kind == 'post_move_pullback' else 2 if count >= 4 else 3
    if (not breakout and (not swing or len(overhead) < distance)) or (breakout and not target_selection):
        return emit('wait', 'support_swing_or_overhead_resistances_unavailable')
    stop_base = (breakout['anchor']['lower'] if breakout else swing['price'] if post_move and anchor
                 else anchor['anchor']['lower'] if anchor else swing['lower'])
    stop = floor((stop_base-settings['stop_offset_ticks']*tick+1e-9)/tick)*tick
    target = target_selection['price'] if breakout else (floor((overhead[distance-1]['upper']+1e-9)/tick)+1)*tick
    if not 0 < stop < o.bid <= o.ask < target:
        return emit('wait', 'invalid_execution_geometry')
    prior_episode_used = bool(episode.get('used'))
    episode['used'] = True
    entry_clock.pop('pending_breakout', None)
    if post_move and anchor:
        entry_clock['consumed_pullbacks'].append(anchor['pivot_at'])
        if pullback_move:
            entry_clock.setdefault('consumed_moves', []).append(pullback_move['id'])
    if typed_persistence and breakout is not None:
        from .arte_assignment_vwap_entry_breakout import validate_entry_breakout_setup
        breakout = validate_entry_breakout_setup(breakout)
    state.update(vwap_ladder_entry=dict(entry_price=o.ask, requested_at=now, broken=[], late=late or entry_kind == 'post_move_pullback',
        cross_known=list(market.get('known', {})),
        target_moves=0, add_opportunities=0, swing=swing, retest_anchor=anchor, episode_id=episode.get('started_at'),
        entry_kind=entry_kind, breakout_setup=breakout, pullback_move=pullback_move,
        prior_episode_used=prior_episode_used), initial_stop=stop, active_stop=stop,
        structural_profit_targets=[target], entry_at=o.observed_at.isoformat(), entry_reference_price=o.ask,
        entries=state.get('entries', 0)+1, entry_acquisition_exit_latched=False)
    if typed_persistence:
        from .arte_assignment_vwap_entry_ids import validate_typed_entry_id_lists
        validate_typed_entry_id_lists({name: state['vwap_ladder_entry'][name]
                                       for name in ('broken', 'cross_known')})
    return emit('enter_long', entry_kind if post_move else 'vwap_midpoint_all_macd', Status.ENTRY_PENDING,
        invalidation_price=stop, profit_target_price=target,
        capital_request=CapitalRequest(mode='mandate_fraction', value=settings['cash_fraction']/3),
        metadata=dict(unreserved_cash_slice=True, initial_stop=stop, active_stop=stop, profit_targets=[target],
            initial_swing=swing, retest_anchor=anchor, entry_kind=entry_kind, breakout_setup=breakout,
            target_selection=target_selection, pullback_move=pullback_move, grouping_threshold=market.get('grouping_threshold'),
            grouping_gap_samples=market.get('grouping_gap_samples')))
