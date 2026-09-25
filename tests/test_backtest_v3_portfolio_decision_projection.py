from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import re

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_typed_projection import project_pending_backtest_v3_prefix
from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime.arte_journal_projection import project_journal_record
from src.trading_runtime.journal_contract import JournalRecord
from src.trading_runtime.portfolio import (
    PortfolioAccountProfile, PortfolioDecisionStatus, PortfolioManagementEngine,
    PortfolioPolicy,
)
from src.trading_runtime.signals import StrategyIntent
from tests.test_arte_journal_writer import MemoryClient, RUN, ATTEMPT, BATCH, RECORD, ZERO


AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)
METRICS = {name: 0.0 for name in (
    "net_liquidation", "available_funds", "buying_power", "gross_exposure",
    "net_exposure", "reserved_notional", "open_risk", "daily_loss",
    "drawdown", "position_count")}


def _record(status="deferred", *, extras=None, event_time=None):
    payload = {
        "event": "portfolio_decision", "ticker": "AAA", "action": "enter_long",
        "decision_id": "decision-1", "request_id": "intent-1",
        "account_key": "primary", "account_id": "DU1", "policy_id": "policy-1",
        "policy_revision": 1, "snapshot_id": "snapshot-1", "status": status,
        "requested_quantity": 10.0, "approved_quantity": 0.0,
        "approved_notional": 0.0, "planned_loss": 0.0,
        "reservation_id": "", "reasons": ("insufficient_capacity",),
        "metrics_before": dict(METRICS), "metrics_after": dict(METRICS),
        "decided_at": AT, "correlation_id": "corr", "causation_id": "intent-1",
        **(extras or {}),
    }
    return JournalRecord(RECORD, RUN, 1, event_time or AT + timedelta(microseconds=1),
                         AT + timedelta(microseconds=2), "portfolio_management",
                         "portfolio_decision", "decision-1", "DU1", payload)


def _project(record):
    return project_journal_record(record, run_month=date(2026, 8, 1),
                                  attempt_id=ATTEMPT, batch_id=BATCH,
                                  prior_batch_id=ZERO, source_cursor="boundary-1",
                                  expected_mode="backtest")


@pytest.mark.parametrize("status", ["approved", "resized", "rejected", "deferred"])
def test_closed_decision_status_and_ordered_reasons(status):
    item = _project(_record(status))
    sealed = dict(writer._sealed_families(item))
    assert sealed["trading_portfolio_decision_v1"][0]["status"] == status
    assert sealed["trading_portfolio_decision_reason_v1"][0]["reason"] == "insufficient_capacity"


def test_decision_rejects_unknown_field_or_reverse_causal_clock():
    with pytest.raises(ValueError, match="incomplete"):
        _project(_record(extras={"unmodeled": "x"}))
    with pytest.raises(ValueError, match="incomplete"):
        _project(_record(event_time=AT - timedelta(microseconds=1)))


def test_actual_v3_pending_prefix_projects_decision():
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
    assert len(units[0].base.portfolio_decisions) == 1
    assert units[0].episodes == ()


def test_real_portfolio_decision_emitter_projects_without_sqlite():
    journal = BacktestMemoryJournal(run_id=RUN)
    engine = PortfolioManagementEngine(
        [PortfolioAccountProfile("primary", "DU1", "backtest", "simulated",
                                 PortfolioPolicy())],
        journal=journal, run_id=RUN, strategy_id="strategy-a", strategy_revision=1)
    intent = StrategyIntent(intent_id="intent-1", ticker="AAA", event_time=AT,
                            action="enter_long", quantity=10.0,
                            reference_price=5.0, invalidation_price=4.0,
                            metadata={"assignment_id": "assignment-1"})
    decision = engine._decision(intent, engine._state("DU1"),
                                PortfolioDecisionStatus.REJECTED, 10.0, 0.0, 0.0,
                                "", ["insufficient_capacity"], METRICS, METRICS, AT)
    records = [row for row in journal.records(RUN)
               if row.entity_type == "portfolio_decision"]
    assert len(records) == 1 and records[0].entity_id == decision.decision_id
    batch = _project(records[0])
    sealed = dict(writer._sealed_families(batch))
    assert sealed["trading_portfolio_decision_v1"][0]["status"] == "rejected"
    assert sealed["trading_portfolio_decision_reason_v1"][0]["reason"] == "insufficient_capacity"


def test_v3_fake_publication_and_cold_hash_tamper(monkeypatch):
    class Client(MemoryClient):
        def execute(self, sql):
            if "groupArray((toString(record_id),toString(content_hash)))" in sql:
                batch_id = re.search(r"batch_id=toUUID\('([0-9a-f-]+)'\)", sql).group(1)
                names = re.findall(r"FROM arte\.([a-z0-9_]+) WHERE batch_id=.*?\) AS ([a-z0-9_]+)", sql)
                return json.dumps({alias: [[row["record_id"], row["content_hash"]]
                    for row in self.tables.get(table, []) if row["batch_id"] == batch_id]
                    for table, alias in names})
            return super().execute(sql)

    monkeypatch.setattr(writer, "_v3_preflight", lambda _client: None)
    monkeypatch.setattr(writer, "_verify_run_identity", lambda _client, _run: {
        "mode": "backtest"})
    client = Client()
    writer.publish_typed_squeeze_batch_v3(client, writer.V3SqueezeBatch(_project(_record()), ()))
    assert client.tables["trading_commit_v3"][0]["portfolio_decision_count"] == 1
    assert client.tables["trading_commit_v3"][0]["portfolio_decision_reason_count"] == 1
    fence = client.tables["trading_commit_v3"][0]
    writer._verify_recovery_chunk(client, [fence], journal_profile="backtest_v3")
    client.tables["trading_portfolio_decision_reason_v1"][0]["reason"] = "tampered"
    with pytest.raises(RuntimeError, match="row content differs"):
        writer._verify_recovery_chunk(client, [fence], journal_profile="backtest_v3")
