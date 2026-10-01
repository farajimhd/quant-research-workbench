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


def test_prepared_release_has_no_installed_executor_admission():
    with pytest.raises(ValueError):
        verify_installed_strategy_thirty_one_release({
            'contract':release_contract().canonical_payload(),
            'approved_digest':release_contract().approved_digest,
            'profit_protection_policy':PROFIT_PROTECTION_POLICY,
            'source_revision_id':PARENT_REVISION_ID,'source_payload_hash':PARENT_PAYLOAD_HASH})
