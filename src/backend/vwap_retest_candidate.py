"""Immutable successor: grouped resistance ladder and reaction-based entry stops."""
from copy import deepcopy

BASELINE_ID = 'ca1c4a43-5644-4d6c-a545-b70dd8d77f21'
BASELINE_HASH = '51b551e961291a26a150a6c9d3202bd127d4749ac1aac9ee74b6d001d72cd8a9'
PROFILE = 'vwap-midpoint-grouped-retests-v2'
LABEL = 'VWAP midpoint / grouped resistances / confirmed retest stops v2'
PLAN = 'v7-setup-recovery-v9-backtest'


def prepare_payload(baseline):
    if baseline['candidate_id'] != BASELINE_ID or baseline['content_hash'] != BASELINE_HASH:
        raise ValueError('Grouped retest baseline changed')
    payload = deepcopy(baseline['payload'])
    parent = next(p for p in payload['strategy']['profiles'] if p['profile_id'] == 'vwap-midpoint-resistance-ladder-v1')
    profile = deepcopy(parent)
    profile.update(profile_id=PROFILE, name=LABEL, publication_status='draft',
        derived_from_profile_id=parent['profile_id'],
        description='Group nearby resistance bands using half the median prior broken-zone gap; '
        'overlap-only warm-up until three positive gaps. Freeze zones at encounter. '
        'After six zone breaks require a confirmed pullback/retest and close above the zone. '
        'Prefer support swings for earlier entries; otherwise require a broken resistance/transition '
        'retest. Retest stops sit below the selected lower band. Preserve MACD episodes and unreserved thirds.')
    profile['parameters']['vwap_ladder'].update(group_resistances=1, require_late_retest=1, allow_retest_stop_fallback=1)
    payload['strategy']['profiles'].append(profile)
    plan = next(p for p in payload['run_plans']['plans'] if p['run_plan_id'] == PLAN)
    if plan['allowed_environments'] != ['backtest']:
        raise ValueError('Retest candidate must remain backtest-only')
    plan.update(profile_id=PROFILE, name=LABEL, description=profile['description'])
    return payload


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    payload = prepare_payload(configuration_candidate(BASELINE_ID, required=True))
    # Baseline execution settings are pinned, but unrelated published profiles
    # must retain today's immutable definitions rather than their old snapshot.
    published = {p['profile_id']: p for p in configuration_base()['strategy']['profiles']
                 if p.get('publication_status') == 'published'}
    profiles = payload['strategy']['profiles']
    payload['strategy']['profiles'] = [deepcopy(published.pop(p['profile_id'], p)) for p in profiles]
    payload['strategy']['profiles'].extend(deepcopy(list(published.values())))
    return create_test_candidate(label=LABEL, canvas_revision=payload['canvas']['revision'],
        canvas_profile=payload['canvas']['profile'], configuration=payload,
        run_plan_id=PLAN, strategy_profile_id=PROFILE)
