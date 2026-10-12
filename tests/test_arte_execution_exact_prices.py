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


def test_exact_execution_v4_commit_readback_retry_and_corruption(monkeypatch):
    from src.trading_runtime import rule_decision_writer_preflight as audit
    from src.trading_runtime.arte_execution_exact_prices import publish_exact_execution_batch_v4
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix, load_verified_commit_v4
    from tests.test_arte_journal_commit_v4 import attached_v4_client
    _, _, _, unit = projected()
    client = attached_v4_client()
    monkeypatch.setattr(audit, "require_rule_decision_writer_audit", lambda actual: None)
    assert publish_exact_execution_batch_v4(client, unit) == unit.base.batch_id
    prefix = load_verified_v4_prefix(client, unit.base.run_id)
    assert prefix.last_sequence == 1 and prefix.last_batch_id == unit.base.batch_id
    assert "trading_execution_v1" not in client.tables
    assert len(client.tables["trading_execution_exact_price_v1"]) == 1
    from src.trading_runtime.arte_journal_writer import load_committed_execution_page
    cold, = load_committed_execution_page(client, prefix, exact_prices=True)
    assert cold["price"] == restore_exact_price_row("trading_execution_v1", unit.execution)["price"]
    assert cold["sequence"] == 1 and cold["execution_id"] == unit.execution["execution_id"]
    from src.trading_runtime.arte_journal_reader import load_typed_event_page, load_complete_typed_protection_history
    event, = load_typed_event_page(client, prefix, exact_prices=True)
    assert event.detail_family == "trading_execution_v1" and event.detail["price"] == cold["price"]
    protection = load_complete_typed_protection_history(client, prefix, exact_prices=True)
    assert protection.records == () and protection.through_sequence == 1
    with pytest.raises(RuntimeError, match="missing or duplicate"):
        load_committed_execution_page(client, prefix)
    assert publish_exact_execution_batch_v4(client, unit) == unit.base.batch_id
    client.tables["trading_execution_exact_price_v1"][0]["price_bits"] += 1
    with pytest.raises(ValueError, match="hash mismatch"):
        load_committed_execution_page(client, prefix, exact_prices=True)
    with pytest.raises(RuntimeError, match="row hash"):
        load_verified_commit_v4(client, run_id=unit.base.run_id, batch_id=unit.base.batch_id)


def test_fee_identity_check_rejects_mixed_execution_representations():
    from src.trading_runtime.arte_journal_writer import _execution_identity_rows
    row = dict(account_id="account", execution_id="execution")
    assert _execution_identity_rows({"trading_execution_exact_price_v1": (row,)}) == (row,)
    assert _execution_identity_rows({"trading_execution_v1": (row,)}) == (row,)
    with pytest.raises(ValueError, match="cannot mix"):
        _execution_identity_rows({"trading_execution_v1": (row,), "trading_execution_exact_price_v1": (row,)})


@pytest.mark.parametrize("queued", [False, True])
def test_exact_fill_and_money_fee_coalesce_under_one_verified_fence(monkeypatch, queued):
    from decimal import Decimal
    from src.trading_runtime.domain import CommissionEvent
    from src.trading_runtime.arte_journal_projection import commission_revision_batch
    from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, publish_compound_v4
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
    from src.trading_runtime import rule_decision_writer_preflight as audit, arte_journal_writer as writer
    from tests.test_arte_journal_commit_v4 import attached_v4_client
    execution, _, _, unit = projected()
    fee = commission_revision_batch(CommissionEvent(execution.execution_id, execution.account,
        Decimal("1"), "USD", source_event_time=AT, received_at=AT),
        run_id=unit.base.run_id, run_month=unit.base.run_month, attempt_id=unit.base.attempt_id,
        batch_id="00000000-0000-0000-0000-000000000005", prior_batch_id=unit.base.batch_id,
        sequence=2, source_cursor="cursor", run_status="running", time_authority="broker")
    compound = coalesce_v4_units((unit, ExactPriceExecutionBatch(fee, None)), max_events=2)
    client = attached_v4_client()
    monkeypatch.setattr(audit, "require_rule_decision_writer_audit", lambda actual: None)
    if queued:
        # Physical startup authority is an explicit external seam; the real
        # queue, publisher, Keeper fence and cold validators execute below.
        monkeypatch.setattr(writer, "_v4_preflight", lambda actual: None)
        monkeypatch.setattr(writer, "_verify_run_identity", lambda *args: dict(mode="backtest", account_ids=("DU1",)))
        lane = writer.ArteJournalWriter(client, run_id=unit.base.run_id,
            journal_profile="backtest_v4", coalesce_batches=False)
        try:
            assert lane.submit_compound_v4(compound).result(timeout=5) == fee.batch_id
        finally:
            lane.close()
    else:
        assert publish_compound_v4(client, compound) == fee.batch_id
    prefix = load_verified_v4_prefix(client, unit.base.run_id)
    assert prefix.last_sequence == 2 and prefix.batch_ids == (fee.batch_id,)
    fill, = writer.load_committed_execution_page(client, prefix, exact_prices=True)
    charge, = writer.load_committed_commission_page(client, prefix)
    assert fill["price"] == execution.price and charge["commission"] == "1.0000000000"
    assert "trading_execution_v1" not in client.tables


def test_exact_fill_uses_real_bounded_queue(monkeypatch):
    from src.trading_runtime import arte_journal_writer as writer, rule_decision_writer_preflight as audit
    from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
    from tests.test_arte_journal_commit_v4 import attached_v4_client
    _, _, _, unit = projected()
    client = attached_v4_client()
    monkeypatch.setattr(audit, "require_rule_decision_writer_audit", lambda actual: None)
    monkeypatch.setattr(writer, "_v4_preflight", lambda actual: None)
    monkeypatch.setattr(writer, "_verify_run_identity", lambda *args: dict(mode="backtest", account_ids=("DU1",)))
    lane = writer.ArteJournalWriter(client, run_id=unit.base.run_id,
        journal_profile="backtest_v4", coalesce_batches=False)
    try:
        assert lane.submit_exact_execution_v4(unit).result(timeout=5) == unit.base.batch_id
    finally:
        lane.close()
    prefix = load_verified_v4_prefix(client, unit.base.run_id)
    assert prefix.last_sequence == 1


def test_later_money_fee_links_only_to_committed_exact_execution(monkeypatch):
    from decimal import Decimal
    from src.trading_runtime.domain import CommissionEvent
    from src.trading_runtime.arte_journal_projection import commission_revision_batch
    from src.trading_runtime.arte_execution_exact_prices import publish_exact_execution_batch_v4
    from src.trading_runtime import rule_decision_writer_preflight as audit
    from src.trading_runtime.arte_journal_commit_v4 import publish_base_typed_batch_v4, load_verified_v4_prefix
    from tests.test_arte_journal_commit_v4 import attached_v4_client
    execution, _, _, unit = projected()
    client = attached_v4_client()
    monkeypatch.setattr(audit, "require_rule_decision_writer_audit", lambda actual: None)
    publish_exact_execution_batch_v4(client, unit)
    fee = commission_revision_batch(CommissionEvent(execution.execution_id, execution.account,
        Decimal("1"), "USD", source_event_time=AT, received_at=AT), run_id=unit.base.run_id,
        run_month=unit.base.run_month, attempt_id=unit.base.attempt_id,
        batch_id="00000000-0000-0000-0000-000000000005", prior_batch_id=unit.base.batch_id,
        sequence=2, source_cursor="cursor", run_status="running", time_authority="broker")
    with pytest.raises(RuntimeError, match="one committed execution"):
        publish_base_typed_batch_v4(client, fee)
    assert publish_exact_execution_batch_v4(client, ExactPriceExecutionBatch(fee, None)) == fee.batch_id
    assert load_verified_v4_prefix(client, unit.base.run_id).last_sequence == 2
