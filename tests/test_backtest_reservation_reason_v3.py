from __future__ import annotations

from hashlib import sha256

import pytest

from src.backend.backtest_reservation_reason_v3 import (
    project_reservation_reasons_v3, seal_reservation_reason_family_v3,
)
from src.backend.backtest_squeeze_episode_schema import RESERVATION_REASON
from src.trading_runtime.journal_contract import canonical_json
from tests.test_backtest_v3_portfolio_reservation_projection import _project, _record
from tests.test_arte_journal_writer import BATCH


def test_release_reason_is_one_normalized_child():
    record = _record(event="reservation_released", extras={"reason": "cancelled"})
    rows = project_reservation_reasons_v3(record, batch_id=BATCH)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {name for name, _ in RESERVATION_REASON.columns}
    assert row["parent_record_id"] == record.record_id
    assert row["ordinal"] == 0
    assert row["reason"] == "cancelled"
    assert row["content_hash"] == sha256(canonical_json({
        key: value for key, value in row.items() if key != "content_hash"
    }).encode()).hexdigest()


def test_reprice_reasons_are_ordered_and_do_not_duplicate_price():
    record = _record(event="entry_reprice_authorized", extras={
        "price": 5, "reasons": ["cash", "risk"]})
    rows = project_reservation_reasons_v3(record, batch_id=BATCH)
    assert [(row["ordinal"], row["reason"]) for row in rows] == [
        (0, "cash"), (1, "risk")]
    assert all("price" not in row and "remaining_quantity" not in row for row in rows)
    assert rows[0]["record_id"] != rows[1]["record_id"]
    assert rows == project_reservation_reasons_v3(record, batch_id=BATCH)


@pytest.mark.parametrize("event", [
    "reservation_created", "cash_tranche_budget_reserved", "reservation_updated"
])
def test_other_emitter_variants_have_no_reason_child(event):
    assert project_reservation_reasons_v3(_record(event=event), batch_id=BATCH) == ()


def test_closed_projection_rejects_lossy_or_redundant_payloads():
    cases = (
        _record(event="reservation_released", extras={"reason": ""}),
        _record(event="reservation_released", extras={"reason": "cancelled", "extra": 1}),
        _record(event="entry_reprice_authorized", extras={"price": 6, "reasons": ["risk"]}),
        _record(event="entry_reprice_authorized", extras={"price": 5, "reasons": "risk"}),
    )
    for record in cases:
        with pytest.raises(ValueError):
            project_reservation_reasons_v3(record, batch_id=BATCH)


def test_repeated_upstream_reason_is_preserved_by_ordinal():
    record = _record(event="entry_reprice_authorized", extras={
        "price": 5, "reasons": ["risk", "risk"]})
    rows = project_reservation_reasons_v3(record, batch_id=BATCH)
    assert [(row["ordinal"], row["reason"]) for row in rows] == [
        (0, "risk"), (1, "risk")]


def test_child_ddl_has_typed_keys_and_ssd_policy():
    ddl = RESERVATION_REASON.ddl()
    assert "parent_record_id UUID" in ddl
    assert "ordinal UInt16" in ddl
    assert "reason String" in ddl
    assert "toYYYYMM(event_month)" in ddl
    assert "live_market_ssd" in ddl


def test_reason_seal_binds_release_child_to_reservation_parent():
    record = _record(event="reservation_released", extras={"reason": "cancelled"})
    rows = project_reservation_reasons_v3(record, batch_id=BATCH)
    # Existing V1 projection deliberately rejects the release payload. Use
    # its identical scalar parent shape, then set the actual emitted event.
    base = _project(_record())
    parent = {**base.portfolio_reservation_events[0], "event": "reservation_released"}
    parents = [base.events[0]]
    expected = {
        "portfolio_reservation_reason_count": 1,
        "portfolio_reservation_reason_hash": sha256(canonical_json([
            (rows[0]["record_id"], rows[0]["content_hash"])]).encode()).hexdigest(),
    }
    assert seal_reservation_reason_family_v3(
        rows, parents, [parent], run_id=record.run_id, batch_id=BATCH) == expected
    assert seal_reservation_reason_family_v3(
        [], parents, [{**parent, "event": "reservation_created"}],
        run_id=record.run_id, batch_id=BATCH)["portfolio_reservation_reason_count"] == 0
    with pytest.raises(ValueError, match="incomplete"):
        seal_reservation_reason_family_v3(
            [], parents, [parent], run_id=record.run_id, batch_id=BATCH)
    with pytest.raises(ValueError, match="parent or hash"):
        seal_reservation_reason_family_v3(
            [{**rows[0], "reason": "other"}], parents, [parent],
            run_id=record.run_id, batch_id=BATCH)
    with pytest.raises(ValueError, match="identity repeats"):
        seal_reservation_reason_family_v3(
            [rows[0], rows[0]], parents, [parent],
            run_id=record.run_id, batch_id=BATCH)
