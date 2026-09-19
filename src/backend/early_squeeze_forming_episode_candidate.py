"""Immutable successor using causal forming one-second MACD episodes."""
from . import early_squeeze_breakout_candidate as base
from src.trading_runtime.early_squeeze_price import FORMING_EPISODE_CONTRACT as CONTRACT

LABEL = 'Early Squeeze / forming MACD and midpoint exit v18'
DESCRIPTION = (
    'Forming 1s MACD defines bullish episodes and entry eligibility using causal trade previews '
    'from authoritative completed 1s MACD. All entries and additions require bullish 100ms MACD. '
    'Reentries select the resistance cleared by the current move, retaining the 10 percent '
    'midpoint-gap threshold and requiring price above the upper band. Same-episode reentry '
    'requires a new episode high, three completed 100ms candles and at least 300ms review; '
    'unbroken forming resistance blocks further reentry for that episode. Adds require a '
    'completed 100ms close above the band. Five contiguous completed 1s candles with at '
    'least two crossings of the same resistance midpoint exit an open position, including '
    'excursions outside the band. '
    'Distinct resistance breaks and target ordinal continue across positions in an episode. '
    'Stops advance only after a green completed 1s upper-band break. Initial risk has a '
    '10-cent minimum except same-episode reentries, which stop under the resistance below.'
)


def build(configuration, baseline):
    return base.build(configuration, baseline, profile_id=CONTRACT, label=LABEL, description=DESCRIPTION)


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    base.require_loaded_executor(CONTRACT)
    candidates = trading_journal().trading_configuration_candidate_summaries()
    existing = next((c for c in candidates if c['label'] == LABEL), None)
    if existing:
        return configuration_candidate(existing['candidate_id'], required=True)
    payload, canvas, plan = build(configuration_base(), configuration_candidate(base.BASELINE_ID, required=True))
    return create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
