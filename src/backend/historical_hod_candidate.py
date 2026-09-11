"""Immutable backtest candidate for 1s historical/HOD breaks and 5s MACD."""
from copy import deepcopy

from .structural_recovery_candidate import build as build_template
from src.trading_runtime.historical_hod import CONTRACT, DEFAULTS

PROFILE_ID = 'historical-hod-1s-macd-5s-v1'
LABEL = 'Historical resistance / HOD - 1s breakout, 5s MACD'


def build(base):
    payload, canvas, plan = build_template(base, align_179=True,
        profile_id=PROFILE_ID, label_override=LABEL)
    profile = next(p for p in payload['strategy']['profiles'] if p['profile_id']==PROFILE_ID)
    p = profile['parameters']
    for key in ('structural_recovery_contract','structural_recovery'):
        p.pop(key,None)
    p.update(historical_hod_contract=CONTRACT,historical_hod=dict(DEFAULTS))
    p['historical_hod'].update(sizing_mode='cash_tranches',cash_fraction=.9,tranche_count=3)
    profile['description'] = ('Non-red completed 1s breakout of historical resistance below HOD, '
        'then current-day resistance or HOD fallback; price above VWAP and completed bullish 5s MACD. '
        'Historical stop advances after a breakout close plus one holding close, initial-risk trailing fallback, structural episode management, '
        'and resistance targets nearest 5% above each broken level. Same-episode prior body-high reentry. '
        'Reserve up to 90% cash for three tranches; add once per higher resistance breakout with shared protection.')
    observe = next(r for r in payload['market_discovery']['rule_sets']
        if r['rule_set_id']==PROFILE_ID+'-observe')
    observe['conditions'].extend([
        dict(condition_id='macd-observed',enabled=True,left_source_id='indicator.macd.line',
            left_timeframe='5s',comparator='greater_than',value=-1000000.),
        dict(condition_id='signal-observed',enabled=True,left_source_id='indicator.macd.signal',
            left_timeframe='5s',comparator='greater_than',value=-1000000.),
        dict(condition_id='vwap-observed',enabled=True,left_source_id='indicator.vwap.execution_value',
            left_timeframe='1s',comparator='greater_than',value=0.),
    ])
    for name in ('trigger','confirmation'):
        p['entry_rules'][name] = {'operator':'all','rule_sets':[deepcopy(observe)]}
    return payload, canvas, plan


def create():
    from .trading_configuration_service import configuration_base, create_test_candidate
    payload, canvas, plan = build(configuration_base())
    return create_test_candidate(label=LABEL,canvas_revision=canvas['revision'],canvas_profile=canvas['profile'],
        configuration=payload,run_plan_id=plan,strategy_profile_id=PROFILE_ID)
