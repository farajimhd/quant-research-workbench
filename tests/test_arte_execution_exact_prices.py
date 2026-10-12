from dataclasses import replace
from datetime import date
import math
import pytest

from tests.test_arte_journal_projection import source, _execution, AT
from src.trading_runtime.arte_journal_projection import broker_fill_details
from src.trading_runtime.arte_journal_writer import TypedJournalBatch, typed_row, _wire_row
from src.trading_runtime.arte_execution_exact_prices import ExactPriceExecutionBatch, seal_exact_execution_batch
from src.trading_runtime.trading_price_rows import restore_exact_price_row


def projected():
    execution = _execution(source(price=math.nextafter(10.25, 11.), commission=1.))
    scope = dict(run_id="backtest:exact-fill", event_month="2026-08-01",
        batch_id="00000000-0000-0000-0000-000000000001",
        execution_record_id="00000000-0000-0000-0000-000000000002",
        commission_record_id="00000000-0000-0000-0000-000000000003", received_at=AT,
        signal_price=math.nextafter(10., 11.))
    details = broker_fill_details(execution, **scope, exact_prices=True)
    row = restore_exact_price_row("trading_execution_v1", details.execution)
    event = typed_row("trading_event_v1", dict(run_id=scope["run_id"],
        event_month=scope["event_month"], batch_id=scope["batch_id"],
        attempt_id="00000000-0000-0000-0000-000000000004", record_id=scope["execution_record_id"],
        sequence=1, event_time=execution.trade_time.isoformat(), recorded_at=AT.isoformat(),
        category="execution", entity_type="fill", entity_id=execution.execution_id,
        account_id=execution.account, correlation_id="", causation_id=""))
    base = TypedJournalBatch(scope["run_id"], date(2026, 8, 1), event["attempt_id"], scope["batch_id"],
        "00000000-0000-0000-0000-000000000000", 1, 1, "cursor", "running", (event,))
    return execution, scope, details, ExactPriceExecutionBatch(base, details.execution)


def test_exact_execution_seal_keeps_native_prices_and_fee_contract():
    execution, scope, details, unit = projected()
    with pytest.raises(ValueError, match="losslessly"):
        broker_fill_details(execution, **scope)
    restored = restore_exact_price_row("trading_execution_v1",
        _wire_row("trading_execution_exact_price_v1", unit.execution), stored_utc=True)
    assert restored["price"].hex() == execution.price.hex()
    assert restored["signal_price"].hex() == scope["signal_price"].hex()
    assert restored["quantity"] == "1.0000000000"
    assert details.commission["commission"] == "1.0000000000"
    families = dict(seal_exact_execution_batch(unit))
    assert "trading_execution_v1" not in families
    assert families["trading_execution_exact_price_v1"] == (dict(unit.execution),)


def test_exact_execution_rejects_tampering_or_foreign_envelope():
    _, _, _, unit = projected()
    with pytest.raises(ValueError, match="hash mismatch"):
        replace(unit, execution={**unit.execution, "price_bits": 1})
    wrong_event = typed_row("trading_event_v1", {**{key: value for key, value in unit.base.events[0].items()
        if key != "content_hash"}, "account_id": "foreign"})
    with pytest.raises(ValueError, match="original event"):
        replace(unit, base=replace(unit.base, events=(wrong_event,)))
    from src.trading_runtime.trading_price_contracts import transform_price_fields
    from src.trading_runtime.trading_price_rows import exact_price_row
    from src.trading_runtime.arte_journal_writer import _CONTRACTS
    native = transform_price_fields("trading_execution_v1",
        {key: value for key, value in unit.execution.items() if key != "content_hash"}, _CONTRACTS, restore=True)
    invalid = exact_price_row("trading_execution_v1", {**native, "price": 0.})
    with pytest.raises(ValueError, match="original event"):
        replace(unit, execution=invalid)
