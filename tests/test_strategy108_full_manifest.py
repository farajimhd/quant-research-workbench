"""Full immutable manifest path; controlled parent certificate fixture only."""
from copy import deepcopy
import pytest
from tests.test_fixed_structural_lot_native import declarations
from src.trading_runtime.strategy_one_hundred_eight_release import derive_strategy_one_hundred_eight_configuration,verify_prepared_strategy_one_hundred_eight_configuration
from src.trading_runtime.strategy_one_hundred_seven_release import derive_strategy_one_hundred_seven_configuration
from src.trading_runtime.strategy_registry import initialize_numbered_fixed_strategies

@pytest.fixture
def manifest():
    initialize_numbered_fixed_strategies()
    parent,*_=declarations()
    from tests.test_fixed_structural_lot_native import cert
    from src.trading_runtime import strategy_forty_two_release as source
    from src.trading_runtime.journal_contract import canonical_json
    from hashlib import sha256
    payload=deepcopy(parent.payload);release=source.release_contract()
    metadata=dict(contract=release.canonical_payload(),approved_digest=release.approved_digest,
        approved_code_commit='a'*40,approved_code_fingerprint='b'*64,
        approval_reference='controlled certified parent transport',publication_mode='backtest_only',
        source_revision_id=source.PARENT_REVISION_ID,source_payload_hash=source.PARENT_PAYLOAD_HASH,
        **deepcopy(source.INHERITED_POLICIES),half_risk_liquidity_policy=deepcopy(source.HALF_RISK_LIQUIDITY_POLICY))
    metadata['manifest_hash']=sha256(canonical_json(metadata).encode()).hexdigest()
    payload['strategy']['numbered_release']=metadata
    parent=cert(payload)
    approval=dict(approved_code_commit='a'*40,approved_code_fingerprint='b'*64,approval_reference='controlled full-manifest fixture')
    return parent,approval,derive_strategy_one_hundred_eight_configuration(parent,**approval)

def test_full108_derivation_and_installed_verification_roundtrip(manifest):
    parent,_,derived=manifest
    assert verify_prepared_strategy_one_hundred_eight_configuration(parent,derived['payload'])==derived

def test_full108_manifest_preserves_every_prior_economic_parameter(manifest):
    parent,approval,derived=manifest
    old=derive_strategy_one_hundred_seven_configuration(parent,**approval)['payload']
    params=deepcopy(derived['payload']['strategy']['parameters'])
    assert params.pop('initial_held_recovery_reuse_policy')==dict(max_contexts=32,max_inventory_entries=8,max_inventory_rows=100000,max_inventory_bytes=67108864)
    assert params.pop('proposal_decision_inventory_reuse_policy')==dict(max_contexts=32,max_inventory_entries=8,max_inventory_rows=100000,max_inventory_bytes=67108864)
    assert params==old['strategy']['parameters']
    assert derived['payload']['accounts']==old['accounts']

@pytest.mark.parametrize('change',('proposal_policy','initial_policy','inherited_account'))
def test_full108_manifest_rejects_resealed_policy_or_inherited_economic_change(manifest,change):
    parent,_,derived=manifest;payload=deepcopy(derived['payload'])
    if change=='inherited_account':payload['accounts']['currency']='CAD'
    else:payload['strategy']['parameters']['proposal_decision_inventory_reuse_policy' if change=='proposal_policy' else 'initial_held_recovery_reuse_policy']['max_contexts']=33
    with pytest.raises(ValueError):verify_prepared_strategy_one_hundred_eight_configuration(parent,payload)
