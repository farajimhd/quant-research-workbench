"""Immutable backtest successor with separate pullback and breakout entries."""
from copy import deepcopy

BASELINE_ID = '15c3410c-698a-4668-ac72-aa34ab91b7b8'
BASELINE_HASH = '9c1d5daf2818a2d2a1f1a5a0f7f8bc30ebd87aa36bead90a8e278ffcb9040ea2'
PROFILE = 'vwap-grouped-pullback-breakout-v3'
PLAN = 'v7-setup-recovery-v9-backtest'
LABEL = 'VWAP grouped ladder / fresh pullback and HOD resistance breakout v3'


def prepare_payload(baseline):
    if baseline['candidate_id'] != BASELINE_ID or baseline['content_hash'] != BASELINE_HASH:
        raise ValueError('Post-move baseline changed')
    payload = deepcopy(baseline['payload'])
    parent = next(p for p in payload['strategy']['profiles'] if p['profile_id']=='vwap-midpoint-grouped-retests-v2')
    profile = deepcopy(parent)
    profile.update(profile_id=PROFILE,name=LABEL,publication_status='draft',
        derived_from_profile_id=parent['profile_id'],
        description='After six grouped resistance breaks, allow one entry per fresh confirmed pullback '
        'using bullish 1s MACD and price above VWAP, independent of the used 10s episode, with stop below its swing low. '
        'Alternatively cross the highest resistance below HOD plus one tick with bullish 100ms/1s/5s/10s '
        'MACDs and an unused 10s episode. Breakout stop is below the band; target is the midpoint '
        'between resistances bracketing one average broken-zone gap above the reference. '
        'Keep initial midpoint entry, unreserved thirds, and the grouped trailing ladder.')
    profile['parameters']['vwap_ladder'].update(post_move_entries=1,pullback_independent_episode=1,pullback_above_vwap_only=1,
                                              breakout_offset_ticks=1)
    payload['strategy']['profiles'].append(profile)
    plan = next(p for p in payload['run_plans']['plans'] if p['run_plan_id']==PLAN)
    if plan['allowed_environments'] != ['backtest']:
        raise ValueError('Post-move candidate must remain backtest-only')
    plan.update(profile_id=PROFILE,name=LABEL,description=profile['description'])
    return payload


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    payload = prepare_payload(configuration_candidate(BASELINE_ID,required=True))
    published = {p['profile_id']:p for p in configuration_base()['strategy']['profiles']
                 if p.get('publication_status')=='published'}
    payload['strategy']['profiles'] = [deepcopy(published.pop(p['profile_id'],p))
                                      for p in payload['strategy']['profiles']]
    payload['strategy']['profiles'].extend(deepcopy(list(published.values())))
    return create_test_candidate(label=LABEL,canvas_revision=payload['canvas']['revision'],
        canvas_profile=payload['canvas']['profile'],configuration=payload,
        run_plan_id=PLAN,strategy_profile_id=PROFILE)
