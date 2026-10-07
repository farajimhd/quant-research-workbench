"""Actual generic storage/permission preflight over explicit synthetic catalogs."""
import json
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime.arte_journal_schema import MARKET_READ_TABLES, BATCH_LOOKUP_INDEX
from src.trading_runtime.arte_original_risk_diagnostic_v4 import DIAGNOSTIC as BINDING
from src.trading_runtime.original_risk_pending_snapshot import selected_snapshot_contracts
TABLES = selected_snapshot_contracts()
from src.trading_runtime.confirmed_original_risk_failure import ConfirmedOriginalRiskPolicy

WAIT = ConfirmedOriginalRiskPolicy()


class Catalog:
    def __init__(self, *, waiting=False, binding_grant=False, defect=None, defect_table=None):
        self.automatic_ladder_profile = False
        self.ladder_geometry_policy = None
        self.entry_spread_risk_profile = False
        self.confirmed_original_risk_policy = WAIT if waiting else None
        self.user = 'backtest_v4_original_risk_runner' if waiting else 'backtest_v4_runner'
        self.base_url,self.password,self.closed='http://catalog.invalid','synthetic-only',False
        self.binding_grant, self.defect, self.statements = binding_grant, defect, []
        self.defect_table=defect_table or BINDING.name
        self.contracts = {t.name:t for t in (*writer.fixed_backtest_v2_contracts(),
            *writer.v4_storage_contracts(),*TABLES,BINDING)}
        self.writable = set(writer.v4_journal_write_tables())
        if binding_grant:
            self.writable.add(BINDING.name)
            self.writable.update(t.name for t in TABLES)
        self.required = self.writable | set(MARKET_READ_TABLES) | {
            t.name for t in writer.fixed_backtest_v2_contracts()}

    def execute(self, sql):
        self.statements.append(sql)
        assert sql.startswith(('SELECT ','SHOW ','CHECK ')), sql
        def lines(rows):
            return '\n'.join(json.dumps(row) for row in rows)
        if sql == 'SELECT currentUser()':
            return self.user
        if sql == "SELECT getSetting('readonly')":
            return '1'
        if sql == 'SHOW GRANTS FINAL':
            grants = [f'GRANT SELECT ON arte.{n} TO {self.user}' for n in sorted(self.required)]
            grants += [f'GRANT INSERT ON arte.{n} TO {self.user}' for n in sorted(self.writable)]
            grants += [f'GRANT SELECT ON system.{n} TO {self.user}' for n in
                ('tables','columns','parts','storage_policies','data_skipping_indices')]
            return '\n'.join(grants)
        if sql.startswith('CHECK GRANT'):
            return '0'
        if 'FROM system.storage_policies' in sql:
            return lines([{'disks':['live_market_ssd']}])
        if 'strategy_one_entry_context_v1' in sql and 'name=' in sql:
            return ''
        match = re.search(r'(?:name|table) IN \((.*?)\)',sql)
        assert match, sql
        names = re.findall(r"'([^']+)'",match[1])
        if 'FROM system.tables' in sql:
            if sql.startswith('SELECT name,engine'):
                rows = []
                for name in names:
                    if name == self.defect_table and self.defect == 'missing':
                        continue
                    table = self.contracts[name]
                    rows.append(dict(name=name,engine='MergeTree',storage_policy=(
                        'default' if name == self.defect_table and self.defect == 'policy' else 'live_market_ssd'),
                        partition_key=table.partition,sorting_key=table.order))
                return lines(rows)
            return lines([{'name':n} for n in names])
        if 'FROM system.columns' in sql:
            return lines([dict(table=n,name=k,type=t) for n in names for k,t in self.contracts[n].columns])
        if 'FROM system.data_skipping_indices' in sql:
            return lines([dict(table=n,name=BATCH_LOOKUP_INDEX,type='bloom_filter',expr='batch_id',granularity=1)
                for n in names if 'batch_id' in dict(self.contracts[n].columns)])
        if 'FROM system.parts' in sql:
            if self.defect_table in names and self.defect == 'parts' and 'disk_name' in sql:
                return lines([dict(table=self.defect_table,disk_name='default')])
            return ''
        pytest.fail(sql)


def test_selected_profile_requires_diagnostic_storage_and_exact_grants():
    client=Catalog(waiting=True,binding_grant=True)
    seal=writer._v4_preflight(client)
    assert seal.confirmed_original_risk_policy==WAIT
    assert any(BINDING.name in sql and 'FROM system.parts' in sql for sql in client.statements)


@pytest.mark.parametrize('defect',['missing','policy','parts'])
def test_selected_profile_rejects_missing_or_misplaced_storage_before_writes(defect):
    client=Catalog(waiting=True,binding_grant=True,defect=defect)
    with pytest.raises((ValueError,RuntimeError)):writer._v4_preflight(client)
    assert not any(sql.startswith(('INSERT','CREATE','GRANT')) for sql in client.statements)


def test_selected_profile_missing_grant_fails_closed():
    client=Catalog(waiting=True)
    with pytest.raises((ValueError,RuntimeError)):writer._v4_preflight(client)


@pytest.mark.parametrize('table',['trading_original_risk_pending_snapshot_v1',
                                  'trading_strategy_one_manager_snapshot_v4'])
@pytest.mark.parametrize('defect',['missing','policy','parts'])
def test_selected_checkpoint_storage_is_mandatory_before_writes(table,defect):
    client=Catalog(waiting=True,binding_grant=True,defect=defect,defect_table=table)
    with pytest.raises((ValueError,RuntimeError)):
        writer._v4_preflight(client)
    assert not any(sql.startswith(('INSERT','CREATE','GRANT')) for sql in client.statements)


@pytest.mark.parametrize('defect',['missing','policy','parts'])
def test_legacy_extra_diagnostic_grant_does_not_select_storage(defect):
    client=Catalog(binding_grant=True,defect=defect)
    with pytest.raises(ValueError,match='unauthorized .* grant'):writer._v4_preflight(client)
    assert not any(BINDING.name in sql for sql in client.statements)


@pytest.mark.parametrize('policy',['foreign',{},True])
def test_foreign_policy_rejected_before_any_read(policy):
    client=Catalog(waiting=True,binding_grant=True);client.confirmed_original_risk_policy=policy
    with pytest.raises(ValueError,match='exclusive exact typed'):writer._v4_preflight(client)
    assert not client.statements


def test_principal_cannot_substitute_for_declared_capability():
    client=Catalog(waiting=True,binding_grant=True);client.user='backtest_v4_runner'
    with pytest.raises(RuntimeError,match='dedicated principal'):writer._v4_preflight(client)


