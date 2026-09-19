"""Immutable successor to 328 with resistance-capped trailing protection."""
from . import early_squeeze_fast_candidate as previous
from src.trading_runtime.early_squeeze_price import RESISTANCE_CEILING_CONTRACT as CONTRACT

LABEL = 'Early Squeeze / resettable breakout / resistance-capped trail v12'
DESCRIPTION = (
    'Preserve Candidate 328 resettable breakout, entry, addition, target, and execution rules. '
    'The whole-position fixed-distance trade-price trail may rise normally between resistance bands, '
    'but cannot rise above the lower edge of the nearest current resistance that has not been fully broken. '
    'While price is inside that resistance band the stop remains at its lower edge; an eligible trade '
    'strictly above the upper edge releases the ceiling and trailing resumes toward the next unbroken resistance.'
)


def build(configuration, baseline):
    return previous.base.build(configuration, baseline, profile_id=CONTRACT, label=LABEL, description=DESCRIPTION)


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    previous.base.require_loaded_executor(CONTRACT)
    candidates = trading_journal().trading_configuration_candidate_summaries()
    existing = next((c for c in candidates if c['candidate_revision'] == 329), None)
    if existing:
        if existing['label'] != LABEL:
            raise ValueError('Candidate 329 is occupied; refusing to replace it')
        return configuration_candidate(existing['candidate_id'], required=True)
    if max((c['candidate_revision'] for c in candidates), default=0) != 328:
        raise ValueError('Next candidate must be exactly 329')
    payload, canvas, plan = build(configuration_base(), configuration_candidate(previous.base.BASELINE_ID, required=True))
    result = create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
    if result['candidate_revision'] != 329:
        raise RuntimeError('Concurrent candidate creation changed the revision; inspect saved candidate')
    return result
