"""Create a separate, immutable backtest candidate without activating live trading."""
from .macd_threshold_candidate import build as build_declarations
from src.trading_runtime.hindsight_long import CONTRACT, settings

PROFILE_ID = 'hindsight-long-1s-v1'
LABEL = 'Hindsight approximation / long 1s MACD episodes'


def build(base, overrides=None):
    parameters = dict(hindsight_long_contract=CONTRACT, hindsight_long=overrides or {})
    config = settings(parameters)
    parameters['hindsight_long'] = config
    payload, canvas, plan = build_declarations(base, profile_id=PROFILE_ID, label=LABEL,
        parameters=parameters, macd_timeframe='1s', quantity=config['quantity'])
    profile = next(p for p in payload['strategy']['profiles'] if p['profile_id'] == PROFILE_ID)
    profile['description'] = (
        'Causal long-only approximation of hindsight MACD swings. Completed 1s bullish '
        'episode entry, opening-low broker stop, executable-bid trailing exit or MACD '
        'episode end. One acquisition attempt per episode. Research candidate; '
        'hindsight closeness and profitability are not yet accepted.')
    profile['lifecycle']['initial_entry']['order_intent']['deadline_ms'] = int(config['entry_deadline_ms'])
    profile['lifecycle']['reentry']['order_intent']['deadline_ms'] = int(config['entry_deadline_ms'])
    return payload, canvas, plan


def create(overrides=None):
    from .trading_configuration_service import configuration_base, create_test_candidate
    payload, canvas, plan = build(configuration_base(), overrides)
    return create_test_candidate(label=LABEL, canvas_revision=canvas['revision'],
        canvas_profile=canvas['profile'], configuration=payload,
        run_plan_id=plan, strategy_profile_id=PROFILE_ID)
