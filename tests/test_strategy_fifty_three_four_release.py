from copy import deepcopy
from dataclasses import replace
import pytest
from test_strategy_fifty_two_release import source_fixture, reseal
from test_strategy_thirty_three_configuration import APPROVAL
from src.trading_runtime import strategy_fifty_three_release as quarter
from src.trading_runtime import strategy_fifty_four_release as half


@pytest.mark.parametrize('module,number,ratio',[(quarter,53,(1,4)),(half,54,(1,2))])
def test_exact_parent_economic_immutability_and_only_declared_additional_rule(module,number,ratio):
    source = source_fixture()
    before = deepcopy(source.payload)
    derive = getattr(module,f'derive_strategy_fifty_{"three" if number==53 else "four"}_configuration')
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
    verify = getattr(module,f'verify_prepared_strategy_fifty_{"three" if number==53 else "four"}_manifest')
    assert verify(result['payload']['strategy'])
    changed = deepcopy(result['payload']['strategy'])
    changed['numbered_release']['entry_spread_risk_policy']['maximum_spread_original_risk']=[1,8]
    reseal(changed['numbered_release'])
    with pytest.raises(ValueError):verify(changed)
    with pytest.raises(ValueError):derive(replace(source,payload_hash='f'*64),**APPROVAL)


@pytest.mark.parametrize('number,word',[(53,'three'),(54,'four')])
def test_source_certification_remains_fail_closed_until_review(monkeypatch,number,word):
    import importlib
    module=importlib.import_module(f'src.backend.backtest_strategy_fifty_{word}_certification')
    monkeypatch.setattr(module,f'STRATEGY{number}_SOURCE_AST',{})
    with pytest.raises(ValueError):getattr(module,f'certify_strategy_fifty_{word}_source')()


@pytest.mark.parametrize('module,number,word',[(quarter,53,'three'),(half,54,'four')])
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

@pytest.mark.parametrize('number,word', [(53,'three'),(54,'four')])
def test_reviewed_complete_source_seal_rejects_each_changed_execution_leaf(number,word,tmp_path):
    import importlib
    from pathlib import Path
    module=importlib.import_module(f'src.backend.backtest_strategy_fifty_{word}_certification')
    pins=getattr(module,f'STRATEGY{number}_SOURCE_AST')
    certify=getattr(module,f'certify_strategy_fifty_{word}_source')
    assert set(pins)==set(module.REQUIRED_SOURCE_FILES)
    assert len(certify())==64
    root=Path(module.__file__).parents[2]
    for index,relative in enumerate(module.REQUIRED_SOURCE_FILES):
        changed=tmp_path/f'mutated-{number}-{index}.py'
        changed.write_text((root/relative).read_text(encoding='utf-8')+'\n_UNREVIEWED_EXECUTION_CHANGE = True\n',encoding='utf-8')
        with pytest.raises(ValueError,match='pinned source changed'):
            certify(source_overrides={relative:changed})
