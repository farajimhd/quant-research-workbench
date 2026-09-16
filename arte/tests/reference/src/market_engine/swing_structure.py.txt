"""Session-only causal directional-change structure. No persistence or hindsight.

One pass over completed OHLC seconds; bounded live state, delta-only output.
An extreme from a bar cannot be confirmed by that same bar: OHLC does not
establish whether its high or low happened first. All times are UTC seconds.
"""
from collections import Counter, deque
from dataclasses import dataclass, asdict
from math import isfinite
from statistics import median


@dataclass(frozen=True)
class SwingSettings:
    reversal_bps: float = 50
    volatility_multiple: float = 2
    major_multiple: float = 3
    volatility_cap_multiple: float = 2
    local_lifetime_seconds: float = 1800
    major_lifetime_seconds: float = 7200
    max_active: int = 2048
    max_segments: int = 20000

    def __post_init__(self):
        for value in asdict(self).values():
            if not isfinite(value) or value <= 0:
                raise ValueError('Swing settings must be finite and positive')
        if self.major_multiple < 1:
            raise ValueError('Major scale must be at least the local scale')


class SwingStructure:
    def __init__(self, settings=SwingSettings()):
        self.settings = settings
        self.ranges = deque(maxlen=30)
        self.previous_close = None
        self.current_close = None
        self.last_time = float('-inf')
        self.detectors = [dict(scale=s, direction=0, high=None, low=None) for s in ('local', 'major')]
        self.active = {}
        self.segments = []
        self.counts = Counter()
        self.sequence = 0
        self.approach = deque()
        self.stalls = {'resistance': None, 'support': None}

    def _publish(self, level, t, reason):
        self.counts['events_'+reason] += 1
        # This API is a visual projection, not a score/state journal. A touch
        # or score change does not change a line. Both pending phases are dashed.
        if reason in ('rejection', 'retest_contact'):
            return
        old = level.get('segment')
        if old is not None:
            self.segments[old]['valid_to'] = t
        if reason == 'expired':
            self.counts['expired'] += 1
            return
        if len(self.segments) >= self.settings.max_segments:
            raise RuntimeError('Swing segment budget exceeded; no partial result')
        level['segment'] = len(self.segments)
        self.segments.append({k: level[k] for k in
            ('level_id', 'price', 'lower', 'upper', 'side', 'scale', 'pivot_at', 'confirmed_at', 'confirmation_kind', 'reversal_distance')}
            | dict(valid_from=t, valid_to=None, reason=reason,
                   state='active' if level['state'] == 'active' else 'pending'))

    def _found(self, detector, extreme, side, t, reason='reversal_confirmed'):
        price, pivot_at, distance = extreme
        tick = .0001 if price < 1 else .01
        width = max(tick, price * .0002)
        # Repeated nearby pivots reinforce one anchored level. Do not union
        # bands transitively or merge support with resistance / local with major.
        matches = [l for l in self.active.values() if l['side'] == side
                   and l['scale'] == detector['scale'] and l['state'] == 'active'
                   and abs(l['price']-price) <= min(width, (l['upper']-l['lower'])/2)]
        if matches:
            level = min(matches, key=lambda l: (abs(l['price']-price), l['level_id']))
            level['last_test'] = t
            self._level_updated(level)
            # A departure/retest already counts the encounter; another pivot
            # must not inflate the score for the same encounter.
            return
        if detector['scale'] == 'major' and self._inside_consolidation(price, pivot_at, t):
            self.counts['suppressed_interior_major'] += 1
            return
        if len(self.active) >= self.settings.max_active:
            raise RuntimeError('Swing active-level budget exceeded; no partial result')
        self.sequence += 1
        level = dict(level_id=self.sequence, price=price, lower=price-width, upper=price+width,
                     side=side, scale=detector['scale'], pivot_at=pivot_at, confirmed_at=t,
                     last_test=t, tests=1, strength=1., state='active', beyond=0,
                     touching=False, previous_contact=False, break_at=None)
        level.update(confirmation_kind=reason, reversal_distance=distance)
        level.update(formed_at=t, last_role_change_at=None, role_retests=0)
        self.active[level['level_id']] = level
        self._level_updated(level)
        self.counts['confirmed_'+detector['scale']] += 1
        self._publish(level, t, reason)

    def _inside_consolidation(self, price, pivot_at, t):
        # A range must already exist before this candidate and contain a real
        # stretch of subsequent trading. Never invent a range retrospectively.
        recent = [row for row in self.approach if row[0] >= t-15]
        if len(recent) < 5 or recent[-1][0]-recent[0][0] < 10:
            return False
        prices = [r[1] for r in recent]
        if self.current_close is not None:
            prices.append(self.current_close)
        minimum, maximum = min(prices), max(prices)
        levels = [l for l in self.active.values() if l['scale']=='major' and l['state']=='active'
                  and l['confirmed_at'] < min(pivot_at,t-15)]
        supports = [l for l in levels if l['side']=='support' and l['upper'] < minimum]
        resistances = [l for l in levels if l['side']=='resistance' and l['lower'] > maximum]
        if not supports or not resistances:
            return False
        bottom, top = max(l['upper'] for l in supports), min(l['lower'] for l in resistances)
        return bottom < price < top

    def _stall(self, t, high, low, close):
        # Adjacent touches are one encounter. A second encounter requires a
        # whole bar away from the boundary before returning. Two candidates only.
        while self.approach and self.approach[0][0] < t-30:
            self.approach.popleft()
        for side, price in (('resistance', high), ('support', low)):
            sign = 1 if side == 'resistance' else -1
            tick = .0001 if price < 1 else .01
            candidate = self.stalls[side]
            if candidate and (t-candidate['first'] > 120 or sign*(close-candidate['price']) > candidate['tick']):
                candidate = None
            if candidate and sign*(price-candidate['price']) > candidate['tolerance']+1e-9:
                candidate = None
            if candidate is None and self.approach:
                origin = min(p[1] for p in self.approach) if sign == 1 else max(p[1] for p in self.approach)
                boundary = max(p[2] for p in self.approach) if sign == 1 else min(p[3] for p in self.approach)
                floor = self.settings.major_multiple*max(2*tick, price*self.settings.reversal_bps/10000)
                if sign*(price-origin) >= floor and sign*(price-boundary) >= -max(3*tick,price*.001):
                    candidate = dict(price=price, first=t, tick=tick,
                        tolerance=max(3*tick,price*.001), tests=0, encounters=1,
                        departure=max(3*tick,price*self.settings.reversal_bps/10000),
                        rejection=floor, away=False, emitted=False)
            if candidate:
                # Compare tick-rounded prices so fractional executions a fraction
                # of a tick from the repeated boundary are not separate barriers.
                near = abs(round(price/candidate['tick'])*candidate['tick']-candidate['price']) <= candidate['tolerance']+1e-9
                if near and sign*(close-candidate['price']) <= candidate['tick']:
                    candidate['tests'] += 1
                    if candidate['away']:
                        candidate['encounters'] += 1
                        candidate['away'] = False
                retreat = sign*(candidate['price']-close)
                rejected = candidate['tests'] >= 3 and retreat >= candidate['rejection'] and self.previous_close is not None and sign*(close-self.previous_close) < 0
                retested = near and candidate['encounters'] >= 2 and retreat >= candidate['tolerance']
                if not candidate['emitted'] and (rejected or retested) and t > candidate['first']:
                    self._found({'scale':'major'}, (candidate['price'],candidate['first'],None), side,t,
                                'boundary_retest_confirmed' if retested else 'boundary_rejection_confirmed')
                    candidate['emitted'] = True
                if not near and sign*(candidate['price']-price) > candidate['tolerance'] and retreat >= candidate['departure']:
                    candidate['away'] = True
            self.stalls[side] = candidate
        self.approach.append((t,close,high,low))

    def _expired(self, level, t):
        ttl = self.settings.local_lifetime_seconds if level['scale']=='local' else self.settings.major_lifetime_seconds
        return t-level['last_test'] >= ttl

    def _levels_to_update(self, t, high, low, close, tick):
        return list(self.active)

    def _level_updated(self, level):
        pass

    def _level_removed(self, key):
        pass

    def observe(self, t, high, low, close):
        if not all(isfinite(x) for x in (t, high, low, close)) or low <= 0 or not low <= close <= high or t <= self.last_time:
            raise ValueError('Require ordered distinct completed bars with valid positive OHLC')
        self.current_close = close
        # Levels and thresholds use only already observed bars.
        tick = .0001 if close < 1 else .01
        # Robust prior-only volatility is capped before freezing each extreme.
        volatility = median(self.ranges) if self.ranges else 0.
        for key in self._levels_to_update(t, high, low, close, tick):
            level = self.active[key]
            if self._expired(level, t):
                self._publish(level, t, 'expired')
                del self.active[key]
                self._level_removed(key)
                continue
            side, lower, upper = level['side'], level['lower'], level['upper']
            contact = low <= upper and high >= lower
            beyond = close < lower-tick if side == 'support' else close > upper+tick
            rejected = close > upper+tick if side == 'support' else close < lower-tick
            if level['state'] == 'active':
                level['beyond'] = level['beyond']+1 if beyond else 0
                if level['beyond'] >= 2:
                    level.update(state='awaiting_retest', break_at=t, touching=False, last_test=t)
                    self._publish(level, t, 'accepted_break')
                    self.counts['accepted_breaks'] += 1
                else:
                    if contact:
                        if not level['previous_contact']:
                            level['touching'] = True
                            level['role_contact_at'] = t
                        level['last_test'] = t
                    if level['touching'] and rejected:
                        level['touching'] = False
                        level['tests'] += 1
                        level['strength'] += 1
                        if t > level.get('role_contact_at', t):
                            level['role_retests'] = level.get('role_retests', 0) + 1
                        self._publish(level, t, 'rejection')
            else:
                # A later bar must touch from the other side, then a subsequent
                # close depart on that side. No role flip on a crossing alone.
                if level['state'] == 'awaiting_retest' and contact and t > level['break_at']:
                    level.update(state='retest_contact', contact_at=t, last_test=t)
                    self._publish(level, t, 'retest_contact')
                elif level['state'] == 'retest_contact' and beyond and t > level['contact_at']:
                    level.update(side='resistance' if side == 'support' else 'support', state='active',
                                 beyond=0, touching=False, last_test=t, confirmed_at=t)
                    level.update(last_role_change_at=t, role_retests=0)
                    self._publish(level, t, 'role_reversal')
                    self.counts['role_reversals'] += 1
                elif rejected:
                    level.update(state='active', beyond=0, touching=False, last_test=t)
                    self._publish(level, t, 'failed_break')
            level['previous_contact'] = contact
            self._level_updated(level)
        self._stall(t,high,low,close)
        for d in self.detectors:
            multiplier = 1 if d['scale'] == 'local' else self.settings.major_multiple
            def distance(price):
                floor = max(2*(.0001 if price < 1 else .01), price*self.settings.reversal_bps/10000)
                return multiplier*max(floor, min(volatility*self.settings.volatility_multiple,
                                                floor*self.settings.volatility_cap_multiple))
            if d['high'] is None:
                d.update(high=(high,t,distance(high)), low=(low,t,distance(low)))
                continue
            if high > d['high'][0]: d['high'] = (high,t,distance(high))
            if low < d['low'][0]: d['low'] = (low,t,distance(low))
            up = d['direction'] >= 0
            down = d['direction'] <= 0
            high_confirm = up and d['high'][1] < t and close < self.previous_close and close <= d['high'][0]-d['high'][2]
            low_confirm = down and d['low'][1] < t and close > self.previous_close and close >= d['low'][0]+d['low'][2]
            if high_confirm and low_confirm:
                # Ambiguous initialization: establish a direction first.
                d.update(high=(high,t,distance(high)), low=(low,t,distance(low)))
            elif high_confirm:
                self._found(d, d['high'], 'resistance', t)
                d.update(direction=-1, low=(low,t,distance(low)), high=(high,t,distance(high)))
            elif low_confirm:
                self._found(d, d['low'], 'support', t)
                d.update(direction=1, high=(high,t,distance(high)), low=(low,t,distance(low)))
        tr = high-low if self.previous_close is None else max(high-low, abs(high-self.previous_close), abs(low-self.previous_close))
        self.ranges.append(tr)
        self.previous_close, self.last_time = close, t
        self.counts['bars'] += 1

    def result(self):
        return dict(algorithm='causal-session-swing-v4', settings=asdict(self.settings),
                    segments=self.segments, counts=dict(self.counts), active_levels=len(self.active),
                    through=self.last_time if self.counts['bars'] else None)
