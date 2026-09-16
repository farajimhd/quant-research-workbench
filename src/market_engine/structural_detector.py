"""Causal candle regimes. No strategy, position, order, or MACD eligibility gates.

Consumers supply completed OHLC and the global book known at that close. A
decision is a value snapshot; later swing confirmations never mutate it.
"""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, asdict
from math import isfinite
from datetime import datetime
from zoneinfo import ZoneInfo

from .swing_structure import SwingSettings
from .swing_pivot_witness import PivotWitnessStructure
from .structural_evidence import Interactions, morphology, swing_bias, interaction_focus
from .structural_progression import Progression
from .structural_volume import VolumeLevels
from .structural_labels import label_packet
from .structural_momentum import observe as observe_momentum
from .structural_signal import observe as observe_signal

VERSION = 'structural-candle-detector-11'


@dataclass(frozen=True)
class DetectorSettings:
    reversal_bps: float = 50
    volatility_multiple: float = 2
    body_half_life: float = 5
    consolidation_body_multiple: float = .25
    proximity_body_multiple: float = 1
    macd_gap_bps: float = 25
    macd_change_bps: float = .1
    momentum_confirm_closes: int = 2
    rsi_period: int = 14
    rsi_neutral_band: float = 5
    rsi_change_points: float = .5
    signal_setup_candles: int = 60
    signal_confirmation_candles: int = 3
    signal_hold_candles: int = 300
    signal_min_reward_risk: float = 1.5
    signal_stop_atr: float = .1
    signal_zone_atr: float = .2
    signal_min_stop_atr: float = 1
    signal_max_risk_atr: float = 4
    signal_max_extension_atr: float = 3
    signal_min_room_atr: float = 1
    signal_progress_candles: int = 20
    signal_follow_through_candles: int = 5
    signal_profit_activation_r: float = 1
    signal_profit_giveback_fraction: float = .4
    tail_range_fraction: float = .5
    indecision_body_fraction: float = .2
    expansion_body_multiple: float = 1.5
    expansion_body_fraction: float = .65
    movement_body_multiple: float = .1
    movement_min_bps: float = 1
    deep_correction_multiple: float = 2
    evidence_memory_candles: int = 1800
    pressure_closes: int = 2
    volume_half_life: float = 5
    volume_change_fraction: float = .1
    volume_warmup_candles: int = 5
    session_level_count: int = 3
    volume_expansion_multiple: float = 1.5
    volume_divergence_min_score: float = 30
    volume_setup_max_candles: int = 20
    atr_period: int = 14
    atr_warmup_candles: int = 5
    break_body_atr: float = .3
    break_body_fraction: float = .4
    penetration_atr: float = .1
    acceptance_closes: int = 2

    def __post_init__(self):
        if type(self.signal_follow_through_candles) is not int or not 1<=self.signal_follow_through_candles<=10000 or not 0<self.signal_profit_giveback_fraction<1:
            raise ValueError('Invalid position lifecycle settings')
        if any(type(v) is not int or not 1<=v<=10000 for v in (self.signal_setup_candles,self.signal_confirmation_candles,self.signal_hold_candles,self.signal_progress_candles)):
            raise ValueError('Invalid signal lifetime')
        if type(self.rsi_period) is not int or not 2<=self.rsi_period<=200 or type(self.momentum_confirm_closes) is not int or not 1<=self.momentum_confirm_closes<=20 or not 0<self.rsi_neutral_band<20:
            raise ValueError('Invalid momentum settings')
        if any(not isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError('Detector settings must be finite and positive')
        if self.volume_change_fraction > 1 or self.volume_divergence_min_score > 100:
            raise ValueError('Volume fraction or evidence score outside its range')
        if any(not isinstance(v,int) for v in (self.volume_warmup_candles,self.session_level_count,self.volume_setup_max_candles)):
            raise ValueError('Volume and session counts must be integers')
        if any(not isinstance(v,int) for v in (self.atr_period,self.atr_warmup_candles,self.acceptance_closes)) or self.atr_warmup_candles>self.atr_period or self.break_body_fraction>1:
            raise ValueError('Invalid ATR qualification settings')


def compact(level):
    return {k: level[k] for k in ('unified_level_id', 'level_id', 'side', 'lower', 'upper',
            'price', 'pivot_at', 'confirmed_at', 'confirmed_at_ms', 'book_version', 'scale', 'prominence', 'score', 'selection_score', 'selection_minimum_score','reversal_distance') if k in level}



class StructuralDetector:
    def __init__(self, settings=DetectorSettings()):
        self.settings = settings
        self.swings = PivotWitnessStructure(SwingSettings(reversal_bps=settings.reversal_bps,
            volatility_multiple=settings.volatility_multiple))
        self.last = None
        self.body = None
        self.pullback = None
        self.global_levels = []
        self.sequence = 0
        self.last_support = None
        self.last_resistance = None
        self.trend = 0
        self.extreme = None
        self.movement_anchor = None
        self.progression = Progression(settings)
        self.volume_levels = VolumeLevels(settings)
        self.local_interactions = Interactions(settings.evidence_memory_candles)
        self.global_interactions = Interactions(settings.evidence_memory_candles)
        self.pivots = deque(maxlen=64)
        self.episode_direction = 0
        self.ema_fast = self.ema_slow = self.signal = None
        self.episode = None
        self.close_times = {}
        self.forming_witness = {}
        self.true_ranges = deque(maxlen=settings.atr_period)
        self.recent_bars = deque(maxlen=6)
        self.label_signature = None
        self.momentum_state = {}
        self.signal_state = {}

    def local_evidence(self, level, *, include_pivot=False):
        result = compact(level)
        for key in ('pivot_at', 'confirmed_at'):
            if result.get(key) is not None:
                result[key] = self.close_times[result[key]]
        if include_pivot:
            result['fresh_pivot'] = self.swings.event_evidence(level, self.close_times)
        return result

    def observe(self, bar, levels=None, global_status='unavailable', *, continuity=None):
        start, end = bar['time'], bar['end']
        if not all(isfinite(bar[k]) for k in ('time', 'end', 'open', 'high', 'low', 'close')):
            raise ValueError('Non-finite candle')
        if end <= start or bar['low'] <= 0 or not bar['low'] <= min(bar['open'], bar['close']) <= max(bar['open'], bar['close']) <= bar['high']:
            raise ValueError('Invalid completed OHLC candle')
        if self.last and (end <= self.last['end'] or start < self.last['end']):
            raise ValueError('Candles must be distinct, ordered, and non-overlapping')
        if bar.get('volume') is not None and (not isfinite(bar['volume']) or bar['volume'] < 0):
            raise ValueError('Volume must be finite and nonnegative, or unavailable')
        gap = bool(self.last and (start>self.last['end'] or end-start<86400 and
            datetime.fromtimestamp(start,ZoneInfo('America/New_York')).date()!=
            datetime.fromtimestamp(self.last['time'],ZoneInfo('America/New_York')).date()))
        certified=bool(gap and continuity and continuity.get('contract')=='canonical-empty-interval-1' and
            continuity.get('start')==self.last['end'] and continuity.get('end')==start and
            0<start-self.last['end']<=30 and continuity.get('fingerprint') and
            datetime.fromtimestamp(start,ZoneInfo('America/New_York')).date()==datetime.fromtimestamp(self.last['time'],ZoneInfo('America/New_York')).date())
        if certified: gap=False
        if gap:
            interrupted_position = self.signal_state.get('setup')
            self.__init__(self.settings)
            if interrupted_position:
                self.signal_state['setup'] = interrupted_position
        previous = self.last['close'] if self.last else None
        atr = sum(self.true_ranges)/len(self.true_ranges) if self.true_ranges else None
        qualification = dict(atr=atr, ready=len(self.true_ranges)>=self.settings.atr_warmup_candles and bool(atr),
            observations=len(self.true_ranges),period=self.settings.atr_period,
            current_body_atr=abs(bar['close']-bar['open'])/atr if atr else None,
            range_atr=(bar['high']-bar['low'])/atr if atr else None,
            close_change_atr=(bar['close']-previous)/atr if atr and previous is not None else None,
            price_floor=(previous or bar['open'])*self.settings.movement_min_bps/10000,
            penetration_atr=self.settings.penetration_atr,body_atr=self.settings.break_body_atr,
            body_fraction=self.settings.break_body_fraction,acceptance_closes=self.settings.acceptance_closes)
        baseline = self.body or max(abs(bar['close']-bar['open']), bar['close']*self.settings.reversal_bps/10000)
        threshold = max(baseline*self.settings.movement_body_multiple,bar['close']*self.settings.movement_min_bps/10000,
                        (atr or 0)*self.settings.penetration_atr if qualification['ready'] else 0)
        local_before = [self.local_evidence(l) for l in self.swings.active.values() if l['scale']=='local' and l['state']=='active']
        proximity = max(baseline*self.settings.proximity_body_multiple,(atr or 0)*self.settings.penetration_atr)
        local_events, local_expired = self.local_interactions.observe(bar, previous, local_before, proximity, self.sequence, qualification)
        # Use the previous as-of snapshot for crossings. A level can disappear
        # or change side in the current snapshot precisely because it broke.
        available = {str(l.get('unified_level_id', l.get('level_id'))): l for l in self.global_levels}
        for level in levels or []:
            known = level.get('confirmed_at_ms', end*1000)/1000
            if known > end:
                raise ValueError('Future global swing evidence')
            available.setdefault(str(level.get('unified_level_id', level.get('level_id'))), level)
        global_events, global_expired = self.global_interactions.observe(bar, previous, list(available.values()), proximity, self.sequence, qualification) if global_status=='available' else ([], 0)
        held = [t['level'] for t in self.global_interactions.tracks.values() if t['phase']!='active' and t['level']['side'] in (-1,'resistance') and bar['close']>t['level']['upper']]
        below = [t['level'] for t in self.global_interactions.tracks.values() if t['phase']!='active' and t['level']['side'] in (1,'support') and bar['close']<t['level']['lower']]
        # The shared local extractor's temporal constants are interpreted in
        # candles, not wall seconds. This keeps 100ms/minute/daily charts causal
        # and prevents every daily level from expiring on the next daily bar.
        local_time = self.sequence+1
        self.close_times[local_time] = end
        self.swings.observe(local_time, bar['high'], bar['low'], bar['close'])
        # The structure engine's visual archive is not this indicator's history.
        # Retain bounded active state; immutable decisions are owned by caller.
        self.swings.segments.clear()
        for level in self.swings.active.values():
            level.pop('segment', None)
        new_local = [self.local_evidence(l) for l in self.swings.active.values() if l['scale']=='local' and l['confirmed_at']==local_time]
        for level in new_local:
            if level['side']=='resistance':
                kind = 'lower_high_confirmed' if self.last_resistance is not None and level['price']<self.last_resistance-threshold else 'equal_high_confirmed' if self.last_resistance is not None and abs(level['price']-self.last_resistance)<=threshold else 'swing_high_confirmed'
                self.last_resistance = level['price']
            else:
                kind = 'higher_low_confirmed' if self.last_support is not None and level['price']>self.last_support+threshold else 'equal_low_confirmed' if self.last_support is not None and abs(level['price']-self.last_support)<=threshold else 'swing_low_confirmed'
            local_events.append(dict(state=kind, level=level))
            if level['side']=='support':
                self.last_support = level['price']
        self.pivots.extend(new_local)
        forming = self.swings.detectors[0]
        for side, sign, name in [('high',1,'resistance_forming'),('low',-1,'support_forming')]:
            extreme = forming.get(side)
            distance=sign*(extreme[0]-bar['close']) if extreme else 0
            fresh=bool(extreme and self.forming_witness.get(side)!=extreme[1])
            if extreme and extreme[1]<local_time and distance>=baseline and (fresh or distance<=baseline*(1+self.settings.proximity_body_multiple)) and previous is not None and sign*(bar['close']-previous)<-threshold:
                local_events.append(dict(state=name,level=dict(price=extreme[0],pivot_at=self.close_times[extreme[1]],confirmed_at=None)))
                self.forming_witness[side]=extreme[1]
        local_bias = swing_bias(list(self.pivots))
        global_bias = swing_bias(list(available.values())) if global_status=='available' else 'unknown'
        anchor = self.movement_anchor if self.movement_anchor is not None else previous
        delta = bar['close']-anchor if anchor is not None else 0
        broken_up = any(e['state'] in ('breakout','support_reclaim') for e in local_events)
        broken_down = any(e['state'] in ('support_failure','resistance_reclaim') for e in local_events)
        if broken_up != broken_down:
            new_trend = 1 if broken_up else -1
            if new_trend != self.trend:
                self.trend = new_trend
                self.extreme = previous
                self.pullback = None
        elif new_local and local_bias in ('bullish','bearish'):
            structural_trend = 1 if local_bias=='bullish' else -1
            if structural_trend != self.trend:
                self.trend = structural_trend
                self.extreme = bar['close']
                self.pullback = None
        if previous is None:
            state, reason = 'unknown','insufficient_history'
        else:
            if self.trend==0 and abs(delta)>threshold:
                self.trend = 1 if delta>0 else -1
                self.extreme = previous
            sign = self.trend or 1
            if abs(delta)<=threshold:
                state,reason = 'no_change','close_progress_below_noise_threshold'
            elif sign*delta<0:
                state = 'pullback' if sign==1 else 'upward_retracement'
                reason = 'countertrend_close_without_confirmed_local_break'
                if self.pullback is None:
                    self.pullback = dict(reference=self.extreme, direction=sign)
            elif self.pullback and sign*(bar['close']-self.pullback['reference'])<=0:
                state = 'recovery' if sign==1 else 'downward_recovery'
                reason = 'recovering_toward_prior_extreme'
            else:
                state = 'advance' if sign==1 else 'decline'
                reason = 'prior_extreme_reclaimed' if self.pullback else 'directional_close'
                self.pullback = None
            if self.extreme is None or sign*(bar['close']-self.extreme)>0:
                self.extreme = bar['close']
        if self.movement_anchor is None or abs(delta)>threshold:
            self.movement_anchor = bar['close']
        progress = self.progression.observe(bar,previous,self.trend,atr or baseline,threshold,local_events,global_events,local_bias,global_bias,movement_delta=delta)
        volume, session_levels = self.volume_levels.observe(bar,new_local,threshold,baseline,state,local_events+global_events)
        shape = morphology(bar,self.last,baseline,self.settings)
        alpha = 1-2**(-1/self.settings.body_half_life)
        self.body = abs(bar['close']-bar['open']) if self.body is None else self.body+alpha*(abs(bar['close']-bar['open'])-self.body)
        self.ema_fast = bar['close'] if self.ema_fast is None else self.ema_fast+2/13*(bar['close']-self.ema_fast)
        self.ema_slow = bar['close'] if self.ema_slow is None else self.ema_slow+2/27*(bar['close']-self.ema_slow)
        macd = self.ema_fast-self.ema_slow
        self.signal = macd if self.signal is None else self.signal+2/10*(macd-self.signal)
        self.sequence += 1
        momentum = observe_momentum(self.momentum_state,bar['close'],previous,macd,self.signal,self.sequence,self.settings)
        for evidence in volume['reversal_candidates']+volume['reversal_outcomes']:
            agreement = momentum['agreement']
            evidence['momentum_context'] = 'unavailable' if agreement=='warming_up' else 'mixed' if agreement=='mixed' else 'supports' if agreement==evidence['direction'] else 'opposes'
            evidence['momentum_effective_at'] = end
        histogram_bps = (macd-self.signal)/bar['close']*10000
        episode_direction = (1 if histogram_bps>=self.settings.macd_gap_bps else -1 if histogram_bps<=-self.settings.macd_gap_bps else 0) if self.sequence>=26 else 0
        active = episode_direction!=0
        if not active:
            self.episode = None
        elif self.episode is None or episode_direction!=self.episode_direction:
            self.episode = end
        self.episode_direction = episode_direction
        result = dict(contract=VERSION, sequence=self.sequence, time=start, effective_at=end,
            state=state, reason=reason, direction=('bullish' if self.trend==1 else 'bearish' if self.trend==-1 else 'unknown'),
            local_bias=local_bias, global_bias=global_bias, candle_shape=shape, progression=progress,
            volume_analysis=volume, session_levels=session_levels,
            movement_threshold=threshold, retained_context=dict(local=self.local_interactions.context(),global_context=self.global_interactions.context()),
            focus_interactions=dict(local=interaction_focus(local_events,bar['close']),global_context=interaction_focus(global_events,bar['close'])),
            expired_interactions=dict(local=local_expired,global_count=global_expired), local_events=local_events, global_events=global_events,
            global_context=('mixed_breaks' if held and below else 'above_broken_resistance' if held else 'below_broken_support' if below else 'between_levels') if global_status=='available' else 'unavailable',
            global_status=global_status, local_swings=local_before, confirmed_swings=new_local,
            pivot_swings=[self.local_evidence(l, include_pivot=True) for l in self.swings.active.values()
                if l['scale']=='local' and l['state']=='active' and l['level_id'] in self.swings.latest_pivots],
            broken_resistance=compact(max(held, key=lambda l:l['upper'])) if held else None,
            broken_support=compact(min(below,key=lambda l:l['lower'])) if below else None,
            body_baseline=baseline, pullback=deepcopy(self.pullback),
            developing_swings={side:dict(price=forming[side][0], pivot_at=self.close_times[forming[side][1]], confirmed=False)
                for side in ('high', 'low') if forming.get(side)},
            macd=dict(histogram_bps=histogram_bps, warmup=self.sequence<26, active=active, direction=episode_direction, episode_started_at=self.episode),
            candle=dict(bar), gap_before=gap)
        result['qualification'] = qualification
        result['momentum'] = momentum
        result['continuity'] = dict(status='certified_empty_interval' if certified else 'reset' if gap else 'contiguous',proof=continuity if certified else None)
        result['technical_signal'] = observe_signal(self.signal_state,result,self.global_levels+local_before,self.settings)
        result['labels'],result['summary'],self.label_signature = label_packet(result,self.label_signature,self.recent_bars,atr)
        self.true_ranges.append(max(bar['high']-bar['low'],abs(bar['high']-previous),abs(bar['low']-previous)) if previous is not None else bar['high']-bar['low'])
        self.recent_bars.append(dict(bar))
        self.last = dict(bar)
        self.global_levels = deepcopy(levels or []) if global_status=='available' else []
        referenced = {local_time} | self.swings.referenced_clocks()
        for level in self.swings.active.values():
            referenced.update((level['pivot_at'], level['confirmed_at']))
        for detector in self.swings.detectors:
            referenced.update(detector[side][1] for side in ('high','low') if detector.get(side))
        self.close_times = {k:v for k,v in self.close_times.items() if k in referenced}
        return result
