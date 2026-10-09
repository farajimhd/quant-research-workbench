"""Complete immutable preparation; never native admission or financial proof."""
from copy import deepcopy
from dataclasses import replace

import pytest

from tests.test_fixed_structural_lot_native import declarations
from src.trading_runtime.fixed_structural_lot_release_v17 import (
    derive_fixed_structural_lot_release, verify_prepared_fixed_structural_lot_release,
)
from src.trading_runtime.strategy_ninety_six_release import (
    release_contract, VALIDATION_REUSE_POLICY, PROJECTION_REUSE_POLICY,
    OWNED_SNAPSHOT_POLICY, EMPTY_CONFIRMATION_POLICY,
)
from src.trading_runtime.strategy_ninety_six_contract import strategy_ninety_six_contract
from src.trading_runtime.strategy_ninety_five_release import release_contract as prior_release
from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
from src.trading_runtime.empty_protection_confirmation_policy import RULE
from src.trading_runtime.fixed_structural_lot_reuse_contract import require_declared_fixed_structural_lot_contract


def prepared():
    parent, _, _, parent_release = declarations()
    kwargs = dict(parent_release=parent_release, release=release_contract(),
        policy=FixedStructuralLotPolicy().payload(), reuse_policy=VALIDATION_REUSE_POLICY,
        projection_reuse_policy=PROJECTION_REUSE_POLICY, owned_snapshot_policy=OWNED_SNAPSHOT_POLICY,
        empty_confirmation_policy=EMPTY_CONFIRMATION_POLICY,
        approved_code_commit='a' * 40, approved_code_fingerprint='b' * 64,
        approval_reference='Prepared only; no source or financial approval')
    return parent, kwargs, derive_fixed_structural_lot_release(parent, **kwargs)


def test_complete_derivation_preserves_all_parent_economic_parameters():
    from src.trading_runtime.fixed_structural_lot_release_v16 import derive_fixed_structural_lot_release as old_derive
    parent, kwargs, actual = prepared()
    prior_args = {k: v for k, v in kwargs.items() if k != 'empty_confirmation_policy'}
    prior_args['release'] = prior_release()
    previous = old_derive(parent, **prior_args)
    parameters = dict(actual['payload']['strategy']['parameters'])
    assert parameters.pop('empty_protection_confirmation_policy') == EMPTY_CONFIRMATION_POLICY.payload()
    assert parameters == previous['payload']['strategy']['parameters']
    assert actual['payload']['accounts'] == previous['payload']['accounts']
    assert verify_prepared_fixed_structural_lot_release(parent, actual['payload'],
        **{k: v for k, v in kwargs.items() if k not in ('policy', 'approved_code_commit',
            'approved_code_fingerprint', 'approval_reference')}) == actual
    factory = strategy_ninety_six_contract()
    assert require_declared_fixed_structural_lot_contract(factory, factory.release) is factory


def test_unpaired_release_and_missing_typed_policy_fail_closed():
    parent, kwargs, _ = prepared()
    draft = replace(kwargs['release'], rule_set_contracts=tuple(
        r for r in kwargs['release'].rule_set_contracts if r != RULE), approved_digest='')
    with pytest.raises(ValueError):
        derive_fixed_structural_lot_release(parent, **{**kwargs,
            'release': replace(draft, approved_digest=draft.digest())})
    with pytest.raises(ValueError):
        derive_fixed_structural_lot_release(parent, **{**kwargs, 'empty_confirmation_policy': None})


def test_complete_tree_reconstruction_rejects_unrelated_economic_change():
    parent, kwargs, actual = prepared()
    payload = deepcopy(actual['payload'])
    payload['strategy']['parameters']['unreviewed_position_scale'] = 100
    with pytest.raises(ValueError, match='complete parent derivation'):
        verify_prepared_fixed_structural_lot_release(parent, payload,
            **{k: v for k, v in kwargs.items() if k not in ('policy', 'approved_code_commit',
                'approved_code_fingerprint', 'approval_reference')})
