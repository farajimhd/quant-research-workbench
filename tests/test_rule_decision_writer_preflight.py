import pytest
from types import SimpleNamespace

from src.trading_runtime import rule_decision_writer_preflight as module
from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime import rule_decision_publication as publication
from src.trading_runtime.rule_decision_basis_schema import STAGED_TABLES


@pytest.mark.parametrize('corruption', [None, 'missing', 'insert', 'unknown', 'wildcard'])
def test_native_runner_read_catalog_passes_real_grant_guard_and_rejects_expansion(monkeypatch, corruption):
    import json
    import re
    from scripts.clickhouse.provision_backtest_v4_program_runner import desired_plan
    from src.backend.signal_context_schema import TABLES
    from src.trading_runtime.arte_journal_schema import TableContract
    plan = desired_plan()
    sources = module.program_signal_source_read_contracts()
    assert sources == tuple(TableContract(t.name, t.columns, 'toYYYYMM(session_key)', t.order) for t in TABLES)
    assert {t.name for t in sources}.isdisjoint(plan.insert_arte)
    grants = list(plan.grants())
    target = sources[0].name
    if corruption == 'missing':
        grants.remove(f'GRANT SELECT ON arte.{target} TO {plan.principal}')
    elif corruption == 'insert':
        grants.append(f'GRANT INSERT ON arte.{target} TO {plan.principal}')
    elif corruption == 'unknown':
        grants.append(f'GRANT SELECT ON arte.unrelated_table TO {plan.principal}')
    elif corruption == 'wildcard':
        grants.append(f'GRANT SELECT ON arte.* TO {plan.principal}')
    def execute(sql):
        if sql == 'SELECT currentUser()':
            return plan.principal
        if sql == 'SHOW GRANTS FINAL':
            return '\n'.join(grants)
        if 'FROM system.tables' in sql:
            match = re.search(r'name IN \((.*?)\)', sql)
            names = re.findall(r"'([^']+)'", match[1]) if match else []
            return '\n'.join(json.dumps(dict(name=n)) for n in names)
        if sql.startswith('CHECK GRANT '):
            return '0'
        raise AssertionError(sql)
    client = SimpleNamespace(program_decision_profile=True, execute=execute)
    monkeypatch.setattr(publication, 'preflight_rule_decision_storage', lambda actual: None)
    from src.trading_runtime import arte_journal_schema
    monkeypatch.setattr(arte_journal_schema, 'storage_preflight', lambda actual, **kwargs: None)
    if corruption:
        with pytest.raises(ValueError, match='cannot read|unauthorized'):
            module.preflight_rule_decision_writer(client)
    else:
        module.preflight_rule_decision_writer(client)


def test_program_write_catalog_is_exact_extension_without_legacy_mutation():
    original = writer.v4_journal_write_tables()
    from src.trading_runtime.trading_price_contracts import program_exact_price_storage_contracts
    exact = {table.name for table in program_exact_price_storage_contracts()}
    assert len(exact) == 10
    from src.trading_runtime.trading_price_contracts import exact_price_storage_contracts
    assert len(exact_price_storage_contracts()) == 11
    assert 'trading_strategy_signal_exact_price_v1' not in exact
    assert exact.isdisjoint(original)
    from src.trading_runtime.arte_entry_acquisition_freeze import FAMILY_NAMES
    from src.trading_runtime.backtest_journal_price_contract import PRICE_CONTRACT
    from src.trading_runtime.columnar_strategy_schema import BROKER
    checkpoints = {table.name for table in module.program_checkpoint_storage_contracts()}
    assert len(checkpoints) == 8 and checkpoints.isdisjoint(original)
    assert module.rule_decision_write_tables() == original | {table.name for table in STAGED_TABLES} | exact | FAMILY_NAMES | {PRICE_CONTRACT.name, BROKER.name} | checkpoints
    assert checkpoints <= {table.name for table in publication.rule_decision_storage_contracts()}
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
        assert read_only_tables == ({table.name for table in writer.fixed_backtest_v2_contracts()}
            | {table.name for table in module.program_signal_source_read_contracts()}) - journal_tables
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


@pytest.mark.parametrize("changed", ["typed_insert_dispatch", "backtest_v4_lease", "user", "base_url"])
def test_publication_reuses_exact_startup_scope_and_rejects_changes(monkeypatch, changed):
    calls = []
    client = SimpleNamespace(program_decision_profile=True, typed_insert_dispatch=object(),
        backtest_v4_lease=object(), user="backtest_v4_program_runner", base_url="http://localhost:18123")
    client.execute = lambda sql: calls.append("principal") or "backtest_v4_program_runner"
    monkeypatch.setattr(publication, "preflight_rule_decision_storage", lambda actual: calls.append("storage"))
    monkeypatch.setattr(writer, "journal_permission_preflight", lambda actual, **kwargs: calls.append("grants"))
    audit = module.preflight_rule_decision_writer(client)
    assert calls == ["principal", "storage", "grants"]
    assert module.require_rule_decision_writer_audit(client) is audit
    assert module.require_rule_decision_writer_audit(client) is audit
    assert len(calls) == 3
    original = getattr(client, changed)
    setattr(client, changed, object())
    with pytest.raises(RuntimeError, match="scope changed"):
        module.require_rule_decision_writer_audit(client)
    assert len(calls) == 3
    setattr(client, changed, original)
    # A new startup always audits again, even on this same client.
    assert module.preflight_rule_decision_writer(client) is not audit
    assert calls == ["principal", "storage", "grants"] * 2


def test_failed_startup_clears_prior_audit(monkeypatch):
    client = SimpleNamespace(program_decision_profile=True, _program_writer_audit=object(),
                             execute=lambda sql: "backtest_v4_runner")
    with pytest.raises(RuntimeError, match="dedicated principal"):
        module.preflight_rule_decision_writer(client)
    assert client._program_writer_audit is None
