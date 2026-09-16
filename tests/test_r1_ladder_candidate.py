from copy import deepcopy

import pytest

from src.backend.r1_ladder_candidate import build, PROFILE_ID
from src.backend.trading_configuration_service import (
    _build_configuration_release,
    configuration_base,
)
from src.trading_runtime.historical_hod import DEFAULTS as TRANSPORT_DEFAULTS


def source():
    # Immutable Strategy 222 candidate 296 admission parameters. Only the new
    # policy's explicitly requested sub-$2 price floor may differ.
    return dict(liquidity_admission=dict(enabled=True, latched=True,
        minimum_price=2., maximum_price=20., minimum_session_dollar_volume=200000.,
        minimum_session_share_volume=25000., minimum_trade_rate_10s=1., minimum_trade_rate_60s=.5,
        minimum_current_trade_rate_10s=5., minimum_current_trade_rate_60s=2.,
        maximum_spread_bps=150., maximum_admission_spread_bps=150., maximum_current_spread_bps=150.),
        historical_hod=dict(TRANSPORT_DEFAULTS, maximum_quote_age_ms=777., maximum_chase_bps=13.),
        execution=dict(tick_size=.01, limit_offset_bps=5.),
        strategy_behavior=dict(side='long', eligible_sessions=['premarket','regular','after_hours'],
                               entry_cutoff_time='19:55:00', flatten_time='19:59:00'))


def test_independent_policy_preserves_source_gates_and_portfolio_authority():
    base = configuration_base()
    original, inputs = deepcopy(base), source()
    old_inputs = deepcopy(inputs)
    payload, canvas, plan_id = build(base, source_parameters=inputs)
    profile = next(p for p in payload['strategy']['profiles'] if p['profile_id']==PROFILE_ID)
    p = profile['parameters']
    assert p['r1_ladder_contract'] == 'r1-hod-resistance-ladder-v1'
    assert p['liquidity_admission'] == dict(inputs['liquidity_admission'], minimum_price=.01)
    assert p['execution'] == inputs['execution']
    assert p['historical_hod']['maximum_quote_age_ms'] == 777.
    assert p['historical_hod']['maximum_chase_bps'] == 13.
    assert p['historical_hod']['forming_macd_entry_enabled'] == 0
    assert p['historical_hod']['v7_zone_enabled'] == 1
    assert p['historical_hod']['setup_minimum_session_relative_volume'] == 2.
    assert p['r1_ladder']['minimum_rvol'] == 2.
    lifecycle = profile['lifecycle']
    assert lifecycle['initial_entry']['add_steps'] == []
    for stage in ('initial_entry','reentry'):
        assert lifecycle[stage]['capital_request']['mode'] == 'mandate_fraction'
        assert lifecycle[stage]['capital_request']['value'] == .9
    assert lifecycle['reentry']['require_new_confirmation'] is True
    assert lifecycle['exit']['rule_sets'] == []
    assert lifecycle['trading_behavior'] == inputs['strategy_behavior']
    assert p['sizing']['request_mode'] == 'mandate_fraction'
    assert p['add']['enabled'] is False
    assert base == original and inputs == old_inputs
    plan = next(x for x in payload['run_plans']['plans'] if x['run_plan_id']==plan_id)
    assert plan['allowed_environments'] == ['backtest']
    assert plan['signal_stream_ids'] == ['price-squeeze-early']
    assert plan['watchlist_ids'] == []
    assert plan['activation'] == dict(event_policy='new_occurrences',
                                      watchlist_policy='not_required',
                                      watch_duration='session')
    universe = next(x for x in payload['run_plans']['universes']
                    if x['universe_id'] == plan['universe_id'])
    assert universe['source'] == 'signal_stream'
    assert universe['signal_stream_ids'] == ['price-squeeze-early']
    assert universe['scanner_view_ids'] == []
    assert universe['watchlist_snapshots'] == []
    assert [x['signal_stream_id'] for x in universe['signal_stream_snapshots']] == [
        'price-squeeze-early'
    ]
    assert universe['signal_stream_snapshots'][0]['occurrence_source'] == 'qmd_squeeze_episode'
    mandates = {x['mandate_id']:x for x in base['portfolio']['mandates']}
    for m in payload['portfolio']['mandates']:
        if m.get('run_plan_id') == plan_id:
            assert m['maximum_planned_risk_fraction'] == mandates[m['mandate_id'].removeprefix(plan_id+'-')]['maximum_planned_risk_fraction']
    quality = next(x for x in payload['market_discovery']['rule_sets'] if x['rule_set_id']==PROFILE_ID+'-tradability')
    assert [x['value'] for x in quality['conditions']] == [.01,20.,200000.,25000.,1.,.5,150.]
    observe = next(x for x in payload['market_discovery']['rule_sets'] if x['rule_set_id']==PROFILE_ID+'-observe')
    assert 'vwap-observed' not in [x['condition_id'] for x in observe['conditions']]


def test_missing_authority_fails_closed():
    with pytest.raises(ValueError, match='liquidity'):
        build(configuration_base(), source_parameters={})


def test_candidate_parameters_resolve_with_transport_and_independent_policy():
    from src.trading_runtime.strategy_engine import resolve_long_momentum_parameters
    payload, _, _ = build(configuration_base(), source_parameters=source())
    profile = next(p for p in payload['strategy']['profiles'] if p['profile_id']==PROFILE_ID)
    resolved = resolve_long_momentum_parameters(profile['parameters'])
    assert resolved['r1_ladder']['cash_fraction'] == .9
    assert resolved['liquidity_admission']['maximum_current_spread_bps'] == 150.
    assert resolved['historical_hod']['setup_minimum_session_relative_volume'] == 2.


def test_compiled_candidate_retains_source_native_early_squeeze_scope():
    payload, canvas, plan_id = build(configuration_base(), source_parameters=source())
    runtime, _, _ = _build_configuration_release(
        canvas_revision=canvas['revision'],
        canvas_profile=canvas['profile'],
        configuration=payload,
        run_plan_id=plan_id,
        strategy_profile_id=PROFILE_ID,
    )
    plan = next(row for row in runtime['run_plans']['plans']
                if row['run_plan_id'] == plan_id)
    universe = next(row for row in runtime['run_plans']['universes']
                    if row['universe_id'] == plan['universe_id'])
    assert plan['signal_stream_ids'] == ['price-squeeze-early']
    assert plan['watchlist_ids'] == []
    assert plan['activation']['watch_duration'] == 'session'
    assert universe['source'] == 'signal_stream'
    assert universe['watchlist_snapshots'] == []
    assert [row['signal_stream_id'] for row in universe['signal_stream_snapshots']] == [
        'price-squeeze-early'
    ]
    assert universe['signal_stream_snapshots'][0]['occurrence_source'] == 'qmd_squeeze_episode'
