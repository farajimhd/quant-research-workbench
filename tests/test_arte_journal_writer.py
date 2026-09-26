from __future__ import annotations

from dataclasses import asdict, fields, replace
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import re
from threading import Event, Thread
from uuid import UUID

import pytest

from src.trading_runtime import arte_journal_writer as writer_module
from src.trading_runtime.arte_portfolio_snapshot import CapturedPortfolioSnapshot
from src.trading_runtime.arte_assignment_command_projection import assignment_command_batch
from src.trading_runtime.arte_journal_projection import (
    broker_fill_batch, commission_revision_batch, runtime_lifecycle_batch,
    operational_fault_batch,
    account_risk_batch, intent_decision_batch,
)
from src.trading_runtime import arte_journal_projection as projection_module
from src.trading_runtime.domain import CommissionEvent
from src.trading_runtime.ibkr_client import _execution
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.risk_supervisor import AccountRiskState, RiskEvaluation
from src.trading_runtime.arte_journal_writer import (
    ArteJournalWriter, CommittedPrefix, JournalQueueFull, TypedJournalBatch,
    load_committed_prefix,
    load_committed_commission_page, load_committed_execution_page,
    load_committed_run_transition_page,
    load_committed_operational_fault_page,
    load_committed_account_risk_page,
    load_committed_order_command_page, load_committed_order_transition_page,
    load_typed_run_context, publish_typed_batch, publish_typed_run,
    publish_typed_run_context, typed_row,
)


RUN = "live:account:session"
ATTEMPT = "00000000-0000-0000-0000-000000000011"
BATCH = "00000000-0000-0000-0000-000000000012"
RECORD = "00000000-0000-0000-0000-000000000013"
ZERO = "00000000-0000-0000-0000-000000000000"


def test_clickhouse_wire_time_preserves_utc_nanoseconds() -> None:
    assert writer_module._datetime_wire("2026-08-18T04:05:00.123456789-04:00", 9) == (
        "2026-08-18 08:05:00.123456789"
    )
    assert writer_module._datetime_wire("2026-08-18T08:05:00+00:00", 6) == (
        "2026-08-18 08:05:00.000000"
    )
    with pytest.raises(ValueError, match="Submicrosecond"):
        writer_module._datetime_wire("2026-08-18T08:05:00.123456789Z", 6)


def test_typed_hash_uses_stored_utc_representation() -> None:
    source = {key: value for key, value in batch().events[0].items()
              if key != "content_hash"}
    source["event_time"] = "2026-08-18T04:05:00-04:00"
    source["recorded_at"] = "2026-08-18T04:05:01-04:00"
    row = typed_row("trading_event_v1", source)
    utc = {**source, "event_time": "2026-08-18T08:05:00Z",
           "recorded_at": "2026-08-18T08:05:01Z"}
    assert typed_row("trading_event_v1", utc)["content_hash"] == row["content_hash"]
    stored = writer_module._wire_row("trading_event_v1", row)
    assert writer_module._canonical_typed_content(
        "trading_event_v1", source,
    ) == writer_module._canonical_typed_content(
        "trading_event_v1", {key: value for key, value in stored.items()
                             if key != "content_hash"}, stored_utc=True,
    )


def test_typed_float_hash_accepts_integral_json_number_from_clickhouse() -> None:
    from src.trading_runtime.arte_journal_projection import project_signal_evidence_nodes

    node = project_signal_evidence_nodes(
        {"ratio": 1.0}, run_id=RUN, event_month="2026-08-01",
        batch_id=BATCH, parent_record_id=RECORD,
    )[1]
    canonical = writer_module._canonical_typed_content(
        "trading_strategy_signal_evidence_node_v1", node)
    as_stored = {**node, "value_float": 1}
    assert writer_module._canonical_typed_content(
        "trading_strategy_signal_evidence_node_v1", as_stored,
        stored_utc=True) == canonical


def test_generic_evidence_rows_cannot_be_published() -> None:
    from src.trading_runtime.arte_journal_projection import project_signal_evidence_nodes

    nodes = project_signal_evidence_nodes(
        {"ratio": 1.0}, run_id=RUN, event_month="2026-08-01",
        batch_id=BATCH, parent_record_id=RECORD,
    )
    client = MemoryClient()
    with pytest.raises(ValueError, match="retired for new writes"):
        publish_typed_batch(client, replace(batch(), signal_evidence_nodes=nodes))
    assert not client.inserts


@pytest.mark.parametrize("encoded", [
    '{"nested":1}', '  ["unmodelled"]', '\ufeff{"nested":1}',
    ' \ufeff ["unmodelled"]',
])
def test_typed_journal_rejects_json_hidden_in_string_column(encoded: str) -> None:
    source = {key: value for key, value in batch().events[0].items()
              if key != "content_hash"}
    source["entity_id"] = encoded
    with pytest.raises(ValueError, match="normalized typed rows"):
        typed_row("trading_event_v1", source)


def test_event_partition_must_match_utc_event_month() -> None:
    source = dict(batch().events[0])
    source["event_month"] = "2026-07-01"
    source.pop("content_hash")
    item = TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                             1, 1, "bucket-1", "running", (source,))
    with pytest.raises(ValueError, match="partition differs"):
        publish_typed_batch(MemoryClient(), item)


def test_typed_journal_client_requires_a_separate_complete_identity(monkeypatch) -> None:
    for key in ("TRADING_JOURNAL_CLICKHOUSE_URL", "TRADING_JOURNAL_CLICKHOUSE_USER",
                "TRADING_JOURNAL_CLICKHOUSE_PASSWORD", "BACKTEST_CLICKHOUSE_USER"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="dedicated ClickHouse"):
        writer_module.journal_client_from_env()
    monkeypatch.setenv("TRADING_JOURNAL_CLICKHOUSE_URL", "http://localhost:8123")
    monkeypatch.setenv("TRADING_JOURNAL_CLICKHOUSE_USER", "journal-only")
    monkeypatch.setenv("TRADING_JOURNAL_CLICKHOUSE_PASSWORD", "test-only")
    monkeypatch.setenv("BACKTEST_CLICKHOUSE_USER", "journal-only")
    with pytest.raises(ValueError, match="differ from market-data readers"):
        writer_module.journal_client_from_env()
    monkeypatch.setenv("BACKTEST_CLICKHOUSE_USER", "market-reader")
    client = writer_module.journal_client_from_env()
    try:
        assert client.user == "journal-only"
        assert client.persistent
    finally:
        client.close()


def test_v4_client_requires_isolated_runner_identity(monkeypatch) -> None:
    for key in ("BACKTEST_V4_RUNNER_CLICKHOUSE_URL",
                "BACKTEST_V4_RUNNER_CLICKHOUSE_USER",
                "BACKTEST_V4_RUNNER_CLICKHOUSE_PASSWORD",
                "BACKTEST_CLICKHOUSE_USER", "TRADING_JOURNAL_CLICKHOUSE_USER"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError, match="dedicated runner"):
        writer_module.backtest_v4_journal_client_from_env()
    monkeypatch.setenv("BACKTEST_V4_RUNNER_CLICKHOUSE_URL", "http://localhost:8123")
    monkeypatch.setenv("BACKTEST_V4_RUNNER_CLICKHOUSE_USER", "backtest_v4_runner")
    monkeypatch.setenv("BACKTEST_V4_RUNNER_CLICKHOUSE_PASSWORD", "test-only")
    monkeypatch.setenv("BACKTEST_CLICKHOUSE_USER", "backtest_v4_runner")
    with pytest.raises(ValueError, match="differ from market"):
        writer_module.backtest_v4_journal_client_from_env()
    monkeypatch.setenv("BACKTEST_CLICKHOUSE_USER", "market-reader")
    client = writer_module.backtest_v4_journal_client_from_env()
    try:
        assert client.user == "backtest_v4_runner" and client.persistent
    finally:
        client.close()


def batch() -> TypedJournalBatch:
    event = typed_row("trading_event_v1", {
        "run_id": RUN, "event_month": "2026-08-01", "attempt_id": ATTEMPT,
        "batch_id": BATCH, "record_id": RECORD, "sequence": 1,
        "event_time": "2026-08-18T08:05:00+00:00",
        "recorded_at": "2026-08-18T08:05:01+00:00",
        "category": "run_state", "entity_type": "lifecycle", "entity_id": "run-1",
        "account_id": "DU1", "correlation_id": "c1", "causation_id": "k1",
    })
    return TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                             1, 1, "bucket-1", "running", (event,))


def test_opaque_backtest_cursor_is_rejected_before_queue_submission() -> None:
    with pytest.raises(ValueError, match="normalized typed fields"):
        replace(batch(), source_cursor='{"market":{"boundary_ms":100},"frame":{}}')
    with pytest.raises(ValueError, match="normalized typed fields"):
        replace(batch(), source_cursor=' \ufeff {"market":{"boundary_ms":100}}')


def captured() -> CapturedPortfolioSnapshot:
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    return CapturedPortfolioSnapshot(
        RUN, "DU1", 1, at, "primary", "enabled", "synchronized",
        "broker-snapshot-1", at, "", 1000.0, None, None,
        (), (), (), (), (), (),
    )


def run_row() -> dict:
    return {
        "run_id": RUN, "run_month": "2026-08-01", "mode": "backtest",
        "evaluation_interval_ms": 100, "session_date": "2026-08-18",
        "configuration_hash": "a" * 64, "code_hash": "b" * 64,
        "market_plan_token": "certified-build", "started_at": "2026-08-18T08:00:00+00:00",
    }


def run_context() -> dict:
    return {
        "strategy_id": "strategy-1", "strategy_revision": 7,
        "anchor_date": "2026-08-18", "run_plan_id": "plan-1",
        "safety_supervisor_enabled": True, "checkpoint_interval_events": 100,
        "write_progress_checkpoints": True,
    }


def test_intent_decision_reasons_are_typed_and_fenced() -> None:
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    record = JournalRecord(
        RECORD, RUN, 1, at, at, "strategy_decision", "intent_rejection",
        "intent-1:portfolio-rejected", "DU1",
        {"intent_id": "intent-1", "event": "intent_rejected", "action": "wait",
         "reason": "portfolio_rejected", "reason_detail": "Portfolio rejected the entry: cash",
         "rejection_reasons": ["cash", "risk"], "ticker": "ABCD",
         "reference_price": 1.25, "strategy_id": "strategy-1",
         "strategy_revision": 7, "status": "watching"},
    )
    item = intent_decision_batch(
        record, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=ZERO, source_cursor="decision",
    )
    assert item.intent_decisions[0]["reason_count"] == 2
    assert [row["reason"] for row in item.intent_decision_reasons] == ["cash", "risk"]
    client = MemoryClient()
    publish_typed_batch(client, item)
    assert load_committed_prefix(client, RUN).last_sequence == 1
    client.tables["trading_intent_decision_reason_v1"][0]["reason"] = "tampered"
    with pytest.raises(RuntimeError, match="content differs from its hash"):
        load_committed_prefix(client, RUN)
    with pytest.raises(ValueError, match="unmodeled"):
        intent_decision_batch(replace(record, payload={**record.payload, "opaque": {}}),
            run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
            batch_id=BATCH, prior_batch_id=ZERO, source_cursor="decision")


def test_execution_rejection_and_portfolio_deferral_have_distinct_contracts() -> None:
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    common = dict(record_id=RECORD, run_id=RUN, sequence=1,
                  event_time=at, recorded_at=at, category="strategy_decision",
                  account_id="DU1")
    immediate = JournalRecord(**common, entity_type="intent_rejection",
        entity_id="intent-1", payload={"intent_id": "intent-1", "action": "wait",
        "reason": "execution_stop_already_triggered", "reason_detail": "stop crossed",
        "ticker": "ABCD"})
    item = intent_decision_batch(immediate, run_month=date(2026, 8, 1),
        attempt_id=ATTEMPT, batch_id=BATCH, prior_batch_id=ZERO,
        source_cursor="decision")
    assert item.intent_decisions[0]["reference_price"] is None
    assert item.intent_decision_reasons == ()
    deferred = JournalRecord(**common, entity_type="intent_deferral",
        entity_id="intent-1:portfolio-deferred", payload={
            "intent_id": "intent-1", "event": "intent_deferred", "action": "wait",
            "reason": "portfolio_deferred", "reason_detail": "capacity pending",
            "rejection_reasons": ["capacity"], "ticker": "ABCD",
            "reference_price": 1.25, "strategy_id": "strategy-1",
            "strategy_revision": 7, "status": "watching"})
    item = intent_decision_batch(deferred, run_month=date(2026, 8, 1),
        attempt_id=ATTEMPT, batch_id=BATCH, prior_batch_id=ZERO,
        source_cursor="decision")
    assert item.intent_decisions[0]["decision_kind"] == "intent_deferral"
    assert item.intent_decision_reasons[0]["reason"] == "capacity"


def test_runtime_lifecycle_is_normalized_and_fence_verified() -> None:
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    common = dict(record_id=RECORD, run_id=RUN, sequence=1,
                  event_time=at, recorded_at=at, category="lifecycle",
                  entity_type="run", entity_id=RUN, account_id="")
    record = JournalRecord(**common, payload={"status": "running", "config": run_context()})
    item = runtime_lifecycle_batch(
        record, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=ZERO, source_cursor="start",
        expected_config=run_context(),
    )
    client = MemoryClient()
    publish_typed_batch(client, item)
    prefix = load_committed_prefix(client, RUN)
    assert prefix is not None
    page = load_committed_run_transition_page(client, prefix)
    assert len(page) == 1 and page[0]["status"] == "running"
    assert page[0]["processed_events"] is None
    assert load_committed_run_transition_page(client, prefix, after_sequence=1) == ()
    for name in ("trading_event_v1", "trading_run_transition_v1"):
        clone = dict(client.tables[name][0])
        clone["batch_id"] = "00000000-0000-0000-0000-000000000099"
        client.tables[name].append(clone)
    assert len(load_committed_run_transition_page(client, prefix)) == 1
    finished = JournalRecord(**common, payload={"status": "completed", "processed_events": 42})
    terminal = runtime_lifecycle_batch(
        finished, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=ZERO, source_cursor="finish",
    )
    assert terminal.run_transitions[0]["processed_events"] == 42
    with pytest.raises(ValueError, match="unmodeled lifecycle evidence"):
        runtime_lifecycle_batch(JournalRecord(**common, payload={
            "status": "completed", "processed_events": 42, "state_json": "{}",
        }), run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
            batch_id=BATCH, prior_batch_id=ZERO, source_cursor="finish")
    with pytest.raises(ValueError, match="differs from the typed run context"):
        runtime_lifecycle_batch(record, run_month=date(2026, 8, 1),
            attempt_id=ATTEMPT, batch_id=BATCH, prior_batch_id=ZERO,
            source_cursor="start", expected_config={**run_context(), "strategy_revision": 99})
    client.tables["trading_run_transition_v1"][0]["status"] = "completed"
    with pytest.raises(RuntimeError, match="row content differs from its hash"):
        load_committed_prefix(client, RUN)


def test_broker_and_risk_faults_share_one_normalized_fenced_family() -> None:
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    client = MemoryClient()
    next_batch = "00000000-0000-0000-0000-000000000019"
    for sequence, batch_id, prior_id, category, entity_type, status in (
        (1, BATCH, ZERO, "broker", "connection_state", "disconnected"),
        (2, next_batch, BATCH, "risk", "risk_snapshot", "stale"),
    ):
        record = JournalRecord(
            record_id=f"00000000-0000-0000-0000-{sequence + 20:012d}",
            run_id=RUN, sequence=sequence, event_time=at, recorded_at=at,
            category=category, entity_type=entity_type, entity_id=RUN,
            account_id="", payload={"status": status, "error": "network timeout",
                                    "entries_frozen": True},
        )
        item = operational_fault_batch(
            record, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
            batch_id=batch_id, prior_batch_id=prior_id,
            source_cursor=f"fault-{sequence}",
        )
        publish_typed_batch(client, item)
    prefix = load_committed_prefix(client, RUN)
    assert prefix is not None
    rows = load_committed_operational_fault_page(client, prefix)
    assert [(row["sequence"], row["category"], row["status"])
            for row in rows] == [(1, "broker", "disconnected"), (2, "risk", "stale")]
    assert len(load_committed_operational_fault_page(
        client, prefix, after_sequence=1)) == 1
    for name in ("trading_event_v1", "trading_operational_fault_v1"):
        clone = dict(client.tables[name][0])
        clone["batch_id"] = "00000000-0000-0000-0000-000000000099"
        client.tables[name].append(clone)
    assert len(load_committed_operational_fault_page(client, prefix)) == 2
    with pytest.raises(ValueError, match="unmodeled or inconsistent"):
        operational_fault_batch(JournalRecord(
            record_id=RECORD, run_id=RUN, sequence=3, event_time=at,
            recorded_at=at, category="risk", entity_type="risk_snapshot",
            entity_id=RUN, account_id="", payload={
                "status": "stale", "error": "x", "entries_frozen": True,
                "raw_json": "{}",
            }), run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
            batch_id=str(UUID(int=30)), prior_batch_id=next_batch,
            source_cursor="fault-3")
    client.tables["trading_operational_fault_v1"][0]["entries_frozen"] = 0
    with pytest.raises(RuntimeError, match="row content differs from its hash"):
        load_committed_prefix(client, RUN)


def test_account_risk_metrics_and_reasons_are_fenced_and_recoverable() -> None:
    assert {field.name for field in fields(RiskEvaluation)} == projection_module._RISK_SOURCE_FIELDS
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    metrics = {key: 1.25 for key in (
        "net_liquidation", "available_funds", "buying_power", "gross_exposure",
        "net_exposure", "reserved_notional", "open_risk", "daily_loss",
        "drawdown",
    )}
    metrics["position_count"] = 2.0
    evaluation = RiskEvaluation(
        "DU1", "primary", AccountRiskState.ENTRIES_PAUSED,
        ("broker_disconnected", "daily_loss_warning"), metrics, at,
        protection_required=100.5, protection_coverage=100.5,
        internal_reaction_ms=12.5,
    )
    record = JournalRecord(RECORD, RUN, 1, at, at, "risk",
        "continuous_risk_state", "DU1", "DU1", asdict(evaluation))
    item = account_risk_batch(record, run_month=date(2026, 8, 1),
        attempt_id=ATTEMPT, batch_id=BATCH, prior_batch_id=ZERO,
        source_cursor="risk-1", expected_mode="live")
    assert item.account_risk_states[0]["position_count"] == 2
    assert item.account_risk_states[0]["daily_loss"] == "1.250000000000000000"
    oversized = replace(item, account_risk_states=({
        **item.account_risk_states[0],
        "net_liquidation": "100000000000000000000.000000000000000000",
    },))
    with pytest.raises(ValueError, match="exceeds decimal width"):
        writer_module._sealed_families(oversized)
    client = MemoryClient()
    publish_typed_batch(client, item)
    prefix = load_committed_prefix(client, RUN)
    assert prefix is not None
    recovered = load_committed_account_risk_page(client, prefix)
    assert len(recovered) == 1
    assert recovered[0]["reasons"] == evaluation.reasons
    assert load_committed_account_risk_page(client, prefix, after_sequence=1) == ()
    disabled = replace(evaluation, state=AccountRiskState.NORMAL)
    disabled_record = JournalRecord(RECORD, RUN, 1, at, at, "risk",
        "continuous_risk_state", "DU1", "DU1", {
            **asdict(disabled), "enforced": False, "mode": "replay",
        })
    disabled_batch = account_risk_batch(disabled_record,
        run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=ZERO, source_cursor="risk-disabled",
        expected_mode="replay")
    assert disabled_batch.account_risk_states[0]["enforced"] == 0
    with pytest.raises(ValueError, match="differs from its run mode"):
        account_risk_batch(disabled_record, run_month=date(2026, 8, 1),
            attempt_id=ATTEMPT, batch_id=BATCH, prior_batch_id=ZERO,
            source_cursor="risk-disabled", expected_mode="live")
    with pytest.raises(ValueError, match="fixed producer contract"):
        account_risk_batch(JournalRecord(RECORD, RUN, 1, at, at, "risk",
            "continuous_risk_state", "DU1", "DU1", {
                **asdict(evaluation), "metrics": {**metrics, "unknown": 1.0},
            }), run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
            batch_id=BATCH, prior_batch_id=ZERO, source_cursor="risk-1",
            expected_mode="live")
    client.tables["trading_account_risk_reason_v1"][0]["reason"] = "tampered"
    with pytest.raises(RuntimeError, match="row content differs from its hash"):
        load_committed_prefix(client, RUN)


def test_runtime_config_and_accounts_require_a_verified_context_fence() -> None:
    client = MemoryClient()
    publish_typed_run(client, run_row())
    with pytest.raises(RuntimeError, match="complete publication fence"):
        load_typed_run_context(client, RUN)
    publish_typed_run_context(client, run_id=RUN, config=run_context(),
                              account_ids=("DU1", "DU2"))
    recovered = load_typed_run_context(client, RUN)
    assert recovered["account_ids"] == ("DU1", "DU2")
    assert recovered["mode"] == run_row()["mode"]
    assert recovered["configuration_hash"] == run_row()["configuration_hash"]
    assert client.inserts[-1] == "trading_run_context_commit_v1"
    before = len(client.inserts)
    publish_typed_run_context(client, run_id=RUN, config=run_context(),
                              account_ids=("DU1", "DU2"))
    assert len(client.inserts) == before
    with pytest.raises(RuntimeError, match="conflicts"):
        publish_typed_run_context(client, run_id=RUN, config=run_context(),
                                  account_ids=("DU2", "DU1"))
    client.tables["trading_run_account_v1"][0]["account_id"] = "tampered"
    with pytest.raises(RuntimeError, match="row content differs"):
        load_typed_run_context(client, RUN)
    client.tables["trading_run_account_v1"][0]["account_id"] = "DU1"
    client.tables["trading_run_v1"][0]["code_hash"] = "c" * 64
    with pytest.raises(RuntimeError, match="differs from its committed fence"):
        load_typed_run_context(client, RUN)
    client.tables["trading_run_v1"][0]["code_hash"] = "b" * 64
    client.tables["trading_run_account_v1"].clear()
    with pytest.raises(RuntimeError, match="missing typed rows"):
        publish_typed_run_context(client, run_id=RUN, config=run_context(),
                                  account_ids=("DU1", "DU2"))


class MemoryClient:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.inserts: list[str] = []
        self.insert_sql: list[str] = []
        self.selects: list[str] = []

    def execute(self, sql: str) -> str:
        if sql.startswith("INSERT INTO arte."):
            name = sql.split("arte.", 1)[1].split(" ", 1)[0]
            self.inserts.append(name)
            self.insert_sql.append(sql)
            self.tables.setdefault(name, []).extend(json.loads(line) for line in sql.split("\n", 1)[1].splitlines())
            return ""
        assert sql.startswith("SELECT ")
        self.selects.append(sql)
        if "groupArray((toString(batch_id),toString(record_id)," in sql:
            ids = set(re.findall(r"toUUID\('([0-9a-f-]+)'\)", sql))
            names = re.findall(r"FROM arte\.([a-z0-9_]+) WHERE batch_id IN", sql)
            return json.dumps({name: [
                [row["batch_id"], row["record_id"], row["content_hash"]]
                for row in self.tables.get(name, []) if row["batch_id"] in ids
            ] for name in names})
        if "groupArray((toString(record_id),toString(content_hash)))" in sql:
            batch_id = sql.split("batch_id=toUUID('", 1)[1].split("'", 1)[0]
            names = re.findall(r"FROM arte\.([a-z0-9_]+) WHERE batch_id=", sql)
            return json.dumps({name: [
                [row["record_id"], row["content_hash"]]
                for row in self.tables.get(name, []) if row["batch_id"] == batch_id
            ] for name in names})
        if "WHERE batch_id IN (" in sql:
            ids = set(re.findall(r"toUUID\('([0-9a-f-]+)'\)", sql))
            name = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
            columns = sql.removeprefix("SELECT ").split(" FROM ", 1)[0].split(",")
            return "\n".join(json.dumps({column: row[column] for column in columns})
                             for row in self.tables.get(name, []) if row["batch_id"] in ids)
        name = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
        columns = sql.removeprefix("SELECT ").split(" FROM ", 1)[0].split(",")
        if "WHERE run_id=" in sql:
            run_id = sql.split("WHERE run_id='", 1)[1].split("'", 1)[0]
            matching = [row for row in self.tables.get(name, []) if row["run_id"] == run_id]
            fault_pair_filter = "AND ((category='broker' AND entity_type='connection_state')" in sql
            command_pair_filter = "OR (category='command' AND entity_type='order')" in sql
            if fault_pair_filter:
                matching = [row for row in matching if
                            (row["category"], row["entity_type"]) in {
                                ("broker", "connection_state"),
                                ("risk", "risk_snapshot"),
                            }]
            if command_pair_filter:
                matching = [row for row in matching if
                            (row["category"], row["entity_type"]) in {
                                ("order_management", "order_command"),
                                ("command", "order"),
                            }]
            for field in ("account_id", "execution_id", "category", "entity_type"):
                if (fault_pair_filter or command_pair_filter) and field in {
                        "category", "entity_type"}:
                    continue
                marker = f"AND {field}='"
                if marker in sql:
                    value = sql.split(marker, 1)[1].split("'", 1)[0]
                    matching = [row for row in matching if row[field] == value]
            for operator, pattern in ((">", r"AND sequence>(\d+)"),
                                      ("<=", r"AND sequence<=(\d+)")):
                found = re.search(pattern, sql)
                if found:
                    bound = int(found.group(1))
                    matching = [row for row in matching if
                                (int(row["sequence"]) > bound if operator == ">"
                                 else int(row["sequence"]) <= bound)]
            if "AND record_id IN (" in sql:
                values = set(re.findall(r"toUUID\('([0-9a-f-]+)'\)", sql))
                matching = [row for row in matching if row["record_id"] in values]
            if "AND parent_record_id IN (" in sql:
                values = set(re.findall(r"toUUID\('([0-9a-f-]+)'\)",
                                        sql.split("AND parent_record_id IN (", 1)[1]))
                matching = [row for row in matching if row["parent_record_id"] in values]
            for field in ("account_id", "intent_id"):
                marker = f"AND {field} IN ("
                if marker in sql:
                    values = set(re.findall(r"'([^']+)'", sql.split(marker, 1)[1].split(")", 1)[0]))
                    matching = [row for row in matching if row[field] in values]
            fence = next((name for name in ("trading_commit_v1", "trading_commit_v2")
                          if f"AND batch_id IN (SELECT batch_id FROM arte.{name} " in sql), None)
            if fence is not None:
                bound = int(re.search(r"AND last_sequence<=(\d+)", sql).group(1))
                committed = {row["batch_id"] for row in self.tables.get(fence, [])
                             if row["run_id"] == run_id and int(row["last_sequence"]) <= bound}
                matching = [row for row in matching if row["batch_id"] in committed]
            if "AND batch_id=toUUID('" in sql:
                value = sql.split("AND batch_id=toUUID('", 1)[1].split("'", 1)[0]
                matching = [row for row in matching if row["batch_id"] == value]
            descending = "ORDER BY last_sequence DESC" in sql
            if "ORDER BY last_sequence" in sql:
                matching.sort(key=lambda row: row["last_sequence"], reverse=descending)
            elif "ORDER BY sequence" in sql:
                matching.sort(key=lambda row: row["sequence"])
            limit = re.search(r"LIMIT (\d+)", sql)
            return "\n".join(json.dumps({column: row[column] for column in columns})
                             for row in (matching[:int(limit.group(1))] if limit else matching))
        batch_id = sql.split("batch_id=toUUID('", 1)[1].split("')", 1)[0]
        return "\n".join(json.dumps({column: row[column] for column in columns})
                         for row in self.tables.get(name, []) if row["batch_id"] == batch_id)


def test_typed_publication_commits_last_and_retry_is_idempotent() -> None:
    client = MemoryClient()
    item = batch()
    assert publish_typed_batch(client, item) == BATCH
    assert client.inserts == ["trading_event_v1", "trading_commit_v1"]
    assert all("async_insert=1,wait_for_async_insert=1" in sql
               for sql in client.insert_sql)
    assert len(client.selects) == 5
    assert publish_typed_batch(client, item) == BATCH
    assert client.inserts == ["trading_event_v1", "trading_commit_v1"]
    assert len(client.selects) == 7
    assert len(client.tables["trading_commit_v1"]) == 1
    assert not any("payload_json" in row for rows in client.tables.values() for row in rows)
    prefix = load_committed_prefix(client, RUN)
    assert len(client.selects) == 42
    assert prefix is not None
    assert (prefix.last_sequence, prefix.source_cursor, prefix.batch_ids) == (1, "bucket-1", (BATCH,))


def test_cold_prefix_rejects_opaque_committed_source_cursor() -> None:
    client = MemoryClient()
    publish_typed_batch(client, batch())
    client.tables["trading_commit_v1"][0]["source_cursor"] = '\ufeff {"hidden":1}'
    with pytest.raises(RuntimeError, match="opaque source cursor"):
        load_committed_prefix(client, RUN)


def test_assignment_command_shared_family_cold_prefix_readback() -> None:
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    saved = {
        "assignment_id": RUN, "account_id": "DU1", "ticker": "ABC",
        "strategy_id": "early-squeeze", "strategy_revision": 24,
        "status": "paused", "state": {}, "updated_at": at.isoformat(),
    }
    record = JournalRecord(
        RECORD, RUN, 1, at, at, "strategy", "strategy_assignment_command",
        RUN, "DU1", {
            "event": "assignment_command", "command": "pause",
            "assignment_id": RUN, "strategy_id": "early-squeeze",
            "strategy_revision": 24, "ticker": "ABC", "status": "paused",
            "detail": {},
        },
    )
    item = assignment_command_batch(
        record, saved, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=ZERO, source_cursor="assignment:1",
    )
    client = MemoryClient()
    assert publish_typed_batch(client, item) == BATCH
    assert client.inserts == [
        "trading_event_v1", "trading_strategy_assignment_command_v1",
        "trading_commit_v1",
    ]
    prefix = load_committed_prefix(client, RUN)
    assert prefix is not None and prefix.last_sequence == 1
    assert client.tables["trading_strategy_assignment_command_v1"][0]["command"] == "pause"
    client.tables["trading_strategy_assignment_command_v1"][0]["status"] = "disabled"
    with pytest.raises(RuntimeError):
        load_committed_prefix(client, RUN)


def test_recovery_groups_batches_but_verifies_each_fence() -> None:
    client = MemoryClient()
    ids = ["00000000-0000-0000-0000-000000000031",
           "00000000-0000-0000-0000-000000000032",
           "00000000-0000-0000-0000-000000000033"]
    for index, batch_id in enumerate(ids):
        event = dict(batch().events[0])
        event.pop("content_hash")
        event.update(batch_id=batch_id, sequence=index + 1,
                     record_id=f"00000000-0000-0000-0000-{index + 31:012d}")
        item = TypedJournalBatch(
            RUN, date(2026, 8, 1), ATTEMPT, batch_id,
            ids[index - 1] if index else ZERO,
            index + 1, index + 1, f"bucket-{index + 1}",
            "completed" if index == 2 else "running", (event,),
        )
        publish_typed_batch(client, item)
    client.selects.clear()
    prefix = load_committed_prefix(client, RUN)
    assert prefix is not None and prefix.last_sequence == 3
    assert len(client.selects) == 35
    client.tables["trading_commit_v1"][0]["event_count"] = 0
    with pytest.raises(RuntimeError, match="not contiguous"):
        load_committed_prefix(client, RUN)
    client.tables["trading_commit_v1"][0]["event_count"] = 1
    client.tables["trading_event_v1"][1]["content_hash"] = "0" * 64
    with pytest.raises(RuntimeError, match="row content differs from its hash"):
        load_committed_prefix(client, RUN)


def test_recovery_detects_content_change_even_when_hash_column_is_unchanged() -> None:
    client = MemoryClient()
    publish_typed_batch(client, batch())
    client.tables["trading_event_v1"][0]["entity_id"] = "tampered"
    with pytest.raises(RuntimeError, match="row content differs from its hash"):
        load_committed_prefix(client, RUN)


@pytest.mark.parametrize("category,entity", [
    ("order_management", "order_command"), ("command", "order"),
])
def test_order_command_and_transition_have_typed_durable_fences(category, entity) -> None:
    first = dict(batch().events[0])
    first.pop("content_hash")
    first.update(category=category, entity_type=entity)
    second_id = "00000000-0000-0000-0000-000000000014"
    second = {**first, "record_id": second_id, "sequence": 2,
              "category": "order_management", "entity_type": "order_transition"}
    common = {"run_id": RUN, "event_month": "2026-08-01", "batch_id": BATCH,
              "account_id": "DU1", "command_id": "command-1",
              "client_order_id": "client-1", "conid": 123, "ticker": "TEST"}
    command = {**common, "record_id": RECORD, "side": "BUY", "order_type": "LIMIT",
               "time_in_force": "DAY", "quantity": "5.0000000000", "cash_quantity": None,
               "limit_price": "12.3400000000", "aux_price": None, "outside_rth": 1,
               "parent_command_id": "", "oca_group": "", "strategy_id": "strategy-1",
               "strategy_revision": 2, "created_at": "2026-08-18T08:05:00+00:00",
               "security_type": "STK", "listing_exchange": "SMART",
               "trailing_amount": None, "trailing_type": "", "single_group": 0,
               "manual_indicator": 0, "external_operator": "", "referrer": "",
               "broker_strategy": "", "parent_broker_order_id": ""}
    transition = {**common, "record_id": second_id, "broker_order_id": "broker-1",
                  "status": "submitted", "broker_status": "Submitted",
                  "total_quantity": "5.0000000000", "filled_quantity": "0.0000000000",
                  "remaining_quantity": "5.0000000000", "average_fill_price": None,
                  "can_modify": 1, "can_cancel": 1, "terminal": 0,
                  "rejection_code": "", "rejection_reason": "",
                  "source_event_time": "2026-08-18T08:05:01+00:00",
                  "received_at": "2026-08-18T08:05:01+00:00"}
    item = TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                             1, 2, "bucket-2", "completed", (first, second),
                             order_commands=(command,), order_transitions=(transition,))
    client = MemoryClient()
    assert publish_typed_batch(client, item) == BATCH
    assert client.inserts == ["trading_event_v1", "trading_order_command_v1",
                              "trading_order_transition_v1", "trading_commit_v1"]
    prefix = load_committed_prefix(client, RUN)
    assert prefix is not None and prefix.last_sequence == 2
    page = load_committed_order_command_page(client, prefix, limit=1)
    assert len(page) == 1, client.selects[-1]
    assert page[0]["sequence"] == 1
    assert page[0]["command_id"] == "command-1"
    assert page[0]["client_order_id"] == "client-1"
    assert load_committed_order_command_page(client, prefix, after_sequence=1) == ()
    transitions = load_committed_order_transition_page(client, prefix, limit=1)
    assert len(transitions) == 1
    assert transitions[0]["sequence"] == 2
    assert transitions[0]["status"] == "submitted"
    assert load_committed_order_transition_page(client, prefix, after_sequence=2) == ()
    unfenced_id = "00000000-0000-0000-0000-000000000099"
    for name in ("trading_event_v1", "trading_order_transition_v1"):
        clone = dict(client.tables[name][-1])
        clone["batch_id"] = unfenced_id
        client.tables[name].append(clone)
    assert len(load_committed_order_transition_page(client, prefix, limit=1)) == 1
    assert load_committed_order_transition_page(client, prefix, after_sequence=2) == ()
    client.tables["trading_order_command_v1"][0]["account_id"] = "wrong"
    with pytest.raises(RuntimeError, match="event envelope"):
        load_committed_order_command_page(client, prefix)


def test_execution_and_commission_pages_require_fenced_typed_details() -> None:
    execution = _execution({
        "execution_id": "fill-1", "symbol": "TEST", "side": "B",
        "trade_time_r": 1787040300000, "size": 1, "price": 10.25,
        "order_id": "broker-1", "order_ref": "client-1", "account": "DU1",
        "conid": 123, "currency": "USD", "exchange": "ARCA",
        "commission": 1.25,
    })
    item = broker_fill_batch(
        execution, run_id=RUN, run_month=date(2026, 8, 1),
        attempt_id=ATTEMPT, batch_id=BATCH, prior_batch_id=ZERO,
        first_sequence=1, source_cursor="fill-1", status="completed",
        received_at=datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc),
    )
    client = MemoryClient()
    publish_typed_batch(client, item)
    prefix = load_committed_prefix(client, RUN)
    assert prefix is not None
    fills = load_committed_execution_page(client, prefix)
    fees = load_committed_commission_page(client, prefix)
    assert len(fills) == len(fees) == 1
    assert fills[0]["sequence"] == 1 and fills[0]["execution_id"] == "fill-1"
    assert fees[0]["sequence"] == 2 and fees[0]["commission"] == "1.2500000000"
    assert load_committed_execution_page(client, prefix, after_sequence=1) == ()
    assert load_committed_commission_page(client, prefix, after_sequence=2) == ()
    for name in ("trading_event_v1", "trading_execution_v1", "trading_commission_v1"):
        clone = dict(client.tables[name][0])
        clone["batch_id"] = "00000000-0000-0000-0000-000000000099"
        client.tables[name].append(clone)
    assert len(load_committed_execution_page(client, prefix)) == 1
    assert len(load_committed_commission_page(client, prefix)) == 1
    client.tables["trading_execution_v1"][0]["execution_id"] = "wrong"
    with pytest.raises(RuntimeError, match="event envelope"):
        load_committed_execution_page(client, prefix)
    client.tables["trading_execution_v1"][0]["execution_id"] = "fill-1"
    client.tables["trading_commission_v1"][0]["account_id"] = "wrong"
    with pytest.raises(RuntimeError, match="event envelope"):
        load_committed_commission_page(client, prefix)


def test_position_snapshot_requires_account_snapshot_in_same_batch() -> None:
    base = dict(batch().events[0])
    base.pop("content_hash")
    account_id = "00000000-0000-0000-0000-000000000015"
    position_id = "00000000-0000-0000-0000-000000000016"
    events = ({**base, "record_id": account_id, "sequence": 1,
               "category": "snapshot", "entity_type": "portfolio"},
              {**base, "record_id": position_id, "sequence": 2,
               "category": "snapshot", "entity_type": "position"})
    common = {"run_id": RUN, "event_month": "2026-08-01", "batch_id": BATCH,
              "snapshot_id": "snapshot-1", "account_id": "DU1",
              "source_event_time": "2026-08-18T08:05:00+00:00"}
    account = {**common, "record_id": account_id, "currency": "USD",
               "net_liquidation": "1000.0000000000", "total_cash_value": "900.0000000000",
               "buying_power": "900.0000000000", "gross_position_value": "100.0000000000",
               "available_funds": "800.0000000000", "excess_liquidity": "700.0000000000",
               "snapshot_complete": 1}
    position = {**common, "record_id": position_id, "conid": 123,
                "ticker": "TEST", "currency": "USD", "asset_class": "STK",
                "quantity": "10.0000000000", "market_price": "10.0000000000",
                "market_value": "100.0000000000", "average_cost": "9.0000000000",
                "average_price": "9.0000000000", "realized_pnl": "0.0000000000",
                "unrealized_pnl": "10.0000000000"}
    item = TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                             1, 2, "bucket-2", "completed", events,
                             account_snapshots=(account,), position_snapshots=(position,))
    client = MemoryClient()
    assert publish_typed_batch(client, item) == BATCH
    assert load_committed_prefix(client, RUN).last_sequence == 2
    orphan = TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                               1, 2, "bucket-2", "completed", events,
                               position_snapshots=(position,))
    with pytest.raises(ValueError, match="lacks its complete account snapshot"):
        publish_typed_batch(MemoryClient(), orphan)


def test_typed_detail_cannot_change_parent_account_or_partition() -> None:
    original = batch()
    event = {**dict(original.events[0]), "category": "snapshot",
             "entity_type": "portfolio", "account_id": "DU1"}
    event.pop("content_hash")
    detail = {
        "record_id": RECORD, "run_id": RUN, "event_month": "2026-08-01",
        "batch_id": BATCH, "snapshot_id": "s1", "account_id": "DU1",
        "currency": "USD", "net_liquidation": "1", "total_cash_value": "1",
        "buying_power": "1", "gross_position_value": "0", "available_funds": "1",
        "excess_liquidity": "1", "snapshot_complete": 1,
        "source_event_time": "2026-08-18T08:05:00+00:00",
    }
    for changed in ({"account_id": "OTHER"}, {"event_month": "2026-09-01"}):
        item = TypedJournalBatch(RUN, original.run_month, ATTEMPT, BATCH, ZERO,
                                 1, 1, "bucket-1", "running", (event,),
                                 account_snapshots=({**detail, **changed},))
        with pytest.raises(ValueError, match="differs from its parent"):
            publish_typed_batch(MemoryClient(), item)


def test_run_identity_is_immutable_and_uses_typed_rows() -> None:
    client = MemoryClient()
    row = run_row()
    assert publish_typed_run(client, row) == RUN
    assert publish_typed_run(client, row) == RUN
    assert client.inserts == ["trading_run_v1"]
    with pytest.raises(RuntimeError, match="conflicts"):
        publish_typed_run(client, {**row, "code_hash": "c" * 64})


def test_typed_publication_detects_conflicting_readback() -> None:
    client = MemoryClient()
    item = batch()
    publish_typed_batch(client, item)
    client.tables["trading_event_v1"][0]["content_hash"] = "0" * 64
    with pytest.raises(RuntimeError, match="conflicting"):
        publish_typed_batch(client, item)
    with pytest.raises(RuntimeError, match="row content differs from its hash"):
        load_committed_prefix(client, RUN)


def test_typed_publication_rejects_orphan_rows_in_empty_family() -> None:
    client = MemoryClient()
    client.tables["trading_execution_v1"] = [{
        "batch_id": BATCH, "record_id": RECORD, "content_hash": "0" * 64,
    }]
    with pytest.raises(RuntimeError, match="trading_execution_v1 has a conflicting"):
        publish_typed_batch(client, batch())
    assert "trading_commit_v1" not in client.inserts


def test_late_commission_requires_a_committed_execution() -> None:
    at = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
    fee = CommissionEvent("execution-1", "DU1", Decimal("1.25"), "USD",
                          source_event_time=at, received_at=at)
    item = commission_revision_batch(
        fee, run_id=RUN, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=ZERO, sequence=1,
        source_cursor="fee-1", run_status="running",
        time_authority="observation",
    )
    client = MemoryClient()
    with pytest.raises(RuntimeError, match="requires one committed execution"):
        publish_typed_batch(client, item)
    source_batch = "00000000-0000-0000-0000-000000000099"
    client.tables["trading_execution_v1"] = [{
        "record_id": RECORD, "batch_id": source_batch, "run_id": RUN,
        "account_id": "DU1", "execution_id": "execution-1",
    }]
    with pytest.raises(RuntimeError, match="requires one committed execution"):
        publish_typed_batch(client, item)
    client.tables["trading_commit_v1"] = [{
        "batch_id": source_batch, "run_id": RUN, "last_sequence": 1,
        "status": "running",
    }]
    continued = commission_revision_batch(
        fee, run_id=RUN, run_month=date(2026, 8, 1), attempt_id=ATTEMPT,
        batch_id=BATCH, prior_batch_id=source_batch, sequence=2,
        source_cursor="fee-1", run_status="running",
        time_authority="observation",
    )
    assert publish_typed_batch(client, continued) == BATCH
    assert "trading_execution_v1" not in client.inserts


def test_event_details_are_required_and_unmapped_events_fail_closed() -> None:
    original = batch()
    base = dict(original.events[0])
    base.pop("content_hash")
    for category, entity_type in (("execution", "fill"),
                                  ("order_management", "order_command"),
                                  ("strategy_decision", "signal")):
        event = {**base, "category": category, "entity_type": entity_type}
        item = TypedJournalBatch(RUN, original.run_month, ATTEMPT, BATCH, ZERO,
                                 1, 1, "bucket-1", "running", (event,))
        with pytest.raises(ValueError, match="required typed detail|no typed contract"):
            publish_typed_batch(MemoryClient(), item)


def test_typed_publication_rejects_out_of_order_prefix() -> None:
    client = MemoryClient()
    item = batch()
    altered = TypedJournalBatch(item.run_id, item.run_month, item.attempt_id,
                                item.batch_id, item.prior_batch_id, 2, 2,
                                item.source_cursor, item.status,
                                (typed_row("trading_event_v1", {
                                    **{key: value for key, value in item.events[0].items()
                                       if key != "content_hash"}, "sequence": 2}),))
    with pytest.raises(RuntimeError, match="committed prefix"):
        publish_typed_batch(client, altered)
    assert not client.inserts


def test_typed_publication_rejects_extension_after_terminal_without_inserting() -> None:
    client = MemoryClient()
    terminal = replace(batch(), status="completed")
    publish_typed_batch(client, terminal)
    source = {key: value for key, value in terminal.events[0].items()
              if key != "content_hash"}
    next_batch = "00000000-0000-0000-0000-000000000099"
    source.update(batch_id=next_batch, sequence=2)
    continued = TypedJournalBatch(
        RUN, terminal.run_month, ATTEMPT, next_batch, BATCH, 2, 2,
        "bucket-2", "running", (source,),
    )
    inserted_before = list(client.inserts)
    with pytest.raises(RuntimeError, match="terminal run"):
        publish_typed_batch(client, continued)
    assert client.inserts == inserted_before


def test_submission_never_waits_for_network_or_queue_space(monkeypatch) -> None:
    entered, release = Event(), Event()

    def stalled(_client, item):
        entered.set()
        assert release.wait(5)
        return item.batch_id

    monkeypatch.setattr(writer_module, "publish_typed_batch", stalled)
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: {"mode": "live"})
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=1)
    try:
        first = journal.submit(batch())
        assert entered.wait(5)
        second = journal.submit(batch())
        with pytest.raises(JournalQueueFull):
            journal.submit(batch())
        assert not first.done() and not second.done()
        pending = journal.metrics()
        assert pending["queue_depth"] == 1
        assert pending["committed_units"] == 0
        assert not pending["failed"]
        release.set()
        assert UUID(first.result(timeout=5)) == UUID(BATCH)
        assert UUID(second.result(timeout=5)) == UUID(BATCH)
        finished = journal.metrics()
        assert finished["committed_units"] == 2
        assert finished["failed_units"] == 0
        assert finished["publish_ns_total"] >= finished["publish_ns_max"] > 0
    finally:
        release.set()
        journal.close()


def test_ordered_barrier_receipt_waits_for_prior_commit_without_blocking_submit(monkeypatch) -> None:
    entered, release = Event(), Event()

    def stalled(_client, item):
        entered.set()
        assert release.wait(5)
        return item.batch_id

    monkeypatch.setattr(writer_module, "publish_typed_batch", stalled)
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: {"mode": "live"})
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=2)
    try:
        with pytest.raises(ValueError, match="prior journal write"):
            journal.submit_barrier()
        write_receipt = journal.submit(batch())
        assert entered.wait(5)
        barrier = journal.submit_barrier()
        assert not write_receipt.done() and not barrier.done()
        release.set()
        assert write_receipt.result(timeout=5) == BATCH
        assert barrier.result(timeout=5) == BATCH
    finally:
        release.set()
        journal.close()


def test_admission_queues_one_persistently_fenced_unit(monkeypatch) -> None:
    from src.trading_runtime import arte_admission_fence as admission_module

    published = []

    def publish_admission(_client, item, snapshot):
        published.append((item.batch_id, snapshot.state_revision))
        return "s" * 64

    monkeypatch.setattr(admission_module, "publish_fenced_admission", publish_admission)
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: {"mode": "live"})
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=1)
    try:
        receipt = journal.submit_admission(batch(), captured())
        assert receipt.result(timeout=5) == "s" * 64
        assert published == [(BATCH, 1)]
    finally:
        journal.close()


def test_terminal_backtest_queues_all_account_anchors_after_events(monkeypatch) -> None:
    from src.trading_runtime import arte_backtest_snapshot_anchor as anchor_module
    from tests.test_arte_admission_fence import captured as captured_state

    order = []
    entered, release = Event(), Event()
    item = replace(batch(), status="completed")
    snapshot = replace(captured_state(), run_id=RUN, account_id="DU1")
    prefix = CommittedPrefix(RUN, item.last_sequence, BATCH,
                             item.source_cursor, "completed", (BATCH,))
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run: {
        "mode": "backtest", "account_ids": ("DU1",),
    })
    monkeypatch.setattr(writer_module, "load_typed_run_context", lambda _client, _run: {
        "mode": "backtest", "account_ids": ("DU1",),
    })
    monkeypatch.setattr(writer_module, "publish_typed_batch", lambda _client, batch: (
        order.append("events") or batch.batch_id))
    monkeypatch.setattr(writer_module, "load_committed_prefix", lambda _client, _run: prefix)
    def persist_anchors(_client, _prefix, _captured):
        entered.set()
        assert release.wait(5)
        order.append("anchor")
        return ("a" * 64,)

    monkeypatch.setattr(anchor_module, "publish_terminal_backtest_snapshots",
                        persist_anchors)
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=1)
    try:
        with pytest.raises(ValueError, match="anchored account snapshots"):
            journal.submit(item)
        receipt = journal.submit_terminal_backtest(item, (snapshot,))
        assert entered.wait(5)
        assert not receipt.done()
        release.set()
        assert receipt.result(timeout=5) == BATCH
        assert order == ["events", "anchor"]
        with pytest.raises(ValueError, match="per run account"):
            journal.submit_terminal_backtest(item, (snapshot, snapshot))
        with pytest.raises(ValueError, match="per run account"):
            journal.submit_terminal_backtest(item, (replace(snapshot, account_id="DU2"),))
        assert order == ["events", "anchor"]
    finally:
        release.set()
        journal.close()


def test_writer_rejects_missing_verified_run_mode(monkeypatch) -> None:
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run: None)
    with pytest.raises(RuntimeError, match="verified run mode"):
        ArteJournalWriter(object(), run_id=RUN)


def test_backtest_writer_requires_verified_account_membership(monkeypatch) -> None:
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity",
                        lambda _client, _run: {"mode": "backtest"})
    with pytest.raises(RuntimeError, match="account membership"):
        ArteJournalWriter(object(), run_id=RUN)


def test_live_writer_cannot_submit_a_backtest_terminal_unit(monkeypatch) -> None:
    from tests.test_arte_admission_fence import captured as captured_state

    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity",
                        lambda _client, _run: {"mode": "live"})
    journal = ArteJournalWriter(object(), run_id=RUN)
    try:
        snapshot = replace(captured_state(), run_id=RUN)
        with pytest.raises(ValueError, match="Terminal Backtest"):
            journal.submit_terminal_backtest(
                replace(batch(), status="completed"), (snapshot,))
        assert journal._queue.empty()
    finally:
        journal.close()


def test_admission_rejects_wrong_account_before_queueing(monkeypatch) -> None:
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: {"mode": "live"})
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=1)
    try:
        with pytest.raises(ValueError, match="one causal account"):
            journal.submit_admission(batch(), replace(captured(), account_id="other"))
        with pytest.raises(ValueError, match="one causal account"):
            journal.submit_admission(batch(), replace(captured(), state_revision=0))
        with pytest.raises(ValueError, match="prior journal write"):
            journal.submit_barrier()
        assert journal._queue.empty()
    finally:
        journal.close()


def test_ordered_barrier_propagates_prior_publication_failure(monkeypatch) -> None:
    entered, release = Event(), Event()

    def fail(_client, _item):
        entered.set()
        assert release.wait(5)
        raise OSError("ClickHouse unavailable")

    monkeypatch.setattr(writer_module, "publish_typed_batch", fail)
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: {"mode": "live"})
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=2)
    try:
        failed = journal.submit(batch())
        assert entered.wait(5)
        barrier = journal.submit_barrier()
        release.set()
        with pytest.raises(OSError, match="ClickHouse unavailable"):
            failed.result(timeout=5)
        with pytest.raises(RuntimeError, match="failed earlier"):
            barrier.result(timeout=5)
    finally:
        release.set()
        with pytest.raises(RuntimeError, match="did not drain durably"):
            journal.close()


def test_keeper_claim_stays_held_until_typed_writer_barrier(monkeypatch) -> None:
    from src.trading_runtime.keeper_receipts import KeeperReceiptSupervisor
    from src.trading_runtime import arte_admission_fence as admission_module

    entered, release = Event(), Event()
    released = Event()

    def stalled(_client, item, snapshot):
        entered.set()
        assert release.wait(5)
        return "s" * 64

    class Coordinator:
        def portfolio_admission_lease_is_current(self, resource, *, owner_id, epoch):
            return True

        def renew_portfolio_admission_lease(self, resource, *, owner_id, epoch, ttl_seconds):
            return {"resource_id": resource, "owner_id": owner_id, "epoch": epoch}

        def release_portfolio_admission_lease(self, resource, *, owner_id, epoch):
            released.set()
            return True

    monkeypatch.setattr(admission_module, "publish_fenced_admission", stalled)
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: {"mode": "live"})
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=1)
    supervisor = KeeperReceiptSupervisor(Coordinator())
    try:
        barrier = journal.submit_admission(batch(), captured())
        assert entered.wait(5)
        completion = supervisor.watch(({
            "resource_id": "portfolio-account:DU1", "owner_id": RUN, "epoch": 1,
        },), barrier)
        assert not completion.done() and not released.is_set()
        release.set()
        assert completion.result(timeout=5) == "s" * 64
        assert released.is_set()
    finally:
        release.set()
        journal.close()
        supervisor.close(timeout_seconds=5)


def test_cancelled_receipt_does_not_poison_durable_writer(monkeypatch) -> None:
    entered, release = Event(), Event()
    published = []

    def publish(_client, item):
        if not published:
            entered.set()
            assert release.wait(5)
        published.append(item.batch_id)
        return item.batch_id

    monkeypatch.setattr(writer_module, "publish_typed_batch", publish)
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: {"mode": "live"})
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=2)
    try:
        cancelled = journal.submit(batch())
        assert entered.wait(5)
        assert cancelled.cancel()
        release.set()
        assert journal.submit(batch()).result(timeout=5) == BATCH
        assert cancelled.cancelled() and published == [BATCH, BATCH]
    finally:
        release.set()
        if journal._thread.is_alive():
            journal.close()


def test_cancelled_receipt_does_not_hide_publication_failure(monkeypatch) -> None:
    entered, release = Event(), Event()

    def fail(_client, _item):
        entered.set()
        assert release.wait(5)
        raise OSError("ClickHouse unavailable")

    monkeypatch.setattr(writer_module, "publish_typed_batch", fail)
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: {"mode": "live"})
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=2)
    try:
        cancelled = journal.submit(batch())
        assert entered.wait(5) and cancelled.cancel()
        waiting = journal.submit(batch())
        release.set()
        with pytest.raises(RuntimeError, match="failed earlier"):
            waiting.result(timeout=5)
        assert cancelled.cancelled()
        with pytest.raises(RuntimeError, match="failed"):
            journal.submit(batch())
    finally:
        release.set()
        with pytest.raises(RuntimeError, match="did not drain durably"):
            journal.close()


def test_close_cannot_place_stop_sentinel_ahead_of_admitted_submission(monkeypatch) -> None:
    monkeypatch.setattr(writer_module, "publish_typed_batch", lambda _client, item: item.batch_id)
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: {"mode": "live"})
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=2)
    entered, release, close_started, close_finished = Event(), Event(), Event(), Event()
    original_put = journal._queue.put_nowait

    def paused_put(item):
        entered.set()
        assert release.wait(5)
        return original_put(item)

    monkeypatch.setattr(journal._queue, "put_nowait", paused_put)
    submitted = []
    failures = []

    def submit():
        try:
            submitted.append(journal.submit(batch()))
        except BaseException as exc:
            failures.append(exc)

    def close():
        close_started.set()
        try:
            journal.close()
        except BaseException as exc:
            failures.append(exc)
        finally:
            close_finished.set()

    submitting = Thread(target=submit)
    closing = Thread(target=close)
    try:
        submitting.start()
        assert entered.wait(5)
        closing.start()
        assert close_started.wait(5)
        assert not close_finished.wait(0.1)
        release.set()
        submitting.join(5)
        closing.join(5)
        assert not submitting.is_alive() and not closing.is_alive()
        assert not failures
        assert len(submitted) == 1
        assert submitted[0].result(timeout=5) == BATCH
    finally:
        release.set()
        submitting.join(5)
        if closing.ident is not None:
            closing.join(5)


@pytest.mark.parametrize("client_fails", [False, True])
def test_concurrent_close_waits_for_client_and_replays_failure(monkeypatch, client_fails) -> None:
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: {"mode": "live"})
    entered, release = Event(), Event()
    class Client:
        calls = 0
        def close(self):
            self.calls += 1
            entered.set()
            assert release.wait(5)
            if client_fails:
                raise OSError("transport close failed")
    client = Client()
    journal = ArteJournalWriter(client, run_id=RUN)
    finished: list[str] = []
    errors: list[BaseException] = []
    def close(label):
        try:
            journal.close()
            finished.append(label)
        except BaseException as exc:
            errors.append(exc)
    first = Thread(target=close, args=("first",))
    second = Thread(target=close, args=("second",))
    try:
        first.start()
        assert entered.wait(5)
        second.start()
        assert not finished and not errors
        assert second.is_alive()
    finally:
        release.set()
        first.join(5)
        second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert client.calls == 1
    if client_fails:
        assert len(errors) == 2
        assert all(isinstance(error, RuntimeError) and
                   "client did not close" in str(error) for error in errors)
    else:
        assert not errors and sorted(finished) == ["first", "second"]
        journal.close()


def test_writer_coalesces_only_contiguous_unpublished_batches(monkeypatch) -> None:
    entered, release = Event(), Event()
    published = []
    ids = ["00000000-0000-0000-0000-000000000021",
           "00000000-0000-0000-0000-000000000022",
           "00000000-0000-0000-0000-000000000023"]

    def micro(sequence: int) -> TypedJournalBatch:
        event = dict(batch().events[0])
        event.pop("content_hash")
        event.update(batch_id=ids[sequence - 1], sequence=sequence,
                     record_id=f"00000000-0000-0000-0000-{sequence:012d}")
        return TypedJournalBatch(
            RUN, date(2026, 8, 1), ATTEMPT, ids[sequence - 1],
            ids[sequence - 2] if sequence > 1 else ZERO,
            sequence, sequence, f"bucket-{sequence}",
            "completed" if sequence == 3 else "running", (event,),
        )

    def record(_client, item):
        if item.first_sequence == 1:
            entered.set()
            assert release.wait(5)
        writer_module._sealed_families(item)
        published.append(item)
        return item.batch_id

    monkeypatch.setattr(writer_module, "publish_typed_batch", record)
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: {"mode": "live"})
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=3)
    try:
        first = journal.submit(micro(1))
        assert entered.wait(5)
        second = journal.submit(micro(2))
        third = journal.submit(micro(3))
        assert not second.done() and not third.done()
        release.set()
        assert first.result(timeout=5) == ids[0]
        assert second.result(timeout=5) == third.result(timeout=5) == ids[2]
        assert [(row.first_sequence, row.last_sequence, row.batch_id)
                for row in published] == [(1, 1, ids[0]), (2, 3, ids[2])]
        assert [row["batch_id"] for row in published[1].events] == [ids[2], ids[2]]
    finally:
        release.set()
        journal.close()


def test_writer_failure_poisoning_is_visible_to_all_receipts(monkeypatch) -> None:
    def rejected(_client, _batch):
        raise OSError("ClickHouse unavailable")

    monkeypatch.setattr(writer_module, "publish_typed_batch", rejected)
    monkeypatch.setattr(writer_module, "storage_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "journal_permission_preflight", lambda _client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda _client, _run_id: {"mode": "live"})
    journal = ArteJournalWriter(object(), run_id=RUN, capacity=2)
    try:
        first = journal.submit(batch())
        with pytest.raises(OSError, match="unavailable"):
            first.result(timeout=5)
        assert journal.metrics()["failed_units"] == 1
        assert journal.metrics()["failed"]
        with pytest.raises(RuntimeError, match="failed"):
            journal.submit(batch())
    finally:
        with pytest.raises(RuntimeError, match="did not drain durably"):
            journal.close()


def test_invalid_family_row_is_rejected_before_publication() -> None:
    item = batch()
    altered = dict(item.events[0])
    altered["payload_json"] = "{}"
    invalid = TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                                1, 1, "bucket-1", "running", (altered,))
    with pytest.raises(ValueError, match="typed columns"):
        publish_typed_batch(MemoryClient(), invalid)


def test_submission_snapshot_is_immutable_and_hashing_stays_off_caller(monkeypatch) -> None:
    source = dict(batch().events[0])
    source.pop("content_hash")
    with monkeypatch.context() as patch:
        patch.setattr(writer_module, "canonical_json", lambda _value: (_ for _ in ()).throw(
            AssertionError("serialization occurred on caller")))
        pending = TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                                    1, 1, "bucket-1", "running", (source,))
    source["entity_id"] = "changed-after-submit"
    assert pending.events[0]["entity_id"] == "run-1"
    assert publish_typed_batch(MemoryClient(), pending) == BATCH


def test_submission_rejects_nested_mutable_data_before_async_handoff() -> None:
    source = dict(batch().events[0])
    source["entity_id"] = {"mutable": "value"}
    with pytest.raises(ValueError, match="mutable or opaque"):
        TypedJournalBatch(RUN, date(2026, 8, 1), ATTEMPT, BATCH, ZERO,
                          1, 1, "bucket-1", "running", (source,))
