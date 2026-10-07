"""Complete declaration preparation and inherited economics; no installed release."""
from copy import deepcopy
from dataclasses import replace

import pytest

from test_fixed_structural_lot_native import cert, declarations
from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
from src.trading_runtime.fixed_structural_lot_release import derive_fixed_structural_lot_release
from src.trading_runtime.fixed_structural_lot_release import verify_prepared_fixed_structural_lot_release
from src.trading_runtime.strategy_one_configuration_tree import decode_nodes


def inputs():
    parent, _, release, parent_release = declarations()
    payload = deepcopy(parent.payload)
    payload['strategy']['execution_interval'] = parent_release.evaluation_interval
    payload['strategy']['numbered_release'].update(
        contract=parent_release.canonical_payload(), approved_digest=parent_release.approved_digest,
        inherited_policy={'version': 1, 'cash_fraction': '1/3'})
    return cert(payload), dict(parent_release=parent_release, release=release,
        policy=FixedStructuralLotPolicy().payload(), approved_code_commit='a'*40,
        approved_code_fingerprint='b'*64, approval_reference='reviewed fixture only')


def test_whole_tree_preparation_preserves_parent_and_normalized_output():
    parent, kwargs = inputs()
    original = deepcopy(parent.payload)
    result = derive_fixed_structural_lot_release(parent, **kwargs)
    assert parent.payload == original
    assert decode_nodes(result['nodes']) == result['payload']
    assert result['node_count'] == len(result['nodes'])
    own = result['payload']
    for key in ('execution', 'costs', 'capital'):
        assert own['strategy']['parameters'][key] == original['strategy']['parameters'][key]
    assert own['accounts'] == original['accounts']
    assert own['strategy']['numbered_release']['inherited_policy'] == original['strategy']['numbered_release']['inherited_policy']
    assert own['strategy']['parameters']['fixed_structural_lot_policy'] == kwargs['policy']
    assert own['strategy_profile']['profile_id'] == own['run_plan']['profile_id']
    from src.backend.backtest_fixed_structural_lot_native import verify_installed_configuration
    assert verify_installed_configuration(parent, cert(own), kwargs['release'], kwargs['parent_release']) == FixedStructuralLotPolicy()
    assert verify_prepared_fixed_structural_lot_release(parent, own,
        parent_release=kwargs['parent_release'], release=kwargs['release']) == result


@pytest.mark.parametrize('change', ['hash', 'nodes', 'assignments', 'contract', 'input', 'rule',
                                  'interval', 'source_commit', 'source_fingerprint', 'approval'])
def test_unsealed_or_changed_parent_and_release_cannot_prepare(change):
    parent, kwargs = inputs()
    if change == 'hash': parent = replace(parent, payload_hash='f'*64)
    elif change == 'nodes': parent = replace(parent, node_hash='f'*64)
    elif change in ('assignments', 'contract'):
        payload = deepcopy(parent.payload)
        if change == 'assignments': payload['assignments'] = [{'account_id': 'mutable'}]
        else: payload['strategy']['numbered_release']['contract']['behavior_specification'] = 'foreign'
        parent = cert(payload)
    elif change in ('input', 'rule', 'interval'):
        release = kwargs['release']
        fields = {'input': {'input_contracts': release.input_contracts[:-1]},
                  'rule': {'rule_set_contracts': release.rule_set_contracts[:-1]},
                  'interval': {'evaluation_interval': 'event'}}[change]
        release = replace(release, **fields, approved_digest='')
        kwargs['release'] = replace(release, approved_digest=release.digest())
    elif change == 'source_commit': kwargs['approved_code_commit'] = 'bad'
    elif change == 'source_fingerprint': kwargs['approved_code_fingerprint'] = 'bad'
    else: kwargs['approval_reference'] = ' '
    with pytest.raises(ValueError):
        derive_fixed_structural_lot_release(parent, **kwargs)


@pytest.mark.parametrize('change', ['cost', 'account', 'identity', 'manifest_extra',
                                  'manifest_inherited', 'manifest_hash', 'parent_reference', 'missing_policy'])
def test_complete_rederivation_rejects_resealed_semantic_and_manifest_changes(change):
    parent, kwargs = inputs()
    own = derive_fixed_structural_lot_release(parent, **kwargs)['payload']
    if change == 'cost': own['strategy']['parameters']['costs']['minimum_per_order'] = 0
    elif change == 'account': own['accounts']['currency'] = 'CAD'
    elif change == 'identity': own['run_plan']['profile_id'] = 'foreign'
    elif change == 'manifest_extra': own['strategy']['numbered_release']['caller_approved'] = True
    elif change == 'manifest_inherited': own['strategy']['numbered_release']['inherited_policy']['cash_fraction'] = '1'
    elif change == 'manifest_hash': own['strategy']['numbered_release']['manifest_hash'] = 'f'*64
    elif change == 'parent_reference': own['strategy']['parameters']['fixed_structural_lot_parent']['revision_id'] = 'foreign'
    else: own['strategy']['parameters'].pop('fixed_structural_lot_policy')
    with pytest.raises(ValueError):
        verify_prepared_fixed_structural_lot_release(parent, own,
            parent_release=kwargs['parent_release'], release=kwargs['release'])
