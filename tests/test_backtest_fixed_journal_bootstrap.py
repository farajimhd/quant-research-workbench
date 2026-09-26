"""Inactive fixed journal assembly performs no disk or database writes."""
from datetime import date, datetime, timezone
import json
import re
from types import SimpleNamespace

import pytest

from src.backend import backtest_fixed_journal_bootstrap as bootstrap
from src.backend.replay_run_service import ReplayRunController, RunMode


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


def test_v4_bootstrap_requires_strict_writer_and_attaches_without_v2_terminal(monkeypatch):
    read, writer_client, terminal = object(), SimpleNamespace(), object()
    dispatch = bootstrap.TypedInsertDispatch(object())
    writer_client.typed_insert_dispatch = dispatch
    writer_client.typed_insert_strict = True
    context = {"mode": "backtest", "account_ids": ("DU1",),
               "run_month": "2026-08-01", "configuration_hash": "c" * 64,
               "market_plan_token": "b" * 64}
    checked = []
    monkeypatch.setattr(bootstrap, "storage_preflight",
                        lambda client, **kw: checked.append((client, kw["tables"])))
    monkeypatch.setattr(bootstrap, "_v4_preflight",
                        lambda client: checked.append((client, "v4")))
    monkeypatch.setattr(bootstrap, "verify_fixed_run_context",
                        lambda found, *_args, **_kw: context if found is dispatch
                        else pytest.fail("wrong dispatch"))
    monkeypatch.setattr(bootstrap, "load_typed_run_context",
                        lambda *_args, **_kw: context)
    token = bootstrap.prepare_fixed_v4_journal_token(
        read, writer_client, terminal, run_id=RUN, account_ids=("DU1",),
        configuration_hash="c" * 64, market_plan_token="b" * 64,
        projection_certifier=lambda: "a" * 64)
    from src.trading_runtime.arte_strategy_one_entry_schema import ENTRY_EVIDENCE
    from src.trading_runtime.arte_broker_acknowledgement_v4 import ACKNOWLEDGEMENT

    assert len(checked) == 3
    expected = {table.name for table in bootstrap.v4_storage_contracts()}
    assert checked[0][0] is read and {table.name for table in checked[0][1]} == expected
    assert checked[1][0] is terminal and {table.name for table in checked[1][1]} == expected
    assert {ENTRY_EVIDENCE.name, ACKNOWLEDGEMENT.name,
            *(table.name for table in bootstrap.PROTECTION_CHANGE_TABLES)} <= expected
    assert checked[2] == (writer_client, "v4")
    class Writer:
        run_id = RUN
        run_mode = "backtest"
        journal_profile = "backtest_v4"
        coalesce_batches = False
        max_events_per_commit = 512
        def close(self):
            pass
    assembly = bootstrap.assemble_fixed_v4_journal(
        read, writer_client, terminal, token, attempt_id=ATTEMPT,
        expected_config={"mode": "backtest"},
        fixed_market_parent_plan=object(), fixed_market_execution_plan=object(),
        expected_market_start=datetime(2026, 8, 18, tzinfo=timezone.utc),
        writer_factory=lambda client, **kwargs: Writer())
    assert assembly.terminal_authority is None
    controller = object.__new__(ReplayRunController)
    controller.run_id = RUN
    controller.definition = SimpleNamespace(
        mode=RunMode.BACKTEST,
        configuration_revision={"content_hash": "c" * 64},
        market_data_plan={"token": "b" * 64})
    controller._journal = None
    controller._attach_fixed_journal_assembly(assembly)
    assert controller._journal_publisher is assembly.publisher
    assembly.journal.close()
    writer_client.typed_insert_strict = False
    with pytest.raises(ValueError, match="strict pinned authorities"):
        bootstrap.prepare_fixed_v4_journal_token(
            read, writer_client, terminal, run_id=RUN, account_ids=("DU1",),
            configuration_hash="c" * 64, market_plan_token="b" * 64,
            projection_certifier=lambda: "a" * 64)


def test_v4_publication_preflights_before_keeper_gate(monkeypatch):
    from src.backend import backtest_fixed_market_authority
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch

    calls = []
    keeper = object()
    context = SimpleNamespace(typed_insert_strict=True,
        typed_insert_dispatch=TypedInsertDispatch(keeper))
    writer = SimpleNamespace(typed_insert_strict=True,
        typed_insert_dispatch=TypedInsertDispatch(keeper))
    read, terminal = object(), object()
    market = SimpleNamespace(token="b" * 64)
    run = dict(run_id=RUN, run_month="2026-08-01", mode="backtest",
        evaluation_interval_ms=100, session_date="2026-08-18",
        configuration_hash="c" * 64, code_hash="d" * 64,
        market_plan_token=market.token, started_at="2026-08-18T08:00:00+00:00")
    config = dict(strategy_id="S", strategy_revision=1,
        anchor_date="2026-08-18", run_plan_id="plan-1",
        safety_supervisor_enabled=True, checkpoint_interval_events=100,
        write_progress_checkpoints=True)
    monkeypatch.setattr(backtest_fixed_market_authority, "_validate_plans",
                        lambda *_: calls.append("market"))
    for name in ("fixed_backtest_v2_preflight", "read_v3_preflight",
                 "terminal_v3_preflight", "_v4_preflight"):
        monkeypatch.setattr(bootstrap, name,
                            lambda _client, name=name: calls.append(name))
    monkeypatch.setattr(bootstrap, "publish_fixed_run_context",
                        lambda *_args, **_kwargs: calls.append("gate"))
    monkeypatch.setattr(bootstrap, "prepare_fixed_v4_journal_token",
                        lambda *_args, **_kwargs: calls.append("token") or object())
    assembly = object()
    monkeypatch.setattr(bootstrap, "assemble_fixed_v4_journal",
                        lambda *_args, **_kwargs: calls.append("assembly") or assembly)
    kwargs = dict(run=run, config=config, account_ids=("DU1",),
        attempt_id=ATTEMPT, expected_config={"strategy": {"strategy_number": 1}},
        fixed_market_parent_plan=market, fixed_market_execution_plan=market,
        expected_market_start=datetime(2026, 8, 18, tzinfo=timezone.utc),
        writer_factory=lambda *_args, **_kwargs: None)
    assert bootstrap.publish_and_assemble_fixed_v4_journal(
        context, read, writer, terminal,
        projection_certifier=lambda: calls.append("certificate") or "a" * 64,
        **kwargs) is assembly
    assert calls == ["market", "fixed_backtest_v2_preflight",
                     "read_v3_preflight", "terminal_v3_preflight",
                     "_v4_preflight", "certificate", "gate", "token", "assembly"]
    calls.clear()
    with pytest.raises(RuntimeError, match="projector"):
        bootstrap.publish_and_assemble_fixed_v4_journal(
            context, read, writer, terminal,
            projection_certifier=lambda: "invalid", **kwargs)
    assert "gate" not in calls
    different = SimpleNamespace(typed_insert_strict=True,
        typed_insert_dispatch=TypedInsertDispatch(object()))
    calls.clear()
    with pytest.raises(ValueError, match="shared-Keeper"):
        bootstrap.publish_and_assemble_fixed_v4_journal(
            context, read, different, terminal,
            projection_certifier=lambda: "a" * 64, **kwargs)
    assert calls == []
    calls.clear()
    with pytest.raises(ValueError, match="Strategy 1"):
        bootstrap.publish_and_assemble_fixed_v4_journal(
            context, read, writer, terminal,
            projection_certifier=lambda: "a" * 64,
            **{**kwargs, "expected_config": {"strategy": {"strategy_number": 350}}})
    assert calls == ["market"]
