"""Whole-tree derivation with an explicit controlled inherited transport."""
from copy import deepcopy
from dataclasses import replace

import pytest

from src.trading_runtime.fixed_structural_lot_release_v31 import (
    derive_fixed_structural_lot_release, verify_prepared_fixed_structural_lot_release)
from src.trading_runtime.strategy_one_hundred_eleven_contract import strategy_one_hundred_eleven_contract
from src.trading_runtime.strategy_one_hundred_twelve_contract import strategy_one_hundred_twelve_contract


def fixture():
    prior = strategy_one_hundred_eleven_contract()
    candidate = strategy_one_hundred_twelve_contract()
    payload = dict(strategy=dict(parameters=dict(
        initial_held_recovery_reuse_policy=prior.initial_held_recovery_reuse_policy.payload(),
        execution=dict(costs='inherited'), sizing=dict(cash=10000)), numbered_release={}),
        strategy_profile={}, run_plan={}, untouched=dict(nested=[1, {'flag': True}]))
    def inherited(parent, **approval):
        result = deepcopy(payload)
        result['strategy']['numbered_release'].update(approval)
        return dict(source_candidate_id='controlled-parent', source_candidate_hash='a'*64,
            payload=result)
    args = dict(inherited_derive=inherited, inherited_release=prior.release,
        release=candidate.release,
        initial_held_recovery_reuse_policy=candidate.initial_held_recovery_reuse_policy)
    return payload, args


def derive(args):
    return derive_fixed_structural_lot_release(None, **args,
        approved_code_commit='a'*40, approved_code_fingerprint='b'*64,
        approval_reference='controlled-test')


def test_rederivation_preserves_nested_inherited_parameters_and_parent():
    parent, args = fixture()
    snapshot = deepcopy(parent)
    result = derive(args)
    expected = deepcopy(parent['strategy']['parameters'])
    expected['initial_held_recovery_reuse_policy']['max_contexts'] = 128
    assert result['payload']['strategy']['parameters'] == expected
    assert result['payload']['untouched'] == parent['untouched']
    assert parent == snapshot
    assert verify_prepared_fixed_structural_lot_release(None, result['payload'], **args) == result


def test_verifier_rejects_inherited_cash_mutation():
    _, args = fixture()
    result = derive(args)
    result['payload']['strategy']['parameters']['sizing']['cash'] = 20000
    with pytest.raises(ValueError, match='whole inherited tree'):
        verify_prepared_fixed_structural_lot_release(None, result['payload'], **args)


def test_deriver_rejects_inventory_memory_limit_change():
    _, args = fixture()
    args['initial_held_recovery_reuse_policy'] = replace(
        args['initial_held_recovery_reuse_policy'], max_inventory_bytes=1024)
    with pytest.raises(ValueError, match='only its declared context count'):
        derive(args)
