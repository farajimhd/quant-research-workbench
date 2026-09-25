from datetime import date, time
import json

import pytest

from src.backend.replay_run_service import ReplayRunDefinition, RunMode
from src.trading_runtime.arte_backtest_definition import (
    TABLES, load_backtest_definition, prepare_backtest_definition,
    verify_backtest_definition_rows,
)
from src.trading_runtime.arte_journal_schema import (
    backtest_definition_ddl, fixed_backtest_v2_contracts,
)


RUN_MONTH = date(2026, 9, 1)  # September execution of an August market session.


def _definition(**changes):
    values = dict(
        session_date=date(2026, 8, 18), start_time=time(4, 0),
        end_time=time(9, 30), initial_cash=100_000.0,
        assignment_ids=("assignment-a", "assignment-b"),
        tickers=("ABCD", "EFGH"),
        configuration_revision={"revision_id": "revision-1",
                                "content_hash": "a" * 64},
        execution_interval="100ms",
        market_data_plan={"token": "certified-plan", "build_id": "build-1",
                          "execution_interval": {"milliseconds": 100}},
        causal_v7_plan={"token": "causal-plan", "build_id": "build-1",
                        "catalog_hash": "b" * 64},
        mode=RunMode.BACKTEST,
        new_order_activation_delay_ms=0.5,
    )
    values.update(changes)
    return ReplayRunDefinition(**values)


def test_fixed_definition_is_typed_ordered_and_contains_no_json_or_blob():
    fixed_contracts = fixed_backtest_v2_contracts()
    names = [table.name for table in fixed_contracts]
    assert len(names) == len(set(names))
    assert {table.name for table in TABLES} <= set(names)
    assert all("JSON" not in column_type and "Blob" not in column_type
               for table in TABLES for _, column_type in table.columns)
    ddl = backtest_definition_ddl()
    assert len(ddl) == len(TABLES)
    assert all("live_market_ssd" in statement and "CREATE TABLE IF NOT EXISTS arte."
               in statement for statement in ddl)
    prepared = prepare_backtest_definition("run-1", _definition(), run_month=RUN_MONTH)
    parent = prepared["definition"]
    assert parent["start_local_ms"] == 4 * 60 * 60 * 1000
    assert parent["end_local_ms"] == (9 * 60 + 30) * 60 * 1000
    assert parent["activation_delay_us"] == 500
    assert parent["initial_cash"] == "100000.0000000000"
    assert parent["configuration_revision_id"] == "revision-1"
    assert parent["causal_v7_plan_token"] == "causal-plan"
    assert parent["structure_book"] == "level-book-v7"
    assert parent["structure_fingerprint"] == "b" * 64
    assert [row["ticker"] for row in prepared["tickers"]] == ["ABCD", "EFGH"]
    assert [row["assignment_id"] for row in prepared["assignments"]] == [
        "assignment-a", "assignment-b"]
    assert prepared["commit"]["definition_hash"] == parent["content_hash"]
    assert parent["run_month"] == RUN_MONTH.isoformat()
    assert prepared == prepare_backtest_definition(
        "run-1", _definition(), run_month=RUN_MONTH)
    assert verify_backtest_definition_rows(
        definitions=(prepared["definition"],), tickers=prepared["tickers"],
        assignments=prepared["assignments"],
        commits=(prepared["commit"],)) == prepared
    stored_parent = {**parent, "initial_cash": "100000"}
    assert verify_backtest_definition_rows(
        definitions=(stored_parent,), tickers=prepared["tickers"],
        assignments=prepared["assignments"],
        commits=(prepared["commit"],)) == prepared
    assert verify_backtest_definition_rows(
        definitions=(stored_parent,), tickers=tuple(reversed(prepared["tickers"])),
        assignments=tuple(reversed(prepared["assignments"])),
        commits=(prepared["commit"],)) == prepared
    assert all("storage_policy = 'live_market_ssd'" in table.ddl()
               for table in TABLES)
    assert all(not isinstance(value, (dict, list, tuple))
               for family in prepared.values()
               for row in ((family,) if isinstance(family, dict) else family)
               for value in row.values())


def test_definition_rejects_missing_identity_and_imprecise_scalars():
    with pytest.raises(ValueError, match="pinned configuration"):
        prepare_backtest_definition("run-1", _definition(
            configuration_revision={"revision_id": "revision-1"}), run_month=RUN_MONTH)
    with pytest.raises(ValueError, match="losslessly"):
        prepare_backtest_definition("run-1", _definition(
            initial_cash=100_000.00000000001), run_month=RUN_MONTH)
    with pytest.raises(ValueError, match="exact microseconds"):
        prepare_backtest_definition("run-1", _definition(
            new_order_activation_delay_ms=0.0001), run_month=RUN_MONTH)
    with pytest.raises(ValueError, match="normal fixed"):
        prepare_backtest_definition("run-1", _definition(
            mode=RunMode.REPLAY, archived_review_only=True), run_month=RUN_MONTH)
    with pytest.raises(ValueError, match="normal fixed"):
        prepare_backtest_definition("run-1", _definition(), run_month=date(2026, 9, 2))


def test_empty_explicit_ticker_list_pins_market_plan_population():
    prepared = prepare_backtest_definition(
        "run-1", _definition(tickers=()), run_month=RUN_MONTH)
    assert prepared["tickers"] == ()
    assert prepared["definition"]["ticker_population_mode"] == "market_plan"
    assert prepared["commit"]["ticker_count"] == 0


def test_cold_definition_rejects_tampered_or_duplicate_membership():
    prepared = prepare_backtest_definition("run-1", _definition(), run_month=RUN_MONTH)
    def verify(*, parent=None, tickers=None, commit=None):
        return verify_backtest_definition_rows(
            definitions=(parent or prepared["definition"],),
            tickers=tickers if tickers is not None else prepared["tickers"],
            assignments=prepared["assignments"],
            commits=(commit or prepared["commit"],))
    with pytest.raises(ValueError, match="content hash differs"):
        verify(parent={**prepared["definition"], "initial_cash": "1"})
    with pytest.raises(ValueError, match="membership differs"):
        verify(tickers=(prepared["tickers"][0], prepared["tickers"][0]))
    with pytest.raises(ValueError, match="commit differs"):
        verify(commit={**prepared["commit"], "assignment_count": 0})


def test_cold_loader_reads_only_exact_definition_tables_and_run_identity():
    prepared = prepare_backtest_definition("run-1", _definition(), run_month=RUN_MONTH)
    names = {"trading_backtest_definition_v1": [prepared["definition"]],
             "trading_backtest_ticker_v1": prepared["tickers"],
             "trading_backtest_assignment_v1": prepared["assignments"],
             "trading_backtest_definition_commit_v1": [prepared["commit"]]}
    class Client:
        queries = []
        def execute(self, sql):
            self.queries.append(sql)
            assert sql.startswith("SELECT ") and "WHERE run_id='run-1'" in sql
            table = sql.split("FROM arte.", 1)[1].split(" ", 1)[0]
            return "\n".join(json.dumps(row) for row in names[table])
    client = Client()
    context = {"run_id": "run-1", "run_month": RUN_MONTH.isoformat(),
               "mode": "backtest", "session_date": "2026-08-18",
               "evaluation_interval_ms": 100,
               "market_plan_token": "certified-plan",
               "configuration_hash": "a" * 64}
    assert load_backtest_definition(client, "run-1", run_context=context) == prepared
    assert len(client.queries) == 4
    with pytest.raises(RuntimeError, match="shared run authority"):
        load_backtest_definition(client, "run-1", run_context={
            **context, "market_plan_token": ""})
    for invalid_context in (
        {**context, "evaluation_interval_ms": 0},
        {**context, "evaluation_interval_ms": 150},
        {**context, "evaluation_interval_ms": True},
        {**context, "run_month": "2026-07-01"},
    ):
        with pytest.raises(RuntimeError, match="shared run authority"):
            load_backtest_definition(client, "run-1", run_context=invalid_context)
