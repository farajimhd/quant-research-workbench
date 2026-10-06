"""Genuine installed65 declaration selects generic waiting cold/storage routes."""
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_strategy_sixty_four_release import source_fixture
from test_strategy_fifty_release import APPROVAL
from test_ladder_waiting_profile import Catalog, WAIT
from src.trading_runtime import strategy_sixty_five_release as release
from src.trading_runtime import arte_journal_writer as writer
from src.trading_runtime.journal_contract import canonical_json
from src.trading_runtime.squeeze_ladder_geometry import declared_ladder_runner_options
from src.trading_runtime.arte_squeeze_ladder_schema import BINDING
from src.backend import backtest_v4_saved_review as review


def prepared_configuration():
    from src.backend.backtest_strategy_one_configuration import CertifiedStrategyOneConfiguration
    result=release.derive_strategy_sixty_five_configuration(source_fixture(),**APPROVAL)
    return CertifiedStrategyOneConfiguration(str(uuid4()),result['payload_hash'],result['node_hash'],
        result['source_candidate_id'],result['source_candidate_hash'],'a'*64,result['payload'])


def test_actual_saved_profile_selects_genuine65_policy():
    sealed=prepared_configuration()
    context=dict(strategy_id=sealed.payload['strategy']['strategy_id'],strategy_revision=65,
                 configuration_hash=sealed.payload_hash)
    assert declared_ladder_runner_options(sealed.payload)==dict(automatic_ladder=True,ladder_geometry_policy=WAIT)
    client=Catalog(waiting=True,binding_grant=True)
    assert review._require_declared_read_profile(client,context,sealed_configuration=sealed)=={}
    assert writer._v4_preflight(client).ladder_geometry_policy==WAIT
    legacy=Catalog()
    assert review._require_declared_read_profile(legacy,context,sealed_configuration=sealed)==dict(
        automatic_ladder=True,ladder_geometry_policy=WAIT)


def test_whole_saved_operation_uses_genuine65_immutable_config_and_read_wrapper(monkeypatch):
    from src.backend import backtest_market_data as markets,backtest_strategy_one_configuration as configurations
    from scripts.clickhouse.report_strategy_one_trades import SelectOnly
    sealed=prepared_configuration()
    context=dict(strategy_id=sealed.payload['strategy']['strategy_id'],strategy_revision=65,
                 configuration_hash=sealed.payload_hash)
    # External producer receipt/transport seam only. Actual installed65 resolver,
    # saved-config policy selector, wrapper and storage/grant preflight execute.
    monkeypatch.setattr(markets,'readonly_clickhouse_client',lambda **k:SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(configurations,'certify_numbered_configuration',lambda *a:sealed)
    selected=[]
    def factory(**options):
        assert options==dict(automatic_ladder=True,ladder_geometry_policy=WAIT)
        client=Catalog(waiting=True,binding_grant=True)
        client.close=lambda:setattr(client,'closed',True)
        selected.append(client)
        return client
    monkeypatch.setattr(writer,'backtest_v4_operator_client_from_env',factory)
    @review.declared_saved_read_operation
    def detail(client,run_id):
        review._require_declared_read_profile(client,context)
        assert client.ladder_geometry_policy==WAIT
        assert not client.client.closed
        return True
    assert detail(SelectOnly(Catalog()),str(uuid4())) is True
    assert len(selected)==1 and selected[0].closed
    assert review._DECLARED_READ_SCOPE.get() is None


@pytest.mark.parametrize('defect',['missing','foreign','wrong_hash','wrong_number'])
def test_saved_profile_rejects_missing_foreign_or_cross_identity_policy(defect):
    sealed=prepared_configuration()
    context=dict(strategy_id=sealed.payload['strategy']['strategy_id'],strategy_revision=65,
                 configuration_hash=sealed.payload_hash)
    if defect=='wrong_hash':sealed=replace(sealed,payload_hash='0'*64)
    elif defect=='wrong_number':sealed.payload['strategy']['strategy_number']=51
    else:
        policy=sealed.payload['strategy']['numbered_release']['automatic_market_policy']
        if defect=='missing':del policy['geometry_binding_policy']
        else:policy['geometry_binding_policy']={'version':'foreign'}
        # Immutable configuration producer validation rejects policy drift even
        # when an adversary recalculates the local manifest hash.
        manifest=sealed.payload['strategy']['numbered_release']
        manifest['manifest_hash']=sha256(canonical_json({k:v for k,v in manifest.items() if k!='manifest_hash'}).encode()).hexdigest()
        with pytest.raises(ValueError):release.verify_prepared_strategy_sixty_five_manifest(sealed.payload['strategy'])
        if defect=='missing':return
    with pytest.raises(ValueError):
        review._require_declared_read_profile(Catalog(waiting=True,binding_grant=True),context,sealed_configuration=sealed)


@pytest.mark.parametrize('defect',['missing','policy','columns'])
def test_genuine65_selected_storage_missing_or_wrong_binding_rejects(defect):
    options=declared_ladder_runner_options(prepared_configuration().payload)
    assert options['ladder_geometry_policy']==WAIT
    class DefectiveCatalog(Catalog):
        def execute(self,sql):
            result=super().execute(sql)
            if defect=='columns' and 'FROM system.columns' in sql and BINDING.name in sql:
                import json
                rows=[json.loads(line) for line in result.splitlines()]
                selected=[row for row in rows if row['table']==BINDING.name]
                assert selected
                selected[0]['type']='String' if selected[0]['type']!='String' else 'UInt64'
                return '\n'.join(json.dumps(row) for row in rows)
            return result
    client=DefectiveCatalog(waiting=True,binding_grant=True,defect=defect)
    with pytest.raises(ValueError,match='schema|storage|table|policy|columns|layout'):
        writer._v4_preflight(client)


@pytest.mark.parametrize('defect',[None,'reader_legacy','terminal_missing_policy','runner_missing_binding'])
def test_actual65_publication_assembly_bootstrap(monkeypatch,defect):
    from datetime import datetime,timezone
    from src.backend import backtest_fixed_journal_bootstrap as bootstrap
    from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
    from tests.test_backtest_strategy_one_loader import plan
    from tests.test_backtest_fixed_journal_bootstrap import RUN,ATTEMPT
    sealed=prepared_configuration()
    assert sealed.strategy_number==65
    dispatch=TypedInsertDispatch(object())
    context_client=SimpleNamespace(typed_insert_strict=True,typed_insert_dispatch=dispatch)
    runner=Catalog(waiting=True,binding_grant=defect!='runner_missing_binding')
    runner.typed_insert_strict,runner.typed_insert_dispatch=True,dispatch
    reader,terminal=Catalog(waiting=True,binding_grant=True),Catalog(waiting=True,binding_grant=True)
    if defect=='reader_legacy':reader.user='backtest_v4_ladder_runner'
    if defect=='terminal_missing_policy':terminal.ladder_geometry_policy=None
    market=replace(plan(),token='b'*64)
    run=dict(run_id=RUN,run_month='2026-08-01',mode='backtest',evaluation_interval_ms=100,
        session_date='2026-08-18',configuration_hash=sealed.payload_hash,code_hash='d'*64,
        market_plan_token=market.token,started_at='2026-08-18T08:00:00+00:00')
    config=dict(strategy_id=sealed.payload['strategy']['strategy_id'],strategy_revision=65,
        anchor_date='2026-08-18',run_plan_id='plan-1',safety_supervisor_enabled=True,
        checkpoint_interval_events=100,write_progress_checkpoints=True)
    published=dict(run_id=RUN,mode='backtest',account_ids=('DU1',),run_month='2026-08-01',
        configuration_hash=sealed.payload_hash,market_plan_token=market.token)
    publications=[]
    # Synthetic external context-producer receipts. Actual cold-reader,
    # principal/grant/storage validators and assembly consumer remain intact.
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
        return bootstrap.publish_and_assemble_fixed_v4_journal(context_client,reader,runner,terminal,
            run=run,config=config,account_ids=('DU1',),attempt_id=ATTEMPT,
            expected_config={'mode':'backtest','strategy_id':config['strategy_id'],'strategy_revision':65},
            fixed_market_parent_plan=market,fixed_market_execution_plan=market,
            expected_market_start=datetime(2026,8,18,tzinfo=timezone.utc),
            projection_certifier=lambda:'a'*64,writer_factory=lambda *a,**k:MemoryWriter())
    if defect:
        with pytest.raises((ValueError,RuntimeError)):assemble()
        assert publications==[]
    else:
        result=assemble()
        try:
            assert result.publisher.expected_config['strategy_revision']==65
            assert publications==['context']
            assert BINDING.name in '\n'.join(runner.statements)
        finally:result.journal.close()
