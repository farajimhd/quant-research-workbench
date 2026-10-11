from copy import deepcopy
from types import SimpleNamespace

import pytest

from research.causal_strategy_features.v7.ranking import ParticipationRankingPolicy
from src.trading_runtime.participation_acquisition_policy import (
    RULE, SOURCE_CONTRACT, POLICY_KEY, ParticipationAcquisitionPolicy,
    parse_participation_acquisition_policy,
)


def declaration():
    policy = ParticipationAcquisitionPolicy(
        ParticipationRankingPolicy(((30000, 1.), (60000, 1.)), .5, .5, 1., 2, 1000),
        'eligible_unranked_last')
    release = SimpleNamespace(rule_set_contracts=(RULE,), input_contracts=(SOURCE_CONTRACT,))
    return release, {POLICY_KEY: policy.payload()}, policy


def test_exact_declaration_round_trip():
    release, payload, policy = declaration()
    assert parse_participation_acquisition_policy(release, payload) == policy


def test_undeclared_strategy_has_no_ranking():
    release = SimpleNamespace(rule_set_contracts=(), input_contracts=())
    assert parse_participation_acquisition_policy(release, {}) is None


@pytest.mark.parametrize('rules,sources,supplied', [
    ((), (SOURCE_CONTRACT,), True), ((RULE,), (), True),
    ((RULE,), (SOURCE_CONTRACT,), False), ((RULE, RULE), (SOURCE_CONTRACT,), True),
    ((RULE,), (SOURCE_CONTRACT, SOURCE_CONTRACT), True)])
def test_incomplete_or_duplicate_release_binding_fails(rules, sources, supplied):
    _, payload, _ = declaration()
    with pytest.raises(ValueError, match='one rule, source and policy'):
        parse_participation_acquisition_policy(
            SimpleNamespace(rule_set_contracts=rules, input_contracts=sources),
            payload if supplied else {})


@pytest.mark.parametrize('key,value', [
    ('missing_score', 'drop'), ('admission', 'score-only'),
    ('cash', 'pending-exit-proceeds'), ('dispatch', 'before-broker'),
    ('ordering', 'ticker-first'), ('max_rows', True), ('volume_weight', 1),
    ('wick_penalty', float('nan')), ('minimum_known_resolutions', 0)])
def test_semantic_or_typed_mutation_fails(key, value):
    release, payload, _ = declaration()
    payload = deepcopy(payload)
    payload[POLICY_KEY][key] = value
    with pytest.raises(ValueError):
        parse_participation_acquisition_policy(release, payload)
