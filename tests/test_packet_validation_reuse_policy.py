from dataclasses import replace

import pytest

from src.trading_runtime.packet_validation_reuse_policy import (
    INPUT, RULE, PacketValidationReusePolicy, parse_packet_validation_reuse_policy,
    declared_packet_validation_reuse_policy,
)
from src.trading_runtime.strategy_registry import NumberedStrategyRelease


def release(inputs=(), rules=()):
    draft = NumberedStrategyRelease(900001, 'test-declaration', 900001, '100ms',
        ('test-input', *inputs), ('test-rule', *rules),
        'Test-only pure packet reuse declaration.', '')
    return replace(draft, approved_digest=draft.digest())


def test_explicit_policy_round_trip_and_paired_selection():
    policy = PacketValidationReusePolicy(2, 30033, 67108864)
    assert parse_packet_validation_reuse_policy(policy.payload()) == policy
    assert declared_packet_validation_reuse_policy(release((INPUT,), (RULE,)), policy.payload()) == policy
    assert declared_packet_validation_reuse_policy(release(), None) is None


@pytest.mark.parametrize('inputs,rules', [((INPUT,), ()), ((), (RULE,)),
                                        ((INPUT, INPUT), (RULE,))])
def test_incomplete_or_duplicate_selection_fails(inputs, rules):
    with pytest.raises(ValueError):
        declared_packet_validation_reuse_policy(release(inputs, rules),
                                               PacketValidationReusePolicy(2, 10, 100).payload())


@pytest.mark.parametrize('field,value', [('max_entries', True), ('max_rows', 10.0),
                                      ('max_bytes', 0), ('scope', 'cache source admission'),
                                      ('mutation', 'trust object identity'), ('extra', 1)])
def test_numeric_aliases_and_semantic_changes_fail(field, value):
    payload = PacketValidationReusePolicy(2, 10, 100).payload()
    payload[field] = value
    with pytest.raises(ValueError):
        parse_packet_validation_reuse_policy(payload)


def test_missing_bounds_and_unselected_claim_fail():
    payload = PacketValidationReusePolicy(2, 10, 100).payload()
    with pytest.raises(ValueError):
        declared_packet_validation_reuse_policy(release(), payload)
    del payload['max_rows']
    with pytest.raises(ValueError, match='cannot receive defaults'):
        parse_packet_validation_reuse_policy(payload)
