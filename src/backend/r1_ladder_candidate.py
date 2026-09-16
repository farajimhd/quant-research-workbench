"""Build an independent R1 ladder candidate without publishing or runtime I/O."""
from copy import deepcopy

from .historical_hod_candidate import build as build_template
from src.trading_runtime.historical_hod import DEFAULTS as TRANSPORT_DEFAULTS

PROFILE_ID = 'r1-hod-resistance-ladder-v1'
LABEL = 'R1 resistance ladder — completed breakout and full exit'
DESCRIPTION = (
    'R1 is the closest resistance below HOD. Enter on a completed 1s breakout '
    'above its upper bound with completed 5s MACD above signal and session RVOL '
    'strictly greater than 2. Target the nearest resistance above entry and exit '
    'the full position. After a target exit, reentry requires a completed close '
    'above the open 5s MACD episode high; spread is ignored for that continuation '
    'only, and its stop is 20 bps below the broken resistance lower edge. '
    'Initial-entry stop distance below $2 is clamped to $0.10–$0.30; otherwise '
    '$0.10–5% of entry. '
    'One portfolio cash allocation up to 90%, no adds or risk-based sizing. '
    'The price floor is $0.01 to support entries below $2; all other liquidity '
    'and spread requirements are inherited unchanged. The historical '
    'contract supplies market inputs only; R1 owns every trading decision.'
)


def build(base, *, source_parameters, profile_id=PROFILE_ID, label=LABEL):
    """Supply the pinned source profile parameters, never an inferred preset."""
    from src.trading_runtime.r1_ladder import CONTRACT, DEFAULTS

    source = deepcopy(source_parameters)
    if not isinstance(source.get('liquidity_admission'), dict) or not source['liquidity_admission']:
        raise ValueError('R1 candidate requires authoritative source liquidity settings')
    if not isinstance(source.get('historical_hod'), dict):
        raise ValueError('R1 candidate requires source market-input settings')
    payload, canvas, plan_id = build_template(base, profile_id=profile_id, label=label)
    profile = next(p for p in payload['strategy']['profiles'] if p['profile_id'] == profile_id)
    parameters = profile['parameters']
    parameters.update(r1_ladder_contract=CONTRACT, r1_ladder=deepcopy(DEFAULTS),
                      liquidity_admission=deepcopy(source['liquidity_admission']))
    parameters['liquidity_admission']['minimum_price'] = .01
    for key in ('execution', 'strategy_behavior', 'market_pressure'):
        if key in source:
            parameters[key] = deepcopy(source[key])
        else:
            parameters.pop(key, None)
    # This adapter retains native preparation/dependency routing, not the old
    # executor. The engine must dispatch r1_ladder before historical_hod.
    adapter = deepcopy(TRANSPORT_DEFAULTS)
    for key in ('maximum_macd_age_ms', 'maximum_source_age_ms', 'maximum_quote_age_ms',
                'maximum_chase_bps', 'confirmation_lifetime_ms', 'minimum_candle_volume'):
        adapter[key] = source['historical_hod'][key]
    adapter.update(v7_zone_enabled=1, v7_center_swing_enabled=1, v7_price_only_enabled=1,
                   v7_encounters_enabled=1, v7_setup_enabled=1, early_green_stop_enabled=0,
                   forming_macd_entry_enabled=0,
                   setup_minimum_session_relative_volume=2.)
    parameters['historical_hod'] = adapter
    settings = parameters['r1_ladder']
    parameters['sizing'].update(request_mode='mandate_fraction', request_value=settings['cash_fraction'])
    parameters['add']['enabled'] = False
    parameters['reentry'].update(enabled=True, cooldown_ms=0, unlimited_attempts=True,
                                 maximum_attempts=0, require_new_confirmation=True)
    capital = dict(mode='mandate_fraction', value=settings['cash_fraction'],
                   maximum_quantity=settings['maximum_quantity'], allow_replacement=False)
    lifecycle = profile['lifecycle']
    lifecycle['initial_entry'].update(capital_request=deepcopy(capital), add_steps=[])
    lifecycle['reentry'].update(capital_request=deepcopy(capital), enabled=True,
                               require_new_confirmation=True, after_protective_exit=True)
    lifecycle['trading_behavior'] = deepcopy(parameters['strategy_behavior'])
    lifecycle['exit'] = {'rule_sets': []}  # Independent policy emits full exits.
    profile['description'] = DESCRIPTION
    plan = next(p for p in payload['run_plans']['plans'] if p['run_plan_id'] == plan_id)
    plan['description'] = DESCRIPTION

    # Whole-market R1 runs are causally seeded by the certified Early Squeeze
    # occurrence stream.  The structural template's synthetic tradability
    # stream has no historical occurrence authority; retaining it here makes
    # an empty ticker selection materialize the entire market Watchlist before
    # replay.  Keep liquidity as a decision-time strategy gate after each
    # occurrence instead of using it to construct a second market population.
    early_stream = deepcopy(next(
        stream for stream in payload['market_discovery']['signal_streams']
        if stream['signal_stream_id'] == 'price-squeeze-early'
    ))
    quality_id = profile_id + '-tradability'
    plan.update(
        signal_stream_ids=['price-squeeze-early'],
        # Retain the compiled plan only as point-in-time identity authority.
        # watchlist_policy=not_required prevents it from admitting or scanning
        # a whole-market population before the Early Squeeze occurrences.
        watchlist_ids=[quality_id],
        activation={
            'event_policy': 'new_occurrences',
            'watchlist_policy': 'not_required',
            'watch_duration': 'session',
        },
    )
    universe = next(
        row for row in payload['run_plans']['universes']
        if row['universe_id'] == plan['universe_id']
    )
    universe.update(
        name='Early Squeeze R1 candidates',
        description=(
            'Tickers enter the R1 observation population at their certified '
            'Early Squeeze occurrence and remain watched for the session.'
        ),
        source='watchlist',
        symbols=[],
        scanner_view_id=quality_id,
        scanner_view_ids=[quality_id],
        signal_stream_ids=['price-squeeze-early'],
        signal_stream_snapshots=[early_stream],
        enabled=bool(early_stream.get('enabled', True)),
    )

    # Mirror the exact source admission thresholds into the discovery rules;
    # updating only executor parameters would leave the template's old gates.
    liquidity = parameters['liquidity_admission']
    fields = dict(zip(
        ('market.last_price:greater_or_equal', 'market.last_price:less_or_equal',
         'market.session_dollar_volume:greater_or_equal', 'market.volume:greater_or_equal',
         'market.trade_rate_10s:greater_or_equal', 'market.trade_rate_60s:greater_or_equal',
         'market.spread_bps:less_or_equal'),
        ('minimum_price', 'maximum_price', 'minimum_session_dollar_volume',
         'minimum_session_share_volume', 'minimum_trade_rate_10s', 'minimum_trade_rate_60s',
         'maximum_admission_spread_bps')))
    for rule in payload['market_discovery']['rule_sets']:
        if rule['rule_set_id'] == profile_id+'-tradability':
            for condition in rule['conditions']:
                condition['value'] = liquidity[fields[condition['left_source_id']+':'+condition['comparator']]]
        if rule['rule_set_id'] == profile_id+'-observe':
            rule['conditions'] = [c for c in rule['conditions'] if c['condition_id'] != 'vwap-observed']
            for stage in ('trigger', 'confirmation'):
                parameters['entry_rules'][stage] = dict(operator='all', rule_sets=[deepcopy(rule)])
    return payload, canvas, plan_id


def create(*, source_parameters):
    """Save a separate backtest candidate after the new executor is activated."""
    from .trading_configuration_service import configuration_base, create_test_candidate
    payload, canvas, plan = build(configuration_base(), source_parameters=source_parameters)
    return create_test_candidate(label=LABEL, canvas_revision=canvas['revision'],
        canvas_profile=canvas['profile'], configuration=payload,
        run_plan_id=plan, strategy_profile_id=PROFILE_ID)
