from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.trading_runtime.selected_exit_publication_policy import (
    INPUT, RULE, SelectedExitPublicationPolicy,
    declared_selected_exit_publication_policy,
    parse_selected_exit_publication_policy,
    installed_selected_exit_publication_policy,
)
from src.trading_runtime.strategy_ninety_eight_release import release_contract


def sealed(inputs, rules):
    original = release_contract()
    draft = replace(original, number=99, executor_revision=99,
                    input_contracts=inputs, rule_set_contracts=rules,
                    approved_digest='')
    return replace(draft, approved_digest=draft.digest())


def test_existing_release_is_unselected_and_cannot_borrow_declaration():
    original = release_contract()
    assert declared_selected_exit_publication_policy(original, None) is None
    with pytest.raises(ValueError, match='paired'):
        declared_selected_exit_publication_policy(
            original, SelectedExitPublicationPolicy(1).payload())


def test_exact_paired_sealed_declaration_and_missing_capabilities():
    original = release_contract()
    policy = SelectedExitPublicationPolicy(1)
    release = sealed((*original.input_contracts, INPUT),
                     (*original.rule_set_contracts, RULE))
    assert declared_selected_exit_publication_policy(release, policy.payload()) == policy
    for inputs, rules in (
        (original.input_contracts, (*original.rule_set_contracts, RULE)),
        ((*original.input_contracts, INPUT), original.rule_set_contracts),
    ):
        with pytest.raises(ValueError, match='paired'):
            declared_selected_exit_publication_policy(sealed(inputs, rules), policy.payload())
    with pytest.raises(ValueError, match='approved seal'):
        declared_selected_exit_publication_policy(
            sealed((*original.input_contracts, INPUT, INPUT),
                   (*original.rule_set_contracts, RULE)), policy.payload())
    with pytest.raises(ValueError):
        declared_selected_exit_publication_policy(
            replace(release, approved_digest='0' * 64), policy.payload())


@pytest.mark.parametrize('change', (
    {'schema_version': True}, {'schema_version': 2},
    {'missing': 'infer missing source'}, {'verification': 'skip ancestry'},
    {'extra': 'undeclared'},
))
def test_weakened_or_noncanonical_policy_is_rejected(change):
    payload = {**SelectedExitPublicationPolicy(1).payload(), **change}
    with pytest.raises(ValueError):
        parse_selected_exit_publication_policy(payload)


def test_unselected_installed_source_and_counterfeit_claim():
    source = SimpleNamespace(installed_json='', installed_payload=None)
    assert installed_selected_exit_publication_policy(source) is None
    source.installed_json = 'controlled-unselected-source'
    source.installed_payload = {'strategy': {
        'parameters': {}, 'numbered_release': {'contract': {}}}}
    assert installed_selected_exit_publication_policy(source) is None
    source.installed_payload['strategy']['parameters']['selected_exit_publication_policy'] = (
        SelectedExitPublicationPolicy(1).payload())
    # This is intentionally not an issued native source. No source certificate
    # or installed-owner check is overridden to make the claim pass.
    with pytest.raises((ValueError, TypeError)):
        installed_selected_exit_publication_policy(source)
