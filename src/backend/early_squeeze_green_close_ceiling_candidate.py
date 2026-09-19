"""Immutable successor to 330 requiring a completed green 1s breakout."""
from . import early_squeeze_fast_candidate as previous
from src.trading_runtime.early_squeeze_price import GREEN_CLOSE_CEILING_CONTRACT as CONTRACT

LABEL = 'Early Squeeze / resettable breakout / green-close resistance trail v14'
DESCRIPTION = (
    'Preserve Candidate 330 entry, addition, target, execution, and stepped trailing rules. '
    'Advance the stop ceiling from one resistance lower edge to the next only after an eligible trade '
    'has broken the next resistance upper edge and a completed green one-second candle has closed '
    'strictly above that same upper edge. Neither a trade break nor a candle close alone advances it. '
    'The fixed trail distance initialized from the filled entry and stop has a ten-cent minimum.'
)


def build(configuration, baseline):
    return previous.base.build(configuration, baseline, profile_id=CONTRACT, label=LABEL, description=DESCRIPTION)


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    previous.base.require_loaded_executor(CONTRACT)
    candidates = trading_journal().trading_configuration_candidate_summaries()
    existing = next((c for c in candidates if c['candidate_revision'] == 331), None)
    if existing:
        if existing['label'] != LABEL:
            raise ValueError('Candidate 331 is occupied; refusing to replace it')
        return configuration_candidate(existing['candidate_id'], required=True)
    if max((c['candidate_revision'] for c in candidates), default=0) != 330:
        raise ValueError('Next candidate must be exactly 331')
    payload, canvas, plan = build(configuration_base(), configuration_candidate(previous.base.BASELINE_ID, required=True))
    result = create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
    if result['candidate_revision'] != 331:
        raise RuntimeError('Concurrent candidate creation changed the revision; inspect saved candidate')
    return result
