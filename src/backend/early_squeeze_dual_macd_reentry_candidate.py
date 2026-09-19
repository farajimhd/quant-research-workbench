"""Immutable successor to 332 with confirmed additions and 100ms reentry review."""
from . import early_squeeze_fast_candidate as previous
from src.trading_runtime.early_squeeze_price import DUAL_MACD_REENTRY_CONTRACT as CONTRACT

LABEL = 'Early Squeeze / dual-MACD confirmed-add reentry v16'
DESCRIPTION = (
    'Preserve Candidate 332 HOD, initial entry, targets, protection, one-second MACD episode-high reentry, '
    'and green-close resistance trail rules. Every entry and addition additionally requires the latest completed 100ms MACD '
    'line strictly above its signal. An addition requires a completed 100ms candle to close strictly above the '
    'crossed resistance upper edge. Same-episode reentry waits for at least three completed 100ms candles after '
    'the exit; if their causal structural evidence reports a forming resistance that is not subsequently broken '
    'by a completed close, reentry remains disabled until the one-second MACD episode ends.'
)


def build(configuration, baseline):
    return previous.base.build(configuration, baseline, profile_id=CONTRACT, label=LABEL, description=DESCRIPTION)


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    previous.base.require_loaded_executor(CONTRACT)
    candidates = trading_journal().trading_configuration_candidate_summaries()
    existing = next((c for c in candidates if c['candidate_revision'] == 333), None)
    if existing:
        if existing['label'] != LABEL:
            raise ValueError('Candidate 333 is occupied; refusing to replace it')
        return configuration_candidate(existing['candidate_id'], required=True)
    if max((c['candidate_revision'] for c in candidates), default=0) != 332:
        raise ValueError('Next candidate must be exactly 333')
    payload, canvas, plan = build(configuration_base(), configuration_candidate(previous.base.BASELINE_ID, required=True))
    result = create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
    if result['candidate_revision'] != 333:
        raise RuntimeError('Concurrent candidate creation changed the revision; inspect saved candidate')
    return result
