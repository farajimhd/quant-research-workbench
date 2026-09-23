"""Repair Strategy 349's unsupported historical Watchlist prior-close source."""
from . import early_squeeze_momentum_349_candidate as previous
from src.trading_runtime.early_squeeze_momentum import CONTRACT

LABEL = 'Early Squeeze / adaptive multi-MACD v27 / Strategy 350'
DESCRIPTION = previous.DESCRIPTION + (
    ' Prior-close admission is enforced fail-closed by the causal strategy observation rather '
    'than by the historical Watchlist interval provider.'
)


def build(configuration, baseline):
    payload, canvas, plan_id = previous.build(configuration, baseline)
    profile = next(p for p in payload['strategy']['profiles'] if p['profile_id'] == CONTRACT)
    profile.update(name=LABEL, description=DESCRIPTION)
    plan = next(p for p in payload['run_plans']['plans'] if p['run_plan_id'] == plan_id)
    plan.update(name=LABEL, description=DESCRIPTION)
    universe = next(u for u in payload['run_plans']['universes'] if u['universe_id'] == plan['universe_id'])
    universe.update(name=LABEL, description=(
        'Early Squeeze latches independently of the live $1 purchase floor; the strategy '
        'executor fail-closes purchases unless the causal prior regular close is below $20.'))
    return payload, canvas, plan_id


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    previous.base.base.require_loaded_executor(CONTRACT)
    existing = next((c for c in trading_journal().trading_configuration_candidate_summaries()
        if c['label'] == LABEL), None)
    if existing:
        return configuration_candidate(existing['candidate_id'], required=True)
    payload, canvas, plan = build(configuration_base(),
        configuration_candidate(previous.base.base.BASELINE_ID, required=True))
    return create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
