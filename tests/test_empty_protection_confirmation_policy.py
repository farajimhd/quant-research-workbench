"""Selection and fail-closed recovery boundaries, not runtime admission."""
import pytest

from tests.test_packet_validation_reuse_policy import release
from src.trading_runtime.empty_protection_confirmation_policy import (
    INPUT, RULE, EmptyProtectionConfirmationPolicy,
    parse_empty_protection_confirmation_policy,
    declared_empty_protection_confirmation_policy, requires_protection_ack_history,
)


def test_paired_complete_declaration_and_unselected_history_preservation():
    policy = EmptyProtectionConfirmationPolicy(1)
    assert declared_empty_protection_confirmation_policy(release((INPUT,), (RULE,)), policy.payload()) == policy
    assert declared_empty_protection_confirmation_policy(release(), None) is None
    assert requires_protection_ack_history(None, command_count=0, recovery_pending=False)
    assert not requires_protection_ack_history(policy, command_count=0, recovery_pending=False)
    assert requires_protection_ack_history(policy, command_count=0, recovery_pending=True)
    assert requires_protection_ack_history(policy, command_count=1, recovery_pending=False)


@pytest.mark.parametrize('inputs,rules', [((INPUT,), ()), ((), (RULE,)), ((INPUT, INPUT), (RULE,))])
def test_incomplete_selection_cannot_enable_elision(inputs, rules):
    with pytest.raises(ValueError):
        declared_empty_protection_confirmation_policy(release(inputs, rules),
            EmptyProtectionConfirmationPolicy(1).payload())


@pytest.mark.parametrize('change', [{'schema_version': True}, {'schema_version': 1.0},
    {'preserved': 'skip prefix validation'}, {'eligibility': 'no commands or unknown recovery'},
    {'omitted': 'all protection verification'}, {'extra': True}])
def test_semantic_or_numeric_aliases_cannot_weaken_confirmation(change):
    with pytest.raises(ValueError):
        parse_empty_protection_confirmation_policy({**EmptyProtectionConfirmationPolicy(1).payload(), **change})


def test_claim_without_release_selection_or_missing_schema_fails():
    value = EmptyProtectionConfirmationPolicy(1).payload()
    with pytest.raises(ValueError):
        declared_empty_protection_confirmation_policy(release(), value)
    with pytest.raises(ValueError):
        parse_empty_protection_confirmation_policy({k: v for k, v in value.items() if k != 'schema_version'})
    with pytest.raises(ValueError):
        requires_protection_ack_history(EmptyProtectionConfirmationPolicy(1),
            command_count=0, recovery_pending=0)
