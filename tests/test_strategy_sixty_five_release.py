"""Prepared waiting baseline identity and serialization boundaries."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import pytest
from test_strategy_sixty_four_release import source_fixture
from test_strategy_fifty_release import APPROVAL
from src.trading_runtime import strategy_sixty_five_release as child
from src.trading_runtime.strategy_sixty_five_contract import strategy_sixty_five_contract
from src.trading_runtime.journal_contract import canonical_json


def test_exact_economics_and_separate_waiting_declaration():
    source=source_fixture();before=deepcopy(source.payload)
    result=child.derive_strategy_sixty_five_configuration(source, **APPROVAL)
    assert source.payload == before
    for key in before.keys()-{'strategy','strategy_profile','run_plan'}:
        assert result['payload'][key] == before[key]
    mutable={'strategy_number','revision','name','profile_id','profile_revision','numbered_release'}
    for key in before['strategy'].keys()-mutable:
        assert result['payload']['strategy'][key] == before['strategy'][key]
    m=result['payload']['strategy']['numbered_release']
    assert m['automatic_market_policy']['geometry_binding_policy']=={'version':'ladder-wait-first-complete-geometry-v1'}
    assert 'population_exclusions' not in m['automatic_market_policy']
    assert m['automatic_market_policy']['gate']['qualification_mode']=='vwap_cross'
    assert m['automatic_entry_policy']['lot_count']==3
    assert m['automatic_entry_policy']['allocation']=='equal'
    assert child.verify_prepared_strategy_sixty_five_manifest(result['payload']['strategy'])
    assert child.release_contract().rule_set_contracts.count('ladder-wait-first-complete-geometry-v1')==1


@pytest.mark.parametrize('field,value',[('payload_hash','a'*64),('attempt_id','00000000-0000-0000-0000-000000000001')])
def test_foreign_parent(field,value):
    with pytest.raises(ValueError): child.derive_strategy_sixty_five_configuration(replace(source_fixture(),**{field:value}),**APPROVAL)


@pytest.mark.parametrize('key',[ 'automatic_entry_policy','automatic_market_policy','economic_policy','source_policy','session_policy'])
def test_resealed_policy_drift_rejected(key):
    s=child.derive_strategy_sixty_five_configuration(source_fixture(),**APPROVAL)['payload']['strategy']
    s['numbered_release'][key]['foreign']=True
    m=s['numbered_release'];m['manifest_hash']=sha256(canonical_json({k:v for k,v in m.items() if k!='manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError): child.verify_prepared_strategy_sixty_five_manifest(s)


def test_source_empty_review_rejects(monkeypatch):
    from src.backend import backtest_strategy_sixty_five_certification as proof
    monkeypatch.setattr(proof, 'STRATEGY65_SOURCE_AST', {})
    with pytest.raises(ValueError,match='not sealed'): proof.certify_strategy_sixty_five_source()


def test_contract_matches_complete_waiting_policy():
    c=strategy_sixty_five_contract()
    assert c.automatic_entry_policy.payload()==child.policies()['automatic_entry_policy']
    assert child.canonical_json(c.automatic_market_policy)==child.canonical_json(child.policies()['automatic_market_policy'])
    assert c.allows_session_exit and not any((c.allows_adds,c.allows_reentry,c.allows_trailing,c.allows_replacement))


def test_behavior_respects_existing_scalar_bound_and_oversize_rejects():
    from src.trading_runtime.strategy_one_configuration_tree import encode_nodes, MAX_TEXT_LENGTH
    assert 0 < len(child.BEHAVIOR) <= MAX_TEXT_LENGTH
    payload = child.derive_strategy_sixty_five_configuration(source_fixture(), **APPROVAL)['payload']
    assert encode_nodes(payload)
    payload['strategy']['numbered_release']['contract']['behavior_specification'] = 'x' * (MAX_TEXT_LENGTH + 1)
    with pytest.raises(ValueError, match='text is too long'):
        encode_nodes(payload)


def test_registry_initialization_preserves_legacy_and_selects_typed_waiting():
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy, declared_automatic_ladder_release
    from src.trading_runtime.strategy_registry import numbered_strategy
    for number in (1, 42, 49, 51, 64):
        assert numbered_fixed_strategy(number).strategy_number == number
        if number != 1:
            assert numbered_strategy(number).number == number
        assert not declared_automatic_ladder_release(number)
    assert declared_automatic_ladder_release(65)
    assert numbered_fixed_strategy(65) == strategy_sixty_five_contract()


def test_actual_compiler_requires_full_native_source_proof():
    from pipelines.strategy_one.strategy_sixty_five_configuration import compile_strategy_sixty_five_configuration
    result=compile_strategy_sixty_five_configuration(source_fixture(),**APPROVAL)
    assert result['payload']['strategy']['strategy_number']==65
    assert child.verify_prepared_strategy_sixty_five_manifest(result['payload']['strategy'])


def test_resealed_gate_value_drift_rejected():
    strategy=child.derive_strategy_sixty_five_configuration(source_fixture(),**APPROVAL)['payload']['strategy']
    manifest=strategy['numbered_release']
    gate=manifest['automatic_market_policy']['gate']
    key=next(key for key,value in gate.items() if type(value)is float and value==10000.)
    gate[key]=100.
    manifest['manifest_hash']=sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError):child.verify_prepared_strategy_sixty_five_manifest(strategy)


@pytest.mark.parametrize('missing', [
    'declared-automatic-ladder-native-adapter@1',
    'ladder-wait-first-complete-geometry-v1',
    'arte.trading_squeeze_ladder_geometry_binding_v1@exact-parent:earliest-causal-pair',
])
def test_semantic_adapter_requires_exact_input_companions(monkeypatch, missing):
    from src.trading_runtime import strategy_registry as registry
    from src.trading_runtime.numbered_fixed_strategy import declared_automatic_ladder_release
    release = child.release_contract()
    release = replace(release, input_contracts=tuple(value for value in release.input_contracts if value != missing), approved_digest='')
    release = replace(release, approved_digest=release.digest())
    monkeypatch.setattr(registry, 'numbered_strategy', lambda number: release)
    if missing == 'declared-automatic-ladder-native-adapter@1':
        assert not declared_automatic_ladder_release(65)
    else:
        with pytest.raises(ValueError, match='exact supported waiting semantics'):
            declared_automatic_ladder_release(65)


@pytest.mark.parametrize('defect',['entry_type','entry_value','binding_missing','binding_foreign','mode_foreign'])
def test_semantic_adapter_rejects_foreign_typed_factory(monkeypatch,defect):
    from types import SimpleNamespace
    from src.trading_runtime import strategy_registry as registry
    from src.trading_runtime.numbered_fixed_strategy import declared_automatic_ladder_release
    registration=registry.fixed_strategy_executor('early-squeeze-strategy',65)
    contract=strategy_sixty_five_contract()
    policy=contract.automatic_entry_policy
    market=deepcopy(contract.automatic_market_policy)
    if defect=='entry_type':policy=SimpleNamespace(**policy.payload())
    elif defect=='entry_value':
        policy=deepcopy(policy)
        object.__setattr__(policy,'policy_id','foreign')
    elif defect=='binding_missing':market.pop('geometry_binding_policy')
    elif defect=='binding_foreign':market['geometry_binding_policy']={'version':'foreign'}
    else:market['gate']['qualification_mode']='foreign'
    foreign=SimpleNamespace(strategy_number=65,strategy_id=contract.strategy_id,
        execution_interval=contract.execution_interval,automatic_entry_policy=policy,automatic_market_policy=market)
    registration=replace(registration,contract_factory=lambda:foreign)
    monkeypatch.setattr(registry,'fixed_strategy_executor',lambda *a:registration)
    with pytest.raises(ValueError):declared_automatic_ladder_release(65)
