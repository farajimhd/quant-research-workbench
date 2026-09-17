"""Completed-candle pullback entries and failed-push exits below session HOD.

Historical HOD supplies shared, causal market inputs, never entry permission.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from math import isfinite

from . import historical_hod as H, session_relative_volume, trade_volume

CONTRACT = 'pullback-hod-v1'
RISE_CONTRACT = 'swing-rise-pullback-hod-v2'
DEFAULTS = dict(entry_zone_fraction=.3, maximum_swing_age_s=30.,
                minimum_pullback_ticks=2., minimum_body_fraction=.3,
                minimum_close_location=.6, top_retreat_atr=.5,
                top_confirmation_seconds=5., entry_macd_10s_enabled=0)


def configure(p):
    if p.get('pullback_hod_contract') not in (CONTRACT,RISE_CONTRACT) or p.get('historical_hod_contract') != H.CONTRACT:
        raise ValueError('Pullback HOD requires its versioned market adapter')
    if p.get('r1_ladder_contract'):
        raise ValueError('Pullback HOD cannot compose R1 policy')
    raw = p.get('pullback_hod', {})
    if set(raw)-set(DEFAULTS):
        raise ValueError('Unknown pullback HOD setting')
    s = dict(DEFAULTS, **raw)
    if type(s['entry_macd_10s_enabled']) not in (int,float) or s['entry_macd_10s_enabled'] not in (0,1):
        raise ValueError('10s MACD entry gate must be a numeric boolean switch')
    if any(type(v) not in (int,float) or not isfinite(v) or v <= 0
           for k,v in s.items() if k != 'entry_macd_10s_enabled'):
        raise ValueError('Pullback settings must be finite positive numbers')
    if any(s[k] > 1 for k in ('entry_zone_fraction','minimum_body_fraction','minimum_close_location')):
        raise ValueError('Pullback fractions must not exceed one')
    p['pullback_hod'] = s


def macd_entry_gate(o):
    """Use only matching, completed native 10s samples; never a 1s fallback."""
    now = o.observed_at.timestamp()
    result = dict(timeframe='10s',kind='completed',passed=False,reason='macd_10s_unavailable')
    stamps=[]
    for field in ('line','signal'):
        sample=o.source_values.get(f'indicator.macd.{field}@10s')
        if not isinstance(sample,dict):return result
        value=sample.get('value')
        if type(value) not in (int,float) or not isfinite(value):return result
        try:
            at=datetime.fromisoformat(str(sample['observed_at']).replace('Z','+00:00'))
        except (KeyError,ValueError,TypeError):
            return result
        if at.tzinfo is None:return result
        stamp=at.timestamp()
        if stamp % 10 != 0 or not 0 <= now-stamp < 10:return result
        stamps.append(stamp)
        result[field]=value
    if stamps[0] != stamps[1]:return result
    passed=result['line'] >= result['signal']
    return dict(result,passed=passed,observed_at=stamps[0],age_seconds=now-stamps[0],
                reason='macd_10s_entry_allowed' if passed else 'macd_10s_below_signal')


def swing_key(swing):
    return (swing['pivot_at'], swing['lower'])


def entry_setup(row, market, settings, tick, used=None, *, rising=False, last_exit=None):
    """V1 requires a prior pullback; V2 separates initial rise from reentry."""
    bar, prior = market.get('bar') or {}, market.get('prior_bar') or {}
    now = bar.get('end', 0)
    hod, vwap = market.get('prior_hod'), market.get('vwap')
    if not hod or not vwap or hod <= vwap or not prior or prior['end'] != bar.get('time'):
        return None, 'pullback_history_unavailable'
    lower = hod-settings['entry_zone_fraction']*(hod-vwap)
    if not lower <= bar['close'] <= hod:
        return None, 'outside_pullback_hod_zone'
    swing = H.initial_swing_low(row, dict(lower=bar['close']), now,
        price_only=True, maximum_age_s=settings['maximum_swing_age_s'])
    if not swing or swing['pivot_at'] >= bar['time']:
        return None, 'confirmed_pullback_low_unavailable'
    if used and swing_key(swing) <= tuple(used):
        return None, 'waiting_for_new_pullback_low'
    reentry = rising and last_exit and last_exit.get('advanced')
    high = None
    if reentry:
        if swing['pivot_at'] <= last_exit['at']:
            return None, 'waiting_for_post_exit_pullback_low'
        high = last_exit['peak']
        if high['price']-swing['price'] < tick*settings['minimum_pullback_ticks']:
            return None, 'waiting_for_post_exit_pullback_depth'
    elif not rising:
        highs = [x for x in row.get('local_swings', [])+row.get('confirmed_swings', [])
                 if x.get('side') in (-1,'resistance')
                 and all(type(x.get(k)) in (int,float) and isfinite(x[k]) for k in ('pivot_at','confirmed_at','price'))
                 and 0 < x['pivot_at'] < swing['pivot_at'] and x['pivot_at'] <= x['confirmed_at'] <= now]
        high = max(highs,key=lambda x:x['pivot_at'],default=None)
        if not high or high['price']-swing['price'] < tick*settings['minimum_pullback_ticks']:
            return None, 'preceding_pullback_high_unavailable'
    width = bar['high']-bar['low']
    body = bar['close']-bar['open']
    if (bar['low'] < swing['lower'] or width <= 0 or body < tick-1e-9
            or body/width < settings['minimum_body_fraction']
            or (bar['close']-bar['low'])/width < settings['minimum_close_location']
            or bar['close'] <= prior['close']):
        return None, 'waiting_for_bullish_pullback_recovery'
    return dict(swing=deepcopy(swing), preceding_high=deepcopy(high), candle=deepcopy(bar),
                hod=hod, zone_lower=lower,
                **(dict(entry_kind='pullback_reentry' if reentry else 'initial_swing_rise',
                        prior_exit=deepcopy(last_exit) if reentry else None) if rising else {})), ''


def observe_advance(active,o,market,tick,fresh):
    """Track only prices observed while actually holding the position."""
    filled = active.get('first_fill_at')
    if filled is None or o.average_price <= 0:
        return
    traded = ('market_data_update' in o.evaluation_events
              and 'market.last_price' in o.changed_source_ids
              and o.observed_at.timestamp() > filled)
    prices = [o.price] if traded else []
    bar = market.get('bar') or {}
    if fresh and bar.get('time',0) >= filled:
        prices.append(bar['high'])
    if not prices:
        return
    price = max(prices)
    if price > active.get('advance_peak',{}).get('price',0):
        active['advance_peak'] = dict(price=price,at=o.observed_at.timestamp())
    if active['advance_peak']['price'] >= o.average_price+tick-1e-9:
        active['advanced'] = True


def record_exit(state,at,remaining):
    """A filled-flat position, never an exit intent, arms the reentry cycle."""
    active = state.get('pullback_entry') or {}
    if remaining is None or abs(float(remaining)) > 1e-9 or not active.get('first_fill_at'):
        return
    state['pullback_last_exit'] = dict(at=at.timestamp(),advanced=bool(active.get('advanced')),
        peak=deepcopy(active.get('advance_peak')),entry_at=active['first_fill_at'],
        reason=state.get('last_exit_reason',''))


def top_failure(active, market, settings, tick, fresh):
    """A rejection near HOD/resistance needs a later bearish break of its low."""
    bar = market.get('bar') or {}
    if not fresh or bar.get('time',0) < active.get('first_fill_at',float('inf')):
        return '', {}
    attempt = active.get('top_attempt')
    now = bar['end']
    hod = active['setup']['hod']
    if active.get('hod_broken') and bar['close'] < bar['open'] and bar['close'] < hod:
        return 'pullback_hod_break_failed', dict(hod=hod,confirmation=deepcopy(bar))
    if bar['close'] > hod:
        active['hod_broken'] = True
    if attempt and now <= attempt['candle']['end']:
        return '', {}
    atr = market.get('closed_atr') or 0.
    retreat = max(2*tick, settings['top_retreat_atr']*atr)
    if attempt:
        peak = attempt['candle']
        if (now-peak['end'] > settings['top_confirmation_seconds']
                or bar['high'] > peak['high'] or bar['time'] != market.get('prior_bar',{}).get('end')):
            active.pop('top_attempt',None)
        elif (bar['close'] < bar['open'] and bar['close'] < peak['low']
                and peak['high']-bar['close'] >= attempt['minimum_retreat']):
            return 'pullback_failed_top', dict(attempt=deepcopy(attempt),confirmation=deepcopy(bar))
    width = bar['high']-bar['low']
    # Use only resistance/HOD known before the rejection candle.
    levels = [market.get('prior_hod') or float('inf')]+[
        x['lower'] for x in market.get('prior_rows',[])
        if x.get('side') in (-1,'resistance') and x.get('role') != 'transition']
    near = [v for v in levels if abs(bar['high']-v) <= max(2*tick,atr)]
    if (near and width > 0 and bar['high']-max(bar['open'],bar['close']) >= .35*width
            and bar['close'] <= bar['low']+.5*width):
        active['top_attempt'] = dict(candle=deepcopy(bar),level=min(near,key=lambda v:abs(v-bar['high'])),
                                     minimum_retreat=retreat)
    return '', dict(attempt=deepcopy(active.get('top_attempt')))


def evaluate(host,a,o,p,state):
    from .strategy_engine import AssignmentStatus as Status, _at_or_after_session_time
    from .signals import CapitalRequest, StrategyIntent
    old = state.get('historical_hod_state',{})
    state = deepcopy({k:v for k,v in state.items() if k != 'historical_hod_state'})
    d = dict(old)
    state['historical_hod_state'] = d
    now = o.observed_at.timestamp()
    session = o.observed_at.astimezone(H.NY).date().isoformat()
    if d.get('session') != session:
        d.clear(); d['session'] = session
        state.pop('pullback_used_swing',None)
        state.pop('pullback_last_exit',None)
    adapter,s,tick = p['historical_hod'],p['pullback_hod'],p['execution']['tick_size']
    rising = p['pullback_hod_contract'] == RISE_CONTRACT
    market = o.structural_detector_state or {}
    passive = market.get('historical_hod_observation') or {}
    if passive.get('session') == session and passive.get('observed_at') == now:
        fresh = ('bar_close' in o.evaluation_events and o.source_timeframe == '1s'
                 and passive.get('closed_at',0) > d.get('closed_at',0))
        d = dict(passive)
        if fresh: d['vwap'] = o.execution_vwap
        state['historical_hod_state'] = d
    else:
        fresh,_ = H.observe(o,d,adapter)
    row = market.get('row') or {}
    detector_fresh = bool(fresh and market.get('book',{}).get('version') in H.BOOK_VERSIONS
        and market.get('book',{}).get('fingerprint') and row.get('effective_at') == now)
    evidence = dict(contract=p['pullback_hod_contract'],historical_hod_reference=dict(hod=d.get('prior_hod'),at=now))
    active = state.get('pullback_entry') or {}
    macd_gate = macd_entry_gate(o) if s['entry_macd_10s_enabled'] else None
    if macd_gate is not None:evidence['entry_macd_10s']=macd_gate
    macd_blocked = macd_gate is not None and not macd_gate['passed']
    acquired = o.position_quantity > 0
    pending = a.status == Status.ENTRY_PENDING or bool(state.get('pending_capital_request'))
    behavior = p.get('strategy_behavior',{})
    flatten = _at_or_after_session_time(o.observed_at,behavior.get('flatten_time','15:55:00'))
    stop = state.get('active_stop') or 0.
    target = (state.get('structural_profit_targets') or [None])[0]
    def result(action,reason,status=None,**kw):
        metadata = dict(evidence,**kw.pop('metadata',{}))
        if action == 'exit':
            state.update(last_exit_reason=reason,entry_acquisition_exit_latched=True)
            state.pop('pending_capital_request',None)
            metadata.update(position_fraction=1.,cancel_entry_acquisition=True,
                            reentry_after_fill=reason != 'session_flatten' and a.permissions.reenter)
        # A partial fill is already a position, but its remaining acquisition
        # must stop when the entry gate closes. Keep all position protection.
        cancel_remaining = (action != 'exit' and acquired and active and macd_blocked
                            and not active.get('macd_acquisition_cancelled'))
        if cancel_remaining:
            active['macd_acquisition_cancelled']=True
            state.pop('pending_capital_request',None)
        output=host._result(a,o,action,reason,1. if action=='enter_long' else 0.,1.,state,
                            status or a.status,metadata=metadata,**kw)
        if cancel_remaining:
            cancel=StrategyIntent(intent_id=output.evaluation.signals[0].signal_id+'-macd-cancel',ticker=o.ticker,
                event_time=o.observed_at,action='cancel_entry',quantity=0,reference_price=o.price,
                reason=macd_gate['reason'],metadata={'assignment_id':a.assignment_id})
            return replace(output,evaluation=replace(output.evaluation,intents=(cancel,*output.evaluation.intents)))
        return output
    if a.status == Status.EXIT_PENDING or o.pending_exit_quantity > 0:
        remaining = max(0.,o.position_quantity-o.pending_exit_quantity)
        return (result('exit',state.get('last_exit_reason') or 'exit_pending',Status.EXIT_PENDING,quantity=remaining)
                if remaining else result('hold' if acquired else 'wait','exit_fill_pending',Status.EXIT_PENDING))
    local = o.observed_at.astimezone(H.NY)
    regular = bool(adapter['regular_luld_enabled']) and (9,30) <= (local.hour,local.minute) < (16,0)
    luld = H.regular_luld(o,adapter,tick,state.setdefault('backtest_luld_estimate',{})
        if adapter['backtest_luld_estimation_enabled'] else None) if regular else None
    if acquired:
        if rising:
            observe_advance(active,o,d,tick,fresh)
        reason = ('session_flatten' if flatten else 'manual_exit' if state.get('manual_exit_requested')
                  else 'protective_stop' if stop and o.price <= stop else '')
        if not reason and luld and not luld['lower_exit'] < o.bid < luld['price']:
            reason = 'luld_buffer_reached'
        if not reason:
            reason,detail = top_failure(active,d,s,tick,fresh)
            evidence['pullback_top'] = detail
        if reason:
            return result('exit',reason,Status.EXIT_PENDING,quantity=o.position_quantity,invalidation_price=stop)
        if detector_fresh and active:
            swing = H.initial_swing_low(row,dict(lower=o.bid),now,price_only=True,
                                       pivot_not_before=active.get('first_fill_at',now))
            proposed = H.stop_below(swing['lower'],adapter,tick) if swing else 0.
            if stop < proposed < o.bid:
                state['active_stop'] = proposed
                return result('replace_protective_stop','pullback_higher_low_trail',Status.MANAGING,
                    invalidation_price=proposed,profit_target_price=target,
                    metadata=dict(previous_stop=stop,active_stop=proposed,stop_swing=swing))
        return result('hold','pullback_structure_valid',Status.MANAGING,invalidation_price=stop,profit_target_price=target)
    if pending:
        invalid = (macd_blocked or flatten or not active or now-active.get('confirmed_at',0) >= adapter['confirmation_lifetime_ms']/1000
                   or o.price <= stop or o.ask > active.get('maximum_buy_price',0))
        if invalid:
            state.pop('pending_capital_request',None)
            reason=macd_gate['reason'] if macd_blocked else 'pullback_entry_expired'
            output = result('wait',reason,Status.WATCHING)
            cancel = StrategyIntent(intent_id=output.evaluation.signals[0].signal_id+'-cancel',ticker=o.ticker,
                event_time=o.observed_at,action='cancel_entry',quantity=0,reference_price=o.price,
                reason=reason,metadata={'assignment_id':a.assignment_id})
            return replace(output,evaluation=replace(output.evaluation,intents=(cancel,)))
        return result('wait','entry_fill_pending',Status.ENTRY_PENDING)
    if a.status in (Status.DISABLED,Status.PAUSED,Status.COMPLETED,Status.ERROR) or not a.permissions.observe or not a.permissions.enter or (state.get('entries',0) and not a.permissions.reenter):
        return result('wait','entry_permission_closed')
    phase = 'premarket' if (local.hour,local.minute)<(9,30) else 'regular' if local.hour<16 else 'after_hours'
    if flatten or not o.market_open or phase not in behavior.get('eligible_sessions',['premarket','regular']) or _at_or_after_session_time(o.observed_at,behavior.get('entry_cutoff_time','15:45:00')):
        return result('wait','outside_entry_session')
    if not detector_fresh:
        return result('wait','waiting_for_completed_pullback_structure')
    if macd_blocked:
        return result('wait',macd_gate['reason'])
    ready,quality = H.tradability(o,dict(p,structural_recovery=dict(H.QUALITY_DEFAULTS,**adapter)),row,state,producer_freshness=True)
    evidence['liquidity_admission'] = quality
    if not ready: return result('wait','liquidity_or_spread_gate')
    if regular and (not luld or not o.previous_close or o.previous_close < adapter['minimum_regular_previous_close']
                    or not luld['lower_exit'] < o.bid <= o.ask < luld['price']):
        return result('wait','regular_session_admission_gate')
    rvol = session_relative_volume.confirm((o.market_pressure or {}).get('session_relative_volume'),
        now=now,minimum_ratio=adapter['setup_minimum_session_relative_volume'])
    evidence['session_relative_volume'] = rvol
    if not rvol['passed']: return result('wait','session_rvol_gate')
    if adapter['setup_minimum_volume_ratio']:
        volume = trade_volume.confirm((o.market_pressure or {}).get('trade_volume'),now=now,
                                      minimum_ratio=adapter['setup_minimum_volume_ratio'])
        evidence['volume_confirmation'] = volume
        if not volume['passed']: return result('wait','completed_volume_gate')
    setup,reason = entry_setup(row,d,s,tick,state.get('pullback_used_swing'),
        rising=rising,last_exit=state.get('pullback_last_exit'))
    if rising:
        evidence['entry_cycle'] = dict(prior_exit=deepcopy(state.get('pullback_last_exit')),
            kind='pullback_reentry' if (state.get('pullback_last_exit') or {}).get('advanced') else 'initial_swing_rise')
    if reason: return result('wait',reason)
    evidence['pullback_setup'] = setup
    swing = setup['swing']
    stop = H.stop_below(swing['lower'],adapter,tick)
    if not H.stop_clears_quote(stop,o.bid,o.ask,adapter['setup_minimum_quote_clearance_spreads']):
        return result('wait','pullback_stop_inside_quote_noise')
    # Broker target remains above the pre-entry HOD to permit its breakout.
    selection = luld if regular else H.available_target(d.get('rows',[]),max(o.ask,setup['hod'])+tick,
                                                       adapter,tick,session=session)
    if not selection or selection['price'] <= max(o.ask,setup['hod']):
        return result('wait','pullback_overhead_target_unavailable')
    target = selection['price']
    ceiling = min(o.ask*(1+adapter['maximum_chase_bps']/10000),setup['hod'],target-tick)
    if not 0 < stop < o.bid <= o.ask <= ceiling:
        return result('wait','invalid_pullback_execution_geometry')
    active = dict(confirmed_at=now,maximum_buy_price=ceiling,setup=setup,stop=stop,target=selection)
    state.update(pullback_entry=active,pullback_used_swing=swing_key(swing),initial_stop=stop,active_stop=stop,
        structural_profit_targets=[target],entry_reference_price=o.ask,entry_at=o.observed_at.isoformat(),
        entries=state.get('entries',0)+1,last_exit_reason='',entry_acquisition_exit_latched=False)
    return result('enter_long',setup.get('entry_kind','bullish_candle_after_pullback_low'),Status.ENTRY_PENDING,
        invalidation_price=stop,profit_target_price=target,
        capital_request=CapitalRequest(mode='mandate_fraction' if adapter['sizing_mode']=='cash_tranches' else 'risk_fraction',
            value=adapter['cash_fraction'] if adapter['sizing_mode']=='cash_tranches' else adapter['risk_fraction'],
            maximum_quantity=adapter['maximum_quantity'],allow_replacement=False),
        order_intent={'execution_policy':'adaptive_urgent','protection_profile':'structural-single-target'},
        metadata=dict(initial_stop=stop,active_stop=stop,profit_targets=[target],profit_target=target,
            **({'cash_tranche':dict(key=f'{a.assignment_id}:{o.observed_at.isoformat()}',index=0,
                count=adapter['tranche_count'],initial_fraction=adapter['setup_initial_tranche_fraction'])}
               if adapter['sizing_mode']=='cash_tranches' else {}),
            mandatory_broker_target=True,maximum_buy_price=ceiling,wait_for_capital=False,
            initial_stop_selection=swing,profit_target_selection=selection,
            entry_quality=dict(quote_clearance_spreads=adapter['setup_minimum_quote_clearance_spreads'],
                               minimum_fee=1.,fee_per_share=.005,slippage_bps=5.,reward_cap_r=2.,minimum_reward_cost_multiple=2.)))
