"""Prepared immutable declaration checks; no installed executor or publication."""
from copy import deepcopy
from dataclasses import replace

import pytest

from src.trading_runtime import strategy_thirty_three_release as parent
from src.trading_runtime import strategy_thirty_four_release as child


def test_declaration_preserves_parent_inputs_and_policies_and_adds_only_exit_contract():
    previous = parent.release_contract()
    declared = child.release_contract()
    declared.verify()
    assert declared.number == declared.executor_revision == 34
    assert declared.executor_strategy_id == previous.executor_strategy_id
    assert declared.input_contracts == previous.input_contracts
    assert declared.evaluation_interval == previous.evaluation_interval
    assert declared.rule_set_contracts[:-1] == previous.rule_set_contracts
    assert declared.rule_set_contracts[-1] == child.CONFIRMED_AH_FAILURE_POLICY['policy_id']
    assert child.INHERITED_POLICIES == parent.INHERITED_POLICIES
    assert child.PROFIT_PROTECTION_POLICY == parent.PROFIT_PROTECTION_POLICY
    assert child.release_contract() == declared


def test_exact_parent_identity_and_independent_policy_copy():
    assert child.PARENT_REVISION_ID == 'strategy-one-33:66f5cdae-3cbb-4fdd-af99-a72d22e77e6c'
    assert child.PARENT_PAYLOAD_HASH == '1dcf2e4d52de04e710880fc1be221ae68fafb4ed5a441691edd183e8c092815b'
    assert child.INHERITED_POLICIES is not parent.INHERITED_POLICIES
    assert child.PROFIT_PROTECTION_POLICY is not parent.PROFIT_PROTECTION_POLICY
    copied = deepcopy(child.PROFIT_PROTECTION_POLICY)
    copied['cutoff_admission_authority'] = 'foreign'
    assert parent.PROFIT_PROTECTION_POLICY['cutoff_admission_authority'] != 'foreign'


def test_altered_declaration_invalidates_approval_seal():
    with pytest.raises(ValueError):
        replace(child.release_contract(), behavior_specification='changed').verify()
