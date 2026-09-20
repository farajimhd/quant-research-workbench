"""Prospective one-second resistance acceptance candidate, not a release."""
from . import early_squeeze_breakout_candidate as base
from src.trading_runtime.early_squeeze_consistent import CONTRACT

LABEL = 'Early Squeeze / unified 1s resistance acceptance v22'
DESCRIPTION = (
    'One midpoint-break event: green completed 1s close above midpoint followed by '
    'the next actual 1s open above midpoint. Entries, additions, target break counts '
    'and trailing ceilings share this event. No next-gap hurdle or rotating entry anchor. '
    'Completed 1s MACD owns episode resets; forming 1s and bullish 100ms MACD gate entries. '
    'Adds are fill-owned once per resistance per episode. Stops ratchet from completed '
    '1s closes only, with continuous broker protection. Entry stops buffer the resistance '
    'by 0.5 prior 14-bar true-range SMA, with a 2-ATR distance floor and 10-cent initial floor. '
    'Purchases expire at the opening event, require ask extension <= max(band width, 2 ATR, tick), '
    'and spread <= half stop distance. Preserves liquidity gates, one-third sizing, '
    '3/2/1 episode target rule and k0.5 midpoint chop. Development candidate; no holdout acceptance.'
)


def build(configuration, baseline):
    return base.build(configuration, baseline, profile_id=CONTRACT, label=LABEL, description=DESCRIPTION)


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    base.require_loaded_executor(CONTRACT)
    existing = next((c for c in trading_journal().trading_configuration_candidate_summaries() if c['label'] == LABEL), None)
    if existing:
        return configuration_candidate(existing['candidate_id'], required=True)
    payload, canvas, plan = build(configuration_base(), configuration_candidate(base.BASELINE_ID, required=True))
    return create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
