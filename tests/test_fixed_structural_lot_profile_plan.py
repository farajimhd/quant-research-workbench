"""Closed issuance and pure storage planning controls; no installed-source fiction."""
import json
import pytest
from src.trading_runtime.fixed_structural_lot_profile import (
    FixedStructuralLotDeclaredProfile, issue_fixed_structural_lot_profile,
    require_fixed_structural_lot_profile, selected_fixed_structural_lot_tables)
from src.trading_runtime.fixed_structural_lot_policy import FixedStructuralLotPolicy
from src.trading_runtime.fixed_structural_lot_entry_schema import TABLES as ENTRY
from src.trading_runtime.fixed_structural_lot_snapshot import TABLES as SNAPSHOT
from src.trading_runtime.fixed_structural_lot_manager_schema import TABLES as MANAGER
from scripts.clickhouse import provision_backtest_v4_fixed_structural_lot_runner as provision


@pytest.mark.parametrize('value', [None, True, {}, object()])
def test_foreign_operation_cannot_issue(value):
    with pytest.raises(ValueError, match='exact native'):
        issue_fixed_structural_lot_profile(value)


def test_constructed_profile_cannot_authorize_tables():
    profile = FixedStructuralLotDeclaredProfile(object())
    for consume in (require_fixed_structural_lot_profile, selected_fixed_structural_lot_tables):
        with pytest.raises(ValueError, match='not issued'):
            consume(profile)


def test_plan_exact_baseline_plus_seven_sole_schemas():
    policy = FixedStructuralLotPolicy()
    base = provision.legacy_plan()
    selected = frozenset(t.name for t in (*ENTRY, *SNAPSHOT, *MANAGER))
    plan = provision.desired_plan(policy)
    assert len(selected) == 7
    assert plan.principal != base.principal
    assert plan.select_arte == base.select_arte | selected
    assert plan.insert_arte == base.insert_arte | selected
    assert provision.table_install_plan(policy) == tuple(t.ddl() for t in (*ENTRY, *SNAPSHOT, *MANAGER))
    assert all("live_market_ssd" in ddl for ddl in provision.table_install_plan(policy))
    assert dict(SNAPSHOT[0].columns)['source_build_id'] == 'String'


@pytest.mark.parametrize('policy', [None, {}, True, object()])
def test_foreign_policy_plan_rejected(policy):
    with pytest.raises(ValueError, match='Exact typed'):
        provision.desired_plan(policy)


def test_default_cli_pure_json(capsys):
    assert provision.main(['--json']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'plan_only'
    assert result['operations_executed'] == 0
    assert len(result['tables']) == 7
    with pytest.raises(SystemExit):
        provision.main(['--apply'])


@pytest.mark.parametrize('disks', [[], ['default'], ['live_market_ssd', 'default']])
def test_wrong_physical_policy_rejected_before_other_queries(disks):
    class Client:
        def __init__(self): self.calls = []
        def execute(self, query):
            self.calls.append(query)
            assert query.startswith('SELECT')
            return json.dumps({'disks': disks})
    client = Client()
    with pytest.raises(ValueError, match='SSD-only'):
        provision.validate_existing_storage(client, FixedStructuralLotPolicy())
    assert len(client.calls) == 1


def test_missing_tables_fail_without_writes():
    class Client:
        def execute(self, query):
            assert query.startswith('SELECT')
            return json.dumps({'disks': ['live_market_ssd']}) if 'system.storage_policies' in query else ''
    with pytest.raises(ValueError, match='tables are missing'):
        provision.validate_existing_storage(Client(), FixedStructuralLotPolicy())


def test_issued_profile_binding_rejects_mutation(monkeypatch):
    # Owner admission seam is explicit: this exercises profile binding only,
    # never asserts a real installed configuration/source certificate exists.
    from src.backend.backtest_fixed_structural_lot_source import PreparedFixedStructuralLotSource
    from src.backend.backtest_fixed_structural_lot_native import NativeFixedStructuralLotOperation
    source = object.__new__(PreparedFixedStructuralLotSource)
    values = {'installed_json': '{"controlled_owner_fixture":true}',
              'selected_configuration_hash': 'a'*64, 'run_id': 'controlled-run',
              'session_date': 'controlled-session', 'policy': FixedStructuralLotPolicy()}
    for key, value in values.items(): object.__setattr__(source, key, value)
    calls = []
    monkeypatch.setattr(PreparedFixedStructuralLotSource, 'require_prepared_source', lambda self: None)
    monkeypatch.setattr(PreparedFixedStructuralLotSource, 'require_installed_admission',
                        lambda self: calls.append(self))
    profile = issue_fixed_structural_lot_profile(NativeFixedStructuralLotOperation(source))
    assert selected_fixed_structural_lot_tables(profile) == (*ENTRY, *SNAPSHOT, *MANAGER)
    for key, value in [('installed_json', '{"foreign":true}'),
                       ('policy', FixedStructuralLotPolicy(count=4)),
                       ('run_id', 'foreign-run'), ('selected_configuration_hash', 'b'*64)]:
        object.__setattr__(source, key, value)
        with pytest.raises(ValueError, match='binding changed'):
            require_fixed_structural_lot_profile(profile)
        object.__setattr__(source, key, values[key])
    assert len(calls) == 6


def test_owner_rejection_cannot_be_issued(monkeypatch):
    from src.backend.backtest_fixed_structural_lot_source import PreparedFixedStructuralLotSource
    from src.backend.backtest_fixed_structural_lot_native import NativeFixedStructuralLotOperation
    source = object.__new__(PreparedFixedStructuralLotSource)
    def rejected(self): raise ValueError('foreign installed JSON owner rejection')
    monkeypatch.setattr(PreparedFixedStructuralLotSource, 'require_prepared_source', lambda self: None)
    monkeypatch.setattr(PreparedFixedStructuralLotSource, 'require_installed_admission', rejected)
    with pytest.raises(ValueError, match='foreign installed JSON'):
        issue_fixed_structural_lot_profile(NativeFixedStructuralLotOperation(source))


def test_unissued_source_cannot_issue_profile_before_installed_guard():
    from src.backend.backtest_fixed_structural_lot_source import PreparedFixedStructuralLotSource
    from src.backend.backtest_fixed_structural_lot_native import NativeFixedStructuralLotOperation
    source=object.__new__(PreparedFixedStructuralLotSource)
    with pytest.raises(ValueError,match='not issued by fresh complete preparation'):
        issue_fixed_structural_lot_profile(NativeFixedStructuralLotOperation(source))


def test_actual_parts_outside_ssd_rejected():
    from src.trading_runtime.arte_journal_schema import BATCH_LOOKUP_INDEX
    tables = (*ENTRY, *SNAPSHOT, *MANAGER)
    class Client:
        def execute(self, query):
            assert query.startswith('SELECT')
            if 'system.storage_policies' in query: rows = [{'disks':['live_market_ssd']}]
            elif 'system.tables' in query:
                rows = [dict(name=t.name,engine='MergeTree',storage_policy='live_market_ssd',
                             partition_key=t.partition,sorting_key=t.order) for t in tables]
            elif 'system.columns' in query:
                rows = [dict(table=t.name,name=n,type=kind) for t in tables for n,kind in t.columns]
            elif 'system.data_skipping_indices' in query:
                rows = [dict(table=t.name,name=BATCH_LOOKUP_INDEX,type='bloom_filter',expr='batch_id',granularity=1)
                        for t in tables if any(n=='batch_id' for n,_ in t.columns)]
            elif 'system.parts' in query: rows = [{'table':tables[0].name,'disk_name':'default'}]
            else: raise AssertionError(query)
            return '\n'.join(json.dumps(row) for row in rows)
    with pytest.raises(ValueError, match='active parts outside'):
        provision.validate_existing_storage(Client(), FixedStructuralLotPolicy())


def test_ordinary_client_context_rejected_before_source():
    from src.trading_runtime.fixed_structural_lot_profile import require_fixed_structural_lot_client_context
    with pytest.raises(ValueError, match='Exact issued'):
        require_fixed_structural_lot_client_context(object(), object())
