"""Whole manifest contracts with controlled parent transport; no admission proof."""
from copy import deepcopy

import pytest

from tests.test_strategy108_full_manifest import manifest
from src.trading_runtime.strategy_one_hundred_nine_release import (
    derive_strategy_one_hundred_nine_configuration,
    verify_prepared_strategy_one_hundred_nine_configuration,
)


def successor(manifest):
    parent, approval, previous = manifest
    candidate = derive_strategy_one_hundred_nine_configuration(parent, **approval)
    return parent, previous, candidate


def test_whole_manifest_roundtrip_and_economic_parity(manifest):
    parent, previous, candidate = successor(manifest)
    assert verify_prepared_strategy_one_hundred_nine_configuration(
        parent, candidate['payload']) == candidate
    current = candidate['payload']
    prior = previous['payload']
    parameters = deepcopy(current['strategy']['parameters'])
    assert parameters.pop('first_inventory_source_reuse_policy') == {
        'max_contexts': 32, 'initial_held': True, 'proposal': True,
    }
    assert parameters == prior['strategy']['parameters']
    for key in prior:
        if key not in {'strategy', 'strategy_profile', 'run_plan'}:
            assert current[key] == prior[key], key
    assert candidate['source_candidate_id'] == previous['source_candidate_id']
    assert candidate['source_candidate_hash'] == previous['source_candidate_hash']


@pytest.mark.parametrize('change', ('bounds', 'initial', 'proposal', 'currency', 'orphan'))
def test_whole_manifest_rejects_changed_policy_or_economics(manifest, change):
    parent, _, candidate = successor(manifest)
    payload = deepcopy(candidate['payload'])
    policy = payload['strategy']['parameters']['first_inventory_source_reuse_policy']
    if change == 'bounds':
        policy['max_contexts'] = 33
    elif change in {'initial', 'proposal'}:
        policy['initial_held' if change == 'initial' else 'proposal'] = False
    elif change == 'currency':
        payload['accounts']['currency'] = 'CAD'
    else:
        del payload['strategy']['parameters']['first_inventory_source_reuse_policy']
    with pytest.raises(ValueError):
        verify_prepared_strategy_one_hundred_nine_configuration(parent, payload)
