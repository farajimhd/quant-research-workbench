"""Immutable successor to 327 with resettable entries and one fixed trail."""
from . import early_squeeze_fast_candidate as previous
from src.trading_runtime.early_squeeze_price import EPISODE_CONTRACT as CONTRACT

LABEL = 'Early Squeeze / resettable breakout / fixed trade-price trail v11'
DESCRIPTION = (
    'Activate at first Early Squeeze; filtered V7 only. R1 is the closest eligible midpoint below prior HOD, '
    'including gray former resistances. Require price above midpoint plus 10% of the gap to the next current '
    'resistance. Only reentry at that resistance also requires a new traded-price high since its first breakout. A trade below the lower band resets that resistance as a fresh breakout. No separate recovery path. '
    'Keep VWAP, 2.5% spread, modest liquidity and one-third available cash sizing from 317. '
    'Each current resistance permits at most one addition attempt per session; the entry resistance is already '
    'consumed for additions. Never retry rejected additions or add on gray levels. '
    'Stops anchor below the broken lower band; additions retain the original single whole-position fixed-distance trail. '
    'Trail on eligible traded-price highs with fixed initial filled entry-to-stop distance; '
    'stop triggers use eligible trades, while execution uses available market liquidity. '
    'Targets advance to the third overhead midpoint before four lifecycle breaks, second at four or five, '
    'first at six or more. Display effective stop and target movements. No MACD or candle confirmation.'
)


def build(configuration, baseline):
    return previous.base.build(configuration, baseline, profile_id=CONTRACT, label=LABEL, description=DESCRIPTION)


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    previous.base.require_loaded_executor(CONTRACT)
    candidates = trading_journal().trading_configuration_candidate_summaries()
    existing = next((c for c in candidates if c['candidate_revision'] == 328), None)
    if existing:
        if existing['label'] != LABEL:
            raise ValueError('Candidate 328 is occupied; refusing to replace it')
        return configuration_candidate(existing['candidate_id'], required=True)
    if max((c['candidate_revision'] for c in candidates), default=0) != 327:
        raise ValueError('Next candidate must be exactly 328')
    payload, canvas, plan = build(configuration_base(), configuration_candidate(previous.base.BASELINE_ID, required=True))
    result = create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
    if result['candidate_revision'] != 328:
        raise RuntimeError('Concurrent candidate creation changed the revision; inspect saved candidate')
    return result
