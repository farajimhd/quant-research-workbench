"""Build a separate candidate with only the agreed Early Squeeze trading policy."""
from copy import deepcopy

from .structural_recovery_candidate import build as build_template
from src.trading_runtime.early_squeeze_breakout import CONTRACT
from src.trading_runtime.structural_recovery import CONTRACT as DATA_CONTRACT, DEFAULTS

PROFILE_ID = CONTRACT
LABEL = 'Early Squeeze / R1 midpoint / green-close adds / fixed-distance trail v3'
BASELINE_ID = 'fc03b276-7584-4772-a50f-51424f9bfea3'
BASELINE_HASH = '85dff0666442f78d63dee8972d6c1e11a5599eb78316727a128fc829636d5bd6'
DESCRIPTION = (
    'Watch from the first available Early Squeeze occurrence only, including single-ticker runs. '
    'Filtered V7 seed and causal completed-candle levels; green completed 1s R1 midpoint crossover '
    'above VWAP with close in its top quarter. Buy one third of eligible cash; each new green '
    '1s overhead resistance break adds the original cash tranche without MACD or close-location gates. '
    'Full-position 3/2/1 overhead-resistance targets with upward-only advances. Initial stop one tick '
    'below broken resistance lower edge; real-time bid-high trailing preserves the initial filled '
    'entry-to-stop distance. After stop-out, green completed 1s close above the frozen post-break '
    'closing high reenters; stop below a confirmed swing above the resistance, otherwise below the '
    'last completed candle open, offset under current bid. No MACD, RVOL, impulse, pullback, ATR, '
    'special midpoint target, or structural trailing trading rules.'
)


def build(base, baseline):
    if baseline['candidate_id'] != BASELINE_ID or baseline['content_hash'] != BASELINE_HASH:
        raise ValueError('Strategy 317 sizing source identity changed')
    source = baseline['payload']
    source_profile = next(p for p in source['strategy']['profiles']
                          if p['profile_id'] == 'vwap-impulse-pullback-breakout-v4')
    payload, canvas, plan_id = build_template(base, profile_id=PROFILE_ID, label_override=LABEL)
    profile = next(p for p in payload['strategy']['profiles'] if p['profile_id'] == PROFILE_ID)
    profile.update(description=DESCRIPTION, derived_from_profile_id=source_profile['profile_id'])
    # Explicit allowlist: no inherited 317 entry, management or exit settings.
    liquidity = deepcopy(source_profile['parameters']['liquidity_admission'])
    liquidity.update(minimum_price=.01, maximum_price=None, maximum_admission_spread_bps=250.,
                     maximum_current_spread_bps=250., maximum_spread_bps=250.)
    parameters = dict(early_squeeze_breakout_contract=CONTRACT,
        structural_recovery_contract=DATA_CONTRACT, structural_recovery=dict(DEFAULTS),
        structural_detector_settings={}, liquidity_admission=liquidity,
        execution=deepcopy(source_profile['parameters'].get('execution', {'tick_size':.01,'limit_offset_bps':5.})),
        strategy_behavior=deepcopy(source_profile['lifecycle']['trading_behavior']),
        entry_rules=deepcopy(profile['parameters']['entry_rules']),
        sizing=dict(request_mode='mandate_fraction', request_value=1/3, allow_replacement=False),
        add=dict(enabled=True), reentry=dict(enabled=True, cooldown_ms=0, unlimited_attempts=True,
            maximum_attempts=0, require_new_confirmation=True),
        require_open_macd_for_entry=False, require_positive_macd_signal_for_entry=False)
    profile['parameters'] = parameters
    lifecycle = profile['lifecycle']
    lifecycle['trading_behavior'] = deepcopy(parameters['strategy_behavior'])
    for stage in ('initial_entry', 'reentry'):
        lifecycle[stage]['capital_request'] = dict(mode='mandate_fraction', value=1/3, allow_replacement=False)
    lifecycle['initial_entry']['add_steps'] = []
    lifecycle['exit'] = {'rule_sets':[]}
    plan = next(p for p in payload['run_plans']['plans'] if p['run_plan_id'] == plan_id)
    plan['action_authority'] = {**plan.get('action_authority', {}), 'add':'automatic'}
    plan.update(description=DESCRIPTION, signal_stream_ids=['price-squeeze-early'],
        # Include an earlier same-session signal when the requested run begins
        # later. Session-watch activation latches the first chronological event.
        activation=dict(event_policy='latest_session_occurrence', watchlist_policy='not_required', watch_duration='session'),
        allowed_environments=['backtest', 'backtest_debug', 'replay'])
    quality_id = PROFILE_ID+'-tradability'
    # Remove the template's synthetic activation source entirely.
    payload['market_discovery']['signal_streams'] = [s for s in payload['market_discovery']['signal_streams']
                                                   if s['signal_stream_id'] != quality_id]
    stream = deepcopy(next(s for s in payload['market_discovery']['signal_streams']
                           if s['signal_stream_id'] == 'price-squeeze-early'))
    universe = next(u for u in payload['run_plans']['universes'] if u['universe_id'] == plan['universe_id'])
    universe.update(name=LABEL, description='Session watch starts at the first Early Squeeze availability time.',
        symbols=[], signal_stream_ids=['price-squeeze-early'], signal_stream_snapshots=[stream])
    fields = dict(zip(('market.last_price:greater_or_equal','market.last_price:less_or_equal',
        'market.session_dollar_volume:greater_or_equal','market.volume:greater_or_equal',
        'market.trade_rate_10s:greater_or_equal','market.trade_rate_60s:greater_or_equal','market.spread_bps:less_or_equal'),
        ('minimum_price','maximum_price','minimum_session_dollar_volume','minimum_session_share_volume',
         'minimum_trade_rate_10s','minimum_trade_rate_60s','maximum_admission_spread_bps')))
    for rule in payload['market_discovery']['rule_sets']:
        if rule['rule_set_id'] == quality_id:
            rule['conditions'] = [c for c in rule['conditions'] if not (
                c['left_source_id'] == 'market.last_price' and c['comparator'] == 'less_or_equal')]
            for condition in rule['conditions']:
                condition['value'] = liquidity[fields[condition['left_source_id']+':'+condition['comparator']]]
    # Copy portfolio constraints from 317, not the template's risk-sizing cap.
    source_plan = next(p for p in source['run_plans']['plans'] if p['profile_id'] == source_profile['profile_id'])
    source_mandates = [m for m in source['portfolio']['mandates'] if m['mandate_id'] in source_plan['mandate_ids']]
    if len(source_mandates) != len(plan['mandate_ids']):
        raise ValueError('Cannot map Strategy 317 portfolio mandates unambiguously')
    for target_id, original in zip(plan['mandate_ids'], source_mandates):
        target = next(m for m in payload['portfolio']['mandates'] if m['mandate_id'] == target_id)
        identity = {k:target[k] for k in ('mandate_id','run_plan_id','principal_id')}
        target.clear()
        target.update(deepcopy(original), **identity)
    return payload, canvas, plan_id


def require_loaded_executor():
    """Prevent an older running backend from falling through to template behavior."""
    import json
    from urllib.error import URLError
    from urllib.request import urlopen

    try:
        with urlopen('http://127.0.0.1:8000/api/trading/strategy-execution-contracts', timeout=5) as response:
            capabilities = json.load(response)
    except (URLError, ValueError, TimeoutError) as exc:
        raise RuntimeError('Candidate activation pending: restart the managed backend when safe to load the executor') from exc
    if not isinstance(capabilities, dict) or CONTRACT not in capabilities.get('contracts', []):
        raise RuntimeError('Candidate activation pending: the running backend has not loaded the required executor')


def create():
    require_loaded_executor()
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    payload, canvas, plan = build(configuration_base(), configuration_candidate(BASELINE_ID, required=True))
    return create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=PROFILE_ID)
