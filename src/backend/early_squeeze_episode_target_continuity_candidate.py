"""Immutable successor to 333 preserving target progression across episode reentries."""
from . import early_squeeze_fast_candidate as previous
from src.trading_runtime.early_squeeze_price import EPISODE_TARGET_CONTINUITY_CONTRACT as CONTRACT

LABEL = 'Early Squeeze / episode target continuity v17'
DESCRIPTION = (
    'Preserve Candidate 333 entry, dual-MACD, confirmed-add, reentry-review, stop, and trailing rules. '
    'Track distinct resistance breaks for the entire bullish one-second MACD episode. Closing one position does '
    'not reset that count: a same-episode reentry selects and advances its target with the existing table—third '
    'overhead target before four breaks, second at four or five, and first at six or more. A new one-second MACD '
    'episode resets the resistance-break count. A completed 100ms resistance-forming event inside a current '
    'resistance band starts a dwell timer; if no completed close breaks the band upper edge for more than five '
    'seconds while the position remains open, liquidate the entire position immediately.'
)


def build(configuration, baseline):
    return previous.base.build(configuration, baseline, profile_id=CONTRACT, label=LABEL, description=DESCRIPTION)


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    previous.base.require_loaded_executor(CONTRACT)
    candidates = trading_journal().trading_configuration_candidate_summaries()
    existing = next((c for c in candidates if c['candidate_revision'] == 334), None)
    if existing:
        if existing['label'] != LABEL:
            raise ValueError('Candidate 334 is occupied; refusing to replace it')
        return configuration_candidate(existing['candidate_id'], required=True)
    if max((c['candidate_revision'] for c in candidates), default=0) != 333:
        raise ValueError('Next candidate must be exactly 334')
    payload, canvas, plan = build(configuration_base(), configuration_candidate(previous.base.BASELINE_ID, required=True))
    result = create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
    if result['candidate_revision'] != 334:
        raise RuntimeError('Concurrent candidate creation changed the revision; inspect saved candidate')
    return result
