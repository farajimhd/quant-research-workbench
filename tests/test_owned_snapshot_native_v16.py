from copy import deepcopy

import pytest

from tests.test_owned_snapshot_release import prepared
from tests.test_fixed_structural_lot_native import cert
from src.backend.backtest_fixed_structural_lot_native_v16 import verify_installed_configuration
from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
from src.trading_runtime.strategy_ninety_five_release import (
    VALIDATION_REUSE_POLICY, PROJECTION_REUSE_POLICY, OWNED_SNAPSHOT_POLICY,
)


def test_complete_native_semantic_binding_retains_all_four_policies():
    parent, kwargs, result = prepared()
    assert verify_installed_configuration(parent, cert(result['payload']), kwargs['release'],
        kwargs['parent_release']) == (FixedStructuralLotPolicy(), VALIDATION_REUSE_POLICY,
            PROJECTION_REUSE_POLICY, OWNED_SNAPSHOT_POLICY)


@pytest.mark.parametrize('change', ['cash', 'costs', 'ownership', 'rows', 'extra'])
def test_resealed_semantic_changes_rejected(change):
    parent, kwargs, result = prepared()
    changed = deepcopy(result['payload'])
    parameters = changed['strategy']['parameters']
    if change == 'cash':
        parameters['capital']['initial_cash'] = 1
    elif change == 'costs':
        parameters['costs']['commission_per_share'] = 0
    elif change == 'ownership':
        parameters['owned_scalar_snapshot_policy']['ownership'] = 'caller aliases'
    elif change == 'rows':
        parameters['owned_scalar_snapshot_policy']['max_rows'] = 1
    else:
        parameters['undeclared'] = True
    with pytest.raises(ValueError):
        verify_installed_configuration(parent, cert(changed), kwargs['release'], kwargs['parent_release'])


def test_undeclared_native_route_delegates_to_original_v15(monkeypatch):
    from src.backend import backtest_fixed_structural_lot_execution_v16 as selected
    from src.trading_runtime.strategy_ninety_four_release import release_contract
    sentinel = object()
    monkeypatch.setattr(selected.legacy, 'prepare_fixed_structural_lot_session', lambda **kwargs: sentinel)
    assert selected.prepare_fixed_structural_lot_session(number=release_contract().number, run_id='run',
        session_date=None, plans=None, market=None, candidates=None, entry=None,
        seeds=None, through_boundary_ms=1, client_factory=None) is sentinel
