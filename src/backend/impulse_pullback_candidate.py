"""Immutable successor for 30-second impulse-qualified pullbacks."""
from copy import deepcopy

BASELINE_ID = '3823f64d-26d2-44ad-a35d-a0af731d48ff'
BASELINE_HASH = 'd94487fc8e55447c474ac282b378d8419a80e2230b728df2e60b9e768579d0b8'
PROFILE = 'vwap-impulse-pullback-breakout-v4'
PLAN = 'v7-setup-recovery-v9-backtest'
LABEL = 'VWAP ladder / 30s impulse pullback v4'


def prepare_payload(baseline):
    if baseline['candidate_id'] != BASELINE_ID or baseline['content_hash'] != BASELINE_HASH:
        raise ValueError('Impulse pullback baseline changed')
    payload = deepcopy(baseline['payload'])
    parent = next(p for p in payload['strategy']['profiles']
                  if p['profile_id'] == 'vwap-grouped-pullback-breakout-v3')
    profile = deepcopy(parent)
    profile.update(profile_id=PROFILE, name=LABEL, publication_status='draft',
        derived_from_profile_id=parent['profile_id'],
        description='Pullbacks require at least 5% rise over 30 seconds followed by a 20%-45% '
        'retracement and fresh level recovery with bullish 1s MACD above VWAP. One pullback per '
        'impulse. Previously broken overhead resistances remain eligible targets. '
        'Preserve initial entry, breakout entry, cash sizing and trailing protection.')
    profile['parameters']['vwap_ladder']['pullback_min_rise_pct'] = 5.
    payload['strategy']['profiles'].append(profile)
    plan = next(p for p in payload['run_plans']['plans'] if p['run_plan_id'] == PLAN)
    if plan['allowed_environments'] != ['backtest']:
        raise ValueError('Impulse candidate must remain backtest-only')
    plan.update(profile_id=PROFILE, name=LABEL, description=profile['description'])
    return payload


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    payload = prepare_payload(configuration_candidate(BASELINE_ID, required=True))
    published = {p['profile_id']: p for p in configuration_base()['strategy']['profiles']
                 if p.get('publication_status') == 'published'}
    payload['strategy']['profiles'] = [deepcopy(published.pop(p['profile_id'], p))
                                      for p in payload['strategy']['profiles']]
    payload['strategy']['profiles'].extend(deepcopy(list(published.values())))
    return create_test_candidate(label=LABEL, canvas_revision=payload['canvas']['revision'],
        canvas_profile=payload['canvas']['profile'], configuration=payload,
        run_plan_id=PLAN, strategy_profile_id=PROFILE)
