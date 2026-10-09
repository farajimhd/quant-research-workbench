"""Full prepared derivation; no source approval or native run is claimed."""
from copy import deepcopy
from dataclasses import replace
import pytest

from test_empty_confirmation_release import prepared as previous_prepared
from src.trading_runtime.fixed_structural_lot_release_v18 import (
    derive_fixed_structural_lot_release, verify_prepared_fixed_structural_lot_release,
)
from src.trading_runtime.strategy_ninety_eight_release import release_contract, COMPLETE_MARKET_POLICY
from src.trading_runtime.strategy_ninety_eight_contract import strategy_ninety_eight_contract
from src.trading_runtime.complete_market_window_policy import RULE


def prepared():
    parent, arguments, previous = previous_prepared()
    arguments = {**arguments, 'release': release_contract(), 'complete_market_policy': COMPLETE_MARKET_POLICY}
    return parent, arguments, previous, derive_fixed_structural_lot_release(parent, **arguments)


def verification(arguments):
    return {key: value for key, value in arguments.items() if key not in (
        'policy', 'approved_code_commit', 'approved_code_fingerprint', 'approval_reference')}


def test_full_economics_and_existing_policies_are_identical():
    parent, arguments, previous, current = prepared()
    parameters = dict(current['payload']['strategy']['parameters'])
    assert parameters.pop('complete_market_window_policy') == COMPLETE_MARKET_POLICY.payload()
    assert parameters == previous['payload']['strategy']['parameters']
    for key in previous['payload'].keys() - {'strategy', 'strategy_profile', 'run_plan'}:
        assert current['payload'][key] == previous['payload'][key]
    assert verify_prepared_fixed_structural_lot_release(parent, current['payload'],
                                                       **verification(arguments)) == current
    factory = strategy_ninety_eight_contract()
    assert factory.complete_market_policy == COMPLETE_MARKET_POLICY
    assert factory.empty_confirmation_policy == arguments['empty_confirmation_policy']


@pytest.mark.parametrize('defect', ['unrelated_sizing', 'response_retry', 'missing_transport'])
def test_complete_tree_rejects_economic_or_transport_drift(defect):
    parent, arguments, _, current = prepared()
    payload = deepcopy(current['payload'])
    parameters = payload['strategy']['parameters']
    if defect == 'unrelated_sizing':
        parameters['unreviewed_position_scale'] = 100
    elif defect == 'response_retry':
        parameters['complete_market_window_policy']['failure'] = 'retry partial response'
    else:
        parameters.pop('complete_market_window_policy')
    with pytest.raises(ValueError, match='complete parent derivation'):
        verify_prepared_fixed_structural_lot_release(parent, payload, **verification(arguments))


def test_exact_typed_policy_and_complete_release_pair_are_mandatory():
    parent, arguments, _, _ = prepared()
    with pytest.raises(ValueError):
        derive_fixed_structural_lot_release(parent, **{**arguments, 'complete_market_policy': None})
    release = arguments['release']
    draft = replace(release, rule_set_contracts=tuple(r for r in release.rule_set_contracts if r != RULE), approved_digest='')
    with pytest.raises(ValueError):
        derive_fixed_structural_lot_release(parent, **{**arguments,
            'release': replace(draft, approved_digest=draft.digest())})


def test_prepared_successor_is_unregistered_but_exact_factory_shape_is_recognized():
    from src.trading_runtime.strategy_registry import numbered_strategy
    from src.trading_runtime.fixed_structural_lot_reuse_contract import require_declared_fixed_structural_lot_contract
    with pytest.raises(ValueError):
        numbered_strategy(98)
    factory = strategy_ninety_eight_contract()
    assert require_declared_fixed_structural_lot_contract(factory, factory.release) is factory
    # Type recognition cannot register a release or mint installed source admission.
    from src.trading_runtime.strategy_ninety_six_contract import strategy_ninety_six_contract
    with pytest.raises(ValueError, match='exact declared release type'):
        require_declared_fixed_structural_lot_contract(strategy_ninety_six_contract(), factory.release)


def test_factory_selection_rejects_unpaired_window_declaration_and_subclasses():
    from src.trading_runtime.fixed_structural_lot_reuse_contract import require_declared_fixed_structural_lot_contract
    from src.trading_runtime.fixed_structural_lot_complete_market_contract import FixedStructuralLotCompleteMarketStrategyContract
    factory = strategy_ninety_eight_contract()
    draft = replace(factory.release, rule_set_contracts=tuple(
        rule for rule in factory.release.rule_set_contracts if rule != RULE), approved_digest='')
    unpaired = replace(draft, approved_digest=draft.digest())
    with pytest.raises(ValueError):
        require_declared_fixed_structural_lot_contract(replace(factory, release=unpaired), unpaired)
    class ExtraFactory(FixedStructuralLotCompleteMarketStrategyContract):
        pass
    forged = ExtraFactory(**{name: getattr(factory, name) for name in factory.__dataclass_fields__})
    with pytest.raises(ValueError, match='exact declared release type'):
        require_declared_fixed_structural_lot_contract(forged, factory.release)


def test_exact_successor_wrapper_rejects_another_valid_lot_count():
    from hashlib import sha256
    from test_strategy_sixty_four_release import source_fixture
    from test_strategy_fifty_release import APPROVAL
    from src.trading_runtime.strategy_ninety_eight_release import (
        derive_strategy_ninety_eight_configuration, verify_prepared_strategy_ninety_eight_configuration,
    )
    from src.trading_runtime.journal_contract import canonical_json
    from src.trading_runtime.strategy_one_configuration_tree import encode_nodes, node_hash
    parent = source_fixture()
    # That older declaration fixture carries a historical payload hash rather
    # than the hash of its synthetic contents. Full derivation correctly rejects
    # it. Make this unit certificate self-consistent, without native admission.
    parent = replace(parent, payload_hash=sha256(canonical_json(parent.payload).encode()).hexdigest(),
                     node_hash=node_hash(encode_nodes(parent.payload)))
    actual = derive_strategy_ninety_eight_configuration(parent, **APPROVAL)
    assert verify_prepared_strategy_ninety_eight_configuration(parent, actual['payload']) == actual
    changed = deepcopy(actual['payload'])
    changed['strategy']['parameters']['fixed_structural_lot_policy']['count'] = 2
    with pytest.raises(ValueError, match='declared fixed lot policy'):
        verify_prepared_strategy_ninety_eight_configuration(parent, changed)
