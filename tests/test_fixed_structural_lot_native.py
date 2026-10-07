"""Closed tree controls; synthetic certificates never install authority."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from uuid import uuid4
import pytest
from test_fixed_structural_lot_source import prepared
from src.backend import backtest_fixed_structural_lot_native as native
from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
from src.trading_runtime.strategy_registry import numbered_strategy
from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
from src.trading_runtime.numbered_fixed_strategy import DECLARED_FIXED_ADAPTER
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_one_configuration_tree import encode_nodes,node_hash


def cert(payload):
    return CertifiedStrategyOneConfiguration(str(uuid4()),sha256(canonical_json(payload).encode()).hexdigest(),
        node_hash(encode_nodes(payload)),'fixture-source','c'*64,'d'*64,payload)

def declarations():
    release=numbered_strategy(42)
    payload=dict(strategy=dict(strategy_id=release.executor_strategy_id,strategy_number=42,revision=42,
        parameters=dict(execution=dict(tick_size=.01),costs=dict(per_share=.005,minimum_per_order=1.),capital=dict(fraction='1/3')),
        execution_interval=release.evaluation_interval,
        numbered_release=dict(approved_code_commit='a'*40,approved_code_fingerprint='b'*64,
            contract=release.canonical_payload(),approved_digest=release.approved_digest,
            inherited_policy={'version':1,'cash_fraction':'1/3'})),
        strategy_profile=dict(profile_id='parent',revision=42),run_plan=dict(profile_id='parent'),accounts=dict(currency='USD'))
    parent=cert(payload)
    own_release=replace(release,number=7001,executor_revision=7001,
        rule_set_contracts=(*release.rule_set_contracts,native.ENTRY_RULE),
        input_contracts=(*release.input_contracts,DECLARED_FIXED_ADAPTER,native.SOURCE_INPUT),approved_digest='')
    own_release=replace(own_release,approved_digest=own_release.digest())
    from src.trading_runtime.fixed_structural_lot_release import derive_fixed_structural_lot_release
    own_payload=derive_fixed_structural_lot_release(parent,parent_release=release,release=own_release,
        policy=FixedStructuralLotPolicy().payload(),approved_code_commit='a'*40,
        approved_code_fingerprint='b'*64,approval_reference='synthetic noninstalled fixture')['payload']
    return parent,cert(own_payload),own_release,release


def test_closed_tree_derivation_preserves_all_inherited_economics():
    parent,own,release,parent_release=declarations()
    assert native.verify_installed_configuration(parent,own,release,parent_release)==FixedStructuralLotPolicy()

@pytest.mark.parametrize('change',['cost','capital','account','tick','parent','parent_source','policy','newfield','missingnode','hash','rule','input'])
def test_resealed_full_own_tree_mutations_cannot_change_inheritance(change):
    parent,own,release,parent_release=declarations();payload=deepcopy(own.payload)
    params=payload['strategy']['parameters']
    if change=='cost':params['costs']['minimum_per_order']=0.
    elif change=='capital':params['capital']['fraction']='1'
    elif change=='account':payload['accounts']['currency']='CAD'
    elif change=='tick':params['execution']['tick_size']=.02
    elif change=='parent':params['fixed_structural_lot_parent']['revision_id']='foreign'
    elif change=='parent_source':params['fixed_structural_lot_parent']['approved_code_commit']='f'*40
    elif change=='policy':params['fixed_structural_lot_policy']['count']=True
    elif change=='newfield':payload['future_interval']={'boundary_ms':1}
    elif change=='missingnode':payload.pop('run_plan')
    elif change=='rule':release=replace(release,rule_set_contracts=(*release.rule_set_contracts,'foreign'),approved_digest='');release=replace(release,approved_digest=release.digest())
    elif change=='input':release=replace(release,input_contracts=parent_release.input_contracts,approved_digest='');release=replace(release,approved_digest=release.digest())
    own=cert(payload)
    if change=='hash':own=replace(own,node_hash='f'*64)
    with pytest.raises(ValueError):native.verify_installed_configuration(parent,own,release,parent_release)


def test_resealed_installed_component_is_not_factory_issued(monkeypatch):
    from src.backend.backtest_fixed_structural_lot_source import derive_fixed_structural_lot_configuration
    source,proposal,_=prepared(monkeypatch)
    own=deepcopy(source.parent_payload);own['strategy'].update(strategy_number=7001,revision=7001)
    selected=derive_fixed_structural_lot_configuration(source.parent_payload,source.policy,installed_configuration=own)
    selected_json=canonical_json(selected)
    forged=replace(source,installed_json=canonical_json(own),selected_json=selected_json,
        selected_configuration_hash=sha256(selected_json.encode()).hexdigest())
    operation=native.NativeFixedStructuralLotOperation(forged)
    with pytest.raises(ValueError,match='not issued'):operation.request(proposal)


def test_unpublished_native_identity_fails_before_database_access():
    class NoDatabase:
        def execute(self,sql):raise AssertionError('unpublished identity queried database')
    with pytest.raises(ValueError):native.prepare_native_fixed_structural_lot_operation(NoDatabase(),
        number=7001,run_id=str(uuid4()),session_date=None,market=None,seeds=None,price_authority=None)


@pytest.mark.parametrize('section,key',[('strategy','profile_id'),('strategy','profile_revision'),
    ('strategy','strategy_number'),('strategy','revision'),('strategy_profile','profile_id'),
    ('strategy_profile','revision'),('strategy_profile','definition_revision'),('run_plan','profile_id')])
def test_resealed_masked_execution_identity_is_not_inheritance_exemption(section,key):
    parent,own,release,parent_release=declarations();payload=deepcopy(own.payload)
    payload[section][key]='foreign' if 'id' in key else True
    with pytest.raises(ValueError):native.verify_installed_configuration(parent,cert(payload),release,parent_release)

@pytest.mark.parametrize('field',['approved_code_commit','approved_code_fingerprint'])
def test_foreign_approved_source_is_rejected_by_actual_current_source_gate(field):
    parent,own,release,parent_release=declarations();payload=deepcopy(own.payload)
    payload['strategy']['numbered_release'][field]='f'*(40 if field.endswith('commit') else 64)
    # Syntax-correct metadata alone cannot certify this dirty/unapproved source.
    with pytest.raises(ValueError,match='exact clean'):
        native.verify_current_installed_source(cert(payload))


@pytest.mark.parametrize('mutation',[None,'manifest_extra','inherited_manifest','manifest_hash','display_identity'])
def test_installed_loader_rederives_whole_compiler_tree_before_source_proof(monkeypatch,mutation):
    from src.backend import backtest_fixed_v4_certification as certification
    parent,own,release,parent_release=declarations()
    payload=deepcopy(own.payload)
    manifest=payload['strategy']['numbered_release']
    if mutation=='manifest_extra':manifest['caller_approval']=True
    elif mutation=='inherited_manifest':manifest['inherited_policy']['cash_fraction']='1'
    elif mutation=='manifest_hash':manifest['manifest_hash']='f'*64
    elif mutation=='display_identity':payload['strategy_profile']['name']='caller name'
    own=cert(payload)
    calls=[]
    monkeypatch.setattr(native,'numbered_strategy_parent',lambda number:parent.strategy_number)
    monkeypatch.setattr(native,'numbered_strategy',lambda number:release if number==release.number else parent_release)
    monkeypatch.setattr(native,'certify_numbered_configuration',lambda client,number:own)
    from types import SimpleNamespace
    from src.trading_runtime.fixed_structural_lot_contract import FixedStructuralLotStrategyContract
    from src.trading_runtime.strategy_forty_two_release import INHERITED_POLICIES,HALF_RISK_LIQUIDITY_POLICY
    registered=FixedStructuralLotStrategyContract(release.number,release.executor_strategy_id,
        release.evaluation_interval,release,canonical_json({**INHERITED_POLICIES,
            'half_risk_liquidity_policy':HALF_RISK_LIQUIDITY_POLICY}),FixedStructuralLotPolicy())
    monkeypatch.setattr(native,'fixed_strategy_executor',lambda *args:SimpleNamespace(contract_factory=lambda:registered))
    monkeypatch.setattr(certification,'certify_numbered_fixed_v4_projection',lambda number:calls.append('proof') or 'e'*64)
    monkeypatch.setattr(native,'verify_current_installed_source',lambda value:calls.append('current_source'))
    if mutation is not None:
        with pytest.raises(ValueError,match='complete parent derivation'):
            native.load_installed_configuration(object(),number=release.number,parent=parent)
        assert calls==[]
    else:
        assert native.load_installed_configuration(object(),number=release.number,parent=parent)==(own,FixedStructuralLotPolicy(),'e'*64)
        assert calls==['proof','current_source']
    # Controlled loader transports above test ordering, not installed admission.
    assert not native._INSTALLED_SOURCES


def test_selected_stop_intent_preserves_original_target_and_default_confirmation_rejects_crossing():
    from datetime import date
    from src.trading_runtime.strategy_one_position import ProtectionState,ProtectionTransition,confirm_protection_transition
    from src.trading_runtime.strategy_one_protection_intent import strategy_one_protection_intents
    from test_strategy_one_protection_intent import financial
    previous=ProtectionState(30000,9.6,10.3)
    transition=ProtectionTransition(ProtectionState(31000,10.33,10.3),
        stop_amendment={'price':10.33,'source':'completed_30s_bar_low'})
    kwargs=dict(session_date=date(2026,8,18),bid=10.38,ask=10.39,strategy_number=42)
    default_commands=strategy_one_protection_intents(previous,transition,financial(),**kwargs)
    with pytest.raises(RuntimeError,match='cross working target'):
        confirm_protection_transition(previous,transition,target_confirmed=False,stop_confirmed=True)
    commands=strategy_one_protection_intents(previous,transition,financial(),stop_ceiling=10.4,**kwargs)
    assert len(commands)==1 and commands[0].action=='replace_protective_stop'
    assert commands[0].invalidation_price==10.33 and commands[0].profit_target_price is None
    assert commands==default_commands
    assert transition.state.target==previous.target==10.3
    with pytest.raises(ValueError,match='cannot amend target'):
        strategy_one_protection_intents(previous,replace(transition,target_amendment={'price':11.}),
            financial(),stop_ceiling=10.4,**kwargs)
    with pytest.raises(ValueError,match='exceeds remaining'):
        strategy_one_protection_intents(previous,transition,financial(),stop_ceiling=10.32,**kwargs)
