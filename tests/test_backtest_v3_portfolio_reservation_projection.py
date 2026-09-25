from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
import json
import re

import pytest

from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.arte_journal_writer import (
    V3SqueezeBatch, _sealed_families, load_committed_prefix,
    publish_typed_batch, publish_typed_squeeze_batch_v3,
)
from src.trading_runtime import arte_journal_writer as writer_module
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.portfolio import PortfolioReservation
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_projection import project_pending_backtest_v3_prefix
from src.backend.backtest_reservation_reason_v3 import project_reservation_reasons_v3
from src.backend.backtest_squeeze_episode_schema import SQUEEZE_COMMIT_V3
from src.backend.backtest_squeeze_episode_v3 import (
    coalesce_squeeze_units_v3, load_verified_squeeze_v3_prefix,
    seal_squeeze_family_v3,
)
from tests.test_backtest_squeeze_episode_v3 import _FakeColdClient
from tests.test_arte_journal_writer import MemoryClient, RUN, ATTEMPT, BATCH, RECORD, ZERO


AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


def _record(*, event: str = "reservation_created", extras: dict | None = None) -> JournalRecord:
    reservation = PortfolioReservation(
        reservation_id="reservation-1", decision_id="decision-1", intent_id="intent-1",
        account_key="primary", account_id="DU1", strategy_id="strategy-1",
        assignment_id="assignment-1", ticker="AAA", action="enter_long",
        quantity=10, remaining_quantity=10, reference_price=5,
        reserved_notional=50, reserved_planned_risk=2, created_at=AT,
    )
    payload = {"event": event, **asdict(reservation), "correlation_id": "corr",
               "causation_id": "decision-1", **(extras or {})}
    return JournalRecord(RECORD, RUN, 1, AT, AT, "portfolio_management",
                         "portfolio_reservation", "reservation-1", "DU1", payload)


def _project(record: JournalRecord):
    return project_journal_record(
        record, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=ZERO, source_cursor="boundary-1",
        expected_mode="backtest")


def test_reservation_projects_to_exact_existing_normalized_family_and_roundtrips():
    item = _project(_record())
    sealed = dict(_sealed_families(item))
    assert len(sealed["trading_portfolio_reservation_event_v1"]) == 1
    assert sealed["trading_portfolio_reservation_event_v1"][0]["event"] == "reservation_created"
    client = MemoryClient()
    publish_typed_batch(client, item)
    assert client.inserts == ["trading_event_v1", "trading_portfolio_reservation_event_v1",
                              "trading_commit_v1"]
    detail = client.tables["trading_portfolio_reservation_event_v1"][0]
    assert detail["reservation_id"] == "reservation-1"
    assert detail["remaining_quantity"] == "10.000000000000000000"
    assert load_committed_prefix(client, RUN).last_sequence == 1
    detail["reserved_notional"] = "51.000000000000000000"
    with pytest.raises(RuntimeError, match="row content differs"):
        load_committed_prefix(client, RUN)


def test_reservation_rejects_unmodeled_event_or_extra_payload():
    for record in (_record(event="entry_reprice_authorized"),
                   _record(extras={"unmodeled": 1})):
        with pytest.raises(ValueError, match="incomplete"):
            _project(record)


def test_reservation_updated_is_exact_existing_typed_row_without_extra_reason():
    item = _project(_record(event="reservation_updated"))
    sealed = dict(_sealed_families(item))
    row = sealed["trading_portfolio_reservation_event_v1"][0]
    assert row["event"] == "reservation_updated"
    assert row["remaining_quantity"] == "10.000000000000000000"
    with pytest.raises(ValueError, match="incomplete"):
        _project(_record(event="reservation_updated", extras={"reason": "extra"}))


@pytest.mark.parametrize("event_name", ["cash_tranche_budget_reserved", "reservation_updated"])
def test_reservation_uses_v3_commit_fence_without_squeeze_child(monkeypatch, event_name):
    class V3Client(MemoryClient):
        def execute(self, sql):
            if "groupArray((toString(record_id),toString(content_hash)))" in sql:
                batch_id = re.search(r"batch_id=toUUID\('([0-9a-f-]+)'\)", sql).group(1)
                names = re.findall(r"FROM arte\.([a-z0-9_]+) WHERE batch_id=.*?\) AS ([a-z0-9_]+)", sql)
                return json.dumps({alias: [[row["record_id"], row["content_hash"]]
                    for row in self.tables.get(table, []) if row["batch_id"] == batch_id]
                    for table, alias in names})
            return super().execute(sql)

    client = V3Client()
    item = _project(_record(event=event_name))
    monkeypatch.setattr(writer_module, "_v3_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run: {
        "mode": "backtest"})
    publish_typed_squeeze_batch_v3(client, V3SqueezeBatch(item, ()))
    assert client.inserts[-1] == "trading_commit_v3"
    assert client.tables["trading_commit_v3"][0]["portfolio_reservation_event_count"] == 1
    assert client.tables["trading_commit_v3"][0]["backtest_squeeze_episode_count"] == 0
    fence = client.tables["trading_commit_v3"][0]
    writer_module._verify_recovery_chunk(client, [fence], journal_profile="backtest_v3")
    client.tables["trading_portfolio_reservation_event_v1"][0]["quantity"] = "11.000000000000000000"
    with pytest.raises(RuntimeError, match="row content differs"):
        writer_module._verify_recovery_chunk(client, [fence], journal_profile="backtest_v3")


def test_actual_v3_pending_prefix_routes_reservation_to_existing_typed_child():
    journal = BacktestMemoryJournal(run_id=RUN)
    source = _record()
    journal.append(run_id=RUN, category=source.category, entity_type=source.entity_type,
                   entity_id=source.entity_id, account_id=source.account_id,
                   event_time=source.event_time, payload=source.payload)
    units = project_pending_backtest_v3_prefix(
        journal, attempt_id=ATTEMPT, run_month=date(2026, 8, 1),
        prior_sequence=0, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    assert len(units) == 1
    assert len(units[0].base.portfolio_reservation_events) == 1
    assert units[0].episodes == ()


@pytest.mark.parametrize("event_name,extras,expected_reasons", [
    ("reservation_released", {"reason": "cancelled"}, ["cancelled"]),
    ("entry_reprice_authorized", {"price": 5,
                                  "reasons": ["cash", "risk"]}, ["cash", "risk"]),
])
def test_v3_pending_prefix_preserves_reasoned_reservation_events(
    event_name, extras, expected_reasons,
):
    journal = BacktestMemoryJournal(run_id=RUN)
    source = _record(event=event_name, extras=extras)
    journal.append(run_id=RUN, category=source.category, entity_type=source.entity_type,
                   entity_id=source.entity_id, account_id=source.account_id,
                   event_time=source.event_time, payload=source.payload)
    unit, = project_pending_backtest_v3_prefix(
        journal, attempt_id=ATTEMPT, run_month=date(2026, 8, 1),
        prior_sequence=0, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    assert unit.base.portfolio_reservation_events[0]["event"] == event_name
    assert [row["reason"] for row in unit.reservation_reasons] == expected_reasons
    assert [row["ordinal"] for row in unit.reservation_reasons] == list(
        range(len(expected_reasons)))

    class V3Client(MemoryClient):
        def execute(self, sql):
            if "groupArray((toString(record_id),toString(content_hash)))" in sql:
                batch_id = re.search(r"batch_id=toUUID\('([0-9a-f-]+)'\)", sql).group(1)
                names = re.findall(r"FROM arte\.([a-z0-9_]+) WHERE batch_id=.*?\) AS ([a-z0-9_]+)", sql)
                return json.dumps({alias: [[row["record_id"], row["content_hash"]]
                    for row in self.tables.get(table, []) if row["batch_id"] == batch_id]
                    for table, alias in names})
            return super().execute(sql)

    client = V3Client()
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(writer_module, "_v3_preflight", lambda _client: None)
        patcher.setattr(writer_module, "_verify_run_identity", lambda _client, _run: {
            "mode": "backtest"})
        publish_typed_squeeze_batch_v3(client, unit)
    assert client.tables["trading_commit_v3"][0]["portfolio_reservation_reason_count"] == len(
        expected_reasons)
    assert [row["reason"] for row in client.tables[
        "trading_portfolio_reservation_reason_v1"]] == expected_reasons
    assert client.inserts[-1] == "trading_commit_v3"


def test_v3_cold_reader_verifies_reservation_reason_fence(monkeypatch):
    import src.backend.backtest_squeeze_episode_v3 as module

    record = _record(event="reservation_released", extras={"reason": "cancelled"})
    base = _project(_record())
    parent_event = dict(base.events[0])
    parent_reservation = {**base.portfolio_reservation_events[0],
                          "event": "reservation_released"}
    reasons = list(project_reservation_reasons_v3(record, batch_id=BATCH))
    commit_base = {name: "" for name, _ in SQUEEZE_COMMIT_V3.columns
                   if name not in {
                       "backtest_squeeze_episode_count", "backtest_squeeze_episode_hash",
                       "portfolio_reservation_reason_count",
                       "portfolio_reservation_reason_hash",
                       "portfolio_reconciliation_difference_count",
                           "portfolio_reconciliation_difference_hash",
                           "portfolio_control_count", "portfolio_control_hash",
                           "trade_proposal_child_count", "trade_proposal_child_hash",
                           "broker_short_order_skip_count", "broker_short_order_skip_hash",
                           "broker_reply_policy_event_count", "broker_reply_policy_event_hash",
                           "broker_reply_policy_message_count", "broker_reply_policy_message_hash",
                           "entry_reprice_deferred_count", "entry_reprice_deferred_hash"}}
    commit_base.update(run_id=RUN, batch_id=BATCH, prior_batch_id=ZERO,
                       first_sequence=1, last_sequence=1, event_count=1,
                       status="completed", source_cursor="bar:1")
    commit = seal_squeeze_family_v3(
        commit_base, [], [parent_event], reservation_reasons=reasons,
        parent_reservations=[parent_reservation])
    monkeypatch.setattr(module, "storage_preflight", lambda client, *, tables: None)
    monkeypatch.setattr(module, "_verify_recovery_chunk",
                        lambda client, commits, *, journal_profile: None)
    client = _FakeColdClient(commit, [], [], reservation_events=[parent_event],
                             reservation_parents=[parent_reservation], reasons=reasons)
    prefix = load_verified_squeeze_v3_prefix(
        client, RUN, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    assert prefix is not None and prefix.last_sequence == 1
    client.reasons = []
    with pytest.raises(ValueError):
        load_verified_squeeze_v3_prefix(
            client, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)


def test_v3_coalescing_rekeys_ordered_reservation_reasons():
    journal = BacktestMemoryJournal(run_id=RUN)
    for event_name, extras in (
        ("reservation_released", {"reason": "cancelled"}),
        ("entry_reprice_authorized", {"price": 5, "reasons": ["risk"]}),
    ):
        source = _record(event=event_name, extras=extras)
        journal.append(run_id=RUN, category=source.category,
                       entity_type=source.entity_type, entity_id=source.entity_id,
                       account_id=source.account_id, event_time=source.event_time,
                       payload=source.payload)
    units = project_pending_backtest_v3_prefix(
        journal, attempt_id=ATTEMPT, run_month=date(2026, 8, 1),
        prior_sequence=0, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    merged = coalesce_squeeze_units_v3(units)
    assert len(merged.base.events) == 2
    assert len(merged.reservation_reasons) == 2
    assert all(row["batch_id"] == merged.base.batch_id
               for row in merged.reservation_reasons)
    families = dict(_sealed_families(merged.base))
    parent_events = families["trading_event_v1"]
    parent_reservations = families["trading_portfolio_reservation_event_v1"]
    commit_base = {name: "" for name, _ in SQUEEZE_COMMIT_V3.columns
                   if name not in {
                       "backtest_squeeze_episode_count", "backtest_squeeze_episode_hash",
                       "portfolio_reservation_reason_count",
                       "portfolio_reservation_reason_hash",
                       "portfolio_reconciliation_difference_count",
                       "portfolio_reconciliation_difference_hash",
                       "portfolio_control_count", "portfolio_control_hash",
                       "trade_proposal_child_count", "trade_proposal_child_hash",
                       "broker_short_order_skip_count", "broker_short_order_skip_hash",
                       "broker_reply_policy_event_count", "broker_reply_policy_event_hash",
                       "broker_reply_policy_message_count", "broker_reply_policy_message_hash",
                       "entry_reprice_deferred_count", "entry_reprice_deferred_hash"}}
    commit_base.update(run_id=RUN, batch_id=merged.base.batch_id)
    seal = seal_squeeze_family_v3(
        commit_base, (), parent_events,
        reservation_reasons=merged.reservation_reasons,
        parent_reservations=parent_reservations)
    assert seal["portfolio_reservation_reason_count"] == 2
