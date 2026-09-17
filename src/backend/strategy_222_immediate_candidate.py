"""Immutable successor to the Aug 21 screened Session RVOL 2x candidate."""
from copy import deepcopy

BASELINE_ID = 'e1379a48-cc62-43f4-8662-87433910dd38'
BASELINE_HASH = '506daedeaacc6ff1902807e70d681ecbad5a34940d9313229ba4124eb1dc31c9'
PARENT_PROFILE = 'v7-222-session-rvol-2x'
PROFILE = 'v7-222-session-rvol-2x-immediate-exits-v1'
PLAN = 'v7-setup-recovery-v9-backtest'
LABEL = 'Strategy 222 / RVOL 2x / $1 floor + immediate exits v1'


def prepare_payload(baseline, published_profiles):
    if baseline['candidate_id'] != BASELINE_ID or baseline['content_hash'] != BASELINE_HASH:
        raise ValueError('Session RVOL 2x baseline identity changed')
    payload = deepcopy(baseline['payload'])
    parent = next(p for p in payload['strategy']['profiles'] if p['profile_id'] == PARENT_PROFILE)
    profile = deepcopy(parent)
    profile.update(profile_id=PROFILE,name=LABEL,publication_status='draft',editable=True,
        derived_from_profile_id=PARENT_PROFILE,
        description='Session RVOL >= 2 with the inherited entry gates and $1 minimum price. '
        'Exit on a forming 1s upper wick greater than 30% of body; reentry requires the '
        'trigger price never breached, normal entry gates, and a one-tick actual-fill stop. '
        'Exit after five continuous seconds in the entry resistance band, or a completed '
        '1s close below a resistance band broken upward while held. Research version; not screened.')
    profile['parameters']['liquidity_admission']['minimum_price'] = 1.
    profile['parameters']['historical_hod'].update(setup_immediate_tail_body_ratio=.3,
        setup_entry_resistance_seconds=5.,setup_resistance_return_exit=1,
        setup_tail_reentry_stop_ticks=1.)
    payload['strategy']['profiles'] = [deepcopy(published_profiles.get(p['profile_id'],p))
        for p in payload['strategy']['profiles']] + [profile]
    plan = next(p for p in payload['run_plans']['plans'] if p['run_plan_id'] == PLAN)
    plan.update(profile_id=PROFILE,name=LABEL,description=profile['description'])
    if plan['allowed_environments'] != ['backtest']:
        raise ValueError('Successor must remain backtest-only')
    rule = next(r for r in payload['market_discovery']['rule_sets']
                if r['rule_set_id'] == 'v7-setup-recovery-v9-tradability')
    condition = next(c for c in rule['conditions'] if c['condition_id'] == 'tradability-0')
    if condition['value'] != 2 or condition['comparator'] != 'greater_or_equal':
        raise ValueError('Unexpected inherited discovery price floor')
    condition['value'] = 1.
    return payload


def create():
    from .trading_configuration_service import configuration_candidate, configuration_base, create_test_candidate
    baseline = configuration_candidate(BASELINE_ID,required=True)
    published = {p['profile_id']:p for p in configuration_base()['strategy']['profiles']
                 if p.get('publication_status') == 'published'}
    payload = prepare_payload(baseline,published)
    return create_test_candidate(label=LABEL,canvas_revision=payload['canvas']['revision'],
        canvas_profile=payload['canvas']['profile'],configuration=payload,
        run_plan_id=PLAN,strategy_profile_id=PROFILE)
