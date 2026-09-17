"""Create an immutable backtest candidate for the VWAP resistance ladder."""
from copy import deepcopy

from src.trading_runtime.vwap_resistance_ladder import CONTRACT, DEFAULTS

BASELINE_ID = '33a3b164-bbdf-4fe2-be70-42bc9096da6d'
BASELINE_HASH = 'ac0ecdf629625cb6fc852cfca845bc62abb72e522f05056b4fab68e0a7fa1a1f'
PROFILE = CONTRACT
LABEL = 'VWAP midpoint / five MACDs / resistance ladder / unreserved thirds v1'
PLAN = 'v7-setup-recovery-v9-backtest'
DESCRIPTION = (
    'One initial entry per bullish 10s MACD episode, above the VWAP-HOD midpoint, '
    'with native 100ms/1s/5s/10s/30s MACD above signal. Initial protection below '
    'a swing low from V7 support. Stop trails two resistances behind, first on R3; '
    'late entries begin trailing on their second break. Target distance 3, then '
    '2 after four session breaks, then 1 after six. Entries after six session '
    'breaks advance the one-level target only twice. Targets sit one tick above '
    'the selected upper band. Buy one third of eligible cash initially and add '
    'up to that same cash amount at the first two subsequent resistance breaks; '
    'no future cash reservation. Pre-04:05 ET trades excluded from derived state. '
    'Research candidate; no profitability or live release acceptance.'
)


def prepare_payload(baseline, published_profiles):
    if baseline['candidate_id'] != BASELINE_ID or baseline['content_hash'] != BASELINE_HASH:
        raise ValueError('VWAP ladder source candidate identity changed')
    payload = deepcopy(baseline['payload'])
    parent = next(p for p in payload['strategy']['profiles']
                  if p['profile_id'] == 'swing-rise-pullback-hod-macd10s-v4')
    profile = deepcopy(parent)
    profile.update(profile_id=PROFILE, name=LABEL, description=DESCRIPTION,
        publication_status='draft', editable=True, derived_from_profile_id=parent['profile_id'])
    parameters = profile['parameters']
    for key in ('pullback_hod_contract', 'pullback_hod', 'r1_ladder_contract', 'r1_ladder'):
        parameters.pop(key, None)
    parameters.update(vwap_ladder_contract=CONTRACT, vwap_ladder=dict(DEFAULTS))
    profile['lifecycle']['initial_entry']['add_steps'] = []
    # The versioned executor owns the two additions; generic lifecycle steps do not.
    payload['strategy']['profiles'] = [deepcopy(published_profiles.get(p['profile_id'], p))
        for p in payload['strategy']['profiles']] + [profile]
    plan = next(p for p in payload['run_plans']['plans'] if p['run_plan_id'] == PLAN)
    if plan['allowed_environments'] != ['backtest']:
        raise ValueError('VWAP ladder candidate must remain backtest-only')
    plan.update(profile_id=PROFILE, name=LABEL, description=DESCRIPTION)
    return payload


def create():
    from .trading_configuration_service import configuration_candidate, configuration_base, create_test_candidate
    baseline = configuration_candidate(BASELINE_ID, required=True)
    published = {p['profile_id']: p for p in configuration_base()['strategy']['profiles']
                 if p.get('publication_status') == 'published'}
    payload = prepare_payload(baseline, published)
    return create_test_candidate(label=LABEL, canvas_revision=payload['canvas']['revision'],
        canvas_profile=payload['canvas']['profile'], configuration=payload,
        run_plan_id=PLAN, strategy_profile_id=PROFILE)
