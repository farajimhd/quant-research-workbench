"""Create exactly candidate 324 without changing published earlier strategies."""
from . import early_squeeze_breakout_candidate as base
from src.trading_runtime.early_squeeze_fast import CONTRACT

LABEL = 'Early Squeeze / 100ms 2x body / five-tick R1 v7'
DESCRIPTION = (
    'Activate at first Early Squeeze only, using filtered V7 history. Select the closest band below HOD '
    'among current resistances and gray transitions from resistance. Entry requires a completed green '
    '100ms candle whose body is at least twice the average body of all prior eligible green 100ms '
    'candles since session start, excluding itself; no prior green candles means no entry. '
    'Close at least five ticks above the upper band, above VWAP, with the original top-quarter '
    'close requirement for fresh R1 entry. No MACD gate. Spread cap 2.5%; existing modest liquidity gates. '
    'One-third cash entry and recovery sizing from 317. Green 100ms resistance closes add the original '
    'cash tranche without the big-body or top-quarter gate. Count distinct green 100ms upper-band '
    'breaks during the position lifecycle only. Each completed 100ms close selects the third overhead '
    'resistance midpoint before four breaks, second at four or five, first at six or more; targets '
    'only move upward. Gray bands are entry anchors, not target candidates. '
    'Initial stop is the nearest valid tick below the lower band, offset below bid immediately if '
    'needed and restored toward the structural anchor when executable. Real-time bid-high trailing '
    'preserves the original filled entry-to-stop distance. Stop-out recovery uses the frozen highest '
    'completed 100ms close and a qualifying large green 100ms candle; stop below a still-valid '
    'confirmed swing above the broken band, else the last completed 100ms open, offset only if needed. '
    'A partial full-position target or stop fill completes liquidation before further entries or adds. '
    'No ATR, MACD, impulse, pullback, cutoff, or special late-breakout trading rules.'
)


def build(configuration, baseline):
    return base.build(configuration, baseline, profile_id=CONTRACT, label=LABEL, description=DESCRIPTION)


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    base.require_loaded_executor(CONTRACT)
    from .trading_runtime_service import trading_journal
    candidates = trading_journal().trading_configuration_candidate_summaries()
    existing = next((c for c in candidates if c['candidate_revision'] == 324), None)
    if existing:
        if existing['label'] != LABEL:
            raise ValueError('Candidate 324 is occupied; refusing to replace it')
        return configuration_candidate(existing['candidate_id'], required=True)
    if max((c['candidate_revision'] for c in candidates), default=0) != 323:
        raise ValueError('Next candidate must be exactly 324')
    payload, canvas, plan = build(configuration_base(), configuration_candidate(base.BASELINE_ID, required=True))
    result = create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
    if result['candidate_revision'] != 324:
        raise RuntimeError('Concurrent candidate creation changed the revision; inspect saved candidate')
    return result
