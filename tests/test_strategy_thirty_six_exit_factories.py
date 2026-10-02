"""Prepared successor identities preserve the complete parent liquidation intent."""
from dataclasses import replace
from datetime import date

import pytest

from src.trading_runtime.strategy_confirmed_ah_failure_exit import confirmed_ah_exit_intent
from src.trading_runtime.strategy_liquidity_fade_exit import liquidity_fade_exit_intent
from test_arte_confirmed_ah_failure_v4 import prepared_case as ah_case
from test_arte_liquidity_fade_failure_v4 import prepared_case as liquidity_case


def inputs(kind):
    if kind == 'ah':
        witness, financial, args, _, _ = ah_case()
        return confirmed_ah_exit_intent, witness, financial, args
    witness, financial, _, row = liquidity_case()
    return liquidity_fade_exit_intent, witness, financial, dict(
        session_date=date(2026, 8, 10),
        source_entry_intent_id=row['source_entry_intent_id'])


@pytest.mark.parametrize('kind', ['ah', 'liquidity'])
def test_successor_changes_only_numbered_identity_and_reason(kind):
    factory, witness, financial, args = inputs(kind)
    parent = factory(witness, financial, **args, strategy_number=35)
    successor = factory(witness, financial, **args, strategy_number=36)
    assert successor == factory(witness, financial, **args, strategy_number=36)
    assert successor.intent_id != parent.intent_id
    assert successor.reason == parent.reason.replace('thirty_five', 'thirty_six')
    assert replace(successor, intent_id=parent.intent_id, reason=parent.reason) == parent


@pytest.mark.parametrize('kind', ['ah', 'liquidity'])
@pytest.mark.parametrize('number', [True, 36.0, '36', 33, 37])
def test_prepared_number_must_be_exact_and_supported(kind, number):
    factory, witness, financial, args = inputs(kind)
    with pytest.raises(ValueError):
        factory(witness, financial, **args, strategy_number=number)


@pytest.mark.parametrize('kind', ['ah', 'liquidity'])
def test_successor_keeps_held_financial_authority_requirement(kind):
    factory, witness, financial, args = inputs(kind)
    with pytest.raises(ValueError):
        factory(witness, replace(financial, pending_exit=True), **args, strategy_number=36)
