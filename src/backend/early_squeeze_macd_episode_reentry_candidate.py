"""Immutable successor to 331 with bullish 1s-MACD episode reentry."""
from . import early_squeeze_fast_candidate as previous
from src.trading_runtime.early_squeeze_price import MACD_EPISODE_REENTRY_CONTRACT as CONTRACT

LABEL = 'Early Squeeze / 1s MACD episode-high reentry v15'
DESCRIPTION = (
    'Preserve Candidate 331 HOD, candle, anti-chase, initial-stop, target, execution, and resistance-ceiling rules. '
    'Every entry requires the latest completed 1s MACD line strictly above its signal. During one bullish '
    '1s MACD episode, retain the causal price high; after an exit in that same episode, reentry requires an '
    'eligible trade strictly above the previously observed episode high. A same-episode reentry places its stop '
    'one tick under the closest fully cleared resistance below price and retains the actual entry-to-stop distance '
    'without applying the ten-cent minimum. A new bullish MACD episode starts with fresh entry eligibility.'
)


def build(configuration, baseline):
    return previous.base.build(configuration, baseline, profile_id=CONTRACT, label=LABEL, description=DESCRIPTION)


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    previous.base.require_loaded_executor(CONTRACT)
    candidates = trading_journal().trading_configuration_candidate_summaries()
    existing = next((c for c in candidates if c['candidate_revision'] == 332), None)
    if existing:
        if existing['label'] != LABEL:
            raise ValueError('Candidate 332 is occupied; refusing to replace it')
        return configuration_candidate(existing['candidate_id'], required=True)
    if max((c['candidate_revision'] for c in candidates), default=0) != 331:
        raise ValueError('Next candidate must be exactly 332')
    payload, canvas, plan = build(configuration_base(), configuration_candidate(previous.base.BASELINE_ID, required=True))
    result = create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
    if result['candidate_revision'] != 332:
        raise RuntimeError('Concurrent candidate creation changed the revision; inspect saved candidate')
    return result
