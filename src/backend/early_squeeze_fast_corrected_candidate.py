"""Immutable successor to 324 with the user's corrected admission and stop rules."""
from . import early_squeeze_fast_candidate as previous
from src.trading_runtime.early_squeeze_fast import CORRECTED_CONTRACT as CONTRACT

LABEL = 'Early Squeeze / 100ms 1.25x body / resistance recovery stop v8'
DESCRIPTION = previous.DESCRIPTION.replace('at least twice', 'at least 1.25 times').replace(
    'stop below a still-valid confirmed swing above the broken band, else the last completed 100ms open, offset only if needed.',
    'stop below the most recently broken resistance lower band, offset below bid only if needed.').replace(
    'No ATR, MACD, impulse, pullback, cutoff, or special late-breakout trading rules.',
    'V7 snapshot freshness uses the actual causal snapshot clock. Admission volume and VWAP use the completed 100ms candle; '
    'no completed 1s detector row is required. No ATR, MACD, impulse, pullback, cutoff, or special late-breakout trading rules.')


def build(configuration, baseline):
    return previous.base.build(configuration, baseline, profile_id=CONTRACT, label=LABEL, description=DESCRIPTION)


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    previous.base.require_loaded_executor(CONTRACT)
    candidates = trading_journal().trading_configuration_candidate_summaries()
    existing = next((c for c in candidates if c['candidate_revision'] == 325), None)
    if existing:
        if existing['label'] != LABEL:
            raise ValueError('Candidate 325 is occupied; refusing to replace it')
        return configuration_candidate(existing['candidate_id'], required=True)
    if max((c['candidate_revision'] for c in candidates), default=0) != 324:
        raise ValueError('Next candidate must be exactly 325')
    payload, canvas, plan = build(configuration_base(), configuration_candidate(previous.base.BASELINE_ID, required=True))
    result = create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
    if result['candidate_revision'] != 325:
        raise RuntimeError('Concurrent candidate creation changed the revision; inspect saved candidate')
    return result
