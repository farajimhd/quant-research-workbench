"""Versioned event-price strategies over midpoint-selected resistance."""
from copy import deepcopy
from dataclasses import replace
from math import floor, isfinite
from . import early_squeeze_breakout as E, historical_hod as H, vwap_resistance_ladder as V
from .early_squeeze_fast import below, levels, is_resistance
from .signals import CapitalRequest

CONTRACT = 'early-squeeze-r1-price-gap-v9'
STRICT_CONTRACT = 'early-squeeze-r1-price-high-v10'
EPISODE_CONTRACT = 'early-squeeze-r1-price-episode-v11'
RESISTANCE_CEILING_CONTRACT = 'early-squeeze-r1-price-resistance-ceiling-v12'
BROKEN_RESISTANCE_CEILING_CONTRACT = 'early-squeeze-r1-price-broken-resistance-ceiling-v13'
GREEN_CLOSE_CEILING_CONTRACT = 'early-squeeze-r1-price-green-close-ceiling-v14'
MACD_EPISODE_REENTRY_CONTRACT = 'early-squeeze-r1-price-macd-1s-episode-reentry-v15'
DUAL_MACD_REENTRY_CONTRACT = 'early-squeeze-r1-price-dual-macd-reentry-v16'
EPISODE_TARGET_CONTINUITY_CONTRACT = 'early-squeeze-r1-price-episode-target-continuity-v17'
FORMING_EPISODE_CONTRACT = 'early-squeeze-r1-price-forming-episode-v18'
CONFIRMED_BREAKOUT_CONTRACT = 'early-squeeze-r1-price-confirmed-breakout-v19'
VOLATILITY_CHOP_CONTRACT = 'early-squeeze-r1-price-volatility-chop-v20'
MIDPOINT_EXECUTION_CONTRACT = 'early-squeeze-r1-price-midpoint-execution-v21'
EPISODE_CONTRACTS = (EPISODE_CONTRACT, RESISTANCE_CEILING_CONTRACT,
                     BROKEN_RESISTANCE_CEILING_CONTRACT, GREEN_CLOSE_CEILING_CONTRACT,
                     MACD_EPISODE_REENTRY_CONTRACT, DUAL_MACD_REENTRY_CONTRACT,
                     EPISODE_TARGET_CONTINUITY_CONTRACT, FORMING_EPISODE_CONTRACT, CONFIRMED_BREAKOUT_CONTRACT, VOLATILITY_CHOP_CONTRACT, MIDPOINT_EXECUTION_CONTRACT)


def midpoint(row):
    return (row['lower'] + row['upper']) / 2


def eligible(row):
    return is_resistance(row) or row.get('role') == 'transition' and row.get('transition_from') == 'resistance'


def entry_level(rows, hod):
    return max((r for r in rows.values() if eligible(r) and hod and midpoint(r) < hod),
               key=lambda r:(midpoint(r), r['unified_level_id']), default=None)


def boundary(anchor, rows):
    above = [r for r in rows.values() if is_resistance(r) and midpoint(r) > midpoint(anchor)]
    if not above:
        return None
    next_level = min(above, key=lambda r:(midpoint(r), r['unified_level_id']))
    return dict(price=midpoint(anchor)+.1*(midpoint(next_level)-midpoint(anchor)),
                midpoint=midpoint(anchor), next_midpoint=midpoint(next_level),
                next_level_id=next_level['unified_level_id'], gap_fraction=.1)


def observe_entry_breakout(d, rows, *, now, price, previous, prior_hod,
                           entries, price_event, closed_100ms, structure_fresh, midpoint_only=False):
    """V19: select the obstacle first, then witness its completed-bar break.

    Initial entries retain the HOD-selected resistance. Later episodes arm
    the nearest not-yet-cleared band at the start of the move, never a search
    for a conveniently broken band below price. Reaching a higher band makes
    that band govern, even if it has not broken yet.
    """
    macd = d.get('macd_1s', {})
    setup = d.get('entry_breakout')
    if not macd.get('open') or setup and setup['episode_id'] != macd.get('episode_id'):
        d.pop('entry_breakout', None)
        setup = None
    if not macd.get('open') or not (price_event or closed_100ms):
        return
    if not structure_fresh:
        # An unobserved close could have invalidated the band. Do not reuse
        # an earlier confirmation after an authority gap.
        d.pop('entry_breakout', None)
        return
    anchor = rows.get(setup['anchor']['unified_level_id']) if setup else None
    if anchor and not eligible(anchor):
        anchor = None
    if not anchor:
        reference = previous if price_event and previous is not None else price
        anchor = (min((r for r in rows.values() if eligible(r) and r['upper'] >= reference),
                      key=lambda r:(midpoint(r), r['unified_level_id']), default=None)
                  if entries else entry_level(rows, prior_hod))
    if anchor:
        approached = [r for r in rows.values() if eligible(r)
                      and midpoint(r) > midpoint(anchor) and r['lower'] <= price]
        if approached:
            anchor = max(approached, key=lambda r:(midpoint(r), r['unified_level_id']))
    limit = boundary(anchor, rows) if anchor else None
    if not anchor or not limit:
        d.pop('entry_breakout', None)
        return
    signature = [anchor['unified_level_id'], anchor['lower'], anchor['upper'],
                 limit['next_level_id'], limit['price']]
    threshold = limit['price'] if midpoint_only else max(anchor['upper'], limit['price'])
    if not setup or setup['signature'] != signature:
        setup = dict(anchor=deepcopy(anchor), boundary=deepcopy(limit), signature=signature,
                     episode_id=macd['episode_id'], armed_at=now, armed_price=price,
                     breakout_at=now, peak_close=None, confirmation=None,
                     approach_observed=bool(price <= threshold or price_event
                         and previous is not None and previous <= threshold))
        d['entry_breakout'] = setup
    if price <= threshold:
        setup['confirmation'] = None
        setup['approach_observed'] = True
    if closed_100ms and setup['approach_observed'] and now > setup['armed_at'] and price > threshold:
        setup['confirmation'] = dict(closed_at=now, close=price, timeframe='100ms',
                                     upper=anchor['upper'], threshold=threshold,
                                     episode_id=macd['episode_id'])
        setup['peak_close'] = max(setup.get('peak_close') or price, price)


def forming_macd_1s(o, state, price_event, *, sparse=False):
    """Preview from consecutive authoritative closes; never compound trade updates."""
    now = o.observed_at.timestamp()
    base = state.get('completed_macd_1s', {})
    if o.source_timeframe == '1s' and 'bar_close' in o.evaluation_events:
        current = dict(at=now, line=o.macd_line, signal=o.macd_signal)
        values = (base.get('line'), o.macd_line, o.macd_signal, o.price)
        if (now > base.get('at', now) if sparse else abs(now-base.get('at', 0)-1.) < 1e-6) and all(
                type(v) in (int, float) and isfinite(v) for v in values):
            af, slow_alpha = 2/13, 2/27
            previous_slow = o.price-(o.macd_line-(1-af)*base['line'])/(af-slow_alpha)
            current['slow'] = slow_alpha*o.price+(1-slow_alpha)*previous_slow
        if now > base.get('at', 0):
            state['completed_macd_1s'] = current
        return current
    proof = (o.structural_detector_state or {}).get('macd_1s_evidence', {})
    source_at = E.stamp(proof.get('source_observed_at'))
    complete_prefix = bool(sparse and proof.get('authority') == 'latest-completed-qmd-1s'
        and E.stamp(proof.get('as_of')) == o.observed_at and source_at
        and source_at.timestamp() == base.get('at') and source_at <= o.observed_at
        and source_at.astimezone(H.NY).date() == o.observed_at.astimezone(H.NY).date()
        and proof.get('line') == base.get('line') and proof.get('signal') == base.get('signal'))
    if not price_event or not (0 <= now-base.get('at', 0) < 1.000001 or complete_prefix) or 'slow' not in base:
        return {}
    line = 2/13*o.price+11/13*(base['slow']+base['line'])-(2/27*o.price+25/27*base['slow'])
    return dict(at=now, base_at=base['at'], line=line, signal=.2*line+.8*base['signal'])


def observe_chop_volatility(state, now, high, low, close):
    """Prior 14 contiguous completed 1s true ranges, in price units, SMA.

    Call only on a new completed 1s bar. The evaluated bar cannot inflate its
    own tolerance. Warm while flat and retain across MACD episodes/positions;
    the session-scoped parent state resets this history at the next session.
    """
    history = state.setdefault('chop_volatility_1s', {})
    if now <= history.get('at', 0):
        return None
    if not all(type(v) in (int, float) and isfinite(v) for v in (high, low, close)) or not 0 < low <= close <= high:
        history.clear()
        return None
    if abs(now-history.get('at', 0)-1.) > 1e-6:
        history.clear()
    samples = history.setdefault('samples', [])
    prior = (dict(value=sum(r[1] for r in samples)/14, window=14,
                  timeframe='1s', estimator='prior-completed-true-range-sma',
                  first_close_at=samples[0][0], last_close_at=samples[-1][0])
             if len(samples) == 14 else None)
    previous = history.get('close', close)
    samples.append([now, max(high-low, abs(high-previous), abs(low-previous))])
    samples[:] = samples[-14:]
    history.update(at=now, close=close)
    return prior


def observe_midpoint_chop(active, rows, now, close, *, volatility_gate=False, volatility=None):
    """Five consecutive one-second observations around a fixed band, two crossings."""
    trackers = active.setdefault('midpoint_chop', {})
    for key in list(trackers):
        if key not in rows or not eligible(rows[key]):
            trackers.pop(key)
    for key, level in rows.items():
        if not eligible(level):
            continue
        tracker = trackers.setdefault(key, dict(midpoint=midpoint(level),
            lower=level['lower'], upper=level['upper'], samples=[]))
        samples = tracker['samples']
        if samples and now <= samples[-1][0]:
            continue
        if samples and abs(now-samples[-1][0]-1.) > 1e-6:
            samples.clear()
        samples.append([now, close])
        samples[:] = samples[-5:]
        signs = [1 if value > tracker['midpoint'] else -1
                 for _, value in samples if value != tracker['midpoint']]
        crossings = sum(a != b for a, b in zip(signs, signs[1:]))
        if volatility_gate:
            threshold = tracker['midpoint']-.5*volatility['value'] if volatility else None
            tracker['volatility_filter'] = dict(k=.5, volatility=deepcopy(volatility),
                exit_threshold=threshold, close=close,
                ready=volatility is not None)
            if threshold is None or close >= threshold-1e-9:
                continue
        if len(samples) == 5 and crossings >= 2:
            active['midpoint_chop_exit'] = dict(tracker, unified_level_id=key, crossings=crossings)


def target_ordinal(broken_count):
    return 1 if broken_count >= 6 else 2 if broken_count >= 4 else 3


def unbroken_resistance_ceiling(rows, price, stop):
    """Return the nearest still-unbroken resistance lower edge above the stop."""
    candidates = [r for r in rows.values() if eligible(r)
                  and r['lower'] >= stop - 1e-9 and price <= r['upper'] + 1e-9]
    return min(candidates, key=lambda r:(r['lower'], r['upper'], r['unified_level_id']), default=None)


def broken_resistance_ceiling(active, breakout_anchors, rows, price, confirmed_ids=None):
    """Advance only after price fully clears a resistance's upper edge."""
    current = active.get('trail_resistance_ceiling') or active['anchor']
    catalog = {r['unified_level_id']: r for r in breakout_anchors.values()}
    catalog.update({r['unified_level_id']: r for r in rows.values() if eligible(r)})
    cleared = [r for r in catalog.values() if (confirmed_ids is None or r['unified_level_id'] in confirmed_ids)
               and r['lower'] > current['lower'] + 1e-9
               and price > r['upper'] + 1e-9]
    if cleared:
        current = max(cleared, key=lambda r:(r['lower'], r['upper'], r['unified_level_id']))
    active['trail_resistance_ceiling'] = deepcopy(current)
    return current


def next_resistance_ceiling(active, breakout_anchors, rows, stop):
    """The next blocker, not the floor of the resistance already cleared."""
    catalog = dict(breakout_anchors)
    catalog[active['anchor']['unified_level_id']] = active['anchor']
    catalog.update(rows)
    confirmations = active.get('green_resistance_closes', {})
    blockers = []
    for key, level in catalog.items():
        if not eligible(level) or level['lower'] < stop-1e-9:
            continue
        proof = confirmations.get(key) or {}
        if proof.get('upper', 0) >= level['upper'] and proof.get('close', 0) > level['upper']:
            continue
        blockers.append(level)
    current = min(blockers, key=lambda r:(r['lower'], midpoint(r), r['unified_level_id']), default=None)
    active['trail_resistance_ceiling'] = deepcopy(current)
    return current


def midpoint_add_available(d, key, episode_id):
    return not any(key in request['keys'] and (not request['terminal']
        or request['filled'] and request['episode_id'] == episode_id)
        for request in d.get('midpoint_add_requests', {}).values())


def update_midpoint_add(state, request_id, *, filled=False, terminal=False):
    """Fill-owned episode consumption survives exits and duplicate callbacks."""
    d = deepcopy(state.get('squeeze_breakout', {}))
    request = d.get('midpoint_add_requests', {}).get(request_id)
    if request:
        request['filled'] = request['filled'] or filled
        request['terminal'] = request['terminal'] or terminal
        state['squeeze_breakout'] = d


def evaluate(host, a, o, p, old_state):
    from .strategy_engine import AssignmentStatus as Status
    midpoint_execution = p['early_squeeze_breakout_contract'] == MIDPOINT_EXECUTION_CONTRACT
    episode = p['early_squeeze_breakout_contract'] in EPISODE_CONTRACTS
    resistance_ceiling = p['early_squeeze_breakout_contract'] == RESISTANCE_CEILING_CONTRACT
    broken_resistance_cap = p['early_squeeze_breakout_contract'] == BROKEN_RESISTANCE_CEILING_CONTRACT
    green_close_cap = p['early_squeeze_breakout_contract'] in (
        GREEN_CLOSE_CEILING_CONTRACT, MACD_EPISODE_REENTRY_CONTRACT, DUAL_MACD_REENTRY_CONTRACT,
        EPISODE_TARGET_CONTINUITY_CONTRACT, FORMING_EPISODE_CONTRACT, CONFIRMED_BREAKOUT_CONTRACT, VOLATILITY_CHOP_CONTRACT, MIDPOINT_EXECUTION_CONTRACT)
    macd_episode_reentry = p['early_squeeze_breakout_contract'] in (
        MACD_EPISODE_REENTRY_CONTRACT, DUAL_MACD_REENTRY_CONTRACT,
        EPISODE_TARGET_CONTINUITY_CONTRACT, FORMING_EPISODE_CONTRACT, CONFIRMED_BREAKOUT_CONTRACT, VOLATILITY_CHOP_CONTRACT, MIDPOINT_EXECUTION_CONTRACT)
    dual_macd_reentry = p['early_squeeze_breakout_contract'] in (
        DUAL_MACD_REENTRY_CONTRACT, EPISODE_TARGET_CONTINUITY_CONTRACT, FORMING_EPISODE_CONTRACT, CONFIRMED_BREAKOUT_CONTRACT, VOLATILITY_CHOP_CONTRACT, MIDPOINT_EXECUTION_CONTRACT)
    episode_target_continuity = p['early_squeeze_breakout_contract'] in (EPISODE_TARGET_CONTINUITY_CONTRACT, FORMING_EPISODE_CONTRACT, CONFIRMED_BREAKOUT_CONTRACT, VOLATILITY_CHOP_CONTRACT, MIDPOINT_EXECUTION_CONTRACT)
    strict = episode or p['early_squeeze_breakout_contract'] == STRICT_CONTRACT
    forming_episode = p['early_squeeze_breakout_contract'] in (FORMING_EPISODE_CONTRACT, CONFIRMED_BREAKOUT_CONTRACT, VOLATILITY_CHOP_CONTRACT, MIDPOINT_EXECUTION_CONTRACT)
    state = deepcopy(old_state)
    a = replace(a, parameters=p)
    now, tick = o.observed_at.timestamp(), p['execution']['tick_size']
    session = o.observed_at.astimezone(H.NY).date().isoformat()
    d = state.setdefault('squeeze_breakout', {})
    if d.get('session') != session:
        d.clear()
        d['session'] = session
    market = o.structural_detector_state or {}
    ctx, row = market.get('fast_squeeze_context', {}), market.get('row', {})
    fresh = (o.source_timeframe == '100ms' and 'bar_close' in o.evaluation_events
             and ctx.get('session') == session and ctx.get('at') == now and now > d.get('closed_at', 0))
    sample = o.source_values.get(E.SIGNAL, {})
    at = E.stamp(sample.get('observed_at'))
    if sample.get('value') is True and at and at <= o.observed_at and at.astimezone(H.NY).date().isoformat() == session:
        d['activated_at'] = min(d.get('activated_at', now), at.timestamp())
        d.setdefault('activation_event_id', sample.get('event_id'))
    rows = levels(o)
    filtered = bool(rows) and all(r.get('input_policy') == V.POLICY and r.get('seed_input_policy') == V.POLICY for r in rows.values())
    proof = market.get('fast_structure_evidence', {})
    cutoff, last_input = proof.get('as_of'), proof.get('max_input_timestamp')
    structure_fresh = (filtered and type(cutoff) in (int,float) and type(last_input) in (int,float)
        and isfinite(cutoff) and isfinite(last_input) and last_input <= cutoff
        and 0 <= now-cutoff < 1.000001)
    held, active = o.position_quantity > 0, state.get('squeeze_entry') or {}
    stop = float(state.get('active_stop') or 0)
    target = float((state.get('structural_profit_targets') or [0])[0])
    quote = V.fresh_quote(o)
    sample_price = o.source_values.get('market.last_price', {})
    price_event = ('market_data_update' in o.evaluation_events
        and 'market.last_price' in o.changed_source_ids
        and E.stamp(sample_price.get('observed_at')) == o.observed_at
        and sample_price.get('value') == o.price
        and not any(s.startswith('quote:') for s in o.source_signal_ids))
    previous = d.get('trade_price', ctx.get('close') if ctx.get('at', now) < now else None)
    prior_hod = max(ctx.get('hod') or 0., d.get('trade_hod') or 0.)
    evidence_price = dict(price_event=price_event, previous_price=previous, prior_hod=prior_hod)
    macd = d.setdefault('macd_1s', {}) if macd_episode_reentry else {}
    macd_fresh = (macd_episode_reentry and o.source_timeframe == '1s'
                  and 'bar_close' in o.evaluation_events and now > macd.get('observed_at', 0))
    preview = forming_macd_1s(o, d, price_event, sparse=midpoint_execution) if forming_episode else None
    if not forming_episode and macd_fresh:
        valid = all(type(value) in (int, float) and isfinite(value)
                    for value in (o.macd_line, o.macd_signal))
        bullish = valid and o.macd_line > o.macd_signal
        if bullish and not macd.get('open'):
            macd.clear()
            macd.update(open=True, episode_id=now, high=0., started_at=now, broken_levels=[])
        elif not bullish:
            macd.clear()
            macd.update(open=False, observed_at=now, line=o.macd_line, signal=o.macd_signal)
        if bullish:
            macd.update(observed_at=now, line=o.macd_line, signal=o.macd_signal,
                        high=max(macd.get('high', 0.), o.bar_high or o.price))
    if forming_episode and (macd_fresh or price_event):
        line, signal = ((preview.get('line'), preview.get('signal')) if forming_episode
                        else (o.macd_line, o.macd_signal))
        valid = all(type(value) in (int, float) and isfinite(value) for value in (line, signal))
        bullish = valid and line > signal
        if bullish and not macd.get('open'):
            macd.clear()
            macd.update(open=True, episode_id=now, high=0., started_at=now, broken_levels=[])
            d.pop('reentry_100ms_review', None)
        elif valid and not bullish:
            macd.clear()
            macd.update(open=False)
        macd.update(available=valid, observed_at=now, line=line, signal=signal)
        if bullish and not forming_episode:
            macd['high'] = max(macd.get('high', 0.), o.bar_high or o.price)
    prior_macd_episode_high = macd.get('high', 0.)
    macd_100ms = d.setdefault('macd_100ms', {}) if dual_macd_reentry else {}
    macd_100ms_fresh = (dual_macd_reentry and o.source_timeframe == '100ms'
                        and 'bar_close' in o.evaluation_events
                        and now > macd_100ms.get('observed_at', 0))
    if macd_100ms_fresh:
        valid = all(type(value) in (int, float) and isfinite(value)
                    for value in (o.macd_line, o.macd_signal))
        macd_100ms.update(open=bool(valid and o.macd_line > o.macd_signal),
                          observed_at=now, line=o.macd_line, signal=o.macd_signal)
    prior_rows = d.get('levels', rows)
    prior_add_close = d.get('midpoint_add_close') or {}
    if midpoint_execution and fresh:
        d['midpoint_add_close'] = dict(price=o.price, at=now,
            levels=deepcopy(rows) if structure_fresh else {})
    crossed = []
    if episode and price_event and structure_fresh:
        # A failed band starts a new episode only below its lower edge.
        # Do not reset HOD, the held position's trail, or lifecycle targets.
        for key in list(d.get('breakout_highs', {})):
            band = rows.get(key) or d.get('breakout_anchors', {}).get(key)
            if band and o.price < band['lower']:
                d['breakout_highs'].pop(key)
                d['entered_levels'] = [value for value in d.get('entered_levels', []) if value != key]
                setup = d.get('initial_breakout')
                if setup and setup['anchor']['unified_level_id'] == key:
                    d.pop('initial_breakout', None)
                active.get('pending_adds', {}).pop(key, None)
                d.setdefault('reset_at', {})[key] = now
    prior_breakout_highs = dict(d.get('breakout_highs', {}))
    if strict and price_event:
        highs = d.setdefault('breakout_highs', {})
        for key in highs:
            highs[key] = max(highs[key], o.price)
    if price_event:
        if structure_fresh and previous is not None and o.price > previous:
            for key, level in prior_rows.items():
                limit = boundary(level, prior_rows)
                if eligible(level) and limit and previous < limit['price'] <= o.price:
                    crossed.append((key, level))
                    if strict:
                        d.setdefault('breakout_highs', {}).setdefault(key, o.price)
                        if episode:
                            d.setdefault('breakout_anchors', {})[key] = deepcopy(level)
            if crossed:
                d['latest_broken_resistance'] = deepcopy(max((r for _,r in crossed), key=midpoint))
        if held and active:
            broken = active.setdefault('broken_levels', [])
            episode_broken = macd.setdefault('broken_levels', []) if episode_target_continuity and macd.get('open') else None
            for key, level in crossed:
                if is_resistance(level) and key not in broken:
                    broken.append(key)
                if is_resistance(level) and episode_broken is not None and key not in episode_broken:
                    episode_broken.append(key)
        d.update(trade_price=o.price, trade_hod=max(prior_hod,o.price), levels=deepcopy(rows))
        if macd_episode_reentry and macd.get('open'):
            macd['high'] = max(macd.get('high', 0.), o.price)
    if fresh:
        d['closed_at'] = now
        pending_setup = d.get('initial_breakout')
        if pending_setup and now > pending_setup['breakout_at']:
            pending_setup['peak_close'] = max(pending_setup.get('peak_close') or o.price, o.price)
        if active and (held or a.status == Status.ENTRY_PENDING) and not active.get('stopout_reference'):
            active['peak_close'] = max(active.get('peak_close') or o.price, o.price)
    if dual_macd_reentry and held and active and macd_100ms_fresh and structure_fresh:
        confirmations = active.setdefault('add_close_confirmations', {})
        for key, level in rows.items():
            if is_resistance(level) and o.price > level['upper'] + 1e-9:
                confirmations[key] = dict(closed_at=now, close=o.price, upper=level['upper'])
            elif forming_episode:
                confirmations.pop(key, None)
        if episode_target_continuity and not forming_episode:
            tracker = active.get('forming_resistance_exit')
            if tracker and o.price > tracker['upper'] + 1e-9:
                active.pop('forming_resistance_exit', None)
            for event in market.get('local_events', ()):
                forming = event.get('level') or {}
                price = forming.get('price')
                if event.get('state') != 'resistance_forming' or type(price) not in (int, float):
                    continue
                band = min((level for level in rows.values() if is_resistance(level)
                            and level['lower'] - 1e-9 <= price <= level['upper'] + 1e-9),
                           key=lambda level:(abs(midpoint(level)-price), level['unified_level_id']),
                           default=None)
                if band:
                    current = active.get('forming_resistance_exit')
                    if not current or current['unified_level_id'] != band['unified_level_id']:
                        active['forming_resistance_exit'] = dict(
                            unified_level_id=band['unified_level_id'], lower=band['lower'],
                            upper=band['upper'], forming_price=float(price), started_at=now)
    if dual_macd_reentry and not held and state.get('entries', 0) and macd.get('open'):
        same_episode = d.get('last_entry_macd_episode') == macd.get('episode_id')
        review = d.get('reentry_100ms_review')
        if same_episode and (not review or review.get('entry_count') != state.get('entries')):
            review = d['reentry_100ms_review'] = dict(
                episode_id=macd.get('episode_id'), entry_count=state.get('entries'), closes=0, started_at=now,
                forming_resistances=[], blocked=False)
        if same_episode and review and macd_100ms_fresh and not review.get('blocked'):
            review['closes'] += 1
            for candidate in review['forming_resistances']:
                if o.price > candidate['price'] + 1e-9:
                    candidate['broken'] = True
            for event in market.get('local_events', ()):
                level = event.get('level') or {}
                price = level.get('price')
                if (event.get('state') == 'resistance_forming'
                        and type(price) in (int, float) and isfinite(price) and price > 0):
                    review['forming_resistances'].append(dict(
                        price=float(price), observed_at=now,
                        broken=o.price > float(price) + 1e-9))
            if review['closes'] >= 3 and any(
                    not candidate['broken'] for candidate in review['forming_resistances']):
                review['blocked'] = True
    volatility_chop = p['early_squeeze_breakout_contract'] in (VOLATILITY_CHOP_CONTRACT, MIDPOINT_EXECUTION_CONTRACT)
    chop_volatility = (observe_chop_volatility(d, now, o.bar_high, o.bar_low, o.price)
                       if volatility_chop and macd_fresh else None)
    if forming_episode and held and active and macd_fresh and structure_fresh:
        observe_midpoint_chop(active, rows, now, o.price,
            volatility_gate=volatility_chop, volatility=chop_volatility)
    green_close = bool(active and held and green_close_cap and o.source_timeframe == '1s'
                       and 'bar_close' in o.evaluation_events and o.bar_open is not None
                       and o.price > o.bar_open
                       and (not midpoint_execution or macd_fresh and structure_fresh))
    if green_close:
        confirmations = active.setdefault('green_resistance_closes', {})
        catalog = {r['unified_level_id']: r for r in d.get('breakout_anchors', {}).values()}
        catalog.update({r['unified_level_id']: r for r in rows.values() if eligible(r)})
        for key, level in catalog.items():
            if o.price > level['upper'] + 1e-9:
                confirmations[key] = dict(closed_at=now, close=o.price, open=o.bar_open,
                                          upper=level['upper'])
    evidence = dict(contract=p['early_squeeze_breakout_contract'], filtered_v7=filtered,
        activation={k:d.get(k) for k in ('activated_at', 'activation_event_id')})
    evidence['price_breakout'] = evidence_price
    if strict:
        evidence.update(stop_trigger_source='eligible_trade', prior_breakout_highs=prior_breakout_highs)
    if macd_episode_reentry:
        evidence['macd_1s_episode'] = dict(macd, prior_high=prior_macd_episode_high)
    if dual_macd_reentry:
        evidence['macd_100ms_gate'] = dict(macd_100ms)
        evidence['reentry_100ms_review'] = deepcopy(d.get('reentry_100ms_review'))
    if p['early_squeeze_breakout_contract'] in (CONFIRMED_BREAKOUT_CONTRACT, VOLATILITY_CHOP_CONTRACT, MIDPOINT_EXECUTION_CONTRACT):
        observe_entry_breakout(d, rows, now=now, price=o.price, previous=previous,
            prior_hod=prior_hod, entries=state.get('entries', 0), price_event=price_event,
            closed_100ms=fresh, structure_fresh=structure_fresh, midpoint_only=midpoint_execution)
        evidence['entry_breakout'] = deepcopy(d.get('entry_breakout'))

    def emit(action, reason, status=None, **kw):
        metadata = dict(evidence, active_stop=state.get('active_stop'),
            profit_targets=list(state.get('structural_profit_targets') or []),
            position_resistance_breaks=len(active.get('broken_levels', [])),
            fixed_trail_distance=active.get('trail_distance'), trailing_bid_high=None if strict else active.get('peak_price'),
            trailing_trade_high=active.get('peak_price') if strict else None,
            bid=o.bid, ask=o.ask, **kw.pop('metadata', {}))
        if action == 'exit':
            if reason != 'complete_position_liquidation':
                state['last_exit_reason'] = reason
            state['entry_acquisition_exit_latched'] = True
            metadata.update(position_fraction=1., cancel_entry_acquisition=True,
                reentry_after_fill=reason == 'protective_stop'
                or forming_episode and state.get('last_exit_reason') == 'resistance_midpoint_chop_exit'
                or state.get('liquidation_origin_fill_role') in ('protective_stop','trailing_stop','protective_exit'))
        result = host._result(a, o, action, reason, float(action in ('enter_long', 'add_long')), 1.,
            state, status or a.status, metadata=metadata,
            order_intent=dict(execution_policy='adaptive_urgent', protection_profile='structural-single-target'), **kw)
        if action in ('enter_long', 'add_long'):
            result = replace(result, evaluation=replace(result.evaluation, intents=tuple(
                replace(i, reference_price=o.ask, metadata={**i.metadata, 'mandatory_broker_target':True,
                    'wait_for_capital':False}) for i in result.evaluation.intents)))
            if action == 'add_long':
                for intent in result.evaluation.intents:
                    if midpoint_execution:
                        d.setdefault('midpoint_add_requests', {})[intent.intent_id] = dict(
                            keys=list(metadata['squeeze_add_levels']), episode_id=macd.get('episode_id'),
                            filled=False, terminal=False)
                    else:
                        active.setdefault('add_requests', {})[intent.intent_id] = list(metadata['squeeze_add_levels'])
        return result

    # A partially filled full target owns liquidation. Never add into it, or
    # freeze indefinitely because an acquisition callback changed the status.
    if held and (state.get('entry_acquisition_exit_latched') or a.status == Status.EXIT_PENDING or o.pending_exit_quantity > 0):
        remaining = max(0., o.position_quantity-o.pending_exit_quantity)
        if remaining > 1e-9:
            return emit('exit', 'complete_position_liquidation', Status.EXIT_PENDING, quantity=remaining)
        return emit('hold', 'exit_fill_pending', Status.EXIT_PENDING)
    if held:
        if strict and active:
            key = active['anchor']['unified_level_id']
            if (not episode or key in d.get('breakout_highs', {})) and key not in d.setdefault('entered_levels', []):
                d['entered_levels'].append(key)
        if not active:
            return emit('hold', 'position_entry_state_unavailable', Status.MANAGING)
        forming_exit = active.get('forming_resistance_exit') if episode_target_continuity else None
        if forming_episode and active.get('midpoint_chop_exit'):
            return emit('exit', 'resistance_midpoint_chop_exit', Status.EXIT_PENDING,
                        quantity=o.position_quantity,
                        metadata=dict(resistance_chop=deepcopy(active['midpoint_chop_exit'])))
        if forming_exit and not forming_episode and now-forming_exit['started_at'] > 5.:
            return emit('exit', 'forming_resistance_dwell_exit', Status.EXIT_PENDING,
                        quantity=o.position_quantity,
                        metadata=dict(forming_resistance=deepcopy(forming_exit), dwell_seconds=now-forming_exit['started_at']))
        if not active.get('trail_distance') and o.average_price > stop > 0:
            distance = o.average_price-stop
            if green_close_cap and not active.get('same_macd_episode_reentry'):
                distance = max(distance, .10)
            active.update(trail_distance=distance, peak_price=o.average_price)
        if (price_event and o.price <= stop) if strict else (quote and o.bid <= stop):
            return emit('exit', 'protective_stop', Status.EXIT_PENDING, quantity=o.position_quantity)
        if state.get('manual_exit_requested'):
            return emit('exit', 'manual_exit', Status.EXIT_PENDING, quantity=o.position_quantity)
        results = []
        trail_update = price_event or green_close if strict else bool(quote)
        if trail_update and active.get('trail_distance') and (not midpoint_execution or structure_fresh):
            trail_price = (o.price if price_event else active.get('peak_price', o.price)) if strict else o.bid
            active['peak_price'] = max(active.get('peak_price', trail_price), trail_price)
            proposal = round(floor((active['peak_price']-active['trail_distance'])/tick+1e-9)*tick, 10)
            desired = active.get('structural_stop', stop)
            if desired < (o.price if strict else o.bid):
                proposal = max(proposal, desired)
            ceiling = (next_resistance_ceiling(active, d.get('breakout_anchors', {}), rows, stop)
                       if midpoint_execution else
                       broken_resistance_ceiling(active, d.get('breakout_anchors', {}), rows, trail_price,
                           set(active.get('green_resistance_closes', {})))
                       if green_close_cap else
                       broken_resistance_ceiling(active, d.get('breakout_anchors', {}), rows, trail_price)
                       if broken_resistance_cap else
                       unbroken_resistance_ceiling(rows, trail_price, stop) if resistance_ceiling else None)
            if ceiling:
                proposal = min(proposal, ceiling['lower'])
            if stop < proposal < (o.price if strict else o.bid):
                previous_stop = stop
                state['active_stop'] = stop = proposal
                results.append(emit('replace_protective_stop', 'fixed_distance_price_trail', Status.MANAGING,
                    quantity=o.position_quantity, invalidation_price=stop, metadata=dict(previous_stop=previous_stop,
                        resistance_ceiling=deepcopy(ceiling))))
        # Re-rank on eligible trade prices using the causal V7 snapshot.
        if price_event and quote and structure_fresh:
            count = len(macd.get('broken_levels', [])) if episode_target_continuity else len(active.get('broken_levels', []))
            ordinal = target_ordinal(count)
            overhead = sorted((r for r in rows.values() if is_resistance(r)
                and E.target_price(r, tick, E.CONTRACT) > o.ask), key=lambda r:(r['lower']+r['upper'], r['unified_level_id']))
            if len(overhead) >= ordinal:
                selected = overhead[ordinal-1]
                proposal = E.target_price(selected, tick, E.CONTRACT)
                if proposal > target:
                    state['structural_profit_targets'] = [proposal]
                    active['target_moves'] += 1
                    results.append(emit('replace_profit_target', 'resistance_target_advance', Status.MANAGING,
                        quantity=o.position_quantity, profit_target_price=proposal,
                        metadata=dict(previous_profit_target=target,
                            squeeze_previous_target_moves=active['target_moves']-1,
                            profit_target_selection=dict(level=deepcopy(selected), price=proposal, ordinal=ordinal))))
                    target = proposal
            if o.bid >= target > 0 and not any(r.evaluation.intents for r in results):
                return emit('exit', 'profit_target', Status.EXIT_PENDING, quantity=o.position_quantity)
        add_confirmation_frame = bool(dual_macd_reentry and macd_100ms_fresh)
        if (midpoint_execution and fresh and structure_fresh and quote and prior_add_close
                and macd.get('open') and macd_100ms.get('open') and a.permissions.add
                and active.get('slice_notional', 0) > 0 and stop < o.bid <= o.ask < target):
            # One request per resistance: even a partial fill consumes exactly
            # that resistance. Rejections may retry only on another crossing.
            for key, level in sorted(rows.items(), key=lambda item:(midpoint(item[1]), item[0])):
                prior = prior_add_close['levels'].get(key)
                if (not is_resistance(level) or not prior or not is_resistance(prior)
                        or midpoint(prior) != midpoint(level)
                        or not prior_add_close['price'] <= midpoint(level) < o.price
                        or not midpoint_add_available(d, key, macd.get('episode_id'))):
                    continue
                results.append(emit('add_long', 'resistance_midpoint_cross_addition', Status.MANAGING,
                    invalidation_price=stop, profit_target_price=target,
                    capital_request=CapitalRequest(mode='fixed_notional', value=active['slice_notional']),
                    metadata=dict(squeeze_add_levels=[key], slice_notional=active['slice_notional'],
                        midpoint_add_crossing=dict(level=deepcopy(level), midpoint=midpoint(level),
                            previous_price=prior_add_close['price'], price=o.price, crossed_at=now,
                            previous_closed_at=prior_add_close['at'], closed_at=now, timeframe='100ms',
                            episode_id=macd.get('episode_id')))))
        if not midpoint_execution and (price_event or add_confirmation_frame) and structure_fresh and quote:
            pending = active.setdefault('pending_adds', {})
            seen = d.setdefault('attempted_add_levels', []) if strict else active.setdefault('added_levels', [])
            for key, level in crossed:
                if is_resistance(level) and key not in seen and (not strict or is_resistance(rows.get(key, {}))):
                    pending.setdefault(key, deepcopy(level))
            for key in list(pending):
                if (strict and not is_resistance(rows.get(key, {}))) or not boundary(pending[key], rows) or o.price < boundary(pending[key], rows)['price']:
                    pending.pop(key)
            confirmed_pending = ({key: level for key, level in pending.items()
                if key in active.get('add_close_confirmations', {})
                and (not forming_episode or active['add_close_confirmations'][key]['upper'] >= level['upper']
                     and active['add_close_confirmations'][key]['close'] > level['upper'])}
                if dual_macd_reentry else pending)
            if (confirmed_pending and (not dual_macd_reentry or macd_100ms.get('open'))
                    and a.permissions.add and active.get('slice_notional', 0) > 0
                    and stop < o.bid <= o.ask < target):
                keys = sorted(confirmed_pending)
                if strict and not episode:
                    anchor_stop = max(below(rows[k]['lower'], tick) for k in keys)
                    proposal = max(stop, min(anchor_stop, below(min(o.price, o.ask), tick)))
                    if proposal > stop:
                        state['active_stop'] = stop = proposal
                        active['structural_stop'] = proposal
                        results.append(emit('replace_protective_stop', 'new_resistance_protection', Status.MANAGING,
                            quantity=o.position_quantity, invalidation_price=stop))
                results.append(emit('add_long', 'price_gap_resistance_break_addition', Status.MANAGING,
                    invalidation_price=stop, profit_target_price=target,
                    capital_request=CapitalRequest(mode='fixed_notional', value=active['slice_notional']*len(keys)),
                    metadata=dict(squeeze_add_levels=keys, slice_notional=active['slice_notional'])))
                seen.extend(keys)
                for key in keys:
                    pending.pop(key, None)
        if results:
            return replace(results[-1], state=state, evaluation=replace(results[-1].evaluation,
                intents=tuple(i for r in results for i in r.evaluation.intents)))
        return emit('hold', 'fixed_distance_trail_and_resistance_target', Status.MANAGING)
    return evaluate_entry(host, a, o, p, state, d, rows, ctx, row, fresh,
                          structure_fresh, quote, evidence, emit, price_event, previous, prior_hod,
                          prior_macd_episode_high)


def evaluate_entry(host, a, o, p, state, d, rows, ctx, row, fresh,
                   structure_fresh, quote, evidence, emit, price_event, previous, prior_hod,
                   prior_macd_episode_high=0.):
    from .strategy_engine import AssignmentStatus as Status
    episode = p['early_squeeze_breakout_contract'] in EPISODE_CONTRACTS
    strict = episode or p['early_squeeze_breakout_contract'] == STRICT_CONTRACT
    macd_episode_reentry = p['early_squeeze_breakout_contract'] == MACD_EPISODE_REENTRY_CONTRACT
    dual_macd_reentry = p['early_squeeze_breakout_contract'] in (
        DUAL_MACD_REENTRY_CONTRACT, EPISODE_TARGET_CONTINUITY_CONTRACT, FORMING_EPISODE_CONTRACT, CONFIRMED_BREAKOUT_CONTRACT, VOLATILITY_CHOP_CONTRACT, MIDPOINT_EXECUTION_CONTRACT)
    episode_target_continuity = p['early_squeeze_breakout_contract'] in (EPISODE_TARGET_CONTINUITY_CONTRACT, FORMING_EPISODE_CONTRACT, CONFIRMED_BREAKOUT_CONTRACT, VOLATILITY_CHOP_CONTRACT, MIDPOINT_EXECUTION_CONTRACT)
    macd_episode_reentry = macd_episode_reentry or dual_macd_reentry
    forming_episode = p['early_squeeze_breakout_contract'] in (FORMING_EPISODE_CONTRACT, CONFIRMED_BREAKOUT_CONTRACT, VOLATILITY_CHOP_CONTRACT, MIDPOINT_EXECUTION_CONTRACT)
    now, tick = o.observed_at.timestamp(), p['execution']['tick_size']
    if a.status == Status.ENTRY_PENDING or state.get('pending_capital_request'):
        return emit('wait', 'entry_fill_pending', Status.ENTRY_PENDING)
    if a.status in (Status.DISABLED, Status.PAUSED, Status.COMPLETED, Status.ERROR):
        return emit('wait', 'entry_permission_closed')
    if not a.permissions.observe or not (a.permissions.reenter if state.get('entries', 0) else a.permissions.enter):
        return emit('wait', 'entry_permission_closed')
    if not o.market_open or not 4 <= o.observed_at.astimezone(H.NY).hour < 20:
        return emit('wait', 'outside_entry_session')
    if now <= d.get('activated_at', float('inf')):
        return emit('wait', 'waiting_for_post_squeeze_candle')
    if not price_event and not fresh:
        return emit('wait', 'waiting_for_price_update')
    if not structure_fresh:
        return emit('wait', 'filtered_v7_unavailable')
    macd = d.get('macd_1s', {})
    if p['early_squeeze_breakout_contract'] == MIDPOINT_EXECUTION_CONTRACT and not macd.get('available'):
        return emit('wait', 'forming_macd_1s_unavailable')
    if macd_episode_reentry and (not macd.get('open') or forming_episode and not macd.get('available')):
        return emit('wait', 'macd_1s_line_above_signal_required')
    if dual_macd_reentry and not d.get('macd_100ms', {}).get('open'):
        return emit('wait', 'macd_100ms_line_above_signal_required')
    same_episode_reentry = bool(macd_episode_reentry and state.get('entries', 0)
                                and d.get('last_entry_macd_episode') == macd.get('episode_id'))
    if same_episode_reentry and (not price_event or not prior_macd_episode_high
                                 or o.price <= prior_macd_episode_high):
        return emit('wait', 'waiting_for_macd_1s_episode_high_break')
    if same_episode_reentry and dual_macd_reentry:
        review = d.get('reentry_100ms_review') or {}
        if review.get('blocked'):
            return emit('wait', 'macd_episode_reentry_stopped_by_forming_resistance')
        if review.get('closes', 0) < 3 or forming_episode and now-review.get('started_at', now) < .3-1e-9:
            return emit('wait', 'waiting_for_three_completed_100ms_reentry_candles')
    confirmed_entry = p['early_squeeze_breakout_contract'] in (CONFIRMED_BREAKOUT_CONTRACT, VOLATILITY_CHOP_CONTRACT, MIDPOINT_EXECUTION_CONTRACT)
    anchor = entry_level(rows, prior_hod)
    if forming_episode and not confirmed_entry and state.get('entries', 0):
        candidates = [level for level in rows.values() if eligible(level)
                      and boundary(level, rows) and boundary(level, rows)['price'] <= o.price
                      and level['upper'] < o.price]
        anchor = max(candidates, key=lambda level:(midpoint(level), level['unified_level_id']), default=None)
    setup = d.get('initial_breakout')
    limit = boundary(anchor, rows) if anchor else None
    if setup and (not anchor or setup['anchor']['unified_level_id'] != anchor['unified_level_id']
                  or not limit or o.price < limit['price']):
        d.pop('initial_breakout', None)
        setup = None
    if price_event and anchor and limit and not confirmed_entry:
        new_high = evidence.get('prior_breakout_highs', {}).get(anchor['unified_level_id'])
        later_high = strict and anchor['unified_level_id'] in d.get('entered_levels', []) and new_high is not None and o.price > new_high
        current_move = (forming_episode and state.get('entries', 0)
                        and anchor['unified_level_id'] in d.get('breakout_highs', {})
                        and o.price >= limit['price'] and o.price > anchor['upper'])
        if not setup and previous is not None and (previous < limit['price'] <= o.price or later_high and o.price >= limit['price'] or current_move):
            setup = dict(anchor=deepcopy(anchor), breakout_at=now, peak_close=None)
            d['initial_breakout'] = setup
        elif setup:
            setup['anchor'] = deepcopy(anchor)
    if confirmed_entry:
        setup = d.get('entry_breakout')
        anchor = setup['anchor'] if setup else None
        limit = setup['boundary'] if setup else None
        confirmation = setup.get('confirmation') if setup else None
        confirmed = bool(price_event and confirmation and o.price > confirmation['threshold'])
    else:
        confirmed = bool(price_event and setup and limit and o.price >= limit['price'])
    evidence['price_breakout'].update(selected_level=deepcopy(anchor), boundary=limit)
    if strict and not confirmed_entry and confirmed and anchor['unified_level_id'] in d.get('entered_levels', []):
        previous_high = evidence['prior_breakout_highs'].get(anchor['unified_level_id'])
        confirmed = previous_high is None or o.price > previous_high
        if not confirmed:
            return emit('wait', 'resistance_breakout_new_high_required')
    recovery = None if strict else d.get('recovery')
    if price_event and recovery:
        if o.price <= recovery['high']:
            recovery.pop('crossed_at', None)
        elif previous is not None and previous <= recovery['high'] < o.price and now > recovery['stopped_at']:
            recovery['crossed_at'] = now
    recovering = bool(price_event and recovery and recovery.get('crossed_at')
                      and now > recovery['stopped_at'] and o.price > recovery['high'])
    if not confirmed and not recovering:
        return emit('wait', 'waiting_for_completed_100ms_resistance_breakout' if confirmed_entry
                    else 'waiting_for_price_gap_breakout' if strict else 'waiting_for_price_gap_breakout_or_recovery')
    quality_row = dict(effective_at=now, candle=dict(volume=o.source_values.get('market.trade_size', {}).get('value') if price_event else o.bar_volume))
    ready, quality = H.tradability(o, dict(p, structural_recovery=dict(H.QUALITY_DEFAULTS, **p['structural_recovery'])),
                                  quality_row, state, producer_freshness=True)
    evidence['liquidity_admission'] = quality
    if not ready:
        return emit('wait', 'liquidity_or_spread_gate')
    vwap_source = 'indicator.vwap.execution_value@100ms'
    source = o.source_values.get(vwap_source, {})
    at, vwap = E.stamp(source.get('observed_at')), source.get('value')
    evidence['vwap_gate'] = dict(source_id=vwap_source, value=vwap,
                               observed_at=source.get('observed_at'), close=o.price)
    prefix = (o.structural_detector_state or {}).get('price_vwap_evidence', {})
    latest_completed = (prefix.get('authority') == 'latest-completed-qmd-100ms'
        and E.stamp(prefix.get('as_of')) == o.observed_at
        and prefix.get('source_observed_at') == source.get('observed_at'))
    evidence['vwap_gate']['prefix_evidence'] = prefix
    if (not at or at > o.observed_at or at.astimezone(H.NY).date() != o.observed_at.astimezone(H.NY).date()
            or not latest_completed or type(vwap) not in (int,float) or not isfinite(vwap) or not 0 < vwap < o.price):
        return emit('wait', 'fresh_price_above_vwap_required')
    trigger_anchor = deepcopy(anchor)
    stop_anchor = anchor
    if same_episode_reentry:
        stop_anchor = max((level for level in rows.values() if eligible(level)
                      and level['upper'] < o.price),
                     key=lambda level:(midpoint(level), level['unified_level_id']), default=None)
        if not stop_anchor:
            return emit('wait', 'reentry_resistance_below_unavailable')
        if not confirmed_entry:
            anchor = stop_anchor
        desired = below(stop_anchor['lower'], tick)
        stop_source = 'reentry_resistance_below_lower'
        recovery = None
    elif confirmed:
        recovery = None
        desired = below(anchor['lower'], tick)
        stop_source = 'broken_resistance_lower'
    else:
        anchor = d.get('latest_broken_resistance', recovery['anchor'])
        desired = below(anchor['lower'], tick)
        stop_source = 'latest_broken_resistance_lower'
    # Trigger/trailing authority is the trade, but a new buy's fixed stop
    # must also be below its executable entry reference. Trades can be above
    # the current ask; offset immediately rather than emitting invalid risk.
    stop = min(desired, below(min(o.price, o.ask) if strict else o.bid, tick))
    if forming_episode and not same_episode_reentry:
        stop = min(stop, round(floor((min(o.price, o.ask)-.10)/tick+1e-9)*tick, 10))
    overhead = sorted((r for r in rows.values() if is_resistance(r)
        and r['unified_level_id'] != anchor['unified_level_id'] and E.target_price(r,tick,E.CONTRACT) > o.ask),
        key=lambda r:(r['lower']+r['upper'],r['unified_level_id']))
    broken_count = len(macd.get('broken_levels', [])) if episode_target_continuity else 0
    ordinal = target_ordinal(broken_count) if episode_target_continuity else 3
    if len(overhead) < ordinal:
        return emit('wait', 'overhead_resistance_target_unavailable')
    target = E.target_price(overhead[ordinal-1], tick, E.CONTRACT)
    if not quote or not (0 < stop < (o.price if strict else o.bid) and 0 < o.bid <= o.ask < target):
        return emit('wait', 'unrepresentable_stop_or_target')
    d['latest_broken_resistance'] = deepcopy(anchor)
    if strict:
        key = anchor['unified_level_id']
        d.setdefault('breakout_highs', {}).setdefault(key, o.price)
        if key not in d.setdefault('attempted_add_levels', []):
            d['attempted_add_levels'].append(key)
        d.pop('recovery', None)
    for key in ('liquidation_origin_fill_role', 'liquidation_origin_reentry_after_fill',
                'profit_target_liquidation_required', 'target_replenishment_pending', 'last_exit_reason'):
        state.pop(key, None)
    state.update(squeeze_entry=dict(anchor=deepcopy(anchor), structural_stop=stop if episode else desired,
        breakout_at=recovery['breakout_at'] if recovery else setup['breakout_at'],
        peak_close=setup.get('peak_close') if not recovery else None,
        entry_price=o.ask, requested_at=now, single_use_adds=strict, added_levels=[anchor['unified_level_id']] if strict else [],
        pending_adds={}, broken_levels=list(macd.get('broken_levels', [])) if episode_target_continuity else [], late=False, target_moves=0,
        same_macd_episode_reentry=same_episode_reentry,
        **(dict(stop_anchor=deepcopy(stop_anchor), trigger_anchor=trigger_anchor) if confirmed_entry else {})),
        initial_stop=stop, active_stop=stop, structural_profit_targets=[target],
        entry_reference_price=o.ask, entry_at=o.observed_at.isoformat(),
        entries=state.get('entries',0)+1, entry_acquisition_exit_latched=False)
    if macd_episode_reentry:
        d['last_entry_macd_episode'] = macd.get('episode_id')
    state['squeeze_entry']['recovery_trigger_anchor'] = deepcopy(recovery['anchor'] if recovery else anchor)
    return emit('enter_long', 'stopout_close_high_reentry' if recovery else 'squeeze_r1_price_gap_breakout', Status.ENTRY_PENDING,
        invalidation_price=stop, profit_target_price=target,
        capital_request=CapitalRequest(mode='mandate_fraction', value=1/3),
        metadata=dict(unreserved_cash_slice=True, entry_selection=deepcopy(anchor), stop_source=stop_source,
            **(dict(stop_selection=deepcopy(stop_anchor), entry_confirmation=deepcopy(confirmation)) if confirmed_entry else {}),
            structural_stop=desired, breakout_boundary=limit if not recovery else dict(price=recovery['high'], kind='frozen_closing_high'),
            frozen_reentry_high=recovery['high'] if recovery else None,
            profit_target_selection=dict(level=deepcopy(overhead[ordinal-1]),price=target,ordinal=ordinal,
                                         episode_resistance_breaks=broken_count),
            unified_structural_trigger={'current_snapshot':{'levels':[dict(anchor, entry_boundary=limit['price'] if not recovery else recovery['high'])],
                'session_high':prior_hod,'selected_at':o.observed_at.isoformat(),'frozen_at_entry':True}}))
