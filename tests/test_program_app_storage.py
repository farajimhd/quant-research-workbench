"""Operator inventory safety; no database connection or DDL is performed."""
import pytest

from scripts.clickhouse import prepare_program_app_storage as operator
from src.backend.program_app_storage import program_app_storage_contracts


def test_real_plan_entrypoint_has_no_connection(capsys):
    assert operator.main([]) == 0
    text = capsys.readouterr().out
    assert 'PLAN ONLY' in text and 'No connection opened' in text
    assert '\x1b' not in text


def test_inventory_reuses_unique_normalized_contracts():
    contracts = program_app_storage_contracts()
    assert len(contracts) == len({table.name for table in contracts})
    assert len(contracts) >= 27
    for table in contracts:
        assert "storage_policy = 'live_market_ssd'" in table.ddl()
    market = [table for table in contracts if table.database == 'q_live']
    assert len(market) == 7 and all(table.name.startswith('qmd_') for table in market)
    assert all(table.database == 'q_live' for table in contracts if table.name.startswith('qmd_'))
    assert 'trading_strategy_signal_exact_price_v1' not in {table.name for table in contracts}
    assert 'trading_oms_observation_snapshot_v1' not in {table.name for table in contracts}
    assert 'trading_strategy_one_oms_observation_snapshot_v1' in {table.name for table in contracts}


def test_apply_requires_explicit_confirmation():
    with pytest.raises(SystemExit) as error:
        operator.main(['--apply'])
    assert error.value.code == 2


@pytest.mark.parametrize('apply', [False, True])
def test_incompatible_existing_storage_blocks_before_any_ddl(monkeypatch, apply):
    contracts = program_app_storage_contracts()
    class Client:
        def execute(self, sql):
            pytest.fail('No DDL permitted before existing storage audit')
    def rows(client, sql):
        if 'storage_policies' in sql:
            return [{'disks': ['live_market_ssd']}]
        return [] if "database='q_live'" in sql else [{'name': contracts[0].name}]
    monkeypatch.setattr(operator, '_rows', rows)
    def reject(*args, **kwargs):
        raise ValueError('Existing placement is incompatible')
    monkeypatch.setattr(operator, 'storage_preflight', reject)
    with pytest.raises(ValueError, match='placement'):
        operator.prepare_with_client(Client(), apply=apply, emit=lambda value: None)


def test_missing_creation_is_restart_safe_and_postaudited(monkeypatch):
    contracts = program_app_storage_contracts()[:2]
    monkeypatch.setattr(operator, 'program_app_storage_contracts', lambda: contracts)
    present = {contracts[0].name}
    calls = []
    def rows(client, sql):
        if 'storage_policies' in sql:
            return [{'disks': ['live_market_ssd']}]
        return [{'name': name} for name in present]
    monkeypatch.setattr(operator, '_rows', rows)
    monkeypatch.setattr(operator, 'storage_preflight',
        lambda client, *, tables: calls.append(('audit', tuple(table.name for table in tables))))
    class Client:
        def execute(self, sql):
            assert sql == contracts[1].ddl()
            calls.append(('create', contracts[1].name))
            present.add(contracts[1].name)
    assert operator.prepare_with_client(Client(), apply=False, emit=lambda value: None) == 1
    assert not any(call[0] == 'create' for call in calls)
    calls.clear()
    approved = frozenset((table.database, table.name) for table in contracts)
    assert operator.prepare_with_client(Client(), apply=True, approved=approved, emit=lambda value: None) == 0
    assert calls == [('audit', (contracts[0].name,)), ('create', contracts[1].name),
        ('audit', (contracts[1].name,)), ('audit', tuple(table.name for table in contracts))]
    calls.clear()
    assert operator.prepare_with_client(Client(), apply=True, approved=approved, emit=lambda value: None) == 0
    assert not any(call[0] == 'create' for call in calls)


def test_backup_disk_policy_rejected_before_inventory(monkeypatch):
    monkeypatch.setattr(operator, '_rows', lambda *args: [{'disks': ['default']}])
    with pytest.raises(ValueError, match='SSD-only'):
        operator.inspect_inventory(object(), program_app_storage_contracts())


def test_native_audit_checks_q_live_schema_and_part_placement():
    import json
    from src.trading_runtime.arte_journal_schema import TableContract, storage_preflight
    table = TableContract('qmd_probe', (('id', 'UInt64'),), 'tuple()', 'id', database='q_live')
    queries = []
    class Client:
        def execute(self, sql):
            queries.append(sql)
            if 'storage_policies' in sql:
                return json.dumps({'disks': ['live_market_ssd']})
            assert "database='q_live'" in sql and "database='arte'" not in sql
            if 'system.tables' in sql:
                return json.dumps(dict(name=table.name, engine='MergeTree', storage_policy='live_market_ssd',
                    partition_key=table.partition, sorting_key=table.order))
            if 'system.columns' in sql:
                return json.dumps(dict(table=table.name, name='id', type='UInt64'))
            return ''
    storage_preflight(Client(), tables=(table,))
    assert any('system.parts' in sql and "disk_name!='live_market_ssd'" in sql for sql in queries)
    assert table.ddl().startswith('CREATE TABLE IF NOT EXISTS q_live.qmd_probe')


def test_mixed_database_audit_is_split_before_native_preflight(monkeypatch):
    from dataclasses import replace
    contracts = program_app_storage_contracts()[:1]
    contracts = (contracts[0], replace(contracts[0], name='qmd_probe', database='q_live'))
    scopes = []
    monkeypatch.setattr(operator, 'storage_preflight', lambda client, *, tables:
        scopes.append(tuple(table.database for table in tables)))
    operator.audit_contracts(object(), contracts)
    assert scopes == [('arte',), ('q_live',)]


def test_apply_outside_reviewed_scope_never_creates(monkeypatch):
    contracts = program_app_storage_contracts()[:1]
    monkeypatch.setattr(operator, 'inspect_inventory', lambda *args: ((), contracts))
    class Client:
        def execute(self, sql):
            pytest.fail('Unapproved DDL')
    with pytest.raises(ValueError, match='approved table scope'):
        operator.prepare_with_client(Client(), apply=True, approved=frozenset())


def test_plan_pins_exact_database_and_schema(tmp_path):
    import json
    contracts = program_app_storage_contracts()[:1]
    plan = tmp_path / 'approval.json'
    plan.write_text(json.dumps(operator.creation_plan(contracts)))
    assert operator.approved_scope(plan, contracts) == frozenset((t.database, t.name) for t in contracts)
    rows = operator.creation_plan(contracts)
    rows[0]['ddl_sha256'] = '0' * 64
    plan.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match='changed contract'):
        operator.approved_scope(plan, contracts)


def test_explicit_url_accepts_only_managed_workstation_ipv4_listener(monkeypatch):
    monkeypatch.setattr(operator.socket, 'getaddrinfo', lambda *args:
        [(2, 1, 6, '', ('192.168.1.218', 18123))])
    assert operator.validate_workstation_url('http://192.168.1.218:18123/') == 'http://192.168.1.218:18123'
    for url in ('http://127.0.0.1:18123', 'http://DESKTOP-SAAI85T:18123',
                'http://192.168.1.218:8123', 'http://user:secret@192.168.1.218:18123',
                'http://192.168.1.218:18123/?readonly=0'):
        with pytest.raises(ValueError, match='workstation IPv4'):
            operator.validate_workstation_url(url)
