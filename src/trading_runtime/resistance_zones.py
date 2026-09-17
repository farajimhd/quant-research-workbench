"""Causal resistance grouping and completed-candle retest evidence."""
from copy import deepcopy
from statistics import median


def observe(o, state, rows):
    from .historical_hod import NY
    session = o.observed_at.astimezone(NY).date().isoformat()
    if state.get('session') != session:
        state.clear()
        state.update(session=session, known={}, broken=[], physical={}, break_rows={})
    previous = state.get('price')
    previous_members = set(state['physical'])
    now = o.observed_at.timestamp()
    # Threshold uses only zones broken before this observation.
    broken = [state['break_rows'][key] for key in state['broken']]
    gaps = [b['lower']-a['upper'] for a, b in zip(broken, broken[1:]) if b['lower'] > a['upper']]
    threshold = .5*median(gaps) if len(gaps) >= 3 else 0.
    frozen = {k: v for k, v in state['known'].items() if v.get('encountered')}
    assigned = {member for zone in frozen.values() for member in zone['members']}
    for key, row in rows.items():
        if key not in assigned and row.get('side') in (-1, 'resistance'):
            state['physical'][key] = deepcopy(row)
    remaining = sorted((r for k, r in state['physical'].items() if k not in assigned),
                       key=lambda r: (r['lower'], r['unified_level_id']))
    groups = []
    for row in remaining:
        if groups and row['lower']-groups[-1]['upper'] <= threshold:
            group = groups[-1]
            group['upper'] = max(group['upper'], row['upper'])
            group['members'].append(row['unified_level_id'])
        else:
            group = deepcopy(row)
            group.update(unified_level_id='zone:'+row['unified_level_id'], members=[row['unified_level_id']],
                         encountered=False, grouping_threshold=threshold)
            groups.append(group)
    for group in groups:
        group['seen_below'] = (previous is not None and previous <= group['upper']
                              and all(member in previous_members for member in group['members']))
    state['known'] = {**frozen, **{r['unified_level_id']: r for r in groups}}
    crossed = []
    for key, row in sorted(state['known'].items(), key=lambda item: item[1]['upper']):
        # A newly discovered zone behind price is not a retrospectively earned break.
        seen_below = row.get('seen_below', False)
        if previous is not None and seen_below and previous <= row['upper'] < o.price and key not in state['broken']:
            row['broken_at'] = now
            state['broken'].append(key)
            state['break_rows'][key] = deepcopy(row)
            crossed.append((key, row))
        if o.price <= row['upper']:
            row['seen_below'] = True
        if o.price >= row['lower']:
            row['encountered'] = True
    state.update(price=o.price, at=now, grouping_threshold=threshold, grouping_gap_samples=len(gaps))
    return crossed


def observe_retests(market, previous, bar, rows, ladder):
    """A break must precede the pivot candle; recovery must be a later close."""
    row = market['row']
    reset = market.get('reset') or previous.get('session') != market.get('session')
    prior = {} if reset else previous.get('vwap_retests', {})
    count = len(ladder.get('broken', []))
    candidates = deepcopy(prior.get('transition_breaks', {}))
    prior_rows = prior.get('levels', {})
    for key, level in prior_rows.items():
        if (level.get('side') in (0, -1, 'transition', 'resistance')
                and prior.get('close', float('inf')) <= level['upper'] < bar['close']):
            candidates.setdefault(key, dict(level, broken_at=bar['end']))
    grouped_members = {member for zone in ladder.get('known', {}).values() for member in zone.get('members', [])}
    anchors = {k: r for k, r in candidates.items() if k not in grouped_members}
    anchors.update(ladder.get('break_rows', {}))
    pivots = {s['pivot_at'] for s in row.get('local_swings', []) + row.get('confirmed_swings', [])
              if s.get('side') in (1, 'support')}
    developing = (row.get('developing_swings') or {}).get('low') or {}
    if developing.get('pivot_at') is not None:
        pivots.add(developing['pivot_at'])
    pivots.add(bar['end'])
    witnesses = [deepcopy(w) for w in prior.get('witnesses', [])
                 if w['pivot_at'] in pivots and w['break_count'] == count]
    for key, anchor in anchors.items():
        if (anchor.get('broken_at', float('inf')) < bar['time']
                and bar['low'] <= anchor['upper'] and bar['high'] >= anchor['lower']):
            witnesses.append(dict(anchor=deepcopy(anchor), pivot_at=bar['end'], pivot_price=bar['low'],
                                  break_count=count))
    for witness in witnesses:
        if (bar['end'] > witness['pivot_at'] and bar['close'] > witness['anchor']['upper']
                and witness.get('recovered_at') is None):
            witness['recovered_at'] = bar['end']
    market['vwap_retests'] = dict(levels=deepcopy(rows), close=bar['close'], transition_breaks=candidates,
                                  witnesses=witnesses)
    row['vwap_retests'] = deepcopy(witnesses)


def entry_anchor(o, ladder, late, threshold):
    from datetime import datetime, time
    from .historical_hod import NY
    row = (o.structural_detector_state or {}).get('row', {})
    count = len(ladder.get('broken', []))
    swings = row.get('local_swings', []) + row.get('confirmed_swings', [])
    now = o.observed_at.timestamp()
    cutoff = datetime.combine(o.observed_at.astimezone(NY).date(), time(4, 5), NY).timestamp()
    candidates = []
    for witness in row.get('vwap_retests', []):
        if (witness['break_count'] != count or late and witness['break_count'] < threshold
                or witness.get('recovered_at', float('inf')) > now
                or o.price <= witness['anchor']['upper'] or o.bid <= witness['anchor']['lower']):
            continue
        swing = next((s for s in swings if s.get('side') in (1, 'support')
            and s.get('state', 'active') == 'active' and s['pivot_at'] == witness['pivot_at']
            and s['price'] == witness['pivot_price'] and cutoff <= s['pivot_at'] <= s['confirmed_at'] <= now), None)
        if swing:
            candidates.append(dict(witness, swing=deepcopy(swing)))
    return deepcopy(max(candidates, key=lambda w: (w['anchor']['lower'], w['pivot_at']))) if candidates else None
