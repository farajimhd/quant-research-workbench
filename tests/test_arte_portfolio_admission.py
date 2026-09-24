from __future__ import annotations

import asyncio
from concurrent.futures import Future
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import date

import pytest

from src.trading_runtime import arte_portfolio_admission as admission
from src.trading_runtime.arte_journal_projection import project_portfolio_admission_records
from src.trading_runtime.arte_journal_writer import _sealed_families
from src.trading_runtime.portfolio import PortfolioReservation
from tests.test_arte_admission_fence import AT, ATTEMPT, BATCH, RUN, SNAPSHOT_HASH, ZERO, batch, captured


class Keeper:
    current = True

    @asynccontextmanager
    async def claim_portfolio_admission(self, run_id, account_id, group_ids):
        assert (run_id, account_id, group_ids) == (RUN, "DU1", ())
        yield "lease"

    def portfolio_admission_claim_is_current(self, lease):
        assert lease == "lease"
        return self.current


class Writer:
    def __init__(self):
        self.future = Future()

    def submit_admission(self, item, image):
        assert item.batch_id == BATCH
        assert image.account_id == "DU1"
        return self.future


def _authority(monkeypatch, writer, keeper):
    monkeypatch.setattr(admission, "load_fenced_admission", lambda _client, **_identity: {
        "snapshot_hash": SNAPSHOT_HASH, "batch_id": BATCH,
    })
    monkeypatch.setattr(admission, "project_portfolio_admission_records",
                        lambda _records, **_identity: batch())
    return admission.TypedPortfolioAdmissionAuthority(
        client=object(), writer=writer, keeper=keeper,
        next_revision=lambda *_args: 1,
        batch_identity=lambda *_args: {
            "run_month": date(2026, 8, 1), "attempt_id": ATTEMPT,
            "batch_id": BATCH, "prior_batch_id": ZERO,
            "first_sequence": 1, "source_cursor": "admission-1"})


def test_publish_waits_for_writer_then_verifies_fence(monkeypatch) -> None:
    writer, keeper = Writer(), Keeper()
    authority = _authority(monkeypatch, writer, keeper)
    records = (("portfolio_decision", "decision-1", "DU1", {}),
               ("portfolio_reservation", "reservation-1", "DU1", {}))

    async def run():
        async with authority.claim(RUN, "DU1", ()) as lease:
            task = asyncio.create_task(authority.publish(records, captured(), lease))
            await asyncio.sleep(0)
            assert not task.done()
            writer.future.set_result(SNAPSHOT_HASH)
            return await task

    receipt = asyncio.run(run())
    assert receipt.snapshot_hash == SNAPSHOT_HASH
    assert receipt.batch_id == BATCH


def test_publish_rejects_missing_fact_or_stale_claim(monkeypatch) -> None:
    writer, keeper = Writer(), Keeper()
    authority = _authority(monkeypatch, writer, keeper)

    async def run():
        with pytest.raises(RuntimeError, match="complete account facts"):
            await authority.publish((("portfolio_decision", "d", "DU1", {}),),
                                    captured(), "lease")
        keeper.current = False
        with pytest.raises(RuntimeError, match="current claim"):
            await authority.publish((("portfolio_decision", "d", "DU1", {}),
                                     ("portfolio_reservation", "r", "DU1", {})),
                                    captured(), "lease")

    asyncio.run(run())


def test_normalized_portfolio_admission_batch_seals_decision_and_reservation() -> None:
    metrics = {name: 0.0 for name in (
        "net_liquidation", "available_funds", "buying_power", "gross_exposure",
        "net_exposure", "reserved_notional", "open_risk", "daily_loss",
        "drawdown", "position_count")}
    decision = {
        "event": "portfolio_decision", "ticker": "AAA", "action": "enter_long",
        "decision_id": "decision-1", "request_id": "intent-1", "account_key": "primary",
        "account_id": "DU1", "policy_id": "policy-1", "policy_revision": 1,
        "snapshot_id": "broker-1", "status": "approved",
        "requested_quantity": 10.0, "approved_quantity": 10.0,
        "approved_notional": 50.0, "planned_loss": 2.0,
        "reservation_id": "reservation-1", "reasons": ("within_limits",),
        "metrics_before": metrics, "metrics_after": metrics,
        "decided_at": AT, "correlation_id": "corr", "causation_id": "intent-1",
    }
    reservation = asdict(PortfolioReservation(
        reservation_id="reservation-1", decision_id="decision-1", intent_id="intent-1",
        account_key="primary", account_id="DU1", strategy_id="strategy-1",
        assignment_id="assignment-1", ticker="AAA", action="enter_long",
        quantity=10, remaining_quantity=10, reference_price=5,
        reserved_notional=50, reserved_planned_risk=2, created_at=AT,
    ))
    item = project_portfolio_admission_records(
        (("portfolio_decision", "decision-1", "DU1", decision),
         ("portfolio_reservation", "reservation-1", "DU1",
          {"event": "reservation_created", **reservation,
           "correlation_id": "corr", "causation_id": "decision-1"})),
        run_id=RUN, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=ZERO, first_sequence=1,
        source_cursor="portfolio-admission-1")
    sealed = dict(_sealed_families(item))
    assert len(sealed["trading_event_v1"]) == 2
    assert len(sealed["trading_portfolio_decision_v1"]) == 1
    assert len(sealed["trading_portfolio_decision_reason_v1"]) == 1
    assert len(sealed["trading_portfolio_reservation_event_v1"]) == 1
    with pytest.raises(ValueError, match="unmodeled staged records"):
        project_portfolio_admission_records(
            (("other", "x", "DU1", {}),), run_id=RUN,
            run_month=date(2026, 8, 1), attempt_id=ATTEMPT, batch_id=BATCH,
            prior_batch_id=ZERO, first_sequence=1, source_cursor="x")
