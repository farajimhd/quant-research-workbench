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
        return [{'name': contracts[0].name}]
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
    assert operator.prepare_with_client(Client(), apply=True, emit=lambda value: None) == 0
    assert calls == [('audit', (contracts[0].name,)), ('create', contracts[1].name),
        ('audit', (contracts[1].name,)), ('audit', tuple(table.name for table in contracts))]
    calls.clear()
    assert operator.prepare_with_client(Client(), apply=True, emit=lambda value: None) == 0
    assert not any(call[0] == 'create' for call in calls)


def test_backup_disk_policy_rejected_before_inventory(monkeypatch):
    monkeypatch.setattr(operator, '_rows', lambda *args: [{'disks': ['default']}])
    with pytest.raises(ValueError, match='SSD-only'):
        operator.inspect_inventory(object(), program_app_storage_contracts())
