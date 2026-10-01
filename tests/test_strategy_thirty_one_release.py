import pytest
from src.trading_runtime.strategy_thirty_one_release import (
    PARENT_REVISION_ID, PARENT_PAYLOAD_HASH, PROFIT_PROTECTION_POLICY,
    release_contract, verify_installed_strategy_thirty_one_release,
)
from src.trading_runtime.strategy_thirty_release import release_contract as parent_release
from src.trading_runtime.strategy_profit_giveback import POLICY_ID


def test_sealed_declaration_preserves_parent_inputs_and_rules():
    release = release_contract();parent = parent_release()
    release.verify()
    assert release.number == release.executor_revision == 31
    assert release.evaluation_interval == parent.evaluation_interval == '100ms'
    assert release.input_contracts == parent.input_contracts
    assert release.rule_set_contracts == (*parent.rule_set_contracts, POLICY_ID)
    assert release.approved_digest == release_contract().approved_digest


def test_policy_pins_parent_and_durable_arming_boundary():
    assert PROFIT_PROTECTION_POLICY['parent_release_revision'] == PARENT_REVISION_ID
    assert PROFIT_PROTECTION_POLICY['parent_release_payload'] == PARENT_PAYLOAD_HASH
    assert PROFIT_PROTECTION_POLICY['current_bucket_arming'] is False
    assert PROFIT_PROTECTION_POLICY['later_high_reference'] == 'frozen_prior_arming_checkpoint_high'


def test_installed_release_requires_exact_sealed_manifest():
    manifest = {
            'contract':release_contract().canonical_payload(),
            'approved_digest':release_contract().approved_digest,
            'profit_protection_policy':PROFIT_PROTECTION_POLICY,
            'source_revision_id':PARENT_REVISION_ID,'source_payload_hash':PARENT_PAYLOAD_HASH}
    assert verify_installed_strategy_thirty_one_release(manifest) == release_contract()
    for field in manifest:
        with pytest.raises(ValueError):
            verify_installed_strategy_thirty_one_release({**manifest, field: None})


def test_installed_capabilities_inherit_parent_and_numbered_session_identity():
    from src.trading_runtime.strategy_registry import fixed_strategy_executor, numbered_strategy_parent
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy, numbered_session_exit_reason
    release = release_contract()
    executor = fixed_strategy_executor(release.executor_strategy_id, 31)
    contract, parent = executor.contract_factory(), numbered_fixed_strategy(30)
    assert contract.strategy_number == 31 and numbered_strategy_parent(31) == 30
    for name in ('allows_session_exit', 'allows_adds', 'allows_completed_30s_trailing',
                 'allows_target_escalation', 'caps_entry_at_reference_ask',
                 'allows_followthrough_failure_exit'):
        assert getattr(contract, name) == getattr(parent, name)
    for boundary in (0, 100, 19500000, 19740000, 19800000, 43200000, 43200100,
                     57000000, 57300000, 57600000):
        for name in ('entry_allowed', 'acquisition_cutoff', 'liquidation_due'):
            assert getattr(contract, name)(boundary) == getattr(parent, name)(boundary)
        assert contract.activation_allowed(boundary, boundary) == parent.activation_allowed(boundary, boundary)
    assert numbered_session_exit_reason(31) == 'strategy_thirty_one_session_exit'


def test_registration_does_not_substitute_for_complete_execution_certification():
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    with pytest.raises(ValueError, match='complete profit-route source certification'):
        certify_numbered_fixed_v4_projection(31)
