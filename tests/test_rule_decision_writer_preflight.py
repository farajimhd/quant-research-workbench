import pytest
from types import SimpleNamespace

from src.trading_runtime import rule_decision_writer_preflight as module
from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime import rule_decision_publication as publication
from src.trading_runtime.rule_decision_basis_schema import STAGED_TABLES


def test_program_write_catalog_is_exact_extension_without_legacy_mutation():
    original = writer.v4_journal_write_tables()
    assert module.rule_decision_write_tables() == original | {table.name for table in STAGED_TABLES}
    assert writer.v4_journal_write_tables() == original


@pytest.mark.parametrize("failure", [None, "storage", "grants"])
def test_preflight_orders_readonly_storage_and_exact_grant_audits(monkeypatch, failure):
    client = SimpleNamespace(program_decision_profile=True,
                             execute=lambda sql: "backtest_v4_program_runner")
    calls = []
    def storage(actual):
        assert actual is client
        calls.append("storage")
        if failure == "storage":
            raise RuntimeError("storage")
    def grants(actual, *, journal_tables, read_only_tables):
        assert actual is client
        assert journal_tables == module.rule_decision_write_tables()
        assert read_only_tables == {table.name for table in writer.fixed_backtest_v2_contracts()} - journal_tables
        calls.append("grants")
        if failure == "grants":
            raise RuntimeError("grants")
    monkeypatch.setattr(publication, "preflight_rule_decision_storage", storage)
    monkeypatch.setattr(writer, "journal_permission_preflight", grants)
    if failure:
        with pytest.raises(RuntimeError, match=failure):
            module.preflight_rule_decision_writer(client)
    else:
        module.preflight_rule_decision_writer(client)
    assert calls == (["storage"] if failure == "storage" else ["storage", "grants"])


@pytest.mark.parametrize("attribute", ["live_v4_lease", "automatic_ladder_profile", "entry_spread_risk_profile"])
def test_other_writer_profiles_rejected_before_storage_access(attribute):
    with pytest.raises(ValueError, match="independent Backtest profile"):
        module.preflight_rule_decision_writer(type("Client", (), {attribute: True})())


def test_program_credentials_are_separate_and_profile_combinations_fail_closed(monkeypatch):
    calls = []
    def credentials(prefix, key):
        calls.append((prefix, key))
        return "http://localhost:18123", "backtest_v4_program_runner", "private-test-value"
    monkeypatch.setattr(writer, "_dedicated_clickhouse_credentials", credentials)
    assert writer._v4_runner_credentials(program_decisions=True)[1] == "backtest_v4_program_runner"
    assert calls == [("BACKTEST_V4_PROGRAM_RUNNER_CLICKHOUSE_", "BACKTEST_V4_PROGRAM_RUNNER_CREDENTIAL_FILE")]
    for options in ({"program_decisions": 1}, {"program_decisions": True, "automatic_ladder": True},
                    {"program_decisions": True, "entry_spread_risk": True}):
        with pytest.raises(ValueError, match="Program runner profile"):
            writer._v4_runner_credentials(**options)
    assert len(calls) == 1


@pytest.mark.parametrize("profile,principal", [(False, "backtest_v4_program_runner"),
                                              (True, "backtest_v4_runner")])
def test_program_preflight_rejects_wrong_profile_or_principal(profile, principal):
    client = SimpleNamespace(program_decision_profile=profile, execute=lambda sql: principal)
    with pytest.raises(RuntimeError, match="dedicated principal"):
        module.preflight_rule_decision_writer(client)


def test_shared_startup_uses_program_audit_once(monkeypatch):
    client = SimpleNamespace(program_decision_profile=True)
    calls = []
    monkeypatch.setattr(module, "preflight_rule_decision_writer", lambda actual: calls.append(actual))
    def forbidden(*args, **kwargs):
        raise AssertionError("Program startup repeated legacy catalog audit")
    monkeypatch.setattr(writer, "storage_preflight", forbidden)
    seal = writer._v4_preflight(client)
    assert seal.client is client and not seal.used
    assert calls == [client]
