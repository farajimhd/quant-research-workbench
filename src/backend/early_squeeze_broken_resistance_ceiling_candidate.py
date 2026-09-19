"""Immutable correction to 329 using the latest fully cleared resistance."""
from . import early_squeeze_fast_candidate as previous
from src.trading_runtime.early_squeeze_price import BROKEN_RESISTANCE_CEILING_CONTRACT as CONTRACT

LABEL = 'Early Squeeze / resettable breakout / cleared-resistance trail v13'
DESCRIPTION = (
    'Preserve Candidate 328 resettable breakout, entry, addition, target, and execution rules. '
    'Cap the whole-position fixed-distance trade-price trail at the lower edge of the latest fully '
    'cleared resistance. While price is between that resistance and the next resistance, or inside '
    'the next resistance band, the stop cannot rise above that lower edge. An eligible trade strictly '
    'above the next band upper edge advances the ceiling to that band lower edge.'
)


def build(configuration, baseline):
    return previous.base.build(configuration, baseline, profile_id=CONTRACT, label=LABEL, description=DESCRIPTION)


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    previous.base.require_loaded_executor(CONTRACT)
    candidates = trading_journal().trading_configuration_candidate_summaries()
    existing = next((c for c in candidates if c['candidate_revision'] == 330), None)
    if existing:
        if existing['label'] != LABEL:
            raise ValueError('Candidate 330 is occupied; refusing to replace it')
        return configuration_candidate(existing['candidate_id'], required=True)
    if max((c['candidate_revision'] for c in candidates), default=0) != 329:
        raise ValueError('Next candidate must be exactly 330')
    payload, canvas, plan = build(configuration_base(), configuration_candidate(previous.base.BASELINE_ID, required=True))
    result = create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
    if result['candidate_revision'] != 330:
        raise RuntimeError('Concurrent candidate creation changed the revision; inspect saved candidate')
    return result
