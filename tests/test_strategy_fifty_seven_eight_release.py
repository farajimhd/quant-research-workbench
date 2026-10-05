from copy import deepcopy
from dataclasses import replace
import pytest
from test_strategy_fifty_two_release import source_fixture, reseal
from test_strategy_thirty_three_configuration import APPROVAL
from src.trading_runtime import strategy_fifty_seven_release as quarter
from src.trading_runtime import strategy_fifty_eight_release as half


@pytest.mark.parametrize('module,number,ratio',[(quarter,57,(1,4)),(half,58,(1,2))])
def test_exact_parent_economic_immutability_and_only_declared_additional_rule(module,number,ratio):
    source = source_fixture()
    before = deepcopy(source.payload)
    derive = getattr(module,f'derive_strategy_fifty_{"seven" if number==57 else "eight"}_configuration')
    result = derive(source,**APPROVAL)
    assert source.payload == before
    release = module.release_contract()
    parent = module.parent_policy.release_contract()
    assert release.number == release.executor_revision == number
    assert release.rule_set_contracts == parent.rule_set_contracts+(module.ENTRY_SPREAD_RISK_POLICY.policy_id,)
    assert module.ENTRY_SPREAD_RISK_POLICY.maximum_spread_original_risk == ratio
    assert 'armed_profit_floor_policy' not in result['payload']['strategy']['numbered_release']
    for key in before.keys()-{'strategy','strategy_profile','run_plan'}:
        assert result['payload'][key] == before[key]
    for key in before['strategy'].keys()-{'strategy_number','revision','name','profile_id','profile_revision','numbered_release'}:
        assert result['payload']['strategy'][key] == before['strategy'][key]
    assert result['payload']['strategy']['numbered_release']['early_original_risk_failure_policy'] == before['strategy']['numbered_release']['early_original_risk_failure_policy']
    verify = getattr(module,f'verify_prepared_strategy_fifty_{"seven" if number==57 else "eight"}_manifest')
    assert verify(result['payload']['strategy'])
    changed = deepcopy(result['payload']['strategy'])
    changed['numbered_release']['entry_spread_risk_policy']['maximum_spread_original_risk']=[1,8]
    reseal(changed['numbered_release'])
    with pytest.raises(ValueError):verify(changed)
    with pytest.raises(ValueError):derive(replace(source,payload_hash='f'*64),**APPROVAL)


@pytest.mark.parametrize('number,word',[(57,'seven'),(58,'eight')])
def test_source_certification_remains_fail_closed_until_review(monkeypatch,number,word):
    import importlib
    module=importlib.import_module(f'src.backend.backtest_strategy_fifty_{word}_certification')
    monkeypatch.setattr(module,f'STRATEGY{number}_SOURCE_AST',{})
    with pytest.raises(ValueError):getattr(module,f'certify_strategy_fifty_{word}_source')()


@pytest.mark.parametrize('module,number,word',[(quarter,57,'seven'),(half,58,'eight')])
def test_compiler_and_publisher_use_own_exact_native_identity(monkeypatch,module,number,word):
    import importlib
    from src.backend import backtest_fixed_v4_certification as cert
    from pipelines.strategy_one.configuration_publisher import _verified_numbered_envelope
    compiler=importlib.import_module(f'pipelines.strategy_one.strategy_fifty_{word}_configuration')
    calls=[]
    def blocked(value):
        calls.append(value)
        raise ValueError('reviewed source seal required')
    monkeypatch.setattr(cert,'certify_numbered_fixed_v4_projection',blocked)
    with pytest.raises(ValueError,match='seal required'):
        getattr(compiler,f'compile_strategy_fifty_{word}_configuration')(source_fixture(),**APPROVAL)
    assert calls==[number]
    envelope=getattr(module,f'derive_strategy_fifty_{word}_configuration')(source_fixture(),**APPROVAL)
    assert _verified_numbered_envelope(envelope)[0]==envelope['payload']


@pytest.mark.parametrize('module,word',[(quarter,'seven'),(half,'eight')])
def test_source_version_is_immutable_even_with_resealed_manifest(module,word):
    value=getattr(module,f'derive_strategy_fifty_{word}_configuration')(source_fixture(),**APPROVAL)['payload']['strategy']
    assert module.QUOTE_SOURCE_CONTRACT in module.release_contract().input_contracts
    value['numbered_release']['entry_spread_risk_quote_source']['contract']='declared-entry-spread-risk-quote-source@1'
    reseal(value['numbered_release'])
    with pytest.raises(ValueError):getattr(module,f'verify_prepared_strategy_fifty_{word}_manifest')(value)


@pytest.mark.parametrize('number,word',[(57,'seven'),(58,'eight')])
def test_every_reviewed_source_leaf_rejects_a_mutation(tmp_path,number,word):
    import importlib
    from pathlib import Path
    module=importlib.import_module(f'src.backend.backtest_strategy_fifty_{word}_certification')
    pins=getattr(module,f'STRATEGY{number}_SOURCE_AST')
    if not pins:
        with pytest.raises(ValueError,match='not sealed'):
            getattr(module,f'certify_strategy_fifty_{word}_source')()
        return
    assert set(pins)==set(module.REQUIRED_SOURCE_FILES) and len(pins)==62
    certify=getattr(module,f'certify_strategy_fifty_{word}_source')
    assert len(certify())==64
    root=Path(module.__file__).parents[2]
    changed=tmp_path/'mutated_source.py'
    for relative in pins:
        changed.write_text((root/relative).read_text(encoding='utf-8')+'\nSOURCE_MUTATION_SENTINEL = 1\n',encoding='utf-8')
        with pytest.raises(ValueError,match='pinned source changed'):
            certify(source_overrides={relative:changed})

@pytest.mark.parametrize('module,number',[(quarter,57),(half,58)])
def test_intent_recovery_source_is_declared_and_immutable(module,number):
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    assert module.INTENT_RECOVERY_CONTRACT in module.release_contract().input_contracts
    assert numbered_fixed_strategy(number).entry_spread_risk_intent_recovery_contract==module.INTENT_RECOVERY_CONTRACT
    assert numbered_fixed_strategy(number-2).entry_spread_risk_intent_recovery_contract=='entry-spread-risk-intent-recovery@1'
