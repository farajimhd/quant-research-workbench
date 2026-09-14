"""Direction-symmetric, completed-candle evidence used by the indicator.

Shapes describe geometry, not predicted reversals. Retests require a later
candle than the break; OHLC cannot establish an intrabar break/retest order.
"""
from copy import deepcopy
from math import isfinite
from .structural_labels import level_context, enclosing_levels
from .immutable_evidence import FrozenDict


def known_at(level):
    value = level.get('confirmed_at_ms')
    return value/1000 if value is not None else level.get('confirmed_at')


def direction(level):
    return 1 if level['side'] in (-1, 'resistance') else -1


def level_evidence(level):
    if isinstance(level,FrozenDict):
        return level.derived('interaction_evidence',lambda:level_evidence(dict(level)))
    return {k:level[k] for k in ('unified_level_id','level_id','side','lower','upper',
        'price','pivot_at','created_at_ms','confirmed_at','confirmed_at_ms','book_version',
        'scale','prominence','score','selection_score','selection_minimum_score','reversal_distance') if k in level}


def band_key(level):
    """Co-located sources describe one encounter, without merging nearby bands."""
    if isinstance(level,FrozenDict):
        return level.derived('interaction_band_key',lambda:band_key(dict(level)))
    return ':'.join((str(direction(level)),format(level['lower'],'.12g'),format(level['upper'],'.12g')))


def band_geometry(level):
    sign=direction(level)
    lower,upper=sorted((sign*level['lower'],sign*level['upper']))
    return sign,lower,upper,max(abs(lower),abs(upper))*1e-12


def interaction_focus(events, close):
    """Presentation focus; all underlying events and source IDs remain available."""
    priority={state:rank for rank,states in enumerate([
        ('failed_breakout','failed_breakdown','resistance_reclaim','support_reclaim'),
        ('breakout','support_failure'),('rejection','support_rejection'),
        ('support_retest_held','resistance_retest_held','support_retest_unresolved','resistance_retest_unresolved'),
        ('resistance_forming','support_forming','swing_high_confirmed','swing_low_confirmed','higher_low_confirmed','lower_high_confirmed'),
        ('testing_resistance','testing_support'),('support_retest_pending','resistance_retest_pending'),
        ('approaching_resistance','approaching_support')]) for state in states}
    def identity(event):
        level=event['level']
        return band_key(level) if 'lower' in level else str(level.get('pivot_at'))+':'+str(level['price'])
    ordered=sorted(events,key=lambda e:(priority.get(e['state'],9),abs(e['level']['price']-close),identity(e)))
    return dict(primary=deepcopy(ordered[0]) if ordered else None,
                other_bands=max(0,len({identity(e) for e in events})-1),event_count=len(events))


def morphology(bar, previous, baseline, settings):
    span = bar['high']-bar['low']
    body = abs(bar['close']-bar['open'])
    upper = bar['high']-max(bar['open'], bar['close'])
    lower = min(bar['open'], bar['close'])-bar['low']
    tags = []
    if span == 0:
        tags.append('flat')
    else:
        if upper/span >= settings.tail_range_fraction:
            tags.append('upper_tail')
        if lower/span >= settings.tail_range_fraction:
            tags.append('lower_tail')
        if body/span <= settings.indecision_body_fraction:
            tags.append('indecision')
        if body >= baseline*settings.expansion_body_multiple and body/span >= settings.expansion_body_fraction:
            tags.append('bullish_expansion' if bar['close']>bar['open'] else 'bearish_expansion')
    if previous:
        if bar['high']<=previous['high'] and bar['low']>=previous['low']:
            tags.append('inside')
        elif bar['high']>=previous['high'] and bar['low']<=previous['low']:
            tags.append('outside')
        if (bar['close']-bar['open'])*(previous['close']-previous['open'])<0 and min(bar['open'],bar['close'])<=min(previous['open'],previous['close']) and max(bar['open'],bar['close'])>=max(previous['open'],previous['close']):
            tags.append('bullish_engulfing' if bar['close']>bar['open'] else 'bearish_engulfing')
    return dict(tags=tags or ['ordinary'], body_fraction=body/span if span else 0,
                upper_tail_fraction=upper/span if span else 0, lower_tail_fraction=lower/span if span else 0,
                close_location=(bar['close']-bar['low'])/span if span else .5)


class Interactions:
    """Separate identity-based encounters, breaks, retests and role changes."""
    def __init__(self, retention_candles=1800):
        self.tracks = {}
        self.retention_candles = retention_candles

    def observe(self, bar, previous, levels, proximity, sequence, qualification=None):
        prior_levels = [l for l in levels if known_at(l) is not None and known_at(l)<=bar['time']]
        enclosing = enclosing_levels(prior_levels,previous) if previous is not None else (None,None)
        incoming = {}
        for level in levels:
            key = band_key(level)
            source = str(level.get('unified_level_id',level.get('level_id')))
            if key not in incoming:
                incoming[key] = dict(level=level, source_ids=set())
            incoming[key]['source_ids'].add(source)
        # Retain break witnesses after the source book removes or flips a band.
        expired = [key for key,t in self.tracks.items() if key not in incoming and
                   sequence-t['last_seen']>self.retention_candles]
        for key in expired:
            del self.tracks[key]
        events = []
        for key, group in incoming.items():
            level = group['level']
            if key not in self.tracks:
                self.tracks[key] = dict(level=level_evidence(level), phase='active', encounters=0,
                    contact=False, contact_bars=0, break_at=None, last_seen=sequence,
                    rejection_closes=0, departure=0., encounter_depth=0., previous_depth=None,
                    rejection_trend='unestablished', source_ids=[])
            elif self.tracks[key]['phase']=='active':
                self.tracks[key]['level'] = level_evidence(level)
            self.tracks[key]['last_seen'] = sequence
            self.tracks[key]['source_ids'] = sorted(group['source_ids'])
        if len(self.tracks)>4096:
            raise ValueError('Structural interaction capacity exceeded; refusing partial evidence')
        candles={sign:(sign*bar['close'],sign*bar['open'],*sorted((sign*bar['low'],sign*bar['high'])),
            sign*previous if previous is not None else None) for sign in (-1,1)}
        q=qualification
        quiet_safe=not q or (type(q.get('ready')) is bool
            and (q.get('atr') is None or type(q['atr']) in (int,float) and isfinite(q['atr']) and q['atr']>=0)
            and all(type(q.get(k)) in (int,float) and isfinite(q[k]) and q[k]>0
                for k in ('penetration_atr','body_atr','body_fraction','acceptance_closes'))
            and type(q.get('price_floor',0)) in (int,float) and isfinite(q.get('price_floor',0)))
        for key,t in self.tracks.items():
            if key not in incoming and t['phase']=='active':
                continue
            level = t['level']
            if isinstance(level,FrozenDict):
                sign,lower,upper,eps=level.derived('interaction_geometry',lambda:band_geometry(level))
                close,opened,low,high,prev=candles[sign]
                # Quiet active bands still advance every bookkeeping field.
                # They cannot emit an event or create an attempt witness.
                if (quiet_safe and t['phase']=='active' and not (high>=lower-eps and low<=upper+eps)
                        and not 0<lower-close<=proximity
                        and not (prev is not None and close>upper+eps and (prev<=upper+eps or t.get('attempt_atr')))):
                    t['contact_bars']=0
                    t['departure']=max(t['departure'],max(lower-high,low-upper,0))
                    if qualification and close<=upper:
                        t.pop('attempt_atr',None);t.pop('attempt_context',None);t['attempt_closes']=0
                    t['contact']=False
                    continue
            else:
                sign,lower,upper,eps=band_geometry(level)
                close,opened,low,high,prev=candles[sign]
            prior_break = t['break_at']
            q = qualification
            atr = t.get('attempt_atr') or (q['atr'] if q else None)
            distance_floor = max(atr*q['penetration_atr'],q.get('price_floor',0)) if q and atr else eps
            context = t.get('attempt_context') or (level_context(level, prior_levels, previous, atr,enclosing) if q and previous is not None else {})
            known_before = known_at(level) is not None and known_at(level)<=bar['time']
            body = abs(close-opened)
            span = high-low
            strong = bool(q and q['ready'] and atr and close>opened and body>=atr*q['body_atr'] and
                          (body/span if span else 0)>=q['body_fraction'])
            contact = high>=lower-eps and low<=upper+eps
            if contact:
                if not t['contact']:
                    # A bar outside the band alone is not an independent retest.
                    if t['encounters']==0 or t['departure']>=max(proximity,upper-lower):
                        if t['encounters']:
                            t['previous_depth'] = t['encounter_depth'] or None
                        t['encounters'] += 1
                        t['encounter_depth'] = 0.
                        t['rejection_closes'] = 0
                    t['departure'] = 0.
                t['contact_bars'] += 1
            else:
                t['contact_bars'] = 0
                t['departure'] = max(t['departure'],max(lower-high,low-upper,0))
            kind = None
            if t['phase']=='active':
                if prev is not None and close>upper+eps and (prev<=upper+eps or t.get('attempt_atr')):
                    if q:
                        t.setdefault('attempt_atr',q['atr'])
                        t.setdefault('attempt_context',context)
                        t['attempt_closes'] = t.get('attempt_closes',0)+1
                    sustained = q and t.get('attempt_closes',0)>=q['acceptance_closes'] and close-upper>=2*distance_floor
                    if not q or q['ready'] and known_before and close-upper>=distance_floor and (strong or sustained) and context.get('significance')!='minor':
                        kind = 'breakout' if sign==1 else 'support_failure'
                        t.update(phase='retest_pending',break_at=bar['end'],accepted=False,acceptance_closes=0,retest_at=None)
                        t['rejection_closes'] = 0
                    else:
                        kind = 'resistance_cross' if sign==1 else 'support_cross'
                elif contact and close<lower-eps and close<opened:
                    qualified = not q or q['ready'] and known_before and lower-close>=distance_floor and context.get('significance')!='minor'
                    kind = ('rejection' if sign==1 else 'support_rejection') if qualified else ('testing_resistance' if sign==1 else 'testing_support')
                elif contact:
                    kind = 'testing_resistance' if sign==1 else 'testing_support'
                elif 0<lower-close<=proximity:
                    kind = 'approaching_resistance' if sign==1 else 'approaching_support'
            else:
                # This branch cannot execute on the breaking candle.
                if close<lower-distance_floor:
                    kind = 'failed_breakout' if sign==1 else 'failed_breakdown'
                    if t['phase']=='role_reversed':
                        kind = 'support_reclaim' if sign==-1 else 'resistance_reclaim'
                    t.update(phase='active',break_at=None,accepted=False)
                    t.pop('attempt_atr',None)
                    t.pop('attempt_context',None)
                    t['attempt_closes']=0
                elif contact:
                    if close>upper+distance_floor and (not q or t.get('retest_at') is not None and t['retest_at']<bar['end']):
                        kind = 'support_retest_held' if sign==1 else 'resistance_retest_held'
                        t['phase']='role_reversed'
                        t['accepted']=True
                    else:
                        kind = 'support_retest_unresolved' if sign==1 else 'resistance_retest_unresolved'
                        if t.get('retest_at') is None:
                            t['retest_at']=bar['end']
                elif t['phase']=='retest_pending':
                    kind = 'support_retest_pending' if sign==1 else 'resistance_retest_pending'
                    if q and t.get('retest_at') is not None and close>upper+distance_floor:
                        kind = 'support_retest_held' if sign==1 else 'resistance_retest_held'
                        t.update(phase='role_reversed',accepted=True)
                if q and t['phase']!='active':
                    t['acceptance_closes'] = t.get('acceptance_closes',0)+1 if close>upper+distance_floor else 0
                    if not t.get('accepted') and t['acceptance_closes']>=q['acceptance_closes']:
                        t['accepted']=True
                        kind='breakout_accepted' if sign==1 else 'breakdown_accepted'
            if q and t['phase']=='active' and close<=upper:
                t.pop('attempt_atr',None)
                t.pop('attempt_context',None)
                t['attempt_closes']=0
            t['contact'] = contact
            if kind in ('rejection','support_rejection'):
                t['rejection_closes'] += 1
                t['encounter_depth'] = max(t['encounter_depth'],lower-close)
                if t['previous_depth'] is not None:
                    difference=t['encounter_depth']-t['previous_depth']
                    t['rejection_trend']='deeper_rejection' if difference>proximity else 'shallower_rejection' if difference<-proximity else 'similar_rejection'
            distance=max(lower-high,low-upper,0)
            if kind and kind.endswith('_pending') and distance>proximity:
                continue  # Witness stays retained; no current interaction occurred.
            if kind:
                event = dict(state=kind,level=deepcopy(level),phase=t['phase'],
                    band_id=key,source_ids=list(t['source_ids']),
                    encounters=t['encounters'],contact_bars=t['contact_bars'],break_at=t['break_at'] or prior_break,
                    rejection_closes=t['rejection_closes'],rejection_depth=t['encounter_depth'],
                    rejection_trend=t['rejection_trend'])
                if q:
                    event.update(context=context,qualification=dict(prior_atr=atr,ready=q['ready'],
                        penetration_atr=max(0,close-upper)/atr if atr else None,
                        body_atr=body/atr if atr else None,body_fraction=body/span if span else 0,
                        required_penetration_atr=q['penetration_atr'],required_body_atr=q['body_atr'],
                        required_body_fraction=q['body_fraction'],accepted=t.get('accepted',False),
                        status='unqualified_cross' if kind.endswith('_cross') else 'qualified' if kind in ('breakout','support_failure','rejection','support_rejection') else 'observed',
                        reasons=(['insufficient_prior_atr'] if not q['ready'] else [])+
                            (['level_not_known_before_candle'] if not known_before else [])+
                            (['minor_level'] if context.get('significance')=='minor' else [])+
                            (['shallow_penetration'] if close>upper and close-upper<distance_floor else [])+
                            (['small_or_weak_body'] if close>upper and not strong else [])))
                events.append(event)
        return events, len(expired)

    def context(self):
        return dict(tracked_bands=len(self.tracks),pending_retests=sum(t['phase']=='retest_pending' for t in self.tracks.values()),
                    role_reversed=sum(t['phase']=='role_reversed' for t in self.tracks.values()))


def swing_bias(levels):
    """Require both highs and lows to agree; never infer trend from MACD."""
    def pivot_time(level):
        # V5 names the source pivot created_at_ms; local swings use pivot_at.
        return level.get('pivot_at') if level.get('pivot_at') is not None else (
            level['created_at_ms']/1000 if level.get('created_at_ms') is not None else None)
    known = [l for l in levels if pivot_time(l) is not None]
    highs = sorted((l for l in known if direction(l)==1),key=pivot_time)[-2:]
    lows = sorted((l for l in known if direction(l)==-1),key=pivot_time)[-2:]
    if len(highs)<2 or len(lows)<2:
        return 'unknown'
    if any(pivot_time(pair[0])==pivot_time(pair[1]) for pair in (highs,lows)):
        return 'unknown'
    changes = [pair[1]['price']-pair[0]['price'] for pair in (highs,lows)]
    return 'bullish' if all(c>0 for c in changes) else 'bearish' if all(c<0 for c in changes) else 'neutral'
