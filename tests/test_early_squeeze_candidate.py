from copy import deepcopy

import pytest

from src.backend import early_squeeze_breakout_candidate as C
from src.backend.trading_configuration_service import configuration_base, _build_configuration_release
from src.trading_runtime.strategy_engine import resolve_long_momentum_parameters, strategy_rule_timeframes
from tests.test_r1_ladder_candidate import source


def baseline(base):
    payload=deepcopy(base)
    profile=deepcopy(next(p for p in payload['strategy']['profiles'] if p['profile_id']=='long-momentum-balanced'))
    profile['profile_id']='vwap-impulse-pullback-breakout-v4'
    profile['parameters']=source()
    profile['lifecycle']['trading_behavior']=source()['strategy_behavior']
    payload['strategy']['profiles'].append(profile)
    next(p for p in payload['run_plans']['plans'] if p['run_plan_id']=='balanced-replay')['profile_id']=profile['profile_id']
    return dict(candidate_id=C.BASELINE_ID,content_hash=C.BASELINE_HASH,payload=payload)


def test_compiled_candidate_has_only_agreed_contract_and_occurrence_population(monkeypatch):
    base=configuration_base()
    # Build against the pre-publication state even when this version is already
    # published in the developer's journal. Publication immutability stays on.
    base['strategy']['profiles'] = [p for p in base['strategy']['profiles'] if p['profile_id'] != C.PROFILE_ID]
    monkeypatch.setattr('src.backend.trading_configuration_service.configuration_base', lambda: deepcopy(base))
    before=deepcopy(base)
    payload,canvas,plan_id=C.build(base,baseline(base))
    assert base==before
    profile=next(p for p in payload['strategy']['profiles'] if p['profile_id']==C.PROFILE_ID)
    p=profile['parameters']
    assert sorted(k for k in p if k.endswith('_contract'))==['early_squeeze_breakout_contract','structural_recovery_contract']
    assert not any(k in p for k in ('vwap_ladder','episode_management','historical_hod','momentum_management','profit_pocket'))
    assert p['liquidity_admission']['maximum_price'] is None
    assert p['strategy_behavior']['entry_cutoff_time']==''
    assert p['strategy_behavior']['flatten_time']==''
    for key in ('maximum_current_spread_bps','maximum_admission_spread_bps','maximum_spread_bps'):
        assert p['liquidity_admission'][key]==250.
    assert strategy_rule_timeframes(p)=={'100ms','1s'}
    assert resolve_long_momentum_parameters(p)['early_squeeze_breakout_contract']==C.PROFILE_ID
    runtime,_,_=_build_configuration_release(canvas_revision=canvas['revision'],canvas_profile=canvas['profile'],
        configuration=payload,run_plan_id=plan_id,strategy_profile_id=C.PROFILE_ID)
    plan=next(p for p in runtime['run_plans']['plans'] if p['run_plan_id']==plan_id)
    assert plan['signal_stream_ids']==['price-squeeze-early']
    assert plan['activation']['watch_duration']=='session'
    assert plan['activation']['watchlist_policy']=='not_required'
    from src.backend.trading_configuration_service import _effective_campaign_policy
    assert _effective_campaign_policy(plan)['add_authority']=='automatic'
    assert 'live' not in plan['allowed_environments']
    assert profile['lifecycle']['initial_entry']['capital_request']['mode']=='mandate_fraction'
    assert profile['lifecycle']['initial_entry']['capital_request']['value']==pytest.approx(1/3)
    assert profile['lifecycle']['initial_entry']['add_steps']==[]
    assert profile['lifecycle']['exit']=={'rule_sets':[]}
    for m in payload['portfolio']['mandates']:
        if m['mandate_id'] in plan['mandate_ids']:
            original=next(x for x in base['portfolio']['mandates'] if x['mandate_id']=='balanced-replay')
            assert m['maximum_planned_risk_fraction']==original['maximum_planned_risk_fraction']


def test_changed_source_cannot_be_used():
    base=configuration_base();b=baseline(base);b['content_hash']='changed'
    with pytest.raises(ValueError,match='317'):
        C.build(base,b)


@pytest.mark.parametrize('payload', [b'{}', b'{"contracts": []}'])
def test_candidate_creation_rejects_backend_without_executor(monkeypatch, payload):
    from io import BytesIO
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: BytesIO(payload))
    with pytest.raises(RuntimeError, match='has not loaded'):
        C.create()


def test_candidate_creation_rejects_old_backend_endpoint(monkeypatch):
    from urllib.error import URLError
    def unavailable(*args, **kwargs):
        raise URLError('404')
    monkeypatch.setattr('urllib.request.urlopen', unavailable)
    with pytest.raises(RuntimeError, match='restart the managed backend'):
        C.create()


def test_loaded_executor_capability(monkeypatch):
    import json
    from io import BytesIO
    from src.trading_runtime.strategy_engine import supported_custom_execution_contracts
    capabilities = json.dumps({'contracts': supported_custom_execution_contracts()}).encode()
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **k: BytesIO(capabilities))
    C.require_loaded_executor()
