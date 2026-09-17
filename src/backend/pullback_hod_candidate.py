"""Immutable pullback strategy retaining the screened family's admission filters."""
from copy import deepcopy

from src.trading_runtime.pullback_hod import CONTRACT, DEFAULTS

BASELINE_ID = '657a81d7-25f4-416e-8c9a-636105abe5a8'
BASELINE_HASH = '787f28b3df6afafb596d0fa7f0ed5b1e03b231cc91cbff68684a447428fa3b62'
PARENT_PROFILE = 'v7-222-session-rvol-2x-closed-tail-v2'
PROFILE = 'pullback-hod-v1'
PLAN = 'v7-setup-recovery-v9-backtest'
LABEL = 'HOD pullback / bullish swing-low recovery v1'
DESCRIPTION = (
    'Independent completed-1s pullback policy. Retains the $1-$20 price, session RVOL >= 2, '
    'liquidity, spread, session, completed-volume and first-allocation sizing settings. '
    'Entry requires a confirmed swing low after a confirmed swing high, at least two ticks '
    'of pullback, then a green candle closing above the preceding close with at least a '
    'one-tick body, body/range >= 30% and close in the upper 40% of its range. '
    'Entry and executable ask must remain below the prior HOD; close must be in the '
    'upper 30% of the VWAP-to-HOD range. Stop below the swing low, then trail new higher lows. '
    'A rejection near HOD/resistance followed within five seconds by a bearish close below '
    'its low exits; retreat must exceed max(two ticks, 0.5 ATR). A bearish close back below '
    'the entry HOD after a completed breakout also exits. Targets remain above entry HOD, '
    'subject to inherited regular-session LULD protection. One entry per confirmed low; '
    'no adds, MACD episode gate, or standalone wick exit. Research candidate; not screened.'
)


def prepare_payload(baseline, published_profiles):
    if baseline['candidate_id'] != BASELINE_ID or baseline['content_hash'] != BASELINE_HASH:
        raise ValueError('Pullback source candidate identity changed')
    payload = deepcopy(baseline['payload'])
    parent = next(p for p in payload['strategy']['profiles'] if p['profile_id']==PARENT_PROFILE)
    profile = deepcopy(parent)
    profile.update(profile_id=PROFILE,name=LABEL,description=DESCRIPTION,publication_status='draft',
                   editable=True,derived_from_profile_id=PARENT_PROFILE)
    parameters = profile['parameters']
    parameters.update(pullback_hod_contract=CONTRACT,pullback_hod=deepcopy(DEFAULTS))
    # Old settings remain source evidence; their executor is not dispatched.
    # Disable old fill-specific hooks explicitly.
    parameters['historical_hod'].update(setup_immediate_tail_body_ratio=0.,
        setup_entry_resistance_seconds=0.,setup_resistance_return_exit=0,setup_stalled_seconds=0.)
    profile['lifecycle']['initial_entry']['add_steps'] = []
    profile['lifecycle']['reentry']['require_new_confirmation'] = True
    payload['strategy']['profiles'] = [deepcopy(published_profiles.get(p['profile_id'],p))
                                      for p in payload['strategy']['profiles']]+[profile]
    plan = next(p for p in payload['run_plans']['plans'] if p['run_plan_id']==PLAN)
    if plan['allowed_environments'] != ['backtest']:
        raise ValueError('Pullback candidate must remain backtest-only')
    plan.update(profile_id=PROFILE,name=LABEL,description=DESCRIPTION)
    return payload


def create():
    from .trading_configuration_service import configuration_candidate, configuration_base, create_test_candidate
    baseline = configuration_candidate(BASELINE_ID,required=True)
    published = {p['profile_id']:p for p in configuration_base()['strategy']['profiles']
                 if p.get('publication_status')=='published'}
    payload = prepare_payload(baseline,published)
    return create_test_candidate(label=LABEL,canvas_revision=payload['canvas']['revision'],
        canvas_profile=payload['canvas']['profile'],configuration=payload,
        run_plan_id=PLAN,strategy_profile_id=PROFILE)
