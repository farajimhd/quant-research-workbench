"""Closed scalar Portfolio control projection, including real memory ingress."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from dataclasses import asdict

import pytest

from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_portfolio_control_v3 import (
    project_portfolio_control_v3, seal_portfolio_control_v3,
)
from src.backend.backtest_typed_projection import project_pending_backtest_v3_prefix
from src.backend.backtest_squeeze_episode_v3 import (
    coalesce_squeeze_units_v3, load_verified_squeeze_v3_prefix,
)
from src.trading_runtime import arte_journal_writer as writer_module
from src.trading_runtime.arte_journal_writer import publish_typed_squeeze_batch_v3
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.portfolio import PortfolioPolicy
from tests.test_arte_journal_writer import ATTEMPT, BATCH, RUN, MemoryClient
from tests.test_backtest_squeeze_episode_v3 import _FakeColdClient


AT = datetime(2026, 8, 18, 8, 5, tzinfo=timezone.utc)


def _record(payload, entity_id="primary"):
    journal = BacktestMemoryJournal(run_id=RUN)
    return journal.append(run_id=RUN, category="portfolio_management",
                          entity_type="portfolio_control", entity_id=entity_id,
                          account_id="DU1", event_time=AT, payload=payload)


def _project(record):
    return project_portfolio_control_v3(
        record, attempt_id=ATTEMPT, batch_id=BATCH, account_key="primary")


@pytest.mark.parametrize("payload,entity_id,expected", [
    ({"event": "control_changed", "control_mode": "entries_paused", "reason": "risk"},
     "primary", ("entries_paused", None, None)),
    ({"event": "strategy_allocation_control_changed", "strategy_id": "s1",
      "enabled": False, "reason": "operator"},
     "primary:s1", (None, "s1", 0)),
])
def test_actual_append_projects_exact_scalar_variant(payload, entity_id, expected):
    source = _record(payload, entity_id)
    projected = _project(source)
    detail = projected.detail
    assert (detail["control_mode"], detail["strategy_id"], detail["enabled"]) == expected
    assert projected.event["correlation_id"] == source.payload["correlation_id"]
    assert projected.event["causation_id"] == source.payload["causation_id"]
    assert detail["content_hash"] == sha256(canonical_json({
        key: value for key, value in detail.items() if key != "content_hash"
    }).encode("utf-8")).hexdigest()
    assert set(source.payload) - {"correlation_id", "causation_id"} == (
        {"event", "control_mode", "reason"} if entity_id == "primary" else
        {"event", "strategy_id", "enabled", "reason"})


@pytest.mark.parametrize("payload,entity_id", [
    ({"event": "portfolio_policy_selected", "policy": {"policy_id": "p"},
      "entries_paused": True, "reason": "operator"}, "primary"),
    ({"event": "control_changed", "control_mode": "enabled", "reason": "",
      "extra": 1}, "primary"),
    ({"event": "control_changed", "control_mode": "invalid", "reason": ""},
     "primary"),
    ({"event": "strategy_allocation_control_changed", "strategy_id": "s1",
      "enabled": 1, "reason": ""}, "primary:s1"),
    ({"event": "strategy_allocation_control_changed", "strategy_id": "s1",
      "enabled": True, "reason": ""}, "other:s1"),
])
def test_unmodeled_or_mismatched_control_fails_closed(payload, entity_id):
    with pytest.raises(ValueError):
        _project(_record(payload, entity_id))


def test_control_seal_rejects_missing_duplicate_and_tampered_child():
    projected = _project(_record({"event": "control_changed",
                                  "control_mode": "entries_paused", "reason": ""}))
    parent, detail = projected.event, projected.detail
    seal = seal_portfolio_control_v3(
        [detail], [parent], run_id=RUN, batch_id=BATCH)
    assert seal["portfolio_control_count"] == 1
    for rows in ([], [detail, detail], [{**detail, "reason": "changed"}],
                 [{**detail, "policy_hash": "a" * 64}]):
        with pytest.raises(ValueError):
            seal_portfolio_control_v3(rows, [parent], run_id=RUN, batch_id=BATCH)


def test_actual_policy_selection_projects_catalog_hash_and_seals():
    from src.trading_runtime.arte_portfolio_policy import _policy_rows

    policy = PortfolioPolicy()
    source = _record({"event": "portfolio_policy_selected",
                      "policy": {**asdict(policy), "identity": policy.identity},
                      "entries_paused": True, "reason": "operator"})
    projected = _project(source)
    assert projected.detail["policy_hash"] == _policy_rows(policy)[0]
    assert projected.detail["entries_paused"] == 1
    assert seal_portfolio_control_v3(
        [projected.detail], [projected.event], run_id=RUN,
        batch_id=BATCH)["portfolio_control_count"] == 1
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.append(run_id=RUN, category="portfolio_management",
                   entity_type="portfolio_control", entity_id="primary",
                   account_id="DU1", event_time=AT,
                   payload={"event": "portfolio_policy_selected",
                            "policy": {**asdict(policy), "identity": policy.identity},
                            "entries_paused": True, "reason": "operator"})
    unit, = project_pending_backtest_v3_prefix(
        journal, attempt_id=ATTEMPT, run_month=AT.date().replace(day=1),
        prior_sequence=0, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    assert unit.policy_selections[0].policy_hash == projected.detail["policy_hash"]
    assert unit.portfolio_controls[0]["policy_hash"] == projected.detail["policy_hash"]


def test_policy_selection_writer_refuses_missing_dispatch_before_any_insert(monkeypatch):
    policy = PortfolioPolicy()
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.append(run_id=RUN, category="portfolio_management",
                   entity_type="portfolio_control", entity_id="primary",
                   account_id="DU1", event_time=AT,
                   payload={"event": "portfolio_policy_selected",
                            "policy": {**asdict(policy), "identity": policy.identity},
                            "entries_paused": True, "reason": "operator"})
    unit, = project_pending_backtest_v3_prefix(
        journal, attempt_id=ATTEMPT, run_month=AT.date().replace(day=1),
        prior_sequence=0, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    monkeypatch.setattr(writer_module, "_v3_preflight", lambda client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda client, run: {
        "mode": "backtest"})
    client = _V3Client()
    with pytest.raises(RuntimeError, match="durable catalog dispatch"):
        publish_typed_squeeze_batch_v3(client, unit)
    assert client.inserts == []


def test_policy_catalog_attested_before_v3_event_insert(monkeypatch):
    import src.backend.backtest_squeeze_episode_v3 as cold_module
    import src.trading_runtime.arte_portfolio_policy as catalog

    policy = PortfolioPolicy()
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.append(run_id=RUN, category="portfolio_management",
                   entity_type="portfolio_control", entity_id="primary",
                   account_id="DU1", event_time=AT,
                   payload={"event": "portfolio_policy_selected",
                            "policy": {**asdict(policy), "identity": policy.identity},
                            "entries_paused": True, "reason": "operator"})
    unit, = project_pending_backtest_v3_prefix(
        journal, attempt_id=ATTEMPT, run_month=AT.date().replace(day=1),
        prior_sequence=0, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    monkeypatch.setattr(writer_module, "_v3_preflight", lambda client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda client, run: {
        "mode": "backtest"})
    actions = []
    client = _V3Client()

    class Dispatch:
        def assert_next_batch(self, **kwargs):
            actions.append("batch_gate")

        def execute_typed_insert(self, current_client, **kwargs):
            actions.append(f"insert:{kwargs['table']}")
            current_client.execute(kwargs["sql"])

        def seal_verified_operation(self, **kwargs):
            pass

        def compact_verified_batch(self, **kwargs):
            pass

    client.typed_insert_dispatch = Dispatch()
    hash_value = unit.policy_selections[0].policy_hash
    monkeypatch.setattr(catalog, "publish_portfolio_policy", lambda client, p:
                        actions.append("publish_catalog") or hash_value)
    monkeypatch.setattr(catalog, "load_attested_portfolio_policy",
                        lambda client, dispatch, h:
                        (actions.append("attest_catalog") or policy))
    publish_typed_squeeze_batch_v3(client, unit)
    assert actions[:3] == ["batch_gate", "publish_catalog", "attest_catalog"]
    assert actions.index("attest_catalog") < actions.index("insert:trading_event_v1")
    monkeypatch.setattr(cold_module, "storage_preflight", lambda client, *, tables: None)
    monkeypatch.setattr(cold_module, "_verify_recovery_chunk",
                        lambda client, commits, *, journal_profile: None)
    cold = _FakeColdClient(client.tables["trading_commit_v3"][0], [], [])
    cold.control_events = client.tables["trading_event_v1"]
    cold.portfolio_controls = client.tables["trading_portfolio_control_v3"]
    cold.typed_insert_dispatch = client.typed_insert_dispatch
    assert load_verified_squeeze_v3_prefix(
        cold, RUN, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64) is not None
    monkeypatch.setattr(catalog, "load_attested_portfolio_policy",
                        lambda client, dispatch, h: None)
    with pytest.raises(RuntimeError, match="catalog is absent or differs"):
        load_verified_squeeze_v3_prefix(
            cold, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)


class _V3Client(MemoryClient):
    def execute(self, sql):
        if "groupArray((toString(record_id),toString(content_hash)))" in sql:
            batch_id = re.search(r"batch_id=toUUID\('([0-9a-f-]+)'\)", sql).group(1)
            names = re.findall(
                r"FROM arte\.([a-z0-9_]+) WHERE batch_id=.*?\) AS ([a-z0-9_]+)", sql)
            return json.dumps({alias: [[row["record_id"], row["content_hash"]]
                               for row in self.tables.get(table, [])
                               if row["batch_id"] == batch_id]
                               for table, alias in names})
        return super().execute(sql)


def test_actual_v3_prefix_writer_seals_scalar_control_before_commit(monkeypatch):
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.append(run_id=RUN, category="portfolio_management",
                   entity_type="portfolio_control", entity_id="primary",
                   account_id="DU1", event_time=AT,
                   payload={"event": "control_changed", "control_mode": "reduce_only",
                            "reason": "operator"})
    unit, = project_pending_backtest_v3_prefix(
        journal, attempt_id=ATTEMPT, run_month=AT.date().replace(day=1),
        prior_sequence=0, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    assert len(unit.portfolio_controls) == 1
    monkeypatch.setattr(writer_module, "_v3_preflight", lambda client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda client, run: {
        "mode": "backtest"})
    client = _V3Client()
    publish_typed_squeeze_batch_v3(client, unit)
    assert client.inserts == ["trading_event_v1", "trading_portfolio_control_v3",
                              "trading_commit_v3"]
    assert client.tables["trading_commit_v3"][0]["portfolio_control_count"] == 1
    publish_typed_squeeze_batch_v3(client, unit)
    assert len(client.tables["trading_portfolio_control_v3"]) == 1
    client.tables["trading_portfolio_control_v3"][0]["reason"] = "tampered"
    with pytest.raises(RuntimeError, match="durable readback"):
        publish_typed_squeeze_batch_v3(client, unit)


def test_coalesced_control_microbatch_preserves_both_child_identities(monkeypatch):
    journal = BacktestMemoryJournal(run_id=RUN)
    for mode in ("entries_paused", "reduce_only"):
        journal.append(run_id=RUN, category="portfolio_management",
                       entity_type="portfolio_control", entity_id="primary",
                       account_id="DU1", event_time=AT,
                       payload={"event": "control_changed", "control_mode": mode,
                                "reason": "operator"})
    units = project_pending_backtest_v3_prefix(
        journal, attempt_id=ATTEMPT, run_month=AT.date().replace(day=1),
        prior_sequence=0, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    merged = coalesce_squeeze_units_v3(units)
    assert len(merged.base.events) == len(merged.portfolio_controls) == 2
    assert {row["batch_id"] for row in merged.portfolio_controls} == {
        merged.base.batch_id}
    monkeypatch.setattr(writer_module, "_v3_preflight", lambda client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda client, run: {
        "mode": "backtest"})
    client = _V3Client()
    publish_typed_squeeze_batch_v3(client, merged)
    assert client.tables["trading_commit_v3"][0]["portfolio_control_count"] == 2


def test_v3_cold_reader_rejects_missing_and_corrupt_control(monkeypatch):
    import src.backend.backtest_squeeze_episode_v3 as cold_module

    monkeypatch.setattr(cold_module, "storage_preflight", lambda client, *, tables: None)
    monkeypatch.setattr(cold_module, "_verify_recovery_chunk",
                        lambda client, commits, *, journal_profile: None)
    journal = BacktestMemoryJournal(run_id=RUN)
    journal.append(run_id=RUN, category="portfolio_management",
                   entity_type="portfolio_control", entity_id="primary",
                   account_id="DU1", event_time=AT,
                   payload={"event": "control_changed", "control_mode": "reduce_only",
                            "reason": "operator"})
    unit, = project_pending_backtest_v3_prefix(
        journal, attempt_id=ATTEMPT, run_month=AT.date().replace(day=1),
        prior_sequence=0, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64)
    monkeypatch.setattr(writer_module, "_v3_preflight", lambda client: None)
    monkeypatch.setattr(writer_module, "_verify_run_identity", lambda client, run: {
        "mode": "backtest"})
    writer = _V3Client()
    publish_typed_squeeze_batch_v3(writer, unit)
    commit = writer.tables["trading_commit_v3"][0]
    parent = writer.tables["trading_event_v1"][0]
    detail = writer.tables["trading_portfolio_control_v3"][0]
    cold = _FakeColdClient(commit, [], [])
    cold.control_events = [parent]
    cold.portfolio_controls = [detail]
    assert load_verified_squeeze_v3_prefix(
        cold, RUN, expected_market_plan_token="a" * 64,
        expected_query_sha256="b" * 64) is not None
    cold.portfolio_controls = []
    with pytest.raises(ValueError, match="lacks exact typed child"):
        load_verified_squeeze_v3_prefix(
            cold, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)
    cold.portfolio_controls = [{**detail, "reason": "tampered"}]
    with pytest.raises(ValueError, match="content hash differs"):
        load_verified_squeeze_v3_prefix(
            cold, RUN, expected_market_plan_token="a" * 64,
            expected_query_sha256="b" * 64)
