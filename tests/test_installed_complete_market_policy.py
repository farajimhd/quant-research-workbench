"""Negative admission tests do not replace actual installed native checks."""
from types import SimpleNamespace

import pytest

from src.trading_runtime.complete_market_window_policy import (
    INPUT, RULE, installed_complete_market_window_policy,
)
from src.trading_runtime.strategy_ninety_eight_release import COMPLETE_MARKET_POLICY


def test_parent_and_unselected_release_keep_original_reader():
    assert installed_complete_market_window_policy(SimpleNamespace(installed_json='')) is None
    source = SimpleNamespace(installed_json='{}', installed_payload={
        'strategy': {'parameters': {}, 'numbered_release': {'contract': {}}}})
    assert installed_complete_market_window_policy(source) is None


@pytest.mark.parametrize('claim', ['parameter', 'input', 'rule'])
def test_duck_typed_source_cannot_select_transport_even_with_valid_policy(claim):
    parameters, contract = {}, {}
    if claim == 'parameter':
        parameters['complete_market_window_policy'] = COMPLETE_MARKET_POLICY.payload()
    elif claim == 'input':
        contract['input_contracts'] = [INPUT]
    else:
        contract['rule_set_contracts'] = [RULE]
    source = SimpleNamespace(installed_json='{}', installed_payload={'strategy': {
        'parameters': parameters, 'numbered_release': {'contract': contract}}},
        require_installed_admission=lambda: pytest.fail('Forged source reached admission'))
    with pytest.raises(ValueError, match='Exact factory-issued fixed-lot source'):
        installed_complete_market_window_policy(source)
