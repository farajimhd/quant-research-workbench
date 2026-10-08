"""Complete prepared declarations; synthetic fixtures confer no admission."""
from copy import deepcopy

import pytest

from tests.test_fixed_structural_lot_native import cert, declarations
from src.trading_runtime.fixed_structural_lot_release_v14 import (
    derive_fixed_structural_lot_release, verify_prepared_fixed_structural_lot_release,
)
from src.trading_runtime.strategy_ninety_three_release import release_contract, VALIDATION_REUSE_POLICY
from src.trading_runtime.strategy_one_configuration_tree import decode_nodes
from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.strategy_registry import numbered_strategy


def prepared():
    parent, _, _, parent_release = declarations()
    kwargs = dict(parent_release=parent_release, release=release_contract(),
        policy=FixedStructuralLotPolicy().payload(), reuse_policy=VALIDATION_REUSE_POLICY,
        approved_code_commit='a' * 40, approved_code_fingerprint='b' * 64,
        approval_reference='Synthetic prepared-only fixture; no native approval')
    return parent, kwargs, derive_fixed_structural_lot_release(parent, **kwargs)


def test_prepared_reuse_retains_complete_parent_economics_and_normalized_nodes():
    parent, kwargs, result = prepared()
    own = result['payload']
    assert decode_nodes(result['nodes']) == own
    assert own['strategy']['parameters']['packet_validation_reuse_policy'] == VALIDATION_REUSE_POLICY.payload()
    assert own['strategy']['parameters']['fixed_structural_lot_policy'] == FixedStructuralLotPolicy().payload()
    for field in ('execution', 'capital', 'costs'):
        assert own['strategy']['parameters'][field] == parent.payload['strategy']['parameters'][field]
    assert own['accounts'] == parent.payload['accounts']
    assert own['strategy']['numbered_release']['source_payload_hash'] == parent.payload_hash
    assert verify_prepared_fixed_structural_lot_release(parent, own,
        parent_release=kwargs['parent_release'], release=kwargs['release'],
        reuse_policy=VALIDATION_REUSE_POLICY) == result
    from src.backend.backtest_fixed_structural_lot_native_v14 import verify_installed_configuration
    assert verify_installed_configuration(parent, cert(own), kwargs['release'],
        kwargs['parent_release']) == (FixedStructuralLotPolicy(), VALIDATION_REUSE_POLICY)


@pytest.mark.parametrize('change', ['capital', 'cost', 'account', 'bounds', 'valid_bounds', 'scope',
                                  'unknown_parameter', 'parent', 'source', 'identity'])
def test_resealed_semantic_changes_fail_complete_rederivation(change):
    parent, kwargs, result = prepared()
    own = deepcopy(result['payload'])
    params = own['strategy']['parameters']
    if change == 'capital': params['capital']['fraction'] = '1'
    elif change == 'cost': params['costs']['minimum_per_order'] = 0.0
    elif change == 'account': own['accounts']['currency'] = 'CAD'
    elif change == 'bounds': params['packet_validation_reuse_policy']['max_entries'] = True
    elif change == 'valid_bounds': params['packet_validation_reuse_policy']['max_entries'] = 4
    elif change == 'scope': params['packet_validation_reuse_policy']['scope'] = 'source admission'
    elif change == 'unknown_parameter': params['future_threshold'] = 0.0
    elif change == 'parent': params['fixed_structural_lot_parent']['payload_hash'] = 'f' * 64
    elif change == 'source': own['strategy']['numbered_release']['source_payload_hash'] = 'f' * 64
    elif change == 'identity': own['strategy_profile']['revision'] = 92
    with pytest.raises(ValueError):
        verify_prepared_fixed_structural_lot_release(parent, own,
            parent_release=kwargs['parent_release'], release=kwargs['release'],
            reuse_policy=VALIDATION_REUSE_POLICY)


def test_preparation_does_not_register_number_or_mutate_parent():
    parent, kwargs, _ = prepared()
    before = canonical_json(parent.payload)
    registered = numbered_strategy(93)
    derive_fixed_structural_lot_release(parent, **kwargs)
    assert canonical_json(parent.payload) == before
    assert numbered_strategy(93) == registered == release_contract()


def test_typed_factory_requires_paired_reuse_without_changing_parent_policies():
    from dataclasses import replace
    from src.trading_runtime.strategy_ninety_three_contract import strategy_ninety_three_contract
    from src.trading_runtime.strategy_ninety_two_contract import strategy_ninety_two_contract
    own, parent_lots = strategy_ninety_three_contract(), strategy_ninety_two_contract()
    assert own.validation_reuse_policy == VALIDATION_REUSE_POLICY
    assert own.policy_json == parent_lots.policy_json
    assert own.fixed_structural_lot_policy == parent_lots.fixed_structural_lot_policy
    with pytest.raises(ValueError, match='Explicit typed'):
        replace(own, validation_reuse_policy=None)


def test_unregistered_number_cannot_query_or_issue_native_source():
    from src.backend.backtest_fixed_structural_lot_native_v14 import load_installed_configuration
    parent, _, _ = prepared()
    class NoDatabase:
        def execute(self, sql):
            raise AssertionError('Unregistered successor queried a database')
    with pytest.raises(ValueError):
        load_installed_configuration(NoDatabase(), number=95, parent=parent)


def test_registered_successor_uses_exact_declared_factory_and_parent():
    from src.trading_runtime.strategy_registry import (
        numbered_strategy_parent, fixed_strategy_executor,
    )
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    from src.trading_runtime.fixed_structural_lot_reuse_contract import FixedStructuralLotReuseStrategyContract
    release = numbered_strategy(93)
    assert numbered_strategy_parent(93) == 42
    registered = fixed_strategy_executor(release.executor_strategy_id,
                                        release.executor_revision).contract_factory()
    assert type(registered) is FixedStructuralLotReuseStrategyContract
    assert numbered_fixed_strategy(93) == registered
    assert registered.validation_reuse_policy == VALIDATION_REUSE_POLICY
