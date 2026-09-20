"""Immutable midpoint execution successor; v20 remains unchanged."""
from . import early_squeeze_breakout_candidate as base
from src.trading_runtime.early_squeeze_price import MIDPOINT_EXECUTION_CONTRACT as CONTRACT

LABEL = 'Early Squeeze / confirmed midpoint execution v21'
DESCRIPTION = (
    'Preserves v20 volatility midpoint exit k0.5, sizing and episode target continuity. '
    'Entries confirm a completed 100ms close above midpoint plus 10 percent of the next '
    'midpoint gap, without requiring the upper band. Trailing is capped by the next '
    'unbroken resistance lower edge until a green completed 1s close breaks its upper edge. '
    'Forming 1s MACD supports quiet intervals with consumed QMD prefix evidence. '
    'Adds require an upward midpoint crossing confirmed by a completed 100ms close '
    'and bullish 100ms MACD; one filled add '
    'per resistance per episode, with pending reservations and unfilled retry handling.'
)


def build(configuration, baseline):
    return base.build(configuration, baseline, profile_id=CONTRACT, label=LABEL, description=DESCRIPTION)


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    base.require_loaded_executor(CONTRACT)
    existing = next((c for c in trading_journal().trading_configuration_candidate_summaries()
                     if c['label'] == LABEL), None)
    if existing:
        return configuration_candidate(existing['candidate_id'], required=True)
    payload, canvas, plan = build(configuration_base(), configuration_candidate(base.BASELINE_ID, required=True))
    return create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
