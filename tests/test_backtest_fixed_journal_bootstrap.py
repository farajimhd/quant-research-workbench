"""Inactive fixed journal assembly performs no disk or database writes."""
from datetime import date, datetime, timezone
import json
import re

import pytest

from src.backend import backtest_fixed_journal_bootstrap as bootstrap


RUN = "00000000-0000-0000-0000-000000000a01"
ATTEMPT = "00000000-0000-0000-0000-000000000a02"


def test_operator_check_names_missing_tables_without_any_write(monkeypatch):
    names = tuple(table.name for table in bootstrap.fixed_backtest_v2_contracts())
    calls = []
    class Catalog:
        def __init__(self, installed):
            self.installed = installed
        def execute(self, sql):
            calls.append(sql)
            assert sql.startswith("SELECT name FROM system.tables")
            selected = set(re.findall(r"'([^']+)'", sql))
            return "\n".join(json.dumps({"name": name}) for name in self.installed
                             if name in selected)
    missing = bootstrap.fixed_journal_operator_check(Catalog(names[:-2]))
    assert missing["status"] == "blocked"
    v3 = [table.name for table in (
        bootstrap.SQUEEZE_EPISODE, bootstrap.RESERVATION_REASON,
            bootstrap.RECONCILIATION_DIFFERENCE,
            *bootstrap.TRADE_PROPOSAL_TABLES,
            *bootstrap.PROTECTION_CHANGE_TABLES,
            bootstrap.SQUEEZE_COMMIT_V3, bootstrap.TERMINAL_COMMIT_V3,
    )]
    assert missing["evidence"]["missing_tables"] == list(names[-2:]) + v3
    checked = []
    monkeypatch.setattr(bootstrap, "terminal_v2_operator_preflight",
                        lambda *_: checked.append("verified"))
    staged = bootstrap.fixed_journal_operator_check(Catalog((*names, *v3)))
    assert staged["status"] == "blocked" and checked == []
    assert "V3 terminal grants" in staged["summary"]
    assert all(sql.startswith("SELECT ") for sql in calls)
    with pytest.raises(RuntimeError, match="ambiguous"):
        bootstrap.fixed_journal_operator_check(Catalog((*names, names[0])))


def _verified(monkeypatch):
    checked = []
    monkeypatch.setattr(bootstrap, "storage_preflight",
                        lambda *_, **kwargs: checked.append(("schema", kwargs["tables"])))
    monkeypatch.setattr(bootstrap, "terminal_v2_operator_preflight",
                        lambda *_: checked.append("v2"))
    monkeypatch.setattr(bootstrap, "terminal_v2_keeper_proof_preflight",
                        lambda *_: checked.append("keeper"))
    context = {"mode": "backtest", "account_ids": ("DU1",),
               "run_month": "2026-08-01", "configuration_hash": "c" * 64,
               "market_plan_token": "market-token"}
    monkeypatch.setattr(bootstrap, "load_typed_run_context", lambda *_, **__: context)
    monkeypatch.setattr(bootstrap, "verify_fixed_run_context",
                        lambda _dispatch, _read, _terminal, *, run_id: context)
    return checked, context


def test_bootstrap_requires_keeper_context_receipt(monkeypatch):
    _verified(monkeypatch)
    def no_receipt(*args, **kwargs):
        raise RuntimeError("Keeper context receipt is missing")
    monkeypatch.setattr(bootstrap, "verify_fixed_run_context", no_receipt)
    with pytest.raises(RuntimeError, match="Keeper context receipt"):
        bootstrap.prepare_fixed_journal_token(
            object(), object(), object(), run_id=RUN, account_ids=("DU1",),
            configuration_hash="c" * 64, market_plan_token="market-token",
            projection_certifier=lambda: "a" * 64)


def test_bootstrap_assembles_bounded_in_memory_lane_without_writes(monkeypatch):
    checked, _ = _verified(monkeypatch)
    read_client, writer_client, terminal_client, keeper = (
        object(), object(), object(), object())
    token = bootstrap.prepare_fixed_journal_token(
        read_client, terminal_client, keeper, run_id=RUN, account_ids=("DU1",),
        configuration_hash="c" * 64, market_plan_token="market-token",
        projection_certifier=lambda: "a" * 64)
    assert [entry[0] if isinstance(entry, tuple) else entry for entry in checked] == [
        "schema", "v2", "keeper"]
    assert {table.name for table in checked[0][1]} == {
        table.name for table in bootstrap.fixed_backtest_v2_contracts()}
    class Writer:
        run_id = RUN
        run_mode = "backtest"
        journal_profile = "backtest_v2"
        coalesce_batches = False
        max_events_per_commit = 512
        def close(self):
            pass
    calls = []
    def factory(*args, **kwargs):
        calls.append((args, kwargs))
        return Writer()
    assembly = bootstrap.assemble_fixed_journal(
        read_client, writer_client, terminal_client, keeper, token,
        attempt_id=ATTEMPT,
        expected_config={"mode": "backtest"},
        fixed_market_parent_plan=object(), fixed_market_execution_plan=object(),
        expected_market_start=datetime(2026, 8, 18, tzinfo=timezone.utc),
        writer_factory=factory)
    assert assembly.journal.run_id == RUN
    assert assembly.publisher.writer is assembly.writer
    assert assembly.terminal_authority.account_ids == ("DU1",)
    assert calls == [((writer_client,), {"run_id": RUN, "capacity": 8,
                      "max_events_per_commit": 512, "coalesce_batches": False,
                      "journal_profile": "backtest_v2"})]
    assert assembly.terminal_authority.client._client is terminal_client
    assembly.journal.close()


def test_bootstrap_rejects_unverified_context_or_missing_projection(monkeypatch):
    _, context = _verified(monkeypatch)
    with pytest.raises(ValueError, match="projection"):
        bootstrap.prepare_fixed_journal_token(
            object(), object(), object(), run_id=RUN, account_ids=("DU1",),
            configuration_hash="c" * 64, market_plan_token="market-token",
            projection_certifier=None)
    context["market_plan_token"] = "changed"
    with pytest.raises(RuntimeError, match="differs"):
        bootstrap.prepare_fixed_journal_token(
            object(), object(), object(), run_id=RUN, account_ids=("DU1",),
            configuration_hash="c" * 64, market_plan_token="market-token",
            projection_certifier=lambda: "a" * 64)


def test_bootstrap_refuses_shared_clients_or_divergent_writer_view(monkeypatch):
    _verified(monkeypatch)
    read_client, writer_client, terminal_client, keeper = (
        object(), object(), object(), object())
    token = bootstrap.prepare_fixed_journal_token(
        read_client, terminal_client, keeper, run_id=RUN, account_ids=("DU1",),
        configuration_hash="c" * 64, market_plan_token="market-token",
        projection_certifier=lambda: "a" * 64)
    kwargs = dict(attempt_id=ATTEMPT, expected_config={"mode": "backtest"},
                  fixed_market_parent_plan=object(),
                  fixed_market_execution_plan=object(),
                  expected_market_start=datetime(2026, 8, 18, tzinfo=timezone.utc),
                  writer_factory=lambda *_, **__: (_ for _ in ()).throw(
                      AssertionError("writer must not start")))
    with pytest.raises(ValueError, match="bounded certified inputs"):
        bootstrap.assemble_fixed_journal(
            read_client, read_client, terminal_client, keeper, token, **kwargs)
    context = {"mode": "backtest", "account_ids": ("DU1",),
               "run_month": "2026-08-01", "configuration_hash": "c" * 64,
               "market_plan_token": "market-token"}
    monkeypatch.setattr(bootstrap, "load_typed_run_context",
                        lambda client, *_: {**context, "market_plan_token": "changed"}
                        if client is writer_client else context)
    with pytest.raises(RuntimeError, match="Batch writer observes"):
        bootstrap.assemble_fixed_journal(
            read_client, writer_client, terminal_client, keeper, token, **kwargs)


def test_v3_bootstrap_uses_distinct_principals_and_bounded_writer(monkeypatch):
    read, writer_client, terminal, keeper = object(), object(), object(), object()
    checked = []
    for name in ("read_v3_preflight", "running_v3_preflight",
                 "terminal_v3_preflight", "terminal_v3_keeper_namespace_preflight"):
        monkeypatch.setattr(bootstrap, name,
                            lambda client, name=name: checked.append((name, client)))
    context = {"mode": "backtest", "account_ids": ("DU1",),
               "run_month": "2026-08-01", "configuration_hash": "c" * 64,
               "market_plan_token": "b" * 64}
    monkeypatch.setattr(bootstrap, "verify_fixed_run_context",
                        lambda *_, **__: context)
    monkeypatch.setattr(bootstrap, "load_typed_run_context",
                        lambda *_, **__: context)
    token = bootstrap.prepare_fixed_v3_journal_token(
        read, writer_client, terminal, keeper, run_id=RUN,
        account_ids=("DU1",), configuration_hash="c" * 64,
        market_plan_token="b" * 64, expected_query_sha256="d" * 64,
        query_hash_certifier=lambda: "d" * 64,
        projection_certifier=lambda: "a" * 64)
    assert checked == [("read_v3_preflight", read),
                       ("running_v3_preflight", writer_client),
                       ("terminal_v3_preflight", terminal),
                       ("terminal_v3_keeper_namespace_preflight", keeper)]
    class Writer:
        run_id = RUN
        run_mode = "backtest"
        journal_profile = "backtest_v3"
        coalesce_batches = False
        max_events_per_commit = 128
        def close(self):
            pass
    calls = []
    def factory(client, **kwargs):
        calls.append((client, kwargs))
        return Writer()
    assembly = bootstrap.assemble_fixed_v3_journal(
        read, writer_client, terminal, keeper, token, attempt_id=ATTEMPT,
        expected_config={"mode": "backtest"},
        fixed_market_parent_plan=object(), fixed_market_execution_plan=object(),
        expected_market_start=datetime(2026, 8, 18, tzinfo=timezone.utc),
        writer_factory=factory, batch_size=128)
    assert calls[0][0] is writer_client
    assert calls[0][1]["journal_profile"] == "backtest_v3"
    assert assembly.publisher.batch_size == 128
    assert assembly.terminal_authority.client._client is terminal
    assembly.journal.close()


def test_v3_bootstrap_rejects_mismatched_query_before_writer(monkeypatch):
    read, writer_client, terminal, keeper = object(), object(), object(), object()
    for name in ("read_v3_preflight", "running_v3_preflight",
                 "terminal_v3_preflight", "terminal_v3_keeper_namespace_preflight"):
        monkeypatch.setattr(bootstrap, name, lambda *_: None)
    context = {"mode": "backtest", "account_ids": ("DU1",),
               "run_month": "2026-08-01", "configuration_hash": "c" * 64,
               "market_plan_token": "b" * 64}
    monkeypatch.setattr(bootstrap, "verify_fixed_run_context",
                        lambda *_, **__: context)
    monkeypatch.setattr(bootstrap, "load_typed_run_context",
                        lambda *_, **__: context)
    with pytest.raises(RuntimeError, match="query hash"):
        bootstrap.prepare_fixed_v3_journal_token(
            read, writer_client, terminal, keeper, run_id=RUN,
            account_ids=("DU1",), configuration_hash="c" * 64,
            market_plan_token="b" * 64, expected_query_sha256="d" * 64,
            query_hash_certifier=lambda: "e" * 64,
            projection_certifier=lambda: "a" * 64)
