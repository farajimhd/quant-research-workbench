"""Immutable successor requiring an episode-local completed resistance break."""
from . import early_squeeze_breakout_candidate as base
from src.trading_runtime.early_squeeze_price import CONFIRMED_BREAKOUT_CONTRACT as CONTRACT

LABEL = 'Early Squeeze / confirmed resistance breakout v19'
DESCRIPTION = (
    'Preserves v18 forming 1s MACD episodes, mandatory bullish 100ms MACD, '
    'episode target continuity, green 1s trailing ceilings and midpoint oscillation exits. '
    'Selects the governing resistance before testing entry eligibility; a higher approached '
    'band supersedes a lower band. Entry requires a witnessed approach and completed 100ms '
    'close strictly above both the upper band and 10 percent midpoint-gap threshold. '
    'New episodes, structural changes and failed breaks invalidate confirmation. '
    'Same-episode reentry additionally requires a new episode high, three completed '
    '100ms candles and at least 300ms review without unresolved forming resistance. '
    'Trigger and stop anchors are recorded separately. The 10-cent initial stop minimum '
    'does not apply to same-episode reentries.'
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
