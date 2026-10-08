"""Prepared-only declarations; fixtures do not confer source authority."""
from copy import deepcopy
from dataclasses import replace

import pytest

from tests.test_fixed_structural_lot_native import declarations
from src.trading_runtime.fixed_structural_lot_release_v15 import (
    derive_fixed_structural_lot_release, verify_prepared_fixed_structural_lot_release,
)
from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
from src.trading_runtime.projected_configuration_reuse_policy import (
    INPUT, RULE, ProjectedConfigurationReusePolicy,
    parse_projected_configuration_reuse_policy, declared_projected_configuration_reuse_policy,
)
from src.trading_runtime.strategy_ninety_four_release import (
    release_contract, VALIDATION_REUSE_POLICY, PROJECTION_REUSE_POLICY,
)
from src.trading_runtime.strategy_registry import numbered_strategy
from src.trading_runtime.strategy_one_configuration_tree import decode_nodes
from src.trading_runtime.journal_contract import canonical_json


def prepared():
    parent, _, _, parent_release = declarations()
    kwargs = dict(parent_release=parent_release, release=release_contract(),
        policy=FixedStructuralLotPolicy().payload(), reuse_policy=VALIDATION_REUSE_POLICY,
        projection_reuse_policy=PROJECTION_REUSE_POLICY, approved_code_commit='a' * 40,
        approved_code_fingerprint='b' * 64,
        approval_reference='Prepared-only fixture, no native approval')
    return parent, kwargs, derive_fixed_structural_lot_release(parent, **kwargs)


def test_complete_prepared_derivation_retains_parent_economics():
    parent, kwargs, result = prepared()
    own = result['payload']
    assert decode_nodes(result['nodes']) == own
    assert own['accounts'] == parent.payload['accounts']
    for field in ('capital', 'costs', 'execution'):
        assert own['strategy']['parameters'][field] == parent.payload['strategy']['parameters'][field]
    assert own['strategy']['parameters']['projected_configuration_reuse_policy'] == PROJECTION_REUSE_POLICY.payload()
    assert verify_prepared_fixed_structural_lot_release(parent, own,
        parent_release=kwargs['parent_release'], release=kwargs['release'],
        reuse_policy=VALIDATION_REUSE_POLICY, projection_reuse_policy=PROJECTION_REUSE_POLICY) == result


@pytest.mark.parametrize('change', ['cash', 'cost', 'account', 'bounds', 'scope', 'unknown', 'parent'])
def test_full_rederivation_rejects_changes(change):
    parent, kwargs, result = prepared()
    own = deepcopy(result['payload'])
    parameters = own['strategy']['parameters']
    if change == 'cash': parameters['capital']['fraction'] = '1'
    elif change == 'cost': parameters['costs']['per_share'] = 0
    elif change == 'account': own['accounts']['currency'] = 'CAD'
    elif change == 'bounds': parameters['projected_configuration_reuse_policy']['max_entries'] = 4
    elif change == 'scope': parameters['projected_configuration_reuse_policy']['scope'] = 'reuse source admission'
    elif change == 'unknown': parameters['future_threshold'] = 0
    elif change == 'parent': parameters['fixed_structural_lot_parent']['payload_hash'] = 'f' * 64
    with pytest.raises(ValueError):
        verify_prepared_fixed_structural_lot_release(parent, own,
            parent_release=kwargs['parent_release'], release=kwargs['release'],
            reuse_policy=VALIDATION_REUSE_POLICY, projection_reuse_policy=PROJECTION_REUSE_POLICY)


def test_typed_factory_and_preparation_do_not_register_or_mutate_parent():
    from src.trading_runtime.strategy_ninety_four_contract import strategy_ninety_four_contract
    from src.trading_runtime.strategy_ninety_three_contract import strategy_ninety_three_contract
    parent, kwargs, _ = prepared()
    before = canonical_json(parent.payload)
    derive_fixed_structural_lot_release(parent, **kwargs)
    assert canonical_json(parent.payload) == before
    own, prior = strategy_ninety_four_contract(), strategy_ninety_three_contract()
    assert own.projection_reuse_policy == PROJECTION_REUSE_POLICY
    assert own.validation_reuse_policy == prior.validation_reuse_policy
    assert own.policy_json == prior.policy_json
    with pytest.raises(ValueError, match='not published'):
        numbered_strategy(94)


@pytest.mark.parametrize('field,value', [('max_entries', True), ('max_rows', 0),
    ('max_input_bytes', 1.0), ('scope', 'source admission'), ('extra', 1)])
def test_policy_requires_exact_types_and_semantics(field, value):
    value_map = PROJECTION_REUSE_POLICY.payload()
    value_map[field] = value
    with pytest.raises(ValueError):
        parse_projected_configuration_reuse_policy(value_map)


def test_policy_requires_paired_selection_and_all_bounds():
    release = release_contract()
    assert declared_projected_configuration_reuse_policy(release, PROJECTION_REUSE_POLICY.payload()) == PROJECTION_REUSE_POLICY
    prior = replace(release, input_contracts=tuple(x for x in release.input_contracts if x != INPUT),
        rule_set_contracts=tuple(x for x in release.rule_set_contracts if x != RULE), approved_digest='')
    prior = replace(prior, approved_digest=prior.digest())
    assert declared_projected_configuration_reuse_policy(prior, None) is None
    with pytest.raises(ValueError):
        declared_projected_configuration_reuse_policy(prior, PROJECTION_REUSE_POLICY.payload())
    incomplete = replace(release, input_contracts=prior.input_contracts, approved_digest='')
    incomplete = replace(incomplete, approved_digest=incomplete.digest())
    with pytest.raises(ValueError):
        declared_projected_configuration_reuse_policy(incomplete, PROJECTION_REUSE_POLICY.payload())
    missing = PROJECTION_REUSE_POLICY.payload()
    del missing['max_bytes']
    with pytest.raises(ValueError, match='cannot receive defaults'):
        parse_projected_configuration_reuse_policy(missing)
