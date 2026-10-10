from dataclasses import replace

import pytest

from src.trading_runtime.strategy_registry import NumberedStrategyRelease
from src.trading_runtime.publication_source_reuse_policy import (
    INPUT, RULE, PublicationSourceReusePolicy, parse_declared_publication_source_reuse,
)


def release(inputs=(), rules=()):
    value = NumberedStrategyRelease(111, 'test-publication-executor', 1, '100ms',
        ('test-source@1', *inputs), ('test-rule@1', *rules), 'Bounded declaration fixture only', '')
    return replace(value, approved_digest=value.digest())


def test_unselected_release_preserves_cold_path():
    assert parse_declared_publication_source_reuse(release(), None) is None
    with pytest.raises(ValueError, match='Undeclared'):
        parse_declared_publication_source_reuse(release(), {'max_image_bytes': 4096})


def test_paired_sealed_release_accepts_exact_policy():
    policy = parse_declared_publication_source_reuse(release((INPUT,), (RULE,)),
        {'max_image_bytes': 4096})
    assert type(policy) is PublicationSourceReusePolicy
    assert policy.payload() == {'max_image_bytes': 4096}


@pytest.mark.parametrize('inputs,rules', [((INPUT,), ()), ((), (RULE,)),
    ((INPUT, INPUT), (RULE,)), ((INPUT,), (RULE, RULE))])
def test_partial_or_duplicate_declarations_reject(inputs, rules):
    expected = 'approved seal' if len(set(inputs)) != len(inputs) or len(set(rules)) != len(rules) else 'paired'
    with pytest.raises(ValueError, match=expected):
        parse_declared_publication_source_reuse(release(inputs, rules), {'max_image_bytes': 4096})


@pytest.mark.parametrize('value', [None, {}, {'max_image_bytes': True},
    {'max_image_bytes': 0}, {'max_image_bytes': 67108865},
    {'max_image_bytes': 4096, 'skip_source_checks': True}])
def test_missing_unbounded_or_extra_policy_rejects(value):
    with pytest.raises(ValueError):
        parse_declared_publication_source_reuse(release((INPUT,), (RULE,)), value)


def test_release_seal_must_match_before_policy_selection():
    value = replace(release((INPUT,), (RULE,)), approved_digest='0' * 64)
    with pytest.raises(ValueError, match='approved seal'):
        parse_declared_publication_source_reuse(value, {'max_image_bytes': 4096})
