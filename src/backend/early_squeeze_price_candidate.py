"""Immutable successor to 325 with the user's corrected admission and stop rules."""
from . import early_squeeze_fast_candidate as previous
from src.trading_runtime.early_squeeze_price import CONTRACT

LABEL = 'Early Squeeze / midpoint 10% gap / price breakout v9'
DESCRIPTION = (
    'Activate at first Early Squeeze; use filtered V7 only. Select R1 by the closest eligible midpoint '
    'below prior HOD, including gray former resistances. An eligible upward trade-price crossing of '
    'R1 midpoint plus 10% of the gap to the next current resistance midpoint confirms the breakout. '
    'No candle close, body size, top-quarter close or five-tick clearance gate for fresh breakouts. '
    'Retain VWAP, 2.5% spread and modest liquidity checks; one-third available cash sizing from 317. '
    'Additional resistance price-gap breaks add the original cash tranche. Distinct current-resistance '
    'price-gap breaks count only within the position lifecycle. On eligible price updates select the '
    'third overhead midpoint before four breaks, second at four or five, first at six or more; '
    'targets only move upward. Gray bands may be entry anchors, not target candidates. '
    'Stop one tick below the broken resistance lower band, offset below bid if needed; realtime bid-high '
    'trailing preserves initial filled entry-to-stop distance. Stop-out recovery retains the frozen highest '
    'completed 100ms close and an upward trade-price crossing above it, with no candle confirmation; '
    'recovery stop uses the latest broken resistance lower band. Partial exits complete liquidation. '
    'Display the actual price threshold and effective stop and target movements. No MACD, ATR or other trading gates.'
)


def build(configuration, baseline):
    return previous.base.build(configuration, baseline, profile_id=CONTRACT, label=LABEL, description=DESCRIPTION)


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    previous.base.require_loaded_executor(CONTRACT)
    candidates = trading_journal().trading_configuration_candidate_summaries()
    existing = next((c for c in candidates if c['candidate_revision'] == 326), None)
    if existing:
        if existing['label'] != LABEL:
            raise ValueError('Candidate 326 is occupied; refusing to replace it')
        return configuration_candidate(existing['candidate_id'], required=True)
    if max((c['candidate_revision'] for c in candidates), default=0) != 325:
        raise ValueError('Next candidate must be exactly 326')
    payload, canvas, plan = build(configuration_base(), configuration_candidate(previous.base.BASELINE_ID, required=True))
    result = create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
    if result['candidate_revision'] != 326:
        raise RuntimeError('Concurrent candidate creation changed the revision; inspect saved candidate')
    return result
