"""Independent 1s historical/HOD breakouts with causal forming 5s MACD entry.

The policy consumes certified V6 snapshots and passive detector observations.
Position management never grants permission to acquire additional shares.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from math import ceil, floor, isfinite
from zoneinfo import ZoneInfo

from .structural_recovery import DEFAULTS as QUALITY_DEFAULTS, LIQUIDITY_181, tradability

CONTRACT = 'historical-hod-1s-macd-5s-1'
BOOK_VERSION = 'causal-swing-closing-book-6'
NY = ZoneInfo('America/New_York')
DEFAULTS = dict(stop_buffer_bps=5., target_offset_ticks=1., target_distance_fraction=.05, entry_breakout_offset=0.,
    management_tolerance_atr=.1, management_failure_closes=2, historical_hold_closes=2,
    maximum_macd_age_ms=5000., maximum_source_age_ms=2000., maximum_quote_age_ms=1000.,
    confirmation_lifetime_ms=1000., maximum_chase_bps=15.,
    minimum_candle_volume=1., risk_fraction=.005, maximum_quantity=10000.,
    sizing_mode='risk_fraction',cash_fraction=.9,tranche_count=3,
    recent_breakout_seconds=30., forming_macd_entry_enabled=1, early_green_stop_enabled=1,
    regular_luld_enabled=0,backtest_luld_estimation_enabled=0,minimum_regular_previous_close=.75,
    luld_buffer_bps=25.,luld_buffer_ticks=2,luld_maximum_age_ms=60000.)


def configure(p):
    if p.get('historical_hod_contract') != CONTRACT:
        raise ValueError('Unknown historical HOD contract')
    conflicts = ('macd_hod_contract','macd_threshold_contract','macd_r3_contract',
        'structural_recovery_contract','v5_breakout_contract','swing_gap_contract',
        'swing_evidence_contract','swing_momentum_contract')
    if any(p.get(k) for k in conflicts):
        raise ValueError('Historical HOD cannot compose another strategy policy')
    raw = p.get('historical_hod', {})
    if set(raw)-set(DEFAULTS):
        raise ValueError('Unknown historical HOD setting')
    s = dict(DEFAULTS, **raw)
    if s['sizing_mode'] not in {'risk_fraction','cash_tranches'}:
        raise ValueError('Unknown historical HOD sizing mode')
    if any(type(v) not in (int,float) or not isfinite(v) or (v < 0 if k in ('entry_breakout_offset','regular_luld_enabled','backtest_luld_estimation_enabled','forming_macd_entry_enabled','early_green_stop_enabled') else v <= 0) for k,v in s.items() if k != 'sizing_mode'):
        raise ValueError('Historical HOD settings must be finite and positive')
    if s['forming_macd_entry_enabled'] not in (0,1):
        raise ValueError('Invalid forming MACD entry policy')
    if s['early_green_stop_enabled'] not in (0,1):
        raise ValueError('Invalid early green stop policy')
    if s['regular_luld_enabled'] not in (0,1) or s['backtest_luld_estimation_enabled'] not in (0,1) or int(s['luld_buffer_ticks']) != s['luld_buffer_ticks']:
        raise ValueError('Invalid regular LULD policy')
    if not 0 < s['cash_fraction'] <= 1 or type(s['tranche_count']) is not int or not 2 <= s['tranche_count'] <= 20:
        raise ValueError('Invalid cash fraction or tranche count')
    if s['risk_fraction'] > 1 or s['confirmation_lifetime_ms'] > 1000 or s['maximum_macd_age_ms'] > 5000:
        raise ValueError('Invalid risk or completed-candle freshness limit')
    if any(int(s[k]) != s[k] for k in ('management_failure_closes','historical_hold_closes','target_offset_ticks')):
        raise ValueError('Candle counts and tick offsets must be integers')
    # Reuse the validated liquidity configuration, not the recovery strategy.
    from .structural_recovery import configure as configure_quality
    checked = deepcopy(p)
    checked['structural_recovery_contract'] = 'v6-structural-recovery-1'
    checked['liquidity_admission'] = dict(LIQUIDITY_181, **p.get('liquidity_admission', {}))
    configure_quality(checked)
    p['historical_hod'] = s
    p['liquidity_admission'] = checked['liquidity_admission']
    p['structural_detector_settings'] = checked['structural_detector_settings']
    p['entry_candle_confirmation']['enabled'] = False
    p['structural_entry']['enabled'] = False
    p.setdefault('entry', {})['breakout_timeframe'] = '1s'
    p['protection']['trailing']['enabled'] = False
    p['protection']['profit_ladder'].update(enabled=True, fixed_at_entry=False)
    p['momentum_management']['macd_backstop']['enabled'] = False


def regular_luld(o, s, tick, estimate_state=None):
    """Prefer official SIP evidence; backtests may supply a research estimate.

    The producer supplies the original effective time and availability time;
    projecting an old band into a new candle must not refresh either timestamp.
    Live observation adapters never supply the backtest reference input.
    """
    band = o.official_luld_band
    now = o.observed_at.timestamp()*1000
    session = o.observed_at.astimezone(NY).date().isoformat()
    if (not isinstance(band,dict) or band.get('source') != 'sip'
            or band.get('session_date') != session
            or any(type(band.get(k)) not in (int,float) or not isfinite(band[k])
                for k in ('lower','upper','effective_at_ms','available_at_ms'))
            or not 0 < band['lower'] < band['upper']
            or not band['effective_at_ms'] <= band['available_at_ms'] <= now
            or not 0 <= now-band['effective_at_ms'] <= s['luld_maximum_age_ms']):
        if estimate_state is None:
            return None
        from .estimated_luld import estimate
        band = estimate(o, estimate_state)
        if not band:
            return None
    buffer = max(band['upper']*s['luld_buffer_bps']/10000,
        tick*s['luld_buffer_ticks'],max(0.,o.ask-o.bid))
    lower_buffer = max(band['lower']*s['luld_buffer_bps']/10000,
        tick*s['luld_buffer_ticks'],max(0.,o.ask-o.bid))
    return dict(price=floor((band['upper']-buffer)/tick+1e-9)*tick,
        lower_exit=ceil((band['lower']+lower_buffer)/tick-1e-9)*tick,
        selection_method='estimated_luld' if band['source'] == 'estimated' else 'official_luld',band=deepcopy(band),buffer=buffer)


def historical(level, session):
    stamp = level.get('oldest_member_confirmed_at_ms')
    # V6 must publish member lineage; newest confirmation loses mixed ancestry.
    return stamp is not None and datetime.fromtimestamp(stamp/1000, NY).date().isoformat() < session


def band_levels(rows):
    """Recover the original V6 bands from the shared point-price projection."""
    return [dict(r,lower=r.get('band_lower',r['lower']),upper=r.get('band_upper',r['upper']),
        strategy_level_contract=CONTRACT) for r in rows]


def selected_levels(o, s, before):
    result = []
    for raw in band_levels((*o.structural_support_levels, *o.structural_resistance_levels)):
        if (raw.get('book_version') != BOOK_VERSION or raw.get('lifecycle') not in ('active',None)
                or raw.get('confirmed_at_ms', float('inf')) > before*1000):
            continue
        if (not raw.get('unified_level_id') or any(type(raw.get(k)) not in (int,float)
                or not isfinite(raw[k]) for k in ('lower','price','upper','confirmed_at_ms','oldest_member_confirmed_at_ms'))
                or not 0 < raw['lower'] <= raw['price'] <= raw['upper']
                or not 0 < raw['oldest_member_confirmed_at_ms'] <= raw['confirmed_at_ms']):
            raise ValueError('Historical HOD requires valid V6 bands and historical member provenance')
        result.append(deepcopy(raw))
    return result


def resistance(level):
    return level.get('side') in (-1, 'resistance')


def entry_level(rows, hod, session):
    below = [r for r in rows if resistance(r) and r['upper'] <= hod]
    old = [r for r in below if historical(r,session)]
    if old or below:
        return deepcopy(max(old or below, key=lambda r:(r['upper'],r['price'])))
    return dict(lower=hod, upper=hod, price=hod, reference_kind='hod')


def stop_below(value, s, tick):
    return floor((value-max(tick,value*s['stop_buffer_bps']/10000))/tick+1e-9)*tick


def initial_swing_low(row, boundary, now):
    candidates = []
    for level in row.get('local_swings',[]) + row.get('confirmed_swings',[]):
        if (level.get('side') not in (1,'support') or level.get('state','active') != 'active'
                or any(type(level.get(k)) not in (int,float) or not isfinite(level[k])
                       for k in ('lower','price','upper','pivot_at','confirmed_at'))):
            continue
        if (0 < level['lower'] <= level['price'] <= level['upper'] < boundary['lower']
                and 0 < level['pivot_at'] <= level['confirmed_at'] <= now):
            candidates.append(level)
    return deepcopy(max(candidates,key=lambda l:(l['pivot_at'],l['confirmed_at'],l['price']))) if candidates else None


def next_historical_resistance(rows, price, session):
    above = [r for r in rows if resistance(r) and historical(r, session) and r['lower'] > price]
    return min(above, key=lambda r:(r['lower'],r['price'])) if above else None


def target_selection(rows, broken, price, s, tick, *, session, minimum_target=0.):
    reference = broken['price']*(1+s['target_distance_fraction'])
    def placement(r):
        offset = s['target_offset_ticks']*tick
        return (ceil((r['upper']+offset)/tick-1e-9)*tick if r['price'] < reference
            else floor((r['lower']-offset)/tick+1e-9)*tick)
    eligible = [r for r in rows if resistance(r) and historical(r,session) and r['lower'] > broken['upper']
        and placement(r) > price]
    if not eligible:
        return None
    level = min(eligible, key=lambda r:(abs(r['price']-reference),r['price']))
    target = placement(level)
    if target <= minimum_target+tick/2:
        return None
    return dict(price=target, level=deepcopy(level), reference=reference, broken_level=deepcopy(broken),
        trigger_level=deepcopy(broken),
        placement='above_upper_band' if level['price'] < reference else 'below_lower_band',
        selection_method='resistance_nearest_five_percent_above_broken_level')


def resistance_attempts(active, bar, previous_bar, levels):
    """Freeze bands for contiguous candle encounters, including multi-bar retests."""
    contiguous = previous_bar and previous_bar['end'] == bar['time']
    saved = active.get('resistance_attempts', {})
    previous = saved.get('levels', []) if contiguous and saved.get('at') == previous_bar['end'] else []
    def key(level):
        return (level.get('unified_level_id'), level.get('level_id'), level.get('scale'),
                level.get('pivot_at')) if level.get('unified_level_id') or level.get('level_id') is not None else (
                    level['lower'], level['upper'])
    frozen = {key(a['level']): deepcopy(a) for a in previous}
    if contiguous:
        for level in levels:
            known = level.get('confirmed_at', level.get('confirmed_at_ms', float('inf'))/1000)
            if (resistance(level) and known <= previous_bar['end']
                    and previous_bar['high'] >= level['lower'] and previous_bar['low'] <= level['upper']):
                frozen.setdefault(key(level), dict(level=deepcopy(level), broken=False))
        for attempt in frozen.values():
            attempt['broken'] |= previous_bar['close'] > attempt['level']['upper']
    attempts = list(frozen.values()) if contiguous else []
    # Keep only the encounter still touching this candle. Departed levels cannot
    # turn an unrelated later red candle into a failed attempt.
    ongoing = {}
    for attempt in attempts:
        level = attempt['level']
        if bar['high'] >= level['lower'] and bar['low'] <= level['upper']:
            updated = deepcopy(attempt)
            updated['broken'] |= bar['close'] > level['upper']
            ongoing[key(level)] = updated
    for level in levels:
        known = level.get('confirmed_at', level.get('confirmed_at_ms', float('inf'))/1000)
        if (resistance(level) and known <= bar['end']
                and bar['high'] >= level['lower'] and bar['low'] <= level['upper']):
            ongoing.setdefault(key(level), dict(level=deepcopy(level),broken=bar['close'] > level['upper']))
    active['resistance_attempts'] = dict(at=bar['end'], levels=list(ongoing.values()))
    return attempts


def management(row, active, bar, s, tick, *, previous_bar=None, resistance_levels=()):
    """Warnings need subsequent price failure; the broker stop is independent."""
    events = row.get('local_events', [])+row.get('global_events', [])
    session = datetime.fromtimestamp(bar['end'], NY).date().isoformat()
    attempts = resistance_attempts(active, bar, previous_bar,
        [level for level in resistance_levels if historical(level, session)])
    if (previous_bar and previous_bar['end'] == bar['time']
            and bar['close'] < bar['open'] and bar['close'] < previous_bar['open']
            and bar['low'] < previous_bar['low']):
        for attempt in attempts:
            level = attempt['level']
            # A close inside the band is a retest, not a failed resistance.
            if historical(level, session) and bar['close'] < level['lower']:
                active['failed_resistance_exit'] = dict(level=deepcopy(level),
                    previous_bar=deepcopy(previous_bar),exit_bar=deepcopy(bar),
                    attempt_kind='failed_breakout' if attempt['broken'] else 'rejection')
                return 'red_close_below_attempt_open'
    atr = row.get('qualification', {}).get('atr') or 0.
    base = active['management_base']
    for event in row.get('local_events', []):
        level = event.get('level', {})
        if (event.get('state') == 'higher_low_confirmed' and level.get('lower',0) > base['lower']
                and level.get('confirmed_at',0) > active['confirmed_at']):
            base = dict(lower=level['lower'], tolerance=max(tick,s['management_tolerance_atr']*atr))
            active['management_base'] = base
            active['failure_closes'] = 0
    below = bar['close'] < base['lower']-base['tolerance']
    active['failure_closes'] = active.get('failure_closes',0)+1 if below else 0
    if active['failure_closes'] >= s['management_failure_closes']:
        return 'protective_swing_failed'
    if below and any(e.get('direction') == 'bearish' and e.get('outcome') == 'structural_reversal_confirmation'
            for e in row.get('volume_analysis',{}).get('reversal_outcomes', [])):
        return 'confirmed_structural_reversal'
    rejection = active.get('rejection')
    if rejection and (not historical(rejection.get('level', {}), session) or bar['close'] > rejection['upper']):
        active.pop('rejection',None)
        rejection = None
    if rejection:
        for e in row.get('local_events', []):
            level = e.get('level', {})
            if (e.get('state') == 'lower_high_confirmed' and level.get('confirmed_at',0) > rejection['at']
                    and level.get('pivot_at',0) > rejection['at']
                    and level.get('price',float('inf')) < rejection['upper']):
                rejection['failed_high'] = level['price']
        if (rejection.get('failed_high') and bar['close'] < rejection['reaction_low']-rejection['tolerance']):
            return 'resistance_rejection_failed_recovery'
        if not rejection.get('failed_high'):
            rejection['reaction_low'] = min(rejection['reaction_low'],bar['low'])
    if not rejection:
        for e in events:
            level = e.get('level', {})
            if (e.get('state') in ('rejection','failed_breakout') and resistance(level) and historical(level, session)
                    and level.get('upper',0) >= bar['close'] and bar['high'] >= level.get('lower',float('inf'))):
                active['rejection'] = dict(level=deepcopy(level),at=bar['end'],upper=level['upper'],reaction_low=bar['low'],
                    tolerance=max(tick,s['management_tolerance_atr']*atr))
                break
    return ''


def forming_macd(o, d):
    """Preview one 12/26/9 EMA step without committing the forming candle.

    Recover the hidden slow EMA from two consecutive authoritative MACD lines:
    L[t] = (1-a_fast)*L[t-1] + (a_fast-a_slow)*(close[t]-slow[t-1]).
    This preserves QMD's warmup rather than reseeding EMAs at strategy admission.
    Only completed 5s samples replace the base; 1s previews never compound it.
    """
    now = o.observed_at.timestamp()
    base = d.get('completed_macd') or {}
    if o.source_timeframe == '5s':
        current = dict(at=now,line=o.macd_line,signal=o.macd_signal)
        if (now-base.get('at',0) == 5 and all(v is not None and isfinite(v)
                for v in (base.get('line'),o.macd_line,o.macd_signal,o.price)) and o.price > 0):
            af, slow_alpha = 2/13, 2/27
            previous_slow = o.price-(o.macd_line-(1-af)*base['line'])/(af-slow_alpha)
            current['slow'] = slow_alpha*o.price+(1-slow_alpha)*previous_slow
        d['completed_macd'] = current
        return
    age = now-base.get('at',0)
    if age == 0:
        # At a shared close timestamp the completed 5s frame may arrive first.
        return dict(at=now,line=base.get('line'),signal=base.get('signal'),kind='completed',base_at=now)
    if not 0 < age <= 5 or 'slow' not in base or not isfinite(o.price) or o.price <= 0:
        return dict(at=now,line=None,signal=None,kind='forming_unavailable',base_at=base.get('at'))
    fast = 2/13*o.price+11/13*(base['slow']+base['line'])
    slow = 2/27*o.price+25/27*base['slow']
    line = fast-slow
    signal = .2*line+.8*base['signal']
    return dict(at=now,line=line,signal=signal,kind='forming',base_at=base['at'])


def observe(o, d, s):
    now = o.observed_at.timestamp()
    if 'bar_close' not in o.evaluation_events:
        return False, False
    macd_closed = False
    if o.source_timeframe == '5s' and now > d.get('completed_macd',{}).get('at',0):
        forming_macd(o,d)
        valid = all(v is not None and isfinite(v) for v in (o.macd_line,o.macd_signal))
        positive = valid and o.macd_line > o.macd_signal
        was_open = d.get('episode') is not None
        if positive and not was_open:
            d.update(episode=now,body_high=0.,used_episode=False,
                breakout_upper=None,failed_breakout=False)
        elif valid and not positive:
            d['episode'] = None
        d.update(macd_at=now,macd_valid=valid,macd_positive=positive,macd_kind='completed',
            macd_line=o.macd_line,macd_signal=o.macd_signal)
        macd_closed = valid and not positive
    if o.source_timeframe != '1s' or now <= d.get('closed_at',0):
        return False, macd_closed
    if any(v is None or not isfinite(v) for v in (o.bar_open,o.bar_low,o.bar_high)):
        return False, macd_closed
    if s.get('forming_macd_entry_enabled',1):
        preview = forming_macd(o,d)
        valid = all(v is not None and isfinite(v) for v in (preview['line'],preview['signal']))
        positive = valid and preview['line'] > preview['signal']
        if positive and d.get('episode') is None:
            d.update(episode=now,body_high=0.,used_episode=False,
                breakout_upper=None,failed_breakout=False)
        # A forming reversal blocks entries but does not issue an early exit.
        d.update(macd_at=now,macd_valid=valid,macd_positive=positive,
            macd_line=preview['line'],macd_signal=preview['signal'],macd_kind=preview['kind'])
    contiguous = now-d.get('closed_at',0) == 1
    d['prior_close'] = d.get('close') if contiguous else None
    # Candle structure is continuous across MACD episode/entry boundaries.
    # Replace the bounded list so passive snapshots remain immutable.
    green = (contiguous and o.price > o.bar_open and o.price > d['prior_close'])
    d['rising_green_run'] = ((d.get('rising_green_run', []) +
        [dict(end=now,open=o.bar_open,close=o.price)])[-3:] if green else [])
    # Completed bars and certified level snapshots are read-only evidence.
    # Advance their references; only the episode's scalar fields change.
    d['prior_bar'] = d.get('bar') if contiguous else None
    d['prior_rows'] = d.get('rows', []) if contiguous else []
    d['prior_hod'] = d.get('hod')
    d['prior_body_high'] = d.get('body_high',0.)
    # Observe the price event even while MACD/admission is closed. Retain only
    # the current selected boundary and invalidate it on loss of its threshold.
    recent = d.get('recent_breakout')
    if recent and (not contiguous or now-recent['at'] > s.get('recent_breakout_seconds',30.)
            or (o.price < recent['threshold'] if recent['inclusive'] else o.price <= recent['threshold'])):
        d.pop('recent_breakout',None)
    if contiguous and d.get('prior_hod'):
        boundary = entry_level(d['prior_rows'],d['prior_hod'],d['session'])
        offset = s['entry_breakout_offset']
        threshold = round(boundary['upper']+offset,9) if offset else boundary['upper']
        crossed = (round(d['prior_close'],9) < threshold <= round(o.price,9) if offset
            else d['prior_close'] <= threshold < o.price)
        if crossed and o.price >= o.bar_open and boundary.get('unified_level_id'):
            d['recent_breakout'] = dict(at=now,level_id=boundary.get('unified_level_id'),
                level=deepcopy(boundary),threshold=threshold,inclusive=bool(offset))
    if d.get('episode') is not None:
        # Observe attempts before admission gates, including before assignment.
        # A rejected excursion cannot become a new first entry at the old band.
        upper = d.get('breakout_upper')
        if upper is not None and o.price <= upper:
            d['failed_breakout'] = True
        if contiguous and d.get('prior_hod'):
            boundary = entry_level(d['prior_rows'],d['prior_hod'],d['session'])
            if d['prior_close'] <= boundary['upper'] < o.price and o.price >= o.bar_open:
                d['breakout_upper'] = boundary['upper']
        d['body_high'] = max(d.get('body_high',0.),o.bar_open,o.price)
    d['hod'] = max(d.get('hod',0.),o.bar_high,o.structural_session_high or 0.)
    d.update(closed_at=now,close=o.price,rows=selected_levels(o,s,now),contiguous=contiguous,
        bar=dict(time=now-1,end=now,open=o.bar_open,high=o.bar_high,low=o.bar_low,
            close=o.price,volume=o.bar_volume),vwap=o.execution_vwap)
    return True, macd_closed


def observe_frame(frame, saved, parameters, snapshot=None):
    """Maintain episode history before discovery creates a trade assignment."""
    from types import SimpleNamespace
    d = dict(saved)
    session = frame.as_of.astimezone(NY).date().isoformat()
    if d.get('session') != session:
        d = {'session':session}
    snapshot = snapshot or {}
    o = SimpleNamespace(observed_at=frame.as_of, source_timeframe=frame.timeframe,
        evaluation_events=('bar_close',), macd_line=frame.indicator.get('macd_line'),
        macd_signal=frame.indicator.get('macd_signal'), price=frame.bar['close'],
        bar_open=frame.bar['open'],bar_low=frame.bar['low'],bar_high=frame.bar['high'],
        bar_volume=frame.bar.get('volume'),structural_support_levels=(),
        structural_resistance_levels=tuple(snapshot.get('unified_levels', [])),
        structural_session_high=frame.indicator.get('qmd_structure_session_high') or snapshot.get('session_high'),
        execution_vwap=frame.indicator.get('execution_vwap'))
    observe(o,d,parameters.get('historical_hod',DEFAULTS))
    d['observed_at'] = frame.as_of.timestamp()
    return d


def confirm_failed_attempt(active, o, *, previous_bar=None):
    """Confirm a failed encounter with two consecutive completed red 1s bars."""
    pending = active.get('pending_failed_attempt')
    if not pending or o.source_timeframe != '1s' or 'bar_close' not in o.evaluation_events:
        return False
    session = o.observed_at.astimezone(NY).date().isoformat()
    level = active.get('failed_resistance_exit', {}).get('level', {})
    if not historical(level, session):
        active.pop('pending_failed_attempt', None)
        return False
    now_ms = round(o.observed_at.timestamp()*1000)
    if now_ms <= pending.get('last_bar_ms', pending['at_ms']):
        return False
    if not all(isfinite(v) and v > 0 for v in (o.price, o.bar_open)):
        return False
    # Reclaiming the failed band's floor cancels the encounter, rather than
    # letting an unrelated later red sequence confirm an obsolete failure.
    if o.price >= level['lower']:
        active.pop('pending_failed_attempt', None)
        active.pop('failed_resistance_exit', None)
        return False
    previous_ms = pending.get('last_bar_ms', pending['at_ms'])
    count = pending.get('red_closes', 1) if now_ms - previous_ms == 1000 else 0
    pending['last_bar_ms'] = now_ms
    pending['red_closes'] = count + 1 if o.price < o.bar_open else 0
    if (pending['red_closes'] < 2 or not previous_bar
            or previous_bar['end'] != o.observed_at.timestamp()-1
            or not isfinite(o.bar_low) or o.bar_low <= 0
            or o.bar_low >= previous_bar['low'] or o.price >= previous_bar['open']):
        return False
    active.pop('pending_failed_attempt', None)
    active['failed_resistance_exit']['candle_confirmation'] = dict(
        timeframe='1s', consecutive_red_closes=2,
        trigger_at_ms=pending['at_ms'], trigger_close=pending['trigger_close'],
        open=o.bar_open, close=o.price, confirmed=True, confirmed_at_ms=now_ms)
    return True


def green_stop_candidate(d, tick):
    sequence = d.get('rising_green_run', [])
    if len(sequence) != 3 or sequence[-1]['end'] != d.get('closed_at'):
        return None
    return dict(price=floor(sequence[1]['close']/tick+1e-9)*tick,
        second_close=sequence[1]['close'],confirmed_at=sequence[-1]['end'],candles=deepcopy(sequence))


def early_green_stop(active, d, tick):
    """Three rising green closes; bodies may overlap. Retire at next break."""
    bar = d['bar']
    if active.get('early_green_graduated'):
        return
    if any(resistance(r) and r['upper'] > active['level']['upper']
           and d.get('prior_close') is not None and d['prior_close'] <= r['upper'] < bar['close']
           and bar['close'] >= bar['open'] for r in d.get('prior_rows', [])):
        active['early_green_graduated'] = True
        return
    if active.get('early_green_stop'):
        return
    candidate = green_stop_candidate(d,tick)
    if candidate:
        active['early_green_stop'] = candidate
        active['desired_stop'] = max(active.get('desired_stop', 0), candidate['price'])


def record_early_stop_fill(state, filled_at, fill_role):
    """Only an actual fill of the still-active initial stop grants reentry."""
    active = state.get('historical_hod_entry') or {}
    early = active.get('early_green_stop') or {}
    # Crossing a resistance retires pattern arming, not this stop's identity.
    # A hold may fail (or a replacement be rejected), leaving the original
    # three-candle stop in force and its reclaim permission still applicable.
    if (not early
            or state.get('active_stop') != early['price']
            or not (fill_role in {'protective_stop', 'trailing_stop', 'protective_exit'}
                    or fill_role == 'managed_exit' and state.get('last_exit_reason') == 'protective_stop')):
        return
    state.setdefault('early_stop_reentry', dict(price=early['price'], episode=active['episode'],
        stopped_at=filled_at.timestamp(), below_seen=True, level=deepcopy(active['level']),
        early_green_stop=deepcopy(early)))


def early_reentry_confirmation(saved, d, o, fresh, row, detector_fresh):
    """Close-cross followed by the immediately next forming 1s candle's open."""
    now = o.observed_at.timestamp()
    if min(o.price,o.bid) <= saved['price']:
        saved['below_seen'] = True
    if fresh:
        saved.pop('confirmation', None)
        if (now > saved['stopped_at'] and detector_fresh and d.get('contiguous')
                and saved.get('below_seen') and saved['price'] < o.price and o.price >= o.bar_open):
            saved['confirmation'] = dict(at=now, close=o.price, row=deepcopy(row))
            saved['below_seen'] = False
        return None
    confirmation = saved.get('confirmation')
    if (not confirmation or 'market_data_update' not in o.evaluation_events
            or o.bar_open is None or not isfinite(o.bar_open)):
        return None
    if not confirmation['at'] <= now < confirmation['at']+1:
        saved.pop('confirmation', None)
        return None
    # Consume the opening opportunity even if price or other gates reject it.
    saved.pop('confirmation', None)
    if o.bar_open <= saved['price'] or min(o.price,o.bid) <= saved['price']:
        return None
    return dict(confirmation, next_open=o.bar_open, validated_at=now, stop_level=saved['price'])


def entry_reference(d, o, s, session):
    """One selected boundary shared by entry evaluation and chart evidence."""
    hod, previous = d.get('prior_hod'), d.get('prior_close')
    if not hod or previous is None:
        return None
    boundary = entry_level(d['prior_rows'],hod,session)
    require_body_high = bool(d.get('used_episode') or d.get('failed_breakout'))
    offset = s['entry_breakout_offset']
    recent = d.get('recent_breakout') or {}
    if (not require_body_high and recent.get('level')
            and 0 <= o.observed_at.timestamp()-recent['at'] <= s.get('recent_breakout_seconds',30.)
            and recent['level']['upper'] <= hod
            and (min(o.price,o.bid) >= recent['threshold'] if recent['inclusive']
                 else min(o.price,o.bid) > recent['threshold'])):
        current_threshold = round(boundary['upper']+offset,9) if offset else boundary['upper']
        current_cross = (round(previous,9) < current_threshold <= round(o.price,9) if offset
            else previous <= current_threshold < o.price)
        if not current_cross:
            boundary = deepcopy(recent['level'])
    resistance_threshold = round(boundary['upper']+offset,9) if offset else boundary['upper']
    threshold = max(resistance_threshold,d['prior_body_high']) if require_body_high else resistance_threshold
    return dict(level=boundary,prior_hod=hod,threshold=threshold,
        resistance_threshold=resistance_threshold,reentry=bool(d.get('used_episode')),
        failed_breakout=bool(d.get('failed_breakout')),entry_breakout_offset=offset)


def evaluate(host, a, o, p, state):
    from .strategy_engine import AssignmentStatus as Status, _at_or_after_session_time
    from .signals import CapitalRequest, StrategyIntent
    # Copy mutable position-management state, but retain the completed market
    # evidence. Copying both full level books on every quote is unnecessary;
    # observe() replaces these snapshots and never mutates their members.
    previous_market = state.get('historical_hod_state', {})
    state = deepcopy({k:v for k,v in state.items() if k!='historical_hod_state'})
    state['historical_hod_state'] = dict(previous_market)
    s = p['historical_hod']; tick = p['execution']['tick_size']; now = o.observed_at.timestamp()
    d = state.setdefault('historical_hod_state', {})
    session = o.observed_at.astimezone(NY).date().isoformat()
    if d.get('session') != session:
        d.clear(); d['session'] = session
    passive = (o.structural_detector_state or {}).get('historical_hod_observation',{})
    if passive.get('observed_at') == now and passive.get('session') == session:
        fresh = o.source_timeframe == '1s' and passive.get('closed_at',0) > d.get('closed_at',0)
        macd_closed = (o.source_timeframe == '5s' and passive.get('completed_macd',{}).get('at',0) > d.get('completed_macd',{}).get('at',0)
            and passive.get('macd_valid') and not passive.get('macd_positive'))
        used = d.get('used_episode',False) if d.get('episode') == passive.get('episode') else False
        d = dict(passive)
        d['used_episode'] = used
        if 'chart_reference' in previous_market:
            d['chart_reference'] = previous_market['chart_reference']
        # Use the runtime's resolved execution VWAP for the current 1s candle.
        if fresh:
            d['vwap'] = o.execution_vwap
        state['historical_hod_state'] = d
    else:
        fresh, macd_closed = observe(o,d,s)
    saved_reentry = state.get('early_stop_reentry')
    if saved_reentry and (not s['early_green_stop_enabled'] or saved_reentry['episode'] != d.get('episode')):
        state.pop('early_stop_reentry', None)
        saved_reentry = None
    active = state.get('historical_hod_entry') or {}
    stop = float(state.get('active_stop') or 0)
    target = float((state.get('structural_profit_targets') or [0])[0])
    acquired = o.position_quantity > 0
    local_clock = o.observed_at.astimezone(NY)
    regular = bool(s['regular_luld_enabled']) and (9,30) <= (local_clock.hour,local_clock.minute) < (16,0)
    luld = regular_luld(o,s,tick,state.setdefault('backtest_luld_estimate',{}) if s['backtest_luld_estimation_enabled'] else None) if regular else None
    prior_close = o.previous_close
    regular_block = ('regular_previous_close_unavailable' if prior_close is None or not isfinite(prior_close) or prior_close <= 0
        else 'regular_previous_close_below_minimum' if prior_close < s['minimum_regular_previous_close']
        else 'official_luld_unavailable' if not luld else
        'inside_luld_buffer' if not luld['lower_exit'] < o.bid <= o.ask < luld['price'] else '') if regular else ''
    pending = a.status == Status.ENTRY_PENDING or bool(state.get('pending_capital_request'))
    evidence = dict(contract=CONTRACT,macd=dict(timeframe='5s',observed_at=d.get('macd_at'),
        line=d.get('macd_line'),signal=d.get('macd_signal'),episode=d.get('episode'),
        kind=d.get('macd_kind'),completed_base_at=d.get('completed_macd',{}).get('at')))
    reference = entry_reference(d,o,s,session) if fresh else None
    if fresh:
        chart_reference = dict(hod=reference['prior_hod'],
            resistance_upper=reference['level']['upper'] if reference['level'].get('reference_kind')!='hod' else None,
            threshold=reference['threshold'],level_id=reference['level'].get('unified_level_id')) if reference else dict(hod=None,resistance_upper=None,threshold=None,level_id=None)
        prior_reference = previous_market.get('chart_reference')
        d['chart_reference'] = chart_reference
        evidence['historical_hod_reference'] = dict(chart_reference,at=now,changed=chart_reference!=prior_reference)
    if regular:
        evidence['regular_session_policy'] = dict(previous_close=prior_close,block_reason=regular_block,luld=luld)
    def result(action, reason, status=None, **kw):
        metadata = dict(evidence, **kw.pop('metadata', {}))
        if action == 'exit':
            metadata.update(reentry_after_fill=reason != 'session_flatten' and a.permissions.reenter,
                cancel_entry_acquisition=True,position_fraction=1.)
        return host._result(a,o,action,reason,1. if action=='enter_long' else 0.,1.,state,status or a.status,
            metadata=metadata,**kw)
    if acquired and d.get('episode') is not None:
        d['used_episode'] = True
    if a.status == Status.EXIT_PENDING or o.pending_exit_quantity > 0:
        if acquired and o.position_quantity > o.pending_exit_quantity:
            return result('exit',state.get('last_exit_reason') or 'exit_pending',Status.EXIT_PENDING,quantity=o.position_quantity)
        return result('hold' if acquired else 'wait','exit_fill_pending',Status.EXIT_PENDING)
    if not acquired and not pending and active:
        state.pop('historical_hod_entry',None); active = {}
    flatten = _at_or_after_session_time(o.observed_at,p.get('strategy_behavior',{}).get('flatten_time','15:55:00'))
    market = o.structural_detector_state or {}
    row = market.get('row', {})
    detector_fresh = (fresh and market.get('book',{}).get('version') == BOOK_VERSION
        and bool(market.get('book',{}).get('fingerprint')) and row.get('effective_at') == now)
    reclaim = (early_reentry_confirmation(saved_reentry,d,o,fresh,row,detector_fresh)
        if saved_reentry and not acquired and not pending else None)
    if acquired and active and fresh:
        # Track the completed sequence before a resistance failure arms an exit.
        sequence = active.setdefault('red_candle_sequence', {})
        bar = d['bar']
        if bar['end'] > sequence.get('end', 0):
            consecutive = sequence.get('end') == bar['time'] and not row.get('gap_before')
            sequence.update(end=bar['end'], count=(sequence.get('count', 0) + 1 if consecutive else 1)
                if bar['close'] < bar['open'] else 0)
    if acquired or pending:
        reason = ('session_flatten' if flatten else 'protective_stop' if stop and o.price <= stop
            else 'manual_exit' if state.get('manual_exit_requested') else 'macd_episode_ended' if macd_closed else '')
        if not reason and luld and (o.bid >= luld['price'] or o.bid <= luld['lower_exit']):
            reason = 'luld_buffer_reached'
        if not reason and acquired and active and confirm_failed_attempt(active,o,previous_bar=d.get('prior_bar')):
            reason = 'red_close_below_attempt_open'
        if not reason and acquired and active and detector_fresh:
            if not d['contiguous'] or row.get('gap_before'):
                active['failure_closes'] = 0; active.pop('rejection',None)
            else:
                reason = management(row,active,d['bar'],s,tick,
                    previous_bar=d.get('prior_bar'),
                    resistance_levels=(*d.get('prior_rows',[]), *d.get('rows',[])))
                if reason == 'red_close_below_attempt_open':
                    count = active.get('red_candle_sequence', {}).get('count', 0)
                    if count >= 2:
                        active.pop('pending_failed_attempt', None)
                        active['failed_resistance_exit']['candle_confirmation'] = dict(
                            timeframe='1s', consecutive_red_closes=count,
                            confirmed=True, confirmed_at_ms=round(now*1000),
                            open=d['bar']['open'], close=d['bar']['close'])
                    else:
                        active.setdefault('pending_failed_attempt', dict(at_ms=round(now*1000),
                            trigger_close=d['bar']['close'], red_closes=count))
                        reason = ''
        if reason:
            state.update(last_exit_reason=reason,entry_acquisition_exit_latched=True)
            state.pop('pending_capital_request',None)
            return result('exit',reason,Status.EXIT_PENDING,quantity=o.position_quantity,invalidation_price=stop,
                metadata={'failed_resistance_exit':active.get('failed_resistance_exit')} if reason=='red_close_below_attempt_open' else {})
    macd_ready = (d.get('macd_valid') and d.get('macd_positive') and d.get('episode') is not None
        and 0 <= (now-d.get('macd_at',0))*1000 <= s['maximum_macd_age_ms'])
    quality_p = dict(p,structural_recovery=dict(QUALITY_DEFAULTS,**{k:v for k,v in s.items() if k in QUALITY_DEFAULTS}))
    ready, quality = tradability(o,quality_p,dict(effective_at=d.get('closed_at',0),candle=d.get('bar',{})),state,producer_freshness=True)
    evidence['liquidity_admission'] = quality
    if pending and (state.get('pending_capital_request') or regular_block) and (regular_block or not active or not ready or not macd_ready or o.price <= (d.get('vwap') or float('inf'))
            or now-active.get('confirmed_at',0) >= s['confirmation_lifetime_ms']/1000
            or o.ask > active.get('maximum_buy_price',0)):
        state.pop('pending_capital_request',None)
        base = result('hold' if acquired else 'wait','entry_acquisition_invalidated',Status.MANAGING if acquired else Status.WATCHING)
        cancel = StrategyIntent(intent_id=base.evaluation.signals[0].signal_id,ticker=o.ticker,event_time=o.observed_at,
            action='cancel_entry',quantity=0,reference_price=o.price,reason='entry_acquisition_invalidated',metadata={'assignment_id':a.assignment_id})
        return replace(base,evaluation=replace(base.evaluation,intents=(cancel,)))
    if acquired:
        if not active:
            return result('hold','position_context_missing',Status.MANAGING,invalidation_price=stop)
        if not active.get('fill_risk_frozen') and not pending and o.average_price > 0:
            active['initial_risk'] = o.average_price-active['stop']
            active['fill_risk_frozen'] = True
        if fresh:
            active.pop('desired_target',None)
            if s['early_green_stop_enabled']:
                early_green_stop(active,d,tick)
            previous = d.get('prior_close')
            crossed = [r for r in d.get('prior_rows',[]) if resistance(r) and previous is not None and previous <= r['upper'] < o.price]
            add_breaks = active.setdefault('add_breaks',{})
            if not d['contiguous']:
                add_breaks.clear()
            frontier = active.get('last_add_level',active['level'])
            for r in crossed:
                if r['upper'] > frontier['upper'] and r['price'] > frontier['price']:
                    add_breaks[str(r['unified_level_id'])] = deepcopy(r)
            for key,r in list(add_breaks.items()):
                if o.price <= r['upper'] or r['upper'] <= frontier['upper']:
                    del add_breaks[key]
            pending_levels = active.setdefault('hold_levels',{})
            cleared = active.setdefault('last_cleared_resistance',deepcopy(active['level']))
            target_breaks = active.setdefault('target_breaks',{})
            if not d['contiguous']:
                target_breaks.clear()
            for r in sorted(crossed,key=lambda level:level['upper']):
                # Stop confirmation includes the breakout close itself.
                if (historical(r,session) and r['upper'] > cleared['upper']
                        and r['price'] > cleared['price']):
                    pending_levels[str(r['unified_level_id'])] = dict(level=r,count=0)
            trigger = active['target'].get('trigger_level')
            if not regular and d['contiguous'] and trigger and historical(trigger, session) and o.price > trigger['upper']:
                target_breaks[str(trigger['unified_level_id'])] = deepcopy(trigger)
            if regular:
                target_breaks.clear()
            for key,r in sorted(list(target_breaks.items()),key=lambda item:item[1]['upper']):
                if o.price <= r['upper']:
                    del target_breaks[key]; continue
                if o.price < o.bar_open:
                    continue
                # A red breakout remains pending until a non-red close confirms
                # it, or a completed close falls back through its frozen band.
                del target_breaks[key]
                # Rebase above this completed close, including when it cleared
                # several bands. The old target is not the next reference.
                anchor = next_historical_resistance(d['rows'],o.price,session)
                selected = (target_selection(d['rows'],anchor,max(o.price,o.ask),s,tick,
                    session=session,minimum_target=target) if anchor else None)
                if selected:
                    selected.update(triggering_breakout=deepcopy(r),
                        selection_method='resistance_nearest_five_percent_above_next_historical_resistance')
                if selected and selected['price'] >= active.get('desired_target',{}).get('price',target):
                    active['desired_target'] = selected
            if len(target_breaks)>4096:
                raise ValueError('Target breakout confirmation capacity exceeded')
            if not d['contiguous']:
                pending_levels.clear()
            for key, item in sorted(list(pending_levels.items()),key=lambda pair:pair[1]['level']['upper']):
                r = item['level']
                if o.price <= r['upper'] or r['upper'] <= cleared['upper'] or r['price'] <= cleared['price']:
                    del pending_levels[key]; continue
                item['count'] += 1
                if item['count'] >= s['historical_hold_closes']:
                    if historical(r,session):
                        active['desired_stop'] = max(active.get('desired_stop',0),stop_below(r['lower'],s,tick))
                        cleared = deepcopy(r)
                        active['last_cleared_resistance'] = cleared
                    del pending_levels[key]
            if len(pending_levels)>4096:
                raise ValueError('Historical stop confirmation capacity exceeded')
            active['best_close'] = max(active.get('best_close',o.price),o.price)
            if not any(historical(r,session) and r['upper'] < o.price for r in d['rows']):
                trailing = floor((active['best_close']-active['initial_risk'])/tick+1e-9)*tick
                active['desired_stop'] = max(active.get('desired_stop',0),trailing)
        if regular:
            active.pop('desired_target',None)
            if luld:
                active['desired_target'] = luld
                active['desired_stop'] = max(active.get('desired_stop',0),luld['lower_exit'])
        elif fresh and active['target'].get('selection_method') in ('official_luld','estimated_luld'):
            anchor = next_historical_resistance(d['rows'],max(o.price,o.ask),session)
            active['desired_target'] = target_selection(d['rows'],anchor,max(o.price,o.ask),s,tick,session=session) if anchor else None
        proposed = active.get('desired_stop',0)
        replacements = []
        early = active.get('early_green_stop') or {}
        if (fresh and not active.get('early_green_graduated') and early.get('price') == proposed
                and stop < proposed and 0 < o.bid <= proposed):
            # An already marketable new stop must protect now, not wait for a
            # later candle to make the replacement guard's bid inequality pass.
            state.update(active_stop=proposed,last_exit_reason='protective_stop',
                entry_acquisition_exit_latched=True)
            state.pop('pending_capital_request',None)
            return result('exit','protective_stop',Status.EXIT_PENDING,quantity=o.position_quantity,
                invalidation_price=proposed,metadata={'early_green_stop':deepcopy(early),
                    'early_stop_marketable_at_confirmation':True})
        if (fresh or luld) and stop < proposed < o.bid:
            state['active_stop'] = proposed
            early_reason = early.get('price') == proposed and not active.get('early_green_graduated')
            replacements.append(result('replace_protective_stop','three_green_second_close' if early_reason else 'historical_hold_or_initial_risk_trail',Status.MANAGING,
                quantity=o.position_quantity,invalidation_price=proposed,profit_target_price=target,
                metadata={'previous_stop':stop,'active_stop':proposed,
                    'early_green_stop':deepcopy(early) if early_reason else None,
                    'last_cleared_resistance':deepcopy(active['last_cleared_resistance'])}))
        selection = active.get('desired_target')
        switching = active['target'].get('selection_method') in ('official_luld','estimated_luld') and not regular
        if (regular or (fresh and o.price >= o.bar_open)) and selection and (selection['price'] > target or ((regular or switching) and selection['price'] != target)) and selection['price'] > max(o.price,o.ask):
            previous_selection = deepcopy(active['target'])
            active['target'] = deepcopy(selection)
            state['structural_profit_targets'] = [selection['price']]
            replacements.append(result('replace_profit_target',selection['selection_method']+'_target_update' if regular else 'resistance_break_target_advance',Status.MANAGING,
                quantity=o.position_quantity,invalidation_price=state['active_stop'],profit_target_price=selection['price'],
                metadata={'previous_profit_target':target,'profit_target':selection['price'],'profit_target_selection':selection,
                    'previous_historical_hod_target':previous_selection}))
        if (not regular_block and s['sizing_mode'] == 'cash_tranches' and fresh and detector_fresh and d['contiguous']
                and not pending and a.permissions.add and ready and macd_ready and o.price > (d.get('vwap') or float('inf'))
                and o.price >= o.bar_open and not active.get('pending_failed_attempt')
                and active.get('tranches_requested',1) < s['tranche_count']):
            frontier = active.get('last_add_level',active['level'])
            new = [r for r in active.get('add_breaks',{}).values()
                if r['upper'] > frontier['upper'] and r['price'] > frontier['price']]
            current_target = active['target']['price']
            ceiling = min(o.ask*(1+s['maximum_chase_bps']/10000),current_target-tick)
            if new and 0 < state['active_stop'] < o.bid <= o.ask <= ceiling:
                broken = min(new,key=lambda r:r['upper'])
                index = active.get('tranches_requested',1)
                active['tranches_requested'] = index+1
                active['last_add_level'] = deepcopy(broken)
                active['add_confirmation'] = dict(confirmed_at=now,maximum_buy_price=ceiling)
                replacements.append(result('add_long','higher_resistance_cash_tranche',Status.MANAGING,
                    invalidation_price=state['active_stop'],profit_target_price=current_target,
                    capital_request=CapitalRequest(mode='fixed_quantity',value=1),
                    order_intent={'execution_policy':'adaptive_urgent','protection_profile':'structural-single-target'},
                    metadata={'cash_tranche':dict(key=active['cash_tranche_key'],index=index,count=s['tranche_count']),
                        'tranche_breakout':deepcopy(broken),'mandatory_broker_target':True,
                        'maximum_buy_price':ceiling,'wait_for_capital':False}))
        # A non-red close consumes this confirmation opportunity even when
        # acquisition gates block it. Never replay old gate failures later.
        if fresh and o.price >= o.bar_open:
            active.pop('add_breaks',None)
        if replacements:
            return replace(replacements[-1], evaluation=replace(replacements[-1].evaluation,
                signals=tuple(signal for r in replacements for signal in r.evaluation.signals),
                intents=tuple(intent for r in replacements for intent in r.evaluation.intents)))
        return result('hold','structure_valid' if detector_fresh else 'awaiting_completed_structure',Status.MANAGING,
            invalidation_price=stop,profit_target_price=target)
    def enter(entry, reason):
        return result('enter_long',reason,Status.ENTRY_PENDING,invalidation_price=entry['stop'],profit_target_price=entry['target']['price'],
            capital_request=CapitalRequest(mode='mandate_fraction' if s['sizing_mode']=='cash_tranches' else 'risk_fraction',
                value=s['cash_fraction'] if s['sizing_mode']=='cash_tranches' else s['risk_fraction'],
                maximum_quantity=s['maximum_quantity'],allow_replacement=False),
            order_intent={'execution_policy':'adaptive_urgent','protection_profile':'structural-single-target'},
            metadata={'initial_stop':entry['stop'],'active_stop':entry['stop'],'profit_targets':[entry['target']['price']],
                **({'cash_tranche':dict(key=entry['cash_tranche_key'],index=0,count=s['tranche_count'])}
                    if s['sizing_mode']=='cash_tranches' else {}),
                'profit_target':entry['target']['price'],'mandatory_broker_target':True,'maximum_buy_price':entry['maximum_buy_price'],
                'initial_stop_selection':entry['initial_stop_selection'],
                'entry_selection':entry['level'],'profit_target_selection':entry['target'],
                'entry_breakout_confirmation':entry.get('breakout_confirmation'),
                'unified_structural_trigger':{'current_snapshot':{'levels':[dict(entry['level'],entry_boundary=entry['level']['upper'])], 'session_high':entry['hod'],
                    'selected_at':o.observed_at.isoformat(),'frozen_at_entry':True}}})
    if pending:
        return enter(active,'historical_hod_entry') if state.get('pending_capital_request') else result('wait','entry_fill_pending',Status.ENTRY_PENDING)
    if (a.status in (Status.DISABLED,Status.PAUSED,Status.COMPLETED,Status.ERROR)
            or not a.permissions.observe or not a.permissions.enter
            or (state.get('entries',0) and not a.permissions.reenter)):
        return result('wait','entry_permission_closed')
    local = o.observed_at.astimezone(NY).time()
    market_session = 'premarket' if (local.hour,local.minute)<(9,30) else 'regular' if local.hour<16 else 'after_hours'
    if (market_session not in p['strategy_behavior'].get('eligible_sessions',['premarket','regular']) or flatten
            or _at_or_after_session_time(o.observed_at,p['strategy_behavior'].get('entry_cutoff_time','15:45:00')) or not o.market_open):
        return result('wait','outside_entry_session')
    if saved_reentry and not reclaim:
        return result('wait','waiting_for_stopped_level_close_and_next_open')
    if (not fresh and not reclaim) or not macd_ready:
        return result('wait','waiting_for_completed_1s_and_bullish_5s_macd')
    if regular_block:
        return result('wait',regular_block)
    if not ready:
        return result('wait','tradability_incomplete')
    if not detector_fresh and not reclaim:
        return result('wait','certified_detector_unavailable')
    if reclaim:
        row = reclaim['row']
    hod = d.get('prior_hod'); previous = d.get('prior_close')
    vwap = d.get('vwap')
    if not hod or previous is None or vwap is None or not isfinite(vwap) or o.price <= vwap:
        return result('wait','hod_history_or_vwap_gate')
    boundary = deepcopy(saved_reentry['level']) if reclaim else reference['level']
    reentry = True if reclaim else reference['reentry']
    failed_breakout = False if reclaim else reference['failed_breakout']
    require_body_high = reentry or failed_breakout
    offset = s['entry_breakout_offset']
    recent = d.get('recent_breakout') or {}
    resistance_threshold = saved_reentry['price'] if reclaim else reference['resistance_threshold']
    threshold = resistance_threshold if reclaim else reference['threshold']
    evidence['entry_selection'] = dict(level=boundary,threshold=threshold,reentry=True) if reclaim else dict(reference)
    if o.price < o.bar_open and not reclaim:
        return result('wait','red_breakout_candle')
    inclusive = bool(offset) and (not require_body_high or resistance_threshold > d['prior_body_high'])
    crossed = (round(previous,9) < threshold <= round(o.price,9) if inclusive
        else previous <= threshold < o.price)
    recent_held = (not require_body_high and bool(recent) and recent.get('level_id') == boundary.get('unified_level_id')
        and recent.get('threshold') == resistance_threshold
        and 0 <= now-recent.get('at',0) <= s.get('recent_breakout_seconds',30.)
        and (min(o.price,o.bid) >= threshold if inclusive else min(o.price,o.bid) > threshold))
    evidence['entry_selection']['recent_breakout'] = deepcopy(recent) if recent_held else None
    if not crossed and not recent_held and not reclaim:
        return result('wait','waiting_for_fresh_body_high_break' if require_body_high else 'waiting_for_fresh_resistance_break')
    target_anchor = next_historical_resistance(d['rows'],max(o.ask,o.price),session)
    selected = (target_selection(d['rows'],target_anchor,max(o.ask,o.price),s,tick,session=session)
        if target_anchor else None)
    if regular:
        selected = luld
    if not selected:
        return result('wait','qualified_target_unavailable')
    swing = initial_swing_low(row,boundary,now)
    if swing is None:
        return result('wait','confirmed_local_swing_low_unavailable')
    stop = stop_below(swing['lower'],s,tick)
    initial_green = green_stop_candidate(d,tick) if s['early_green_stop_enabled'] else None
    if reclaim:
        # Reclaim continues the initial protection phase of this episode; it
        # must not discard the remembered stop for the older local swing low.
        initial_green = deepcopy(saved_reentry['early_green_stop'])
    if initial_green:
        stop = max(stop,initial_green['price'])
    if luld:
        stop = max(stop,luld['lower_exit'])
    # Bound execution slippage from the executable quote, not the last trade.
    ceiling = min(o.ask*(1+s['maximum_chase_bps']/10000),selected['price']-tick)
    if not 0 < stop < o.bid <= o.ask <= ceiling:
        return result('wait','invalid_stop_or_entry_price')
    atr = row.get('qualification',{}).get('atr') or 0.
    entry = dict(confirmed_at=now,level=boundary,hod=hod,stop=stop,target=selected,maximum_buy_price=ceiling,
        breakout_confirmation=dict(recent=bool(recent_held and not crossed),
            breakout=deepcopy(recent) if recent_held else None,validated_at=now,
            stopped_level_reclaim={k:v for k,v in reclaim.items() if k!='row'} if reclaim else None),
        cash_tranche_key=f'{a.assignment_id}:{o.observed_at.isoformat()}',tranches_requested=1,
        last_add_level=deepcopy(boundary),
        initial_stop_selection=swing,
        last_cleared_resistance=deepcopy(boundary),
        initial_risk=o.ask-stop,best_close=o.price,episode=d['episode'],
        management_base=dict(lower=boundary['lower'],tolerance=max(tick,s['management_tolerance_atr']*atr)),hold_levels={})
    if initial_green:
        entry['early_green_stop'] = initial_green
        entry['initial_stop_selection'] = dict(swing,early_green_stop=deepcopy(initial_green))
    state.update(historical_hod_entry=entry,initial_stop=stop,active_stop=stop,structural_profit_targets=[selected['price']],
        entry_reference_price=o.ask,entry_at=o.observed_at.isoformat(),entries=state.get('entries',0)+1,
        last_exit_reason='',entry_acquisition_exit_latched=False)
    return enter(entry,'stopped_level_reclaim' if reclaim else 'historical_hod_entry')
