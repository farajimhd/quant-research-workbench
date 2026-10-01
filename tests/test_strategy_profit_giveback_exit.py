from dataclasses import replace
from datetime import date, datetime, timezone
from uuid import UUID
import pytest
from src.trading_runtime.strategy_engine import AssignmentStatus, StrategyPermissions
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView
from src.trading_runtime.strategy_profit_giveback import profit_giveback
from src.trading_runtime.strategy_profit_giveback_exit import (
    REASON, profit_giveback_exit_intent, validate_profit_giveback_witness,
)
from test_strategy_profit_giveback import sample

ENTRY = '5a5cd03e-ae25-4c29-af8a-fb70fdfd8af5'


def financial():
    return StrategyOneFinancialView('assignment', 'account', 'DXST',
                                   AssignmentStatus.WATCHING, StrategyPermissions(),
                                   123., False, False, False, 1)


def intent(witness=None, held=None, entry=ENTRY):
    return profit_giveback_exit_intent(
        witness if witness is not None else profit_giveback(sample()),
        held if held is not None else financial(),
        session_date=date(2026, 8, 4), source_entry_intent_id=entry)


def test_deterministic_identity_full_held_quantity_and_native_utc():
    x = intent()
    assert x == intent()
    UUID(x.intent_id)
    assert x.event_time == datetime(2026, 8, 4, 8, 0, 10, tzinfo=timezone.utc)
    assert x.quantity == 123. and x.reference_price == 10.5
    assert x.action == 'exit' and x.outside_rth and x.reason == REASON and x.metadata == {}


def test_changed_source_entry_or_account_changes_identity():
    assert intent().intent_id != intent(entry='675b59ec-8cc1-47bc-9e3a-a895c903f47c').intent_id
    assert intent().intent_id != intent(held=replace(financial(), account_id='other')).intent_id


@pytest.mark.parametrize('field,value', [
    ('prior_high_int', 109999), ('prior_high_through_boundary_ms', 10000),
    ('completed_close_int', 105001), ('macd_line', .02), ('bid', 10.5001),
    ('quote_age_us', 1000001), ('macd_signal', float('nan')),
])
def test_forged_witness_cannot_create_intent(field, value):
    with pytest.raises(ValueError):
        intent(witness=replace(profit_giveback(sample()), **{field:value}))


@pytest.mark.parametrize('field,value', [
    ('position_quantity', 0.), ('position_quantity', float('inf')),
    ('pending_exit', True), ('account_id', ''), ('assignment_id', ''), ('ticker', ''),
])
def test_unheld_or_unowned_financial_input_cannot_create_intent(field, value):
    with pytest.raises(ValueError, match='financial authority'):
        intent(held=replace(financial(), **{field:value}))


def test_legacy_witness_or_non_uuid_source_is_rejected():
    with pytest.raises(ValueError, match='exact scalar'):
        validate_profit_giveback_witness(sample().completed)
    with pytest.raises(ValueError):
        intent(entry='not-an-intent')
