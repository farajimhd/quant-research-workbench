"""Build Strategy 349 without mutating immutable Strategy 348."""
from copy import deepcopy

from . import early_squeeze_momentum_candidate as base
from src.trading_runtime.early_squeeze_momentum import CONTRACT, SUCCESSOR

LABEL = 'Early Squeeze / adaptive multi-MACD v27 / Strategy 349'
DESCRIPTION = (
    'Prior regular close below $20 and session-latched Early Squeeze admit the ticker; current '
    'price must be at least $1 for every purchase but may fall below $1 while a position is managed. '
    'Entries and additions require bullish forming 1s, 5s, 10s and 30s MACD. At a 15% session '
    'advance, entries require price in the 30% band below prior causal HOD, the existing resistance '
    'cross, and a local-high BOS based on a supported low or reclaimed resistance. Up to three filled '
    'purchases are allowed; each resistance can add once per open position and resets after closure. '
    'Rapid reentry inside ten seconds, or any reentry on the same resistance, must cross the prior '
    'position resistance-interaction high; target exits still block the rest of their one-second candle. '
    'Initial protection uses structural selection plus causal 2s/5s noise, at least $0.10 away and no '
    'farther than max($0.10, 5% of entry); entries needing more distance are deferred. After five seconds '
    'a frozen-distance trade-price trail joins the unchanged three-resistance stop; after ten seconds '
    'VWAP is a hard floor and the highest valid stop wins without loosening. At two minutes additions '
    'stop: green positions keep only a green-side resistance bracket, red positions get one minute to '
    'recover and qualify for that bracket, otherwise liquidation occurs. Research candidate only.'
)


def build(configuration, baseline):
    payload, canvas, plan_id = base.build(configuration, baseline)
    profile = next(p for p in payload['strategy']['profiles'] if p['profile_id'] == CONTRACT)
    profile.update(name=LABEL, description=DESCRIPTION)
    parameters = profile['parameters']
    parameters['momentum_successor'] = SUCCESSOR
    parameters['momentum_full_session'] = {
        **parameters['momentum_full_session'], 'age_seconds':120., 'red_grace_seconds':60.}
    parameters['momentum_price_policy'] = dict(
        prior_close_maximum=20., current_purchase_minimum=1., late_mode_gain=0.15,
        hod_zone_floor_fraction=0.70)
    parameters['momentum_forming_macd_timeframes'] = ['1s', '5s', '10s', '30s']
    parameters['momentum_adaptive_stop'] = dict(minimum_dollars=.10, maximum_entry_fraction=.05,
        short_seconds=2, short_multiplier=1.5, session_seconds=5,
        session_percentile=.90, session_multiplier=1.25, minimum_session_samples=6)
    plan = next(p for p in payload['run_plans']['plans'] if p['run_plan_id'] == plan_id)
    plan.update(name=LABEL, description=DESCRIPTION)
    universe = next(u for u in payload['run_plans']['universes'] if u['universe_id'] == plan['universe_id'])
    universe.update(name=LABEL, description=(
        'Early Squeeze latches independently of the live $1 purchase floor; prior regular close must be below $20.'))
    quality_id = CONTRACT+'-tradability'
    rule = next(r for r in payload['market_discovery']['rule_sets'] if r['rule_set_id'] == quality_id)
    rule['conditions'] = [c for c in rule['conditions'] if not (
        c['left_source_id'] == 'market.last_price' and c['comparator'] == 'greater_or_equal')]
    rule['conditions'].append(dict(condition_id='prior-close-under-20', enabled=True,
        left_source_id='market.previous_close', left_field_ref='data.market.previous_close@1:value',
        comparator='less_than', right_source_id='', value=20.))
    return payload, canvas, plan_id


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    base.base.require_loaded_executor(CONTRACT)
    existing = next((c for c in trading_journal().trading_configuration_candidate_summaries()
        if c['label'] == LABEL), None)
    if existing:
        return configuration_candidate(existing['candidate_id'], required=True)
    payload, canvas, plan = build(configuration_base(), configuration_candidate(base.base.BASELINE_ID, required=True))
    return create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
