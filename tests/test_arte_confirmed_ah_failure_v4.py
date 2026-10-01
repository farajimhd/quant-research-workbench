"""Prepared factory/scalar persistence checks; no connected writer or replay."""
from dataclasses import replace
from datetime import date
from uuid import uuid4

import pytest

from src.trading_runtime.arte_confirmed_ah_failure_v4 import (
    CONFIRMED_AH_FAILURE, project_confirmed_ah_failure, restore_confirmed_ah_failure,
)
from src.trading_runtime.strategy_confirmed_ah_failure_exit import confirmed_ah_exit_intent
from src.trading_runtime.strategy_confirmed_ah_risk_failure import (
    ConfirmedAhRiskFailureInput, confirmed_ah_risk_failure,
)
from src.trading_runtime.strategy_followthrough_failure import FollowThroughFailureInput
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions


def prepared_case():
    five = FollowThroughFailureInput(
        43_700_000, 43_647_500, 2.08, 1.81, 43_700_000, 19_700, True,
        0.01971676900139796, 0.028269653367226016, 1.96, 1.97, 48, 42.0, False,
    )
    witness = confirmed_ah_risk_failure(ConfirmedAhRiskFailureInput(
        five, 43_700_000, True, 0.05043722423951946, 0.05238791450068928,
    ))
    financial = StrategyOneFinancialView(
        'assignment', 'account', 'WAFU', AssignmentStatus.MANAGING,
        StrategyPermissions(), 42.0, False, False, False, 1,
    )
    args = dict(session_date=date(2026, 8, 10), source_entry_intent_id=str(uuid4()))
    intent = confirmed_ah_exit_intent(witness, financial, **args)
    row = project_confirmed_ah_failure(
        witness, intent, financial, **args, run_id='development-run',
        batch_id=str(uuid4()), parent_record_id=str(uuid4()),
    )
    return witness, financial, args, intent, row


def test_factory_and_exact_complete_scalar_roundtrip():
    witness, financial, args, intent, row = prepared_case()
    assert confirmed_ah_exit_intent(witness, financial, **args) == intent
    assert restore_confirmed_ah_failure(row) == witness
    assert set(row) == {name for name, _ in CONFIRMED_AH_FAILURE.columns} - {'content_hash'}
    assert intent.event_time.isoformat() == '2026-08-10T20:08:20+00:00'
    assert intent.quantity == 42 and intent.reference_price == 1.96
    assert intent.metadata == {} and intent.execution_policy.quote_source == 'qmd'
    assert confirmed_ah_exit_intent(witness, replace(financial, ticker='OTHER'), **args).intent_id != intent.intent_id


@pytest.mark.parametrize('field,value', [
    ('strategy_number', 33), ('strategy_number', True),
    ('first_held_boundary_ms', '43647500'), ('first_held_boundary_ms', 43_699_900),
    ('completed_ten_second_boundary_ms', 43_710_000),
    ('ten_second_macd_line', 0.06), ('ten_second_macd_signal', float('nan')),
    ('reference_ask', True), ('bid', 2.02),
])
def test_scalar_restore_rejects_missing_or_altered_authority(field, value):
    *_, row = prepared_case()
    with pytest.raises(ValueError):
        restore_confirmed_ah_failure(dict(row, **{field: value}))


def test_factory_rejects_pending_exit_and_projection_rejects_altered_order():
    witness, financial, args, intent, row = prepared_case()
    with pytest.raises(ValueError):
        confirmed_ah_exit_intent(witness, replace(financial, pending_exit=True), **args)
    with pytest.raises(ValueError):
        project_confirmed_ah_failure(
            witness, replace(intent, quantity=43), financial, **args,
            run_id=row['run_id'], batch_id=row['batch_id'], parent_record_id=row['parent_record_id'],
        )
