from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.clickhouse import provision_backtest_v4_program_runner as program
from scripts.clickhouse import provision_backtest_v4_runner as legacy
from src.trading_runtime.rule_decision_basis_schema import STAGED_TABLES
from src.trading_runtime.rule_decision_writer_preflight import rule_decision_write_tables


def test_program_plan_preserves_legacy_and_uses_central_contracts():
    before = legacy.desired_plan()
    plan = program.desired_plan()
    tables = {table.name for table in STAGED_TABLES}
    assert plan.principal == "backtest_v4_program_runner"
    assert plan.insert_arte == rule_decision_write_tables()
    from src.trading_runtime.trading_price_contracts import exact_price_storage_contracts
    exact = {table.name for table in exact_price_storage_contracts()}
    assert len(exact) == 11
    from src.trading_runtime.arte_entry_acquisition_freeze import FAMILY_NAMES
    from src.trading_runtime.backtest_journal_price_contract import PRICE_CONTRACT
    from src.trading_runtime.columnar_strategy_schema import BROKER
    assert plan.insert_arte - before.insert_arte == tables | exact | FAMILY_NAMES | {PRICE_CONTRACT.name, BROKER.name}
    assert plan.select_arte - before.select_arte == tables | exact | FAMILY_NAMES | {PRICE_CONTRACT.name, BROKER.name}
    assert before.select_arte - plan.select_arte == {program.ENTRY_CONTEXT_TABLE.split(".")[1]}
    assert plan.select_reference == before.select_reference
    assert plan.select_system == before.select_system
    assert legacy.desired_plan() == before
    assert all("CREATE" not in grant and "ALTER" not in grant for grant in plan.grants())
    assert all(program.PRINCIPAL in grant for grant in plan.grants())


def test_default_plan_has_no_network_or_credential_operations(capsys):
    with patch.object(program, "_admin_client", side_effect=AssertionError), \
         patch.object(program, "credential", side_effect=AssertionError):
        assert program.main([]) == 0
    output = capsys.readouterr().out
    assert "Plan only" in output and "backtest_v4_program_runner" in output
    assert "\x1b" not in output


def test_apply_requires_explicit_confirmation_before_any_access():
    with patch.object(program, "_admin_client", side_effect=AssertionError), \
         patch.object(program, "credential", side_effect=AssertionError):
        with pytest.raises(SystemExit) as stopped:
            program.main(["--apply"])
        assert stopped.value.code == 2


def test_storage_failure_prevents_credential_or_grant_mutation(monkeypatch):
    calls = []
    def execute(sql):
        calls.append(sql)
        return "admin" if sql == "SELECT currentUser()" else "0"
    def audit(client, *, tables):
        assert {table.name for table in STAGED_TABLES} <= {table.name for table in tables}
        raise RuntimeError("Incorrect physical part placement")
    monkeypatch.setattr(program, "storage_preflight", audit)
    def forbidden(*args, **kwargs):
        raise AssertionError("Storage audit must precede credential and writer creation")
    with pytest.raises(RuntimeError, match="physical part placement"):
        program.apply_with_clients(admin=SimpleNamespace(execute=execute),
            credential=forbidden, client_factory=forbidden)
    assert len(calls) == 2 and not any(sql.startswith(("GRANT", "CREATE")) for sql in calls)


def test_admin_connection_failure_does_not_print_private_exception(monkeypatch, capsys):
    monkeypatch.setattr(program.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(program, "SECRET_ROOT", SimpleNamespace(is_dir=lambda: True))
    monkeypatch.setattr(program.socket, "getaddrinfo", lambda *args, **kwargs:
                        [(None, None, None, None, (program.WORKSTATION_IPV4, 18123))])
    def fail(transport):
        raise RuntimeError("synthetic-private-credential-value")
    monkeypatch.setattr(program, "_admin_client", fail)
    assert program.main(["--apply", "--confirm-program-runner"]) == 1
    output = capsys.readouterr()
    assert "RuntimeError" in output.err
    assert "synthetic-private" not in output.out + output.err
