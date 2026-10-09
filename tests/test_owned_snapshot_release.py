from copy import deepcopy
from dataclasses import replace

import pytest

from tests.test_fixed_structural_lot_native import declarations
from src.trading_runtime.fixed_structural_lot_release_v16 import (
    derive_fixed_structural_lot_release, verify_prepared_fixed_structural_lot_release,
)
from src.trading_runtime.strategy_ninety_five_release import (
    release_contract, VALIDATION_REUSE_POLICY, PROJECTION_REUSE_POLICY, OWNED_SNAPSHOT_POLICY,
)
from src.trading_runtime.owned_scalar_snapshot_policy import (
    RULE, parse_owned_scalar_snapshot_policy, declared_owned_scalar_snapshot_policy,
)
from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
from src.trading_runtime.strategy_ninety_four_release import release_contract as prior_release


def prepared():
    parent, _, _, parent_release = declarations()
    kwargs = dict(parent_release=parent_release, release=release_contract(),
        policy=FixedStructuralLotPolicy().payload(), reuse_policy=VALIDATION_REUSE_POLICY,
        projection_reuse_policy=PROJECTION_REUSE_POLICY, owned_snapshot_policy=OWNED_SNAPSHOT_POLICY,
        approved_code_commit='a' * 40, approved_code_fingerprint='b' * 64,
        approval_reference='Prepared only; no source or financial approval')
    return parent, kwargs, derive_fixed_structural_lot_release(parent, **kwargs)


def test_complete_prepared_owned_snapshot_derivation_preserves_economics():
    parent, kwargs, actual = prepared()
    from src.trading_runtime.fixed_structural_lot_release_v15 import derive_fixed_structural_lot_release as prior_derive
    prior_args = {k: v for k, v in kwargs.items() if k != 'owned_snapshot_policy'}
    prior_args['release'] = prior_release()
    previous = prior_derive(parent, **prior_args)
    parameters = dict(actual['payload']['strategy']['parameters'])
    assert parameters.pop('owned_scalar_snapshot_policy') == OWNED_SNAPSHOT_POLICY.payload()
    assert parameters == previous['payload']['strategy']['parameters']
    assert actual['payload']['accounts'] == previous['payload']['accounts']
    assert verify_prepared_fixed_structural_lot_release(parent, actual['payload'],
        **{k: v for k, v in kwargs.items() if k not in ('policy', 'approved_code_commit',
            'approved_code_fingerprint', 'approval_reference')}) == actual


def test_mismatched_row_bounds_and_unpaired_rule_fail_closed():
    parent, kwargs, _ = prepared()
    kwargs['owned_snapshot_policy'] = replace(OWNED_SNAPSHOT_POLICY, max_rows=1)
    with pytest.raises(ValueError, match='bounds differ'):
        derive_fixed_structural_lot_release(parent, **kwargs)
    release = release_contract()
    with pytest.raises(ValueError):
        declared_owned_scalar_snapshot_policy(replace(release,
            rule_set_contracts=tuple(r for r in release.rule_set_contracts if r != RULE)),
            OWNED_SNAPSHOT_POLICY.payload())
    assert declared_owned_scalar_snapshot_policy(prior_release(), None) is None


def test_prepared_typed_factory_rejects_wrong_ownership_or_bounds():
    from src.trading_runtime.strategy_ninety_five_contract import strategy_ninety_five_contract
    contract = strategy_ninety_five_contract()
    assert contract.release == release_contract()
    assert contract.owned_snapshot_policy == OWNED_SNAPSHOT_POLICY
    with pytest.raises(ValueError, match='typed owned'):
        replace(contract, owned_snapshot_policy=None)
    with pytest.raises(ValueError, match='bounds differ'):
        replace(contract, owned_snapshot_policy=replace(OWNED_SNAPSHOT_POLICY, max_rows=1))


@pytest.mark.parametrize('key,value', [('max_rows', True), ('max_entries', 0),
    ('max_bytes', -1), ('scope', 'source admission'), ('ownership', 'caller aliases')])
def test_owned_policy_semantic_and_type_changes_rejected(key, value):
    changed = deepcopy(OWNED_SNAPSHOT_POLICY.payload())
    changed[key] = value
    with pytest.raises(ValueError):
        parse_owned_scalar_snapshot_policy(changed)
