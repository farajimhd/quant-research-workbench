"""Immutable successor with a volatility-buffered midpoint oscillation exit."""
from . import early_squeeze_breakout_candidate as base
from src.trading_runtime.early_squeeze_price import VOLATILITY_CHOP_CONTRACT as CONTRACT

LABEL = 'Early Squeeze / volatility midpoint exit k0.5 v20'
DESCRIPTION = (
    'Preserves v19 confirmed resistance entries, forming 1s MACD, bullish 100ms MACD, '
    'same-episode reentry review/high rules, target continuity and protective stops. '
    'Midpoint oscillation exit requires five contiguous completed 1s closes with at '
    'least two midpoint crossings AND a current close strictly below midpoint minus '
    '0.5 times the average true range of the prior 14 completed 1s candles. '
    'The evaluated candle is excluded from volatility. Missing/invalid/gapped history '
    'requires a fresh 14-bar warmup; protective stops remain active during warmup.'
)


def build(configuration, baseline):
    return base.build(configuration, baseline, profile_id=CONTRACT, label=LABEL, description=DESCRIPTION)


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    base.require_loaded_executor(CONTRACT)
    candidates = trading_journal().trading_configuration_candidate_summaries()
    existing = next((c for c in candidates if c['label'] == LABEL), None)
    if existing:
        return configuration_candidate(existing['candidate_id'], required=True)
    payload, canvas, plan = build(configuration_base(), configuration_candidate(base.BASELINE_ID, required=True))
    return create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
