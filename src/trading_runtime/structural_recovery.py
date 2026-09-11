"""Independent V6 support-recovery policy, sharing only execution infrastructure."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from math import floor, isfinite
from zoneinfo import ZoneInfo

from src.market_engine.structural_detector import StructuralDetector, DetectorSettings, VERSION
from src.market_engine.structural_detector_checkpoint import checkpoint, restore

CONTRACT = 'v6-structural-recovery-1'
BOOK_VERSION = 'causal-swing-closing-book-6'
NY = ZoneInfo('America/New_York')
DEFAULTS = dict(warmup_candles=30, setup_lifetime_seconds=120, confirmation_lifetime_ms=1000,
    maximum_source_age_ms=2000, maximum_quote_age_ms=1000, maximum_chase_bps=15.,
    stop_buffer_bps=5., minimum_reward_risk=1.5, cost_bps_per_side=5.,
    risk_fraction=.005, maximum_quantity=10000, minimum_candle_volume=1.)
LIQUIDITY = dict(enabled=True, latched=False, minimum_price=2., maximum_price=50.,
    minimum_session_dollar_volume=1_000_000., minimum_session_share_volume=100_000.,
    minimum_trade_rate_10s=5., minimum_trade_rate_60s=5., maximum_admission_spread_bps=60.,
    maximum_current_spread_bps=60., maximum_spread_bps=60.)
LIQUIDITY_181 = dict(LIQUIDITY, latched=True, minimum_trade_rate_10s=1.,
    minimum_trade_rate_60s=.5, maximum_current_spread_bps=100., maximum_spread_bps=100.,
    minimum_current_trade_rate_10s=5., minimum_current_trade_rate_60s=5.)


def configure(p):
    if p.get('structural_recovery_contract') != CONTRACT:
        raise ValueError('Unsupported structural recovery contract')
    if any(p.get(k) for k in ('v5_breakout_contract','swing_gap_contract','swing_evidence_contract','swing_momentum_contract')):
        raise ValueError('Structural recovery cannot compose legacy strategy contracts')
    raw = p.get('structural_recovery', {})
    if set(raw)-set(DEFAULTS):
        raise ValueError('Unknown structural recovery setting')
    settings = dict(DEFAULTS, **raw)
    if any(type(v) not in (int,float) or not isfinite(v) or v <= 0 for v in settings.values()):
        raise ValueError('Structural recovery settings must be finite and positive')
    if settings['risk_fraction'] > 1 or settings['confirmation_lifetime_ms'] > 1000:
        raise ValueError('Invalid risk fraction or candle confirmation lifetime')
    p['structural_recovery'] = settings
    p['structural_detector_settings'] = dict(p.get('structural_detector_settings') or {})
    DetectorSettings(**p['structural_detector_settings'])
    liquidity = dict(LIQUIDITY, **p.get('liquidity_admission', {}))
    if not liquidity['enabled'] or type(liquidity.get('latched')) is not bool:
        raise ValueError('Structural recovery requires enabled tradability gates and an explicit latch policy')
    if liquidity['latched'] and any(type(liquidity.get(k)) not in (int,float)
            or not isfinite(liquidity[k]) or liquidity[k] <= 0
            for k in ('minimum_current_trade_rate_10s','minimum_current_trade_rate_60s')):
        raise ValueError('Latched admission requires positive current trade-rate gates')
    for key in LIQUIDITY:
        if key not in ('enabled','latched') and (type(liquidity[key]) not in (int,float)
                or not isfinite(liquidity[key]) or liquidity[key] <= 0):
            raise ValueError('Tradability thresholds must be finite and positive')
    if liquidity['minimum_price'] >= liquidity['maximum_price']:
        raise ValueError('Invalid tradable price range')
    p['liquidity_admission'] = liquidity
    p['entry_candle_confirmation']['enabled'] = False
    p['structural_entry']['enabled'] = False
    p['protection']['trailing']['enabled'] = False
    p['protection']['profit_ladder'].update(enabled=True, fixed_at_entry=True)
    p['momentum_management']['macd_backstop']['enabled'] = False


def observe_market(bar, levels, book, saved, settings=None):
    """Passive stream, independent of assignment/position; caller persists saved."""
    stream = MarketStream(saved)
    stream.observe(bar, levels, book, settings)
    return stream.checkpoint()


class MarketStream:
    """Own one detector in memory; export JSON only at persistence boundaries."""

    def __init__(self, saved=None):
        self.saved = {k:v for k,v in (saved or {}).items() if k!='checkpoint'}
        self._checkpoint = (saved or {}).get('checkpoint')
        self.engine = None

    def observe(self, bar, levels, book, settings=None):
        _validate_market(bar, levels, book)
        saved = self.saved
        if saved.get('row', {}).get('effective_at', 0) >= bar['end']:
            return saved
        session = datetime.fromtimestamp(bar['time'], NY).date().isoformat()
        reset = (saved.get('session') != session or saved.get('book') != book
                 or saved.get('row', {}).get('effective_at') != bar['time'])
        if reset:
            self.engine = StructuralDetector(DetectorSettings(**(settings or {})))
        elif self.engine is None:
            self.engine = restore(self._checkpoint)
        self._checkpoint = None
        # Published observations must not alias the detector's mutable state.
        row = deepcopy(self.engine.observe(bar, levels, 'available'))
        self.saved = dict(session=session, book=deepcopy(book), row=row, reset=reset)
        return self.saved

    def checkpoint(self):
        saved = deepcopy(self.saved)
        if self.engine is not None:
            saved['checkpoint'] = checkpoint(self.engine)
        elif self._checkpoint is not None:
            saved['checkpoint'] = deepcopy(self._checkpoint)
        return saved


def _validate_market(bar, levels, book):
    if book.get('version') != BOOK_VERSION or not book.get('fingerprint') or not book.get('id'):
        raise ValueError('Structural recovery requires a pinned certified V6 book')
    if any(l.get('book_version') != BOOK_VERSION or l.get('confirmed_at_ms') is None
           or l['confirmed_at_ms'] > bar['end']*1000 for l in levels):
        raise ValueError('Invalid or future V6 level evidence')


def _source(o, key, age):
    item = o.source_values.get(key) or {}
    try:
        stamp = datetime.fromisoformat(str(item['observed_at']).replace('Z','+00:00'))
        value = float(item['value'])
        if stamp.tzinfo is None or not isfinite(value) or not 0 <= (o.observed_at-stamp).total_seconds()*1000 <= age:
            return None
        return value
    except (KeyError, TypeError, ValueError):
        return None


def tradability(o, p, row, state=None, *, producer_freshness=False):
    from .strategy_engine import _liquidity_admission_result, _current_execution_quality_result
    s = p['structural_recovery']
    # Session/rolling facts retain their producer timestamp. The caller's
    # candle interval must not silently replace its explicit source-age limit.
    values = {key:_source(o,key,s['maximum_source_age_ms']) for key in (
        'market.session_dollar_volume','market.volume','market.trade_rate_10s','market.trade_rate_60s')}
    resolved = values if producer_freshness else None
    admitted, evidence = _liquidity_admission_result(o, p['liquidity_admission'],liquidity_values=resolved)
    checks = dict(evidence['checks'])
    for key in ('market.session_dollar_volume','market.volume','market.trade_rate_10s','market.trade_rate_60s'):
        checks[key+'_fresh'] = values[key] is not None
    quote_spread = _source(o,'market.spread_bps',s['maximum_quote_age_ms'])
    checks['fresh_uncrossed_quote'] = quote_spread is not None and 0 < o.bid <= o.ask and all(isfinite(x) for x in (o.bid,o.ask))
    checks['current_spread'] = (checks['fresh_uncrossed_quote'] and
        (o.ask-o.bid)/((o.ask+o.bid)/2)*10000 <= p['liquidity_admission']['maximum_current_spread_bps'])
    volume = row.get('candle', {}).get('volume')
    checks['completed_candle_volume'] = volume is not None and isfinite(volume) and volume >= s['minimum_candle_volume']
    checks['detector_fresh'] = 0 <= o.observed_at.timestamp()-row.get('effective_at',0) <= s['maximum_source_age_ms']/1000
    if p['liquidity_admission']['latched']:
        if state is None:
            raise ValueError('Latched admission requires assignment state')
        session = o.observed_at.astimezone(NY).date().isoformat()
        if state.get('recovery_admission_session') != session:
            state.pop('recovery_admission',None)
            state['recovery_admission_session'] = session
        freshness = {k:v for k,v in checks.items() if k.endswith('_fresh')
            or k in ('fresh_uncrossed_quote','completed_candle_volume')}
        actual_admission_spread = checks['fresh_uncrossed_quote'] and (
            (o.ask-o.bid)/((o.ask+o.bid)/2)*10000 <= p['liquidity_admission']['maximum_admission_spread_bps'])
        if admitted and actual_admission_spread and all(freshness.values()) and not state.get('recovery_admission'):
            state['recovery_admission'] = dict(observed_at=o.observed_at.isoformat(), **deepcopy(evidence))
        _, current = _current_execution_quality_result(o,p['liquidity_admission'],liquidity_values=resolved)
        checks = dict(current['checks'], **freshness,
            admission_latched=bool(state.get('recovery_admission')),
            current_spread=checks['current_spread'] and current['checks']['current_spread'])
        evidence['admission'] = deepcopy(state.get('recovery_admission'))
    return all(checks.values()), dict(facts=evidence['facts'], checks=checks,
        admission=evidence.get('admission'), failed=[k for k,v in checks.items() if not v])


def update_setup(o, state, row, settings):
    now = row['effective_at']
    if now <= state.get('recovery_observed_at',0):
        return
    state['recovery_observed_at'] = now
    setup = state.get('recovery_setup')
    bar = row['candle']
    if setup and (now-setup['started_at'] > settings['setup_lifetime_seconds']
                  or bar['close'] < setup['support']['lower'] or row.get('gap_before')):
        state.pop('recovery_setup',None)
        setup = None
    if setup and not setup.get('confirmed_at'):
        # A later candle must clear the frozen test-candle high. Never move
        # that trigger to the same candle whose close is being tested.
        setup['low'] = min(setup['low'],bar['low'])
        if (now > setup['started_at'] and bar['close'] > setup['trigger']
                and row['direction']=='bullish' and row['state'] in ('advance','recovery')
                and bar['close'] > setup['support']['upper']):
            setup.update(confirmed_at=now, confirmation_close=bar['close'])
    if setup:
        return
    held = [e for e in row['global_events'] if e['state'] in ('testing_support','support_rejection','support_retest_held')
            and bar['close'] >= e['level']['lower']]
    if held:
        event = max(held,key=lambda e:e['level']['upper'])
        state['recovery_setup'] = dict(support=deepcopy(event['level']), started_at=now,
            trigger=bar['high'], low=bar['low'], interaction=event['state'],
            setup_id=f"{o.ticker}:{event['level'].get('unified_level_id')}:{now}")


def evaluate(host, assignment, o, p, state):
    from .strategy_engine import AssignmentStatus as Status, _at_or_after_session_time
    from .signals import CapitalRequest, StrategyIntent
    state = deepcopy(state)
    s = p['structural_recovery']
    market = o.structural_detector_state or {}
    row = market.get('row') or {}
    book = market.get('book') or {}
    evidence = dict(contract=CONTRACT, detector_contract=row.get('contract'), book=book,
        detector={k:row.get(k) for k in ('effective_at','sequence','state','direction','progression','volume_analysis','labels','summary','qualification')})
    def result(action, reason, status=None, **kwargs):
        return host._result(assignment,o,action,reason,1. if action=='enter_long' else 0.,1.,state,
            status or assignment.status,metadata={**evidence,**kwargs.pop('metadata',{})},**kwargs)
    if state.get('recovery_book') != book or (market.get('reset') and row.get('effective_at',0)>state.get('recovery_observed_at',0)):
        state.pop('recovery_setup',None)
        state['recovery_book'] = deepcopy(book)
    if row.get('contract') == VERSION and row.get('effective_at',float('inf')) <= o.observed_at.timestamp():
        update_setup(o,state,row,s)
    ready, quality = tradability(o,p,row,state)
    evidence['tradability'] = quality
    setup = state.get('recovery_setup') or {}
    evidence['setup'] = deepcopy(setup)
    behavior = p.get('strategy_behavior') or {}
    flatten = _at_or_after_session_time(o.observed_at,behavior.get('flatten_time','15:55:00'))
    acquired = o.position_quantity > 0
    pending = assignment.status == Status.ENTRY_PENDING or bool(state.get('pending_capital_request'))
    active = state.get('recovery_entry') or {}
    stop = state.get('active_stop') or state.get('initial_stop') or 0
    structural_failure = bool(active and row and row['effective_at'] > active['confirmed_at']
        and 0 <= o.observed_at.timestamp()-row['effective_at'] <= s['maximum_source_age_ms']/1000
        and row['candle']['close'] < active['support']['lower'])
    invalid = flatten or structural_failure or (stop > 0 and o.price <= stop)
    # Stops/exits retain authority even if observation quality deteriorates.
    if o.pending_exit_quantity > 0 or assignment.status == Status.EXIT_PENDING:
        if o.position_quantity > o.pending_exit_quantity+1e-9:
            return result('exit',state.get('last_exit_reason') or 'structural_exit_pending',Status.EXIT_PENDING,
                quantity=o.position_quantity,metadata={'cancel_entry_acquisition':True,'position_fraction':1.})
        return result('hold','exit_fill_pending',Status.EXIT_PENDING)
    if acquired or pending:
        if invalid:
            reason = 'session_flatten' if flatten else 'v6_support_failed' if structural_failure else 'protective_stop'
            state.update(last_exit_reason=reason,entry_acquisition_exit_latched=True)
            state.pop('pending_capital_request',None)
            return result('exit',reason,Status.EXIT_PENDING,quantity=o.position_quantity,
                invalidation_price=stop,metadata={'cancel_entry_acquisition':True,'position_fraction':1.})
        stale_setup = o.observed_at.timestamp()-active.get('confirmed_at',0) > s['confirmation_lifetime_ms']/1000
        chase = active and o.ask > active.get('maximum_buy_price',0)
        if (not ready or stale_setup or chase) and not state.get('recovery_acquisition_cancelled'):
            state.pop('pending_capital_request',None)
            base = result('hold' if acquired else 'wait','structural_acquisition_invalidated',
                          Status.MANAGING if acquired else Status.WATCHING)
            cancel = StrategyIntent(intent_id=base.evaluation.signals[0].signal_id,ticker=o.ticker,
                event_time=o.observed_at,action='cancel_entry',quantity=0,reference_price=o.price,
                reason='structural_acquisition_invalidated',metadata={'assignment_id':assignment.assignment_id})
            # Once invalidated, the acquisition may not restart on recovered quotes.
            state['recovery_acquisition_cancelled'] = True
            return replace(base,evaluation=replace(base.evaluation,intents=(cancel,)))
        if acquired or assignment.status == Status.ENTRY_PENDING:
            return result('hold','structural_support_holds',Status.MANAGING if acquired else Status.ENTRY_PENDING,
                invalidation_price=stop,profit_target_price=active.get('target'))
        if state.get('pending_capital_request'):
            setup = active
    if assignment.status in (Status.DISABLED,Status.COMPLETED,Status.ERROR,Status.PAUSED) or not assignment.permissions.observe:
        return result('wait','assignment_not_active')
    if not assignment.permissions.enter or (state.get('entries',0) and not assignment.permissions.reenter):
        return result('wait','entry_not_authorized')
    local = o.observed_at.astimezone(NY).time()
    session = 'premarket' if local.hour < 9 or (local.hour==9 and local.minute<30) else 'regular' if local.hour<16 else 'after_hours'
    if not o.market_open or session not in behavior.get('eligible_sessions',['premarket','regular']) or flatten or _at_or_after_session_time(o.observed_at,behavior.get('entry_cutoff_time','15:45:00')):
        return result('wait','outside_entry_session',Status.WATCHING)
    if book.get('version') != BOOK_VERSION or not book.get('fingerprint') or row.get('contract') != VERSION:
        return result('wait','certified_v6_detector_unavailable',Status.WATCHING)
    if row.get('sequence',0) < s['warmup_candles']:
        return result('wait','structural_detector_warming_up',Status.WATCHING)
    if not ready:
        return result('wait','structural_tradability_incomplete',Status.WATCHING)
    stamp = setup.get('confirmed_at',0)
    if not stamp or not 0 <= o.observed_at.timestamp()-stamp < s['confirmation_lifetime_ms']/1000:
        if stamp:
            state.pop('recovery_setup',None)
        return result('wait','waiting_for_support_recovery',Status.WATCHING)
    if setup['setup_id'] == state.get('consumed_recovery_setup') and not state.get('pending_capital_request'):
        return result('wait','recovery_confirmation_consumed',Status.WATCHING)
    tick = p['execution']['tick_size']
    boundary = min(setup['support']['lower'],setup['low'])
    stop = floor((boundary-max(tick,boundary*s['stop_buffer_bps']/10000))/tick+1e-9)*tick
    levels = (*o.structural_support_levels,*o.structural_resistance_levels)
    overhead = [l for l in levels if l.get('book_version')==BOOK_VERSION and l.get('side') in (-1,'resistance')
        and l.get('confirmed_at_ms',float('inf')) <= o.observed_at.timestamp()*1000
        and l.get('lower',0) > max(o.price,o.ask)]
    if not overhead:
        return result('wait','v6_target_unavailable',Status.WATCHING)
    resistance = min(overhead,key=lambda l:l['lower'])
    target = floor((resistance['lower']-tick)/tick+1e-9)*tick
    cost = o.ask*s['cost_bps_per_side']*2/10000
    chase_ceiling = setup['confirmation_close']*(1+s['maximum_chase_bps']/10000)
    risk_ceiling = (target+s['minimum_reward_risk']*stop-cost)/(1+s['minimum_reward_risk'])
    ceiling = min(chase_ceiling, risk_ceiling)
    entry_checks = dict(valid_stop=0 < stop < o.bid, uncrossed_quote=o.bid <= o.ask,
        reward_risk=o.ask <= risk_ceiling, chase=o.ask <= chase_ceiling)
    evidence['entry_quality'] = dict(bid=o.bid, ask=o.ask, stop=stop, target=target,
        cost_allowance=cost, reward_risk_ceiling=risk_ceiling, chase_ceiling=chase_ceiling,
        maximum_buy_price=ceiling, checks=entry_checks,
        failed=[key for key,passed in entry_checks.items() if not passed])
    if not 0 < stop < o.bid <= o.ask <= ceiling:
        return result('wait','structural_reward_risk_or_chase_failed',Status.WATCHING,
            metadata={'stop':stop,'target':target,'maximum_buy_price':ceiling})
    active = dict(setup,stop=stop,target=target,maximum_buy_price=ceiling,resistance=deepcopy(resistance))
    state.update(recovery_entry=active,consumed_recovery_setup=setup['setup_id'],
        initial_stop=stop,active_stop=stop,structural_profit_targets=[target],
        entry_reference_price=o.ask,entry_at=o.observed_at.isoformat(),
        entry_acquisition_exit_latched=False,recovery_acquisition_cancelled=False,
        last_exit_reason='',entries=state.get('entries',0)+(0 if state.get('pending_capital_request') else 1))
    state.pop('recovery_setup',None)
    return result('enter_long','v6_support_recovery_confirmed',Status.ENTRY_PENDING,
        invalidation_price=stop,profit_target_price=target,
        capital_request=CapitalRequest(mode='risk_fraction',value=s['risk_fraction'],
            maximum_quantity=s['maximum_quantity'],allow_replacement=False),
        order_intent={'execution_policy':'adaptive_urgent','protection_profile':'structural-single-target'},
        metadata={'initial_stop':stop,'profit_targets':[target],'mandatory_broker_target':True,
            'maximum_buy_price':ceiling,'protective_stop_selection':active,
            'profit_target_selection':{'selected_level':resistance,'target':target},
            'cancel_entry_acquisition':False})
