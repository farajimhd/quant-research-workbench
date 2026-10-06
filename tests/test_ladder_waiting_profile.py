"""Actual generic storage/permission preflight over explicit synthetic catalogs."""
import json
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime.arte_journal_schema import MARKET_READ_TABLES, BATCH_LOOKUP_INDEX
from src.trading_runtime.arte_squeeze_ladder_schema import BINDING, TABLES
from src.trading_runtime.squeeze_ladder_geometry import LadderGeometryBindingPolicy, declared_ladder_runner_options

WAIT = LadderGeometryBindingPolicy()


class Catalog:
    def __init__(self, *, waiting=False, binding_grant=False, defect=None):
        self.automatic_ladder_profile = True
        self.ladder_geometry_policy = WAIT if waiting else None
        self.user = 'backtest_v4_waiting_ladder_runner' if waiting else 'backtest_v4_ladder_runner'
        self.base_url,self.password,self.closed='http://catalog.invalid','synthetic-only',False
        self.binding_grant, self.defect, self.statements = binding_grant, defect, []
        self.contracts = {t.name:t for t in (*writer.fixed_backtest_v2_contracts(),
            *writer.v4_storage_contracts(),*TABLES,BINDING)}
        self.writable = set(writer.v4_journal_write_tables()) | {t.name for t in TABLES}
        if binding_grant:
            self.writable.add(BINDING.name)
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
                    if name == BINDING.name and self.defect == 'missing':
                        continue
                    table = self.contracts[name]
                    rows.append(dict(name=name,engine='MergeTree',storage_policy=(
                        'default' if name == BINDING.name and self.defect == 'policy' else 'live_market_ssd'),
                        partition_key=table.partition,sorting_key=table.order))
                return lines(rows)
            return lines([{'name':n} for n in names])
        if 'FROM system.columns' in sql:
            return lines([dict(table=n,name=k,type=t) for n in names for k,t in self.contracts[n].columns])
        if 'FROM system.data_skipping_indices' in sql:
            return lines([dict(table=n,name=BATCH_LOOKUP_INDEX,type='bloom_filter',expr='batch_id',granularity=1)
                for n in names if 'batch_id' in dict(self.contracts[n].columns)])
        if 'FROM system.parts' in sql:
            if BINDING.name in names and self.defect == 'parts' and 'disk_name' in sql:
                return lines([dict(table=BINDING.name,disk_name='default')])
            return ''
        pytest.fail(sql)


@pytest.mark.parametrize('defect',['missing','policy','parts'])
def test_legacy_extra_grant_never_selects_binding_storage_or_writability(defect):
    client = Catalog(binding_grant=True,defect=defect)
    # The unchanged exact grant-surface rule rejects an undeclared extra grant.
    with pytest.raises(ValueError,match='unauthorized .* grant'):
        writer._v4_preflight(client)
    assert not any(BINDING.name in sql for sql in client.statements)
    assert not any(sql.startswith(('INSERT','CREATE','ALTER')) for sql in client.statements)


def test_legacy_exact_profile_still_passes_generic_preflight():
    writer._v4_preflight(Catalog())


@pytest.mark.parametrize('defect',['missing','policy','parts'])
def test_waiting_profile_requires_real_binding_schema_and_ssd_before_writes(defect):
    client = Catalog(waiting=True,binding_grant=True,defect=defect)
    with pytest.raises(ValueError,match='missing|layout differs|outside live_market_ssd'):
        writer._v4_preflight(client)
    assert any(BINDING.name in sql for sql in client.statements)


def test_waiting_profile_requires_binding_grant_before_writes():
    client = Catalog(waiting=True,binding_grant=False)
    with pytest.raises(ValueError,match='cannot read|incorrect insert authority'):
        writer._v4_preflight(client)


def test_waiting_exact_declared_profile_storage_and_permissions_pass():
    client = Catalog(waiting=True,binding_grant=True)
    seal = writer._v4_preflight(client)
    assert seal.ladder_geometry_policy == WAIT
    assert any(BINDING.name in sql and 'FROM system.parts' in sql for sql in client.statements)


def test_missing_or_foreign_typed_policy_or_principal_fails_closed():
    for profile in ('future', {}, True):
        client = Catalog(waiting=True,binding_grant=True)
        client.ladder_geometry_policy = profile
        with pytest.raises(ValueError,match='typed declared ladder profile'):
            writer._v4_preflight(client)
        assert client.statements == []
    client = Catalog(waiting=True,binding_grant=True)
    client.user = 'backtest_v4_ladder_runner'
    with pytest.raises(RuntimeError,match='dedicated principal'):
        writer._v4_preflight(client)


def test_options_derive_only_from_complete_declared_policy():
    from tests.test_ladder_waiting_geometry import waiting_native_unit
    unit,_,_ = waiting_native_unit()
    config = unit.request.market_context.configuration.payload
    assert declared_ladder_runner_options(config) == {'automatic_ladder':True,'ladder_geometry_policy':WAIT}
    with pytest.raises(ValueError,match='declared automatic ladder'):
        declared_ladder_runner_options({'strategy':{'numbered_release':{
            'automatic_market_policy':{'geometry_binding_policy':WAIT.payload()}}}})


def test_cached_preflight_seal_cannot_change_geometry_profile(monkeypatch):
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    client = Catalog()
    seal = writer._v4_preflight(client)
    client.typed_insert_strict = True
    client.typed_insert_dispatch = TypedInsertDispatch(object())
    client.ladder_geometry_policy = WAIT
    monkeypatch.setattr(writer,'_verify_run_identity',lambda *a:{'mode':'backtest','account_ids':('DU1',)})
    with pytest.raises(RuntimeError,match='fresh same-client preflight'):
        writer.ArteJournalWriter(client,run_id=str(uuid4()),journal_profile='backtest_v4',
                                 coalesce_batches=False,v4_preflight_seal=seal)


def test_waiting_credentials_never_reuse_old_principal_or_namespace(monkeypatch):
    import src.trading_runtime.clickhouse_transport as transport
    observed=[]
    monkeypatch.setattr(transport,'workstation_ipv4_transport',lambda value:value)
    def credentials(prefix,path):
        observed.append((prefix,path))
        return 'http://catalog.invalid','backtest_v4_ladder_runner','synthetic-only'
    monkeypatch.setattr(writer,'_dedicated_clickhouse_credentials',credentials)
    with pytest.raises(ValueError,match='dedicated runner credential'):
        writer._v4_runner_credentials(automatic_ladder=True,ladder_geometry_policy=WAIT)
    assert observed == [('BACKTEST_V4_WAITING_LADDER_RUNNER_CLICKHOUSE_',
                         'BACKTEST_V4_WAITING_LADDER_RUNNER_CREDENTIAL_FILE')]


def test_actual_operator_factory_carries_configuration_selected_typed_profile(monkeypatch):
    import research.mlops.clickhouse as transport
    import src.trading_runtime.clickhouse_transport as address
    from tests.test_ladder_waiting_geometry import waiting_native_unit
    captured=[]
    class ReadClient:
        def __init__(self,*args,**kwargs):
            captured.append((args,kwargs))
    monkeypatch.setattr(transport,'ClickHouseHttpClient',ReadClient)
    monkeypatch.setattr(address,'workstation_ipv4_transport',lambda value:value)
    monkeypatch.setattr(writer,'_dedicated_clickhouse_credentials',lambda *a:
        ('http://catalog.invalid','backtest_v4_waiting_ladder_runner','synthetic-only'))
    unit,_,_ = waiting_native_unit()
    options = declared_ladder_runner_options(unit.request.market_context.configuration.payload)
    client = writer.backtest_v4_operator_client_from_env(**options)
    assert client.automatic_ladder_profile is True
    assert client.ladder_geometry_policy == WAIT
    assert captured[0][1]['default_query_params']['readonly'] == 1


@pytest.mark.parametrize('defect',[None,'read_legacy','terminal_no_policy','foreign_policy'])
def test_actual_publication_and_assembly_bootstrap_uses_waiting_cold_readers(monkeypatch,defect):
    from datetime import datetime,timezone
    from dataclasses import replace
    from src.backend import backtest_fixed_journal_bootstrap as bootstrap
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from tests.test_backtest_strategy_one_loader import plan
    from tests.test_backtest_fixed_journal_bootstrap import RUN,ATTEMPT
    dispatch = TypedInsertDispatch(object())
    context_client = SimpleNamespace(typed_insert_strict=True,typed_insert_dispatch=dispatch)
    runner = Catalog(waiting=True,binding_grant=True)
    runner.typed_insert_strict,runner.typed_insert_dispatch = True,dispatch
    read,terminal = Catalog(waiting=True,binding_grant=True),Catalog(waiting=True,binding_grant=True)
    if defect == 'read_legacy': read.user='backtest_v4_ladder_runner'
    if defect == 'terminal_no_policy': terminal.ladder_geometry_policy=None
    if defect == 'foreign_policy': read.ladder_geometry_policy='foreign'
    market = replace(plan(),token='b'*64)
    run = dict(run_id=RUN,run_month='2026-08-01',mode='backtest',evaluation_interval_ms=100,
        session_date='2026-08-18',configuration_hash='c'*64,code_hash='d'*64,
        market_plan_token=market.token,started_at='2026-08-18T08:00:00+00:00')
    config = dict(strategy_id='early-squeeze-strategy',strategy_revision=49,
        anchor_date='2026-08-18',run_plan_id='plan-1',safety_supervisor_enabled=True,
        checkpoint_interval_events=100,write_progress_checkpoints=True)
    published = dict(run_id=RUN,mode='backtest',account_ids=('DU1',),run_month='2026-08-01',
        configuration_hash='c'*64,market_plan_token=market.token)
    publications=[]
    # External context-principal/producer receipt reads only; cold-reader and
    # runner storage/grant validators and actual assembly remain untouched.
    monkeypatch.setattr(bootstrap,'fixed_backtest_v2_preflight',lambda *_:None)
    monkeypatch.setattr(bootstrap,'publish_fixed_run_context',lambda *a,**k:publications.append('context') or published)
    monkeypatch.setattr(bootstrap,'load_typed_run_context',lambda *_:published)
    class MemoryWriter:
        run_id=RUN
        run_mode='backtest'
        journal_profile='backtest_v4'
        coalesce_batches=False
        max_events_per_commit=1024
        def close(self):pass
    def assemble():
        return bootstrap.publish_and_assemble_fixed_v4_journal(context_client,read,runner,terminal,
            run=run,config=config,account_ids=('DU1',),attempt_id=ATTEMPT,
            expected_config={'mode':'backtest','strategy_id':config['strategy_id'],'strategy_revision':49},
            fixed_market_parent_plan=market,fixed_market_execution_plan=market,
            expected_market_start=datetime(2026,8,18,tzinfo=timezone.utc),
            projection_certifier=lambda:'a'*64,writer_factory=lambda *a,**k:MemoryWriter())
    if defect:
        with pytest.raises((ValueError,RuntimeError),match='unexpected principal|typed declared ladder profile'):
            assemble()
        assert publications == []
    else:
        result=assemble()
        try:
            assert result.publisher.expected_config['strategy_revision']==49
            assert publications == ['context']
            assert BINDING.name in '\n'.join(runner.statements)
        finally: result.journal.close()


def test_whole_saved_read_uses_sealed_waiting_profile_and_preserves_report_wrapper(monkeypatch):
    from dataclasses import replace
    from src.backend import backtest_v4_saved_review as review
    from src.backend import backtest_market_data as markets,backtest_strategy_one_configuration as configurations
    from src.trading_runtime import numbered_fixed_strategy as numbered
    from scripts.clickhouse.report_strategy_one_trades import SelectOnly
    from tests.test_ladder_waiting_geometry import waiting_native_unit
    unit,_,_=waiting_native_unit()
    sealed=unit.request.market_context.configuration
    context=dict(strategy_id=sealed.payload['strategy']['strategy_id'],strategy_revision=sealed.strategy_number,
                 configuration_hash=sealed.payload_hash)
    # Installed immutable release seam: no new strategy is registered by tests.
    monkeypatch.setattr(numbered,'resolve_numbered_fixed_strategy',lambda *a:
                        SimpleNamespace(automatic_entry_policy=unit.request.policy))
    monkeypatch.setattr(markets,'readonly_clickhouse_client',lambda **k:SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(configurations,'certify_numbered_configuration',lambda *a:sealed)
    selected=[]
    def factory(**options):
        assert options == {'automatic_ladder':True,'ladder_geometry_policy':WAIT}
        client=Catalog(waiting=True,binding_grant=True)
        client.closed=False
        client.close=lambda:setattr(client,'closed',True)
        selected.append(client)
        return client
    monkeypatch.setattr(writer,'backtest_v4_operator_client_from_env',factory)
    @review.declared_saved_read_operation
    def detail(client,run_id):
        review._require_declared_read_profile(client,context)
        assert client.ladder_geometry_policy == WAIT
        assert not client.client.closed
        return client
    original=Catalog()
    original.close=lambda:None
    # Same automatic flag with absent geometry cannot satisfy waiting authority.
    result=detail(SelectOnly(original),unit.base.run_id)
    assert isinstance(result,SelectOnly)
    assert len(selected)==1 and selected[0].closed
    assert review._DECLARED_READ_SCOPE.get() is None
    foreign = Catalog(waiting=True,binding_grant=True)
    foreign.ladder_geometry_policy='foreign'
    detail(SelectOnly(foreign),unit.base.run_id)
    assert len(selected)==2
    wrong=replace(sealed,payload_hash='0'*64)
    with pytest.raises(ValueError,match='sealed run configuration'):
        review._require_declared_read_profile(original,context,sealed_configuration=wrong)
    assert review._DECLARED_READ_SCOPE.get() is None
