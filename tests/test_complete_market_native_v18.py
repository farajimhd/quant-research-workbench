"""Native semantic checks only; no installed source proof is fabricated."""
from copy import deepcopy

import pytest

from tests.test_complete_market_release import prepared
from tests.test_fixed_structural_lot_native import cert
from src.backend.backtest_fixed_structural_lot_native_v18 import verify_installed_configuration
from src.trading_runtime.strategy_ninety_eight_contract import strategy_ninety_eight_contract


def test_complete_native_binding_retains_every_declared_policy():
    parent, arguments, _, result = prepared()
    factory = strategy_ninety_eight_contract()
    assert verify_installed_configuration(parent, cert(result['payload']),
        arguments['release'], arguments['parent_release']) == (
        factory.fixed_structural_lot_policy, factory.validation_reuse_policy,
        factory.projection_reuse_policy, factory.owned_snapshot_policy,
        factory.empty_confirmation_policy, factory.complete_market_policy)


@pytest.mark.parametrize('change', ['cash', 'costs', 'ownership', 'rows',
    'extra', 'retry', 'empty', 'missing_window'])
def test_resealed_changes_cannot_hide_economic_or_policy_drift(change):
    parent, arguments, _, result = prepared()
    payload = deepcopy(result['payload'])
    parameters = payload['strategy']['parameters']
    if change == 'cash':
        parameters['capital']['initial_cash'] = 1
    elif change == 'costs':
        parameters['costs']['commission_per_share'] = 0
    elif change == 'ownership':
        parameters['owned_scalar_snapshot_policy']['ownership'] = 'caller aliases'
    elif change == 'rows':
        parameters['owned_scalar_snapshot_policy']['max_rows'] = 1
    elif change == 'retry':
        parameters['complete_market_window_policy']['failure'] = 'retry partial response'
    elif change == 'empty':
        parameters['empty_protection_confirmation_policy']['omitted'] = 'all protection checks'
    elif change == 'missing_window':
        parameters.pop('complete_market_window_policy')
    else:
        parameters['undeclared'] = True
    with pytest.raises(ValueError):
        verify_installed_configuration(parent, cert(payload), arguments['release'], arguments['parent_release'])


def test_unselected_native_route_delegates_to_original_v16(monkeypatch):
    from src.backend import backtest_fixed_structural_lot_execution_v18 as selected
    from src.trading_runtime.strategy_ninety_five_release import release_contract
    sentinel = object()
    monkeypatch.setattr(selected.legacy, 'prepare_fixed_structural_lot_session', lambda **kwargs: sentinel)
    assert selected.prepare_fixed_structural_lot_session(number=release_contract().number,
        run_id='run', session_date=None, plans=None, market=None, candidates=None,
        entry=None, seeds=None, through_boundary_ms=1, client_factory=None) is sentinel
