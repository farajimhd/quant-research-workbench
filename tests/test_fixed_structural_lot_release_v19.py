"""Complete compiler-tree checks; synthetic parent grants no source admission."""
from copy import deepcopy

import pytest

from tests.test_fixed_structural_lot_native import declarations, cert
from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
from src.trading_runtime.fixed_structural_lot_release_v19 import (
    derive_fixed_structural_lot_release, verify_prepared_fixed_structural_lot_release,
)
from src.trading_runtime.strategy_ninety_nine_release import _compiler_arguments, SELECTED_EXIT_POLICY
from src.trading_runtime.strategy_one_configuration_tree import decode_nodes


def prepared():
    parent, _, _, _ = declarations()
    arguments = _compiler_arguments()
    result = derive_fixed_structural_lot_release(parent, **arguments,
        policy=FixedStructuralLotPolicy().payload(), approved_code_commit='a' * 40,
        approved_code_fingerprint='b' * 64, approval_reference='Synthetic compiler-only check')
    return parent, arguments, result


def test_complete_successor_tree_roundtrip_preserves_parent_economics():
    parent, arguments, result = prepared()
    own = result['payload']
    assert decode_nodes(result['nodes']) == own
    assert own['strategy']['parameters']['selected_exit_publication_policy'] == SELECTED_EXIT_POLICY.payload()
    for name in ('execution', 'capital', 'costs'):
        assert own['strategy']['parameters'][name] == parent.payload['strategy']['parameters'][name]
    assert own['accounts'] == parent.payload['accounts']
    assert verify_prepared_fixed_structural_lot_release(parent, own, **arguments) == result


@pytest.mark.parametrize('field', ('costs', 'capital', 'exit_policy', 'missing_exit_policy', 'transport', 'source'))
def test_modified_tree_is_not_accepted_by_reconstruction(field):
    parent, arguments, result = prepared()
    own = deepcopy(result['payload'])
    params = own['strategy']['parameters']
    if field == 'costs': params['costs']['per_share'] = 0
    elif field == 'capital': params['capital']['fraction'] = '1'
    elif field == 'exit_policy': params['selected_exit_publication_policy']['missing'] = 'infer source'
    elif field == 'missing_exit_policy': del params['selected_exit_publication_policy']
    elif field == 'transport': params['complete_market_window_policy']['schema_version'] = 2
    else: own['strategy']['numbered_release']['source_payload_hash'] = 'f' * 64
    with pytest.raises(ValueError):
        verify_prepared_fixed_structural_lot_release(parent, own, **arguments)


def test_v19_native_semantic_verifier_retains_complete_parent_binding():
    from src.backend.backtest_fixed_structural_lot_native_v19 import verify_installed_configuration
    parent, arguments, result = prepared()
    policies = verify_installed_configuration(parent, cert(result['payload']),
        arguments['release'], arguments['parent_release'])
    assert len(policies) == 7 and policies[-1] == SELECTED_EXIT_POLICY
    assert policies[0] == FixedStructuralLotPolicy()


@pytest.mark.parametrize('field', ('cost', 'policy', 'parent', 'identity'))
def test_v19_native_semantic_verifier_rejects_resealed_foreign_tree(field):
    from src.backend.backtest_fixed_structural_lot_native_v19 import verify_installed_configuration
    parent, arguments, result = prepared()
    own = deepcopy(result['payload'])
    params = own['strategy']['parameters']
    if field == 'cost': params['costs']['per_share'] = 0
    elif field == 'policy': params['selected_exit_publication_policy']['missing'] = 'infer'
    elif field == 'parent': params['fixed_structural_lot_parent']['payload_hash'] = 'f' * 64
    else: own['strategy']['revision'] = 98
    with pytest.raises(ValueError):
        verify_installed_configuration(parent, cert(own),
            arguments['release'], arguments['parent_release'])
