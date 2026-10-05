"""Ladder evidence installation is explicit, isolated and SSD-fenced."""
import json

import pytest

from scripts.clickhouse import install_trading_journal_layout as install
from scripts.clickhouse import plan_trading_journal_layout as plan
from src.trading_runtime.arte_squeeze_ladder_schema import TABLES
from test_install_trading_journal_layout import Client


def test_ladder_profile_plans_only_its_two_scalar_tables(monkeypatch):
    assert plan.profile_contracts('squeeze-ladder-evidence') == TABLES
    assert not set(TABLES).intersection(plan.profile_contracts('commit-v4'))
    client = Client()
    client.execute = lambda sql: '' if sql.startswith('SELECT name FROM system.tables') else pytest.fail(sql)
    monkeypatch.setattr(plan, 'storage_preflight', lambda *_args, **_kwargs: None)
    missing, ddl = plan.plan_missing(client, profile='squeeze-ladder-evidence')
    assert missing == tuple(table.name for table in TABLES)
    assert len(ddl) == 2
    assert all('live_market_ssd' in sql and 'JSON' not in sql for sql in ddl)


def test_ladder_install_is_opt_in_and_resume_checks_only_missing_table(monkeypatch):
    client = Client()
    missing = (TABLES[1].name,)
    monkeypatch.setattr(install, 'plan_missing', lambda _, *, profile:
        (missing, ()) if profile == 'squeeze-ladder-evidence' else pytest.fail(profile))
    verified = []
    monkeypatch.setattr(install, 'storage_preflight', lambda _, *, tables:
        verified.append(tuple(table.name for table in tables)))
    assert install.install_missing(client, apply=False, profile='squeeze-ladder-evidence') == (1, 0)
    assert all(sql.startswith('SELECT ') for sql in client.statements)
    assert install.install_missing(client, apply=True, profile='squeeze-ladder-evidence') == (1, 1)
    assert verified == [missing, tuple(table.name for table in TABLES)]
    writes = [sql for sql in client.statements if not sql.startswith('SELECT ')]
    assert writes == [TABLES[1].ddl()]


def test_ladder_install_rejects_default_disk_before_table_access(monkeypatch):
    client = Client()
    client.execute = lambda sql: json.dumps({'disks': ['default']})
    monkeypatch.setattr(install, 'plan_missing', lambda *_args, **_kwargs: pytest.fail('DDL must stay closed'))
    with pytest.raises(RuntimeError, match='SSD-only'):
        install.install_missing(client, apply=True, profile='squeeze-ladder-evidence')
