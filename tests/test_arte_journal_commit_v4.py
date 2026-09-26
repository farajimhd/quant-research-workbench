"""Strategy 1 commit family authority stays tabular and exact."""
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.trading_runtime.arte_journal_commit_v4 import (
    load_verified_commit_v4, load_verified_v4_prefix, prepare_commit_v4,
    publish_base_typed_batch_v4, publish_broker_acknowledgement_batch_v4,
    publish_terminal_typed_batch_v4,
    verify_commit_v4,
)
from src.trading_runtime.arte_journal_schema import (
    TABLES, V4_COMMIT_TABLES, fixed_backtest_v2_contracts,
)
from src.trading_runtime.arte_journal_writer import _sealed_families
from src.trading_runtime.arte_journal_writer import ArteJournalWriter
from src.trading_runtime import arte_journal_writer as writer_module
from src.trading_runtime.arte_journal_writer import typed_row
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
from src.trading_runtime.arte_journal_projection import commission_revision_batch
from src.trading_runtime.arte_broker_acknowledgement_v4 import (
    ACKNOWLEDGEMENT, broker_acknowledgement_batch_v4,
)
from src.trading_runtime.arte_protection_change_v4 import protection_change_batch_v4
from src.backend.backtest_protection_change_v3 import TABLES as PROTECTION_CHANGE_TABLES
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.arte_journal_reader import load_typed_event_page
from src.backend.backtest_typed_activity import load_fixed_typed_activity_page
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.replay_run_service import ReplayRunController, RunMode
from src.trading_runtime.domain import CommissionEvent
from tests.test_arte_journal_writer import MemoryClient, batch, captured


def test_v4_preflight_audits_one_exact_storage_union(monkeypatch):
    scans = []
    permissions = []
    monkeypatch.setattr(writer_module, "storage_preflight",
                        lambda client, *, tables: scans.append(tuple(tables)))
    monkeypatch.setattr(writer_module, "journal_permission_preflight",
                        lambda client, **kwargs: permissions.append(kwargs))
    client = object()
    writer_module._v4_preflight(client)
    assert len(scans) == len(permissions) == 1
    names = [table.name for table in scans[0]]
    assert len(names) == len(set(names))
    assert {table.name for table in (*fixed_backtest_v2_contracts(),
                                    *V4_COMMIT_TABLES, ACKNOWLEDGEMENT,
                                    *PROTECTION_CHANGE_TABLES)} <= set(names)
    assert set(permissions[0]["journal_tables"]) <= set(names)


def terminal_batch():
    item = batch()
    event = dict(item.events[0])
    event.update(category="lifecycle", entity_type="run", entity_id=item.run_id,
                 account_id="")
    event.pop("content_hash")
    event = typed_row("trading_event_v1", event)
    transition = typed_row("trading_run_transition_v1", {
        "record_id": event["record_id"], "run_id": item.run_id,
        "event_month": event["event_month"], "batch_id": item.batch_id,
        "account_id": "", "status": "completed", "processed_events": 1,
        "source_event_time": event["event_time"],
    })
    return replace(item, status="completed", events=(event,),
                   run_transitions=(transition,))


def terminal_broker_unit():
    from uuid import uuid4
    from src.backend.backtest_terminal_broker_snapshot_v4 import (
        project_v4_terminal_broker_batch,
    )
    from src.backend.backtest_terminal_snapshot_v2 import (
        ACCOUNT_METRICS, position_set_sha256,
    )

    run_id = "run-v4-terminal-queue"
    at = datetime(2026, 8, 18, 13, 30, tzinfo=timezone.utc)
    source = BacktestMemoryJournal(run_id=run_id)
    account = {name: {"amount": 1000.0, "currency": "USD", "timestamp": 123}
               for name, _ in ACCOUNT_METRICS}
    source.append(
        run_id=run_id, category="snapshot", entity_type="portfolio",
        entity_id="DU1", account_id="DU1", event_time=at,
        payload={**account, "snapshot_id": str(uuid4()),
                 "expected_position_count": 0,
                 "position_set_sha256": position_set_sha256(())})
    source.append(
        run_id=run_id, category="lifecycle", entity_type="run",
        entity_id=run_id, event_time=at,
        payload={"status": "completed", "processed_events": 2})
    unit = project_v4_terminal_broker_batch(
        tuple(source.unfenced_records()), run_id=run_id,
        account_ids=("DU1",), attempt_id=str(uuid4()),
        run_month=date(2026, 8, 1), prior_batch_id=str(UUID(int=0)),
        source_cursor="2026-08-18:34200000")
    source.close()
    capture = replace(captured(), run_id=run_id, state_revision=2,
                      snapshot_at=at)
    return unit, capture


class MemoryV4Dispatch(TypedInsertDispatch):
    """Test transport only; production uses the real Keeper CAS dispatch."""

    def __init__(self):
        super().__init__(object())
        self.reserved = []
        self.sealed = []
        self.compacted = []
        self.timeline = []
        self.ambiguous_tokens = set()

    def assert_next_batch(self, **kwargs):
        self.reserved.append(kwargs)
        self.timeline.append("reserve")

    def execute_typed_insert(self, client, **kwargs):
        token = kwargs["token"]
        if token in self.ambiguous_tokens:
            raise RuntimeError("ambiguous pending Keeper INSERT")
        self.timeline.append("insert:" + kwargs["table"])
        try:
            client.execute(kwargs["sql"])
        except OSError:
            self.ambiguous_tokens.add(token)
            raise

    def seal_verified_operation(self, **kwargs):
        self.sealed.append(kwargs)
        self.timeline.append("seal:" + kwargs["table"])

    def compact_verified_batch(self, **kwargs):
        self.compacted.append(kwargs)
        self.timeline.append("compact")


def attached_v4_client(client=None):
    client = client or MemoryClient()
    client.typed_insert_dispatch = MemoryV4Dispatch()
    client.typed_insert_strict = True
    return client


def test_v4_cold_verified_prefix_reads_bounded_typed_event_page():
    client = attached_v4_client()
    item = batch()
    publish_base_typed_batch_v4(client, item)
    prefix = load_verified_v4_prefix(client, item.run_id)
    page = load_typed_event_page(client, prefix, limit=1)
    assert len(page) == 1
    assert page[0].event["batch_id"] == item.batch_id
    assert load_typed_event_page(client, prefix, after_sequence=1) == ()
    activity = load_fixed_typed_activity_page(client, prefix, limit=1)
    assert activity["verified_prefix_sequence"] == 1
    assert activity["scanned_event_count"] == 1
    assert activity["caught_up_to_prefix"]
    controller = object.__new__(ReplayRunController)
    controller.run_id = item.run_id
    controller.definition = SimpleNamespace(mode=RunMode.BACKTEST)
    controller._journal = BacktestMemoryJournal(run_id=item.run_id)
    controller._journal_publisher = SimpleNamespace(
        writer=SimpleNamespace(journal_profile="backtest_v4"),
        fenced_sequence=prefix.last_sequence,
        _batch_id=prefix.last_batch_id,
    )
    assert controller.fixed_typed_activity_page(client=client)["next_sequence"] == 1


def source():
    item = batch()
    return dict(
        run_id=item.run_id, run_month=item.run_month,
        attempt_id=item.attempt_id, batch_id=item.batch_id,
        prior_batch_id=item.prior_batch_id,
        first_sequence=item.first_sequence, last_sequence=item.last_sequence,
        source_cursor=item.source_cursor, status=item.status,
        sealed_families=_sealed_families(item),
        committed_at=datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc),
    )


def test_v4_commit_has_two_narrow_normalized_ssd_tables():
    contracts = {table.name: table for table in (*TABLES, *V4_COMMIT_TABLES)}
    for name in ("trading_commit_v4", "trading_commit_family_v4"):
        ddl = contracts[name].ddl()
        assert "storage_policy = 'live_market_ssd'" in ddl
        assert "PARTITION BY toYYYYMM(run_month)" in ddl
        assert not any(word in ddl for word in ("JSON", "Array(", "Map(", "Blob"))
    commit, families = prepare_commit_v4(**source())
    assert set(commit) == {name for name, _ in contracts["trading_commit_v4"].columns}
    assert set(families[0]) == {
        name for name, _ in contracts["trading_commit_family_v4"].columns}
    assert commit["event_count"] == 1
    assert commit["family_count"] == 1
    assert families[0]["family_name"] == "trading_event_v1"
    assert prepare_commit_v4(**source()) == (commit, families)


def test_v4_commit_rejects_missing_duplicate_or_foreign_detail_family():
    options = source()
    options["sealed_families"] = ()
    with pytest.raises(ValueError, match="event family"):
        prepare_commit_v4(**options)
    options = source()
    options["sealed_families"] = options["sealed_families"] * 2
    with pytest.raises(ValueError, match="duplicate"):
        prepare_commit_v4(**options)
    options = source()
    name, rows = options["sealed_families"][0]
    options["sealed_families"] = ((name, ({**rows[0], "run_id": "other"},)),)
    with pytest.raises(ValueError, match="batch authority"):
        prepare_commit_v4(**options)
    options = source()
    options["last_sequence"] = 2
    with pytest.raises(ValueError, match="sequence span"):
        prepare_commit_v4(**options)
    options = source()
    name, rows = options["sealed_families"][0]
    options["last_sequence"] = 2
    options["sealed_families"] = ((name, (
        rows[0], {**rows[0], "content_hash": "0" * 64})),)
    with pytest.raises(ValueError, match="repeated a typed row identity"):
        prepare_commit_v4(**options)


def test_v4_readback_requires_exact_family_set_and_detail_hashes():
    options = source()
    commit, families = prepare_commit_v4(**options)
    event = options["sealed_families"][0][1][0]
    details = {"trading_event_v1": [(event["record_id"], event["content_hash"])]}
    verify_commit_v4(commit, families, details)
    with pytest.raises(ValueError, match="detail identities"):
        verify_commit_v4(commit, families,
                         {"trading_event_v1": [(event["record_id"], "0" * 64)]})
    with pytest.raises(ValueError, match="scalar content"):
        verify_commit_v4({**commit, "family_set_hash": "0" * 64}, families, details)
    with pytest.raises(ValueError, match="scalar content"):
        verify_commit_v4({**commit, "source_cursor": "changed"}, families, details)
    with pytest.raises(ValueError, match="normalized families"):
        verify_commit_v4(commit, families, {})
    with pytest.raises(ValueError, match="count or sequence"):
        verify_commit_v4({**commit, "event_count": 2}, families, details)


def test_v4_cold_readback_recomputes_each_typed_row_hash():
    options = source()
    commit, families = prepare_commit_v4(**options)
    event = dict(options["sealed_families"][0][1][0])
    for column, precision in (("event_time", 9), ("recorded_at", 6)):
        parsed = datetime.fromisoformat(event[column]).astimezone(timezone.utc)
        event[column] = (parsed.strftime("%Y-%m-%d %H:%M:%S.%f")
                         + ("000" if precision == 9 else ""))
    client = MemoryClient()
    client.tables = {
        "trading_commit_v4": [dict(commit)],
        "trading_commit_family_v4": [dict(families[0])],
        "trading_event_v1": [event],
    }
    assert load_verified_commit_v4(
        client, run_id=commit["run_id"], batch_id=commit["batch_id"]
    ) == (commit, families)
    assert all("FORMAT JSONEachRow" in query for query in client.selects)
    client.tables["trading_event_v1"][0]["entity_id"] = "tampered"
    with pytest.raises(RuntimeError, match="row hash"):
        load_verified_commit_v4(
            client, run_id=commit["run_id"], batch_id=commit["batch_id"])
    client.tables["trading_event_v1"][0]["entity_id"] = (
        options["sealed_families"][0][1][0]["entity_id"])
    client.tables["trading_commit_v4"][0]["source_cursor"] = "changed"
    with pytest.raises(RuntimeError, match="family seal"):
        load_verified_commit_v4(
            client, run_id=commit["run_id"], batch_id=commit["batch_id"])


def test_v4_publication_is_detail_first_commit_last_and_idempotent():
    client = attached_v4_client()
    item = batch()
    assert publish_base_typed_batch_v4(client, item) == item.batch_id
    assert client.inserts == ["trading_event_v1", "trading_commit_family_v4",
                              "trading_commit_v4"]
    assert client.typed_insert_dispatch.timeline == [
        "reserve", "insert:trading_event_v1",
        "insert:trading_commit_family_v4", "insert:trading_commit_v4",
        "seal:trading_event_v1", "seal:trading_commit_family_v4",
        "seal:trading_commit_v4", "compact"]
    count = len(client.inserts)
    assert publish_base_typed_batch_v4(client, item) == item.batch_id
    assert len(client.inserts) == count
    client.tables["trading_event_v1"][0]["entity_id"] = "tampered"
    with pytest.raises(RuntimeError, match="row hash"):
        publish_base_typed_batch_v4(client, item)


def test_v4_broker_acknowledgement_is_fenced_and_cold_verified():
    at = datetime(2026, 8, 18, 8, 0, 31, tzinfo=timezone.utc)
    source = JournalRecord(
        str(UUID(int=181)), "run-ack", 1, at, at,
        "broker", "order_acknowledgement", "1001", "DU1",
        {"order_id": "1001", "order_status": "Submitted",
         "local_order_id": "coid-1", "order_group_id": "group-1",
         "decision_to_submit_ms": 1.25, "ticker": "AAA",
         "action": "enter_long", "intent_id": "intent-1",
         "correlation_id": "correlation-1", "causation_id": "causation-1",
         "strategy_id": "early-squeeze-strategy", "strategy_revision": 1})
    unit = broker_acknowledgement_batch_v4(
        source, run_month=date(2026, 8, 1), attempt_id=str(UUID(int=182)),
        batch_id=str(UUID(int=183)), prior_batch_id=str(UUID(int=0)),
        source_cursor="2026-08-18:31000")
    client = attached_v4_client()
    assert publish_broker_acknowledgement_batch_v4(
        client, unit.base, acknowledgement=unit.acknowledgement) == unit.base.batch_id
    assert client.inserts == ["trading_event_v1", ACKNOWLEDGEMENT.name,
                              "trading_commit_family_v4", "trading_commit_family_v4",
                              "trading_commit_v4"]
    verified, families = load_verified_commit_v4(
        client, run_id=source.run_id, batch_id=unit.base.batch_id)
    assert verified["family_count"] == 2
    assert {row["family_name"] for row in families} == {
        "trading_event_v1", ACKNOWLEDGEMENT.name}
    client.tables[ACKNOWLEDGEMENT.name][0]["order_status"] = "Inactive"
    with pytest.raises(RuntimeError, match="row hash"):
        load_verified_commit_v4(
            client, run_id=source.run_id, batch_id=unit.base.batch_id)


def test_v4_broker_acknowledgement_uses_nonblocking_writer_lane(monkeypatch):
    at = datetime(2026, 8, 18, 8, 0, 31, tzinfo=timezone.utc)
    source = JournalRecord(
        str(UUID(int=191)), "run-ack-writer", 1, at, at,
        "broker", "order_acknowledgement", "1002", "DU1",
        {"order_id": "1002", "order_status": "Submitted",
         "local_order_id": "coid-2", "order_group_id": "group-2",
         "decision_to_submit_ms": None, "ticker": "AAA",
         "action": "enter_long", "intent_id": "intent-2",
         "correlation_id": "correlation-2", "causation_id": "causation-2",
         "strategy_id": "early-squeeze-strategy", "strategy_revision": 1})
    unit = broker_acknowledgement_batch_v4(
        source, run_month=date(2026, 8, 1), attempt_id=str(UUID(int=192)),
        batch_id=str(UUID(int=193)), prior_batch_id=str(UUID(int=0)),
        source_cursor="2026-08-18:31000")
    client = attached_v4_client()
    monkeypatch.setattr(writer_module, "storage_preflight",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity",
                        lambda _client, _run: {
                            "mode": "backtest", "account_ids": ("DU1",)})
    writer = ArteJournalWriter(
        client, run_id=source.run_id, journal_profile="backtest_v4",
        coalesce_batches=False)
    try:
        receipt = writer.submit_broker_acknowledgement_v4(unit)
        assert receipt.result(timeout=5) == unit.base.batch_id
        assert load_verified_v4_prefix(client, source.run_id).last_sequence == 1
    finally:
        writer.close()


def test_v4_protection_change_fences_numbered_children_and_cold_readback():
    from src.trading_runtime.arte_journal_commit_v4 import (
        publish_protection_change_batch_v4,
    )

    at = datetime(2026, 8, 18, 8, 0, 31, tzinfo=timezone.utc)
    record = JournalRecord(
        str(UUID(int=194)), "run-protection", 1, at, at,
        "protection", "protection_change", "broker-1", "DU1",
        {"schema_version": 1, "order_group_id": "group-1",
         "entry_order_ids": ["entry-1", "entry-2"],
         "order_id": "broker-1", "client_order_id": "client-1",
         "kind": "stop", "phase": "effective", "price": 5.75,
         "active": True, "ticker": "AAA", "source_intent_id": "intent-1",
         "strategy_id": "early-squeeze-strategy", "strategy_revision": 1,
         "action": "enter_long", "intent_id": "intent-1",
         "correlation_id": "correlation-1", "causation_id": "causation-1"})
    unit = protection_change_batch_v4(
        record, run_month=date(2026, 8, 1), attempt_id=str(UUID(int=195)),
        batch_id=str(UUID(int=196)), prior_batch_id=str(UUID(int=0)),
        source_cursor="2026-08-18:31000")
    client = attached_v4_client()
    assert publish_protection_change_batch_v4(
        client, unit.base, change=unit.change,
        entry_orders=unit.entry_orders) == unit.base.batch_id
    assert client.inserts[:3] == ["trading_event_v1",
                                 PROTECTION_CHANGE_TABLES[0].name,
                                 PROTECTION_CHANGE_TABLES[1].name]
    verified, families = load_verified_commit_v4(
        client, run_id=record.run_id, batch_id=unit.base.batch_id)
    assert verified["family_count"] == 3
    assert {row["family_name"] for row in families} == {
        "trading_event_v1", *(table.name for table in PROTECTION_CHANGE_TABLES)}
    client.tables[PROTECTION_CHANGE_TABLES[1].name][0]["entry_order_id"] = "tampered"
    with pytest.raises(RuntimeError, match="row hash"):
        load_verified_commit_v4(
            client, run_id=record.run_id, batch_id=unit.base.batch_id)


def test_v4_terminal_is_lifecycle_last_and_anchors_all_accounts(monkeypatch):
    from src.trading_runtime import arte_backtest_snapshot_anchor as anchors

    client = attached_v4_client()
    unit, capture = terminal_broker_unit()
    item = unit.base
    monkeypatch.setattr(writer_module, "load_typed_run_context",
                        lambda _client, _run: {
                            "mode": "backtest", "account_ids": ("DU1",)})
    anchored = []
    monkeypatch.setattr(anchors, "publish_terminal_backtest_snapshots",
                        lambda _client, prefix, captures: anchored.append(
                            (prefix, captures)))
    prefix = publish_terminal_typed_batch_v4(
        client, item, captures=(capture,),
        broker_snapshots=unit.broker_snapshots)
    assert prefix.status == "completed" and prefix.last_batch_id == item.batch_id
    assert len(anchored) == 1 and anchored[0][1] == (capture,)
    assert client.inserts[-1] == "trading_commit_v4"
    assert publish_terminal_typed_batch_v4(
        client, item, captures=(capture,),
        broker_snapshots=unit.broker_snapshots) == prefix
    assert len(client.tables["trading_commit_v4"]) == 1
    with pytest.raises(ValueError, match="running event batch"):
        publish_base_typed_batch_v4(client, item)


def test_v4_terminal_broker_snapshots_share_commit_and_cold_readback(monkeypatch):
    from uuid import uuid4
    from src.backend.backtest_terminal_broker_snapshot_v4 import (
        project_v4_terminal_broker_batch,
    )
    from src.backend.backtest_terminal_snapshot_v2 import (
        ACCOUNT_METRICS, position_set_sha256, project_position_scalars,
    )
    from src.trading_runtime import arte_backtest_snapshot_anchor as anchors

    run_id = "run-v4-broker-snapshot"
    at = datetime(2026, 8, 18, 13, 30, tzinfo=timezone.utc)
    journal = BacktestMemoryJournal(run_id=run_id)
    account = {name: {"amount": 1000.0, "currency": "USD", "timestamp": 123}
               for name, _ in ACCOUNT_METRICS}
    position = {
        "acctId": "DU1", "conid": 42, "contractDesc": "AAA",
        "currency": "USD", "assetClass": "STK", "position": 3.0,
        "mktPrice": 1.0000000000000002, "mktValue": 3.0,
        "avgCost": 0.8, "avgPrice": 0.8,
        "realizedPnl": -0.0, "unrealizedPnl": 0.6,
    }
    snapshot_id = str(uuid4())
    journal.append(
        run_id=run_id, category="snapshot", entity_type="portfolio",
        entity_id="DU1", account_id="DU1", event_time=at,
        payload={**account, "snapshot_id": snapshot_id,
                 "expected_position_count": 1,
                 "position_set_sha256": position_set_sha256((
                     project_position_scalars(position, account_id="DU1"),))})
    journal.append(
        run_id=run_id, category="snapshot", entity_type="position",
        entity_id="42", account_id="DU1", event_time=at,
        payload={**position, "parent_snapshot_id": snapshot_id, "ordinal": 0})
    journal.append(
        run_id=run_id, category="lifecycle", entity_type="run",
        entity_id=run_id, event_time=at,
        payload={"status": "completed", "processed_events": 3})
    unit = project_v4_terminal_broker_batch(
        tuple(journal.unfenced_records()), run_id=run_id,
        account_ids=("DU1",), attempt_id=str(uuid4()),
        run_month=date(2026, 8, 1), prior_batch_id=str(UUID(int=0)),
        source_cursor="2026-08-18:34200000")
    client = attached_v4_client()
    monkeypatch.setattr(writer_module, "load_typed_run_context",
                        lambda _client, _run: {
                            "mode": "backtest", "account_ids": ("DU1",)})
    monkeypatch.setattr(anchors, "publish_terminal_backtest_snapshots",
                        lambda *_args: None)
    capture = replace(captured(), run_id=run_id, state_revision=3,
                      snapshot_at=at)
    prefix = publish_terminal_typed_batch_v4(
        client, unit.base, captures=(capture,),
        broker_snapshots=unit.broker_snapshots)
    assert prefix.last_sequence == 3
    assert {row["family_name"] for row in client.tables["trading_commit_family_v4"]} == {
        "trading_event_v1", "trading_run_transition_v1",
        "trading_backtest_account_snapshot_v2",
        "trading_backtest_position_snapshot_v2"}
    assert len(client.tables["trading_backtest_account_snapshot_v2"]) == 1
    assert client.tables["trading_backtest_position_snapshot_v2"][0][
        "market_price"] == 1.0000000000000002
    assert load_verified_commit_v4(
        client, run_id=run_id, batch_id=unit.base.batch_id)[0]["status"] == "completed"
    assert publish_terminal_typed_batch_v4(
        client, unit.base, captures=(capture,),
        broker_snapshots=unit.broker_snapshots) == prefix
    assert len(client.tables["trading_backtest_account_snapshot_v2"]) == 1
    assert len(client.tables["trading_backtest_position_snapshot_v2"]) == 1
    client.tables["trading_backtest_position_snapshot_v2"][0]["market_price"] = 1.0
    with pytest.raises(RuntimeError, match="row hash"):
        load_verified_commit_v4(client, run_id=run_id, batch_id=unit.base.batch_id)
    journal.close()


def test_v4_terminal_rejects_missing_capture_before_insert(monkeypatch):
    client = attached_v4_client()
    with pytest.raises(ValueError, match="captures are incomplete"):
        publish_terminal_typed_batch_v4(client, terminal_batch(), captures=())
    assert client.inserts == []


def test_v4_rejects_unfenced_client_before_any_write(monkeypatch):
    client = MemoryClient()
    item = batch()
    with pytest.raises(RuntimeError, match="Keeper-fenced insert dispatch"):
        publish_base_typed_batch_v4(client, item)
    assert client.inserts == []
    monkeypatch.setattr(writer_module, "storage_preflight", lambda *_, **__: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight",
                        lambda *_, **__: None)
    with pytest.raises(RuntimeError, match="Keeper-fenced insert dispatch"):
        ArteJournalWriter(client, run_id=item.run_id,
                          journal_profile="backtest_v4", coalesce_batches=False)
    assert client.inserts == []


def test_v4_base_publisher_cannot_write_terminal_without_account_capture():
    client = attached_v4_client()
    with pytest.raises(ValueError, match="running event batch"):
        publish_base_typed_batch_v4(client, replace(batch(), status="completed"))
    assert client.inserts == []


def test_v4_publication_keeps_ambiguous_commit_pending_without_duplicate_rows():
    class InterruptedClient(MemoryClient):
        fail_commit_once = True

        def execute(self, sql):
            if sql.startswith("INSERT INTO arte.trading_commit_v4 ") \
                    and self.fail_commit_once:
                self.fail_commit_once = False
                raise OSError("simulated commit transport failure")
            return super().execute(sql)

    client = attached_v4_client(InterruptedClient())
    item = batch()
    with pytest.raises(OSError, match="transport failure"):
        publish_base_typed_batch_v4(client, item)
    assert client.inserts == ["trading_event_v1", "trading_commit_family_v4"]
    with pytest.raises(RuntimeError, match="ambiguous pending Keeper INSERT"):
        publish_base_typed_batch_v4(client, item)
    assert client.inserts == ["trading_event_v1", "trading_commit_family_v4"]
    assert len(client.tables["trading_event_v1"]) == 1
    assert len(client.tables["trading_commit_family_v4"]) == 1


def test_v4_late_commission_requires_v4_committed_execution_before_insert():
    base = batch()
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    fee = CommissionEvent("execution-1", "DU1", Decimal("1.25"), "USD",
                          source_event_time=at, received_at=at)
    item = commission_revision_batch(
        fee, run_id=base.run_id, run_month=base.run_month,
        attempt_id=base.attempt_id, batch_id=base.batch_id,
        prior_batch_id=base.prior_batch_id, sequence=base.first_sequence,
        source_cursor="fee-1", run_status="running",
        time_authority="observation")
    client = attached_v4_client()
    source_batch = "00000000-0000-0000-0000-000000000099"
    client.tables["trading_execution_v1"] = [{
        "record_id": "00000000-0000-0000-0000-000000000098",
        "batch_id": source_batch, "run_id": item.run_id,
        "account_id": "DU1", "execution_id": "execution-1"}]
    client.tables["trading_commit_v3"] = [{
        "batch_id": source_batch, "run_id": item.run_id}]
    with pytest.raises(RuntimeError, match="requires one committed execution"):
        publish_base_typed_batch_v4(client, item)
    assert client.inserts == []


def test_v4_continuation_requires_exact_sealed_predecessor():
    first = batch()
    next_batch_id = "00000000-0000-0000-0000-000000000024"
    next_record_id = "00000000-0000-0000-0000-000000000025"
    event = typed_row("trading_event_v1", {
        **{key: value for key, value in first.events[0].items()
           if key != "content_hash"},
        "record_id": next_record_id, "batch_id": next_batch_id,
        "sequence": 2,
    })
    continued = replace(
        first, batch_id=next_batch_id, prior_batch_id=first.batch_id,
        first_sequence=2, last_sequence=2, events=(event,))
    client = attached_v4_client()
    with pytest.raises(RuntimeError, match="committed predecessor"):
        publish_base_typed_batch_v4(client, continued)
    assert client.inserts == []

    assert publish_base_typed_batch_v4(client, first) == first.batch_id
    client.tables["trading_commit_v4"][0]["source_cursor"] = "tampered"
    with pytest.raises(RuntimeError, match="seal the contiguous run prefix"):
        publish_base_typed_batch_v4(client, continued)
    assert len(client.tables["trading_event_v1"]) == 1
    client.tables["trading_commit_v4"][0]["source_cursor"] = first.source_cursor
    assert publish_base_typed_batch_v4(client, continued) == next_batch_id
    assert len(client.tables["trading_commit_v4"]) == 2
    prefix = load_verified_v4_prefix(client, first.run_id)
    assert prefix.last_sequence == 2
    assert prefix.last_batch_id == next_batch_id
    assert prefix.batch_ids == (first.batch_id, next_batch_id)
    with pytest.raises(RuntimeError, match="memory bound"):
        load_verified_v4_prefix(client, first.run_id, max_commits=1)

    fork_batch_id = "00000000-0000-0000-0000-000000000026"
    fork_event = typed_row("trading_event_v1", {
        **{key: value for key, value in event.items() if key != "content_hash"},
        "record_id": "00000000-0000-0000-0000-000000000027",
        "batch_id": fork_batch_id,
    })
    fork = replace(continued, batch_id=fork_batch_id, events=(fork_event,))
    with pytest.raises(RuntimeError, match="already has a committed batch"):
        publish_base_typed_batch_v4(client, fork)
    assert len(client.tables["trading_commit_v4"]) == 2
    client.tables["trading_commit_v4"][1]["prior_batch_id"] = str(UUID(int=0))
    with pytest.raises(RuntimeError, match="forked or not contiguous"):
        load_verified_v4_prefix(client, first.run_id)


def test_v4_opt_in_writer_queues_base_batch_and_keeps_live_contract_isolated(monkeypatch):
    client = attached_v4_client()
    observed = []
    monkeypatch.setattr(
        writer_module, "storage_preflight",
        lambda _client, **kwargs: observed.append(kwargs.get("tables")))
    monkeypatch.setattr(
        writer_module, "journal_permission_preflight",
        lambda _client, **kwargs: observed.append((
            kwargs["journal_tables"], kwargs["read_only_tables"])))
    monkeypatch.setattr(
        writer_module, "_verify_run_identity",
        lambda _client, _run_id: {"mode": "backtest", "account_ids": ("DU1",)})
    item = batch()
    journal = ArteJournalWriter(
        client, run_id=item.run_id, journal_profile="backtest_v4",
        coalesce_batches=False)
    try:
        with pytest.raises(RuntimeError, match="explicit family envelope"):
            journal.submit(item)
        assert journal.submit_base_v4(item).result(timeout=5) == item.batch_id
        assert client.inserts == ["trading_event_v1", "trading_commit_family_v4",
                                  "trading_commit_v4"]
    finally:
        journal.close()
    from src.trading_runtime.arte_strategy_one_entry_schema import ENTRY_EVIDENCE

    assert len(observed) == 2
    assert {table.name for table in observed[0]} == {
        table.name for table in (*fixed_backtest_v2_contracts(),
                                 *V4_COMMIT_TABLES, ENTRY_EVIDENCE,
                                 ACKNOWLEDGEMENT, *PROTECTION_CHANGE_TABLES)}
    writable = frozenset(writer_module._v4_family_table(table)
                         for table, _, _, _ in writer_module._FAMILIES) | \
        frozenset(table.name for table in V4_COMMIT_TABLES) | {
            ENTRY_EVIDENCE.name, ACKNOWLEDGEMENT.name,
            *(table.name for table in PROTECTION_CHANGE_TABLES),
            "trading_backtest_account_snapshot_v2",
            "trading_backtest_position_snapshot_v2"}
    assert observed[1] == (
        writable, frozenset(table.name for table in fixed_backtest_v2_contracts()) - writable)


def test_v4_terminal_writer_queue_waits_for_anchor_before_receipt(monkeypatch):
    from threading import Event
    from src.trading_runtime import arte_backtest_snapshot_anchor as anchors

    client = attached_v4_client()
    unit, capture = terminal_broker_unit()
    item = unit.base
    entered, release = Event(), Event()
    monkeypatch.setattr(writer_module, "storage_preflight",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity",
                        lambda _client, _run: {
                            "mode": "backtest", "account_ids": ("DU1",)})
    monkeypatch.setattr(writer_module, "load_typed_run_context",
                        lambda _client, _run: {
                            "mode": "backtest", "account_ids": ("DU1",)})

    def anchors_after_commit(_client, prefix, captures):
        assert client.tables["trading_commit_v4"][0]["status"] == "completed"
        entered.set()
        assert release.wait(5)
        return ("anchored",)

    monkeypatch.setattr(anchors, "publish_terminal_backtest_snapshots",
                        anchors_after_commit)
    journal = ArteJournalWriter(
        client, run_id=item.run_id, journal_profile="backtest_v4",
        coalesce_batches=False)
    try:
        with pytest.raises(ValueError, match="typed broker snapshot"):
            journal.submit_terminal_backtest(item, (capture,))
        receipt = journal.submit_terminal_backtest(
            item, (capture,), unit.broker_snapshots)
        assert entered.wait(5)
        assert not receipt.done()
        release.set()
        assert receipt.result(timeout=5) == item.batch_id
        assert journal.metrics()["committed_units"] == 1
    finally:
        release.set()
        journal.close()


def test_v4_strategy_signal_uses_installed_v2_table_and_readback():
    item = batch()
    event = typed_row("trading_event_v1", {
        key: ("strategy_decision" if key == "category" else
              "signal" if key == "entity_type" else value)
        for key, value in item.events[0].items() if key != "content_hash"
    })
    signal = typed_row("trading_strategy_signal_v1", {
        "record_id": event["record_id"], "run_id": item.run_id,
        "event_month": "2026-08-01", "batch_id": item.batch_id,
        "account_id": "DU1", "strategy_id": "strategy-1",
        "strategy_revision": 1, "signal_id": "signal-1",
        "signal_type": "entry", "ticker": "ABCD", "action": "watch",
        "direction": "long", "score": "1", "confidence": "1",
        "reason": "candidate", "working_timeframe": "100ms",
        "invalidation_price": None, "source_signal_count": 0,
        "evidence_node_count": 0, "decision_assignment_id": None,
        "decision_reference_price": None, "decision_status": None,
        "decision_reason_detail": None,
        "source_event_time": event["event_time"],
    })
    item = replace(item, events=(event,), signals=(signal,))
    client = attached_v4_client()
    assert publish_base_typed_batch_v4(client, item) == item.batch_id
    assert "trading_strategy_signal_v1" not in client.inserts
    assert "trading_strategy_signal_v2" in client.inserts
    assert client.tables["trading_commit_family_v4"][-1]["family_name"] == \
        "trading_strategy_signal_v2"
    assert load_verified_commit_v4(
        client, run_id=item.run_id, batch_id=item.batch_id)[0]["event_count"] == 1
