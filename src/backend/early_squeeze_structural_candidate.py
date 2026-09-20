"""User-approved resistance-only stop successor to the v22 trial."""
from . import early_squeeze_breakout_candidate as base
from src.trading_runtime.early_squeeze_consistent import STRUCTURAL_CONTRACT as CONTRACT

LABEL = 'Early Squeeze / unified 1s structural stops v23'
DESCRIPTION = (
    'One midpoint-break event: green completed 1s close above midpoint followed by the '
    'next actual 1s open above midpoint. A green candle opening below the midpoint can '
    'confirm a fresh reclaim after a failed next-open acceptance. Entries, additions, '
    'target break counts and stop advancement share this event. Stops advance ONLY '
    'after a higher resistance is accepted, to its lower edge minus 0.5 prior 1s ATR; '
    'no trailing between resistances. Hard broker stops remain continuously active. '
    'Completed 1s MACD owns episode resets; forming 1s and bullish 100ms MACD gate purchases. '
    'Adds are fill-owned once per resistance per episode. Entries use a resistance lower '
    'edge buffer of 0.5 prior 14-bar true-range SMA, a 2-ATR distance floor and a 10-cent '
    'initial floor. Opening-event expiry, extension <= max(band width, 2 ATR, tick), '
    'spread <= half stop distance, liquidity gates, one-third sizing, 3/2/1 target '
    'continuity and k0.5 chop are retained. Prospective candidate, not holdout accepted.'
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
