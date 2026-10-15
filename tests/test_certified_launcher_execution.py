import io
import json
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

import pytest

from scripts import prepare_certified_research as launcher
from pipelines.certified_research import capacity_profile,installation,runtime_plan,recipe
from pipelines.certified_research.contracts import CampaignConfig,digest


def test_source_stop_marker_is_runtime_scoped_and_watcher_drains():
    from tempfile import TemporaryDirectory
    from threading import Event,enumerate as threads
    from pipelines.certified_research import acquisition
    with TemporaryDirectory(prefix='source-stop-',dir='D:/TradingML/runtimes') as folder:
        marker=Path(folder)/'stop.request'
        args=launcher.parse_args(['prepare-sources','--end-date','2026-01-02',
            '--runtime-root',folder,'--stop-file',str(marker)])
        assert args.stop_file==marker
        signal=Event()
        with acquisition.stop_marker(signal,marker):
            marker.write_text('stop')
            assert signal.wait(3)
        assert not any(t.name=='certified-source-stop-marker' for t in threads())
        with pytest.raises(SystemExit):launcher.parse_args(['prepare-sources','--end-date','2026-01-02',
            '--runtime-root',folder,'--stop-file',str(marker)])
        for command,path in (('plan',str(marker)),('prepare-sources','stop.request'),
                ('prepare-sources',str(Path(folder).parent/'outside-stop.request'))):
            with pytest.raises(SystemExit):launcher.parse_args([command,'--end-date','2026-01-02',
                '--runtime-root',folder,'--stop-file',path])


def test_execution_parser_requires_frozen_dataset_and_qualified_profile():
    dataset='00000000-0000-0000-0000-000000000001'
    for command in ('run','resume'):
        with pytest.raises(SystemExit):launcher.parse_args([command,'--dataset-id',dataset])
        args=launcher.parse_args([command,'--dataset-id',dataset,'--capacity-profile','pilot.json'])
        assert args.command==command
        with pytest.raises(SystemExit):launcher.parse_args([command,'--dataset-id',dataset,
            '--capacity-profile','pilot.json','--acquire-missing'])


def test_sec_source_document_selection_is_explicit_unique_and_capacity_bound():
    value='0000320193,0000320193-25-000079,filing.htm'
    args=launcher.parse_args(['prepare-sources','--end-date','2026-01-02','--capacity-profile','pilot.json',
        '--sec-share-document',value])
    assert args.sec_share_document==[('320193','0000320193-25-000079','filing.htm')]
    for argv in (['prepare-sources','--sec-share-document',value],
        ['resume','--dataset-id','00000000-0000-0000-0000-000000000001','--capacity-profile','pilot.json','--sec-share-document',value],
        ['prepare-sources','--capacity-profile','pilot.json','--sec-share-document',value,'--sec-share-document',value]):
        with pytest.raises(SystemExit):launcher.parse_args(argv)


def test_schema_json_exposes_source_tables_and_capacity_recipe_without_connecting(capsys):
    import json
    with patch('scripts.build_market_day.Client') as client:
        assert launcher.main(['schema','--format','json'])==0
    client.assert_not_called()
    candidate=json.loads(capsys.readouterr().out)['sec_source_candidates']
    assert candidate['database']=='q_live' and candidate['product']=='sec_share_source'
    assert len(candidate['tables'])==3 and len(candidate['recipe_hash'])==64


def test_sec_candidate_wrapper_propagates_controls_and_preserves_candidate_boundary():
    from pipelines.certified_research import sec_share_preparation
    args=launcher.parse_args(['prepare-sources','--end-date','2026-01-02','--capacity-profile','pilot.json',
        '--sec-share-document','320193,0000320193-25-000079,filing.htm'])
    result=dict(status='complete',captured=1,reused=0,candidate_only=True,allocation_certified=False)
    client=Mock();config=Mock();claim=Mock();factory=Mock();dictionary=Mock();stop=Mock()
    with patch.object(sec_share_preparation,'prepare',return_value=result) as prepare,\
         patch('pipelines.certified_research.capacity_profile.load',return_value={'profile':'fixture'}):
        assert launcher.prepare_sec_share_sources(args,client,config,claim,factory,dictionary,stop)==result
    assert prepare.call_args.args[2]==args.sec_share_document
    assert prepare.call_args.kwargs['stop'] is stop


def test_fresh_launch_requires_explicit_context_and_retains_acquisition_controls():
    common=['launch','--dataset-id','00000000-0000-0000-0000-000000000001','--capacity-profile','pilot.json']
    with pytest.raises(SystemExit):launcher.parse_args(common)
    args=launcher.parse_args([*common,'--end-date','2026-01-02','--context-start-date','2025-12-01',
        '--fundamental-start-date','2024-01-01','--recover-retained-reporting','--rebuild-origin-date','2025-12-01'])
    assert args.command=='launch' and args.recover_retained_reporting
    assert args.rebuild_origin_date==date(2025,12,1)
    with pytest.raises(SystemExit):launcher.parse_args(['resume','--dataset-id',common[2],
        '--capacity-profile','pilot.json','--rebuild-origin-date','2025-12-01'])


def test_backfill_parser_requires_new_target_and_forbids_acquisition():
    source='00000000-0000-0000-0000-000000000001';target='00000000-0000-0000-0000-000000000002'
    common=['backfill-v7','--dataset-id',target,'--source-dataset-id',source,'--capacity-profile','pilot.json']
    args=launcher.parse_args([*common,'--backfill-start-date','2026-01-05'])
    assert str(args.source_dataset_id)==source and args.backfill_start_date==date(2026,1,5)
    for flag in ('--acquire-missing','--stage-archive','--recover-retained-reporting'):
        with pytest.raises(SystemExit):launcher.parse_args([*common,flag])
    with pytest.raises(SystemExit):launcher.parse_args(['backfill-v7','--dataset-id',source,
        '--source-dataset-id',source,'--capacity-profile','pilot.json'])


def test_sec_lineage_cli_is_independent_of_market_acquisition_and_closes_ownership(capsys):
    client=Mock();client.clean_error.side_effect=str;claim=Mock()
    keeper=SimpleNamespace(client=Mock(),close=Mock())
    with patch('scripts.build_market_day.Client',return_value=client),\
         patch('src.trading_runtime.keeper_session.open_workstation_keeper_session',return_value=keeper),\
         patch('pipelines.certified_research.publication.DictionaryClaim',return_value=claim) as metadata_claim,\
         patch('pipelines.certified_research.sec_lineage.qualify',return_value=dict(qualified=('a',),unqualified=('b',))) as qualify,\
         patch('pipelines.certified_research.sec_coverage.inventory',return_value=dict(ready=False,gaps={'sec_daily_archive':()})),\
         patch('pipelines.certified_research.acquisition.prepare_sources') as acquire:
        code=launcher.main(['qualify-sec-lineage','--fundamental-start-date','2024-01-01',
            '--end-date','2026-01-02','--format','json'])
    assert code==2
    output=json.loads(capsys.readouterr().out)
    assert output['qualified']==['a'] and not output['coverage']['ready']
    assert qualify.call_args.args[2:]==(date(2024,1,1),date(2026,1,3))
    metadata_claim.assert_called_once_with(keeper.client,'q_live')
    acquire.assert_not_called()
    for owner in (claim,keeper,client):owner.close.assert_called_once()


@pytest.mark.parametrize('output_format',('json','text'))
def test_backfill_cli_uses_source_plan_resources_without_source_acquisition(output_format,capsys):
    source='00000000-0000-0000-0000-000000000001';target='00000000-0000-0000-0000-000000000002'
    config=CampaignConfig(date(2026,1,2),date(2026,1,5),workers=2,query_threads=3,
        query_memory_bytes=128*1024**2,global_memory_bytes=512*1024**2)
    inspection=Mock();client=Mock();client.clean_error.side_effect=str
    keeper=SimpleNamespace(client=Mock(),close=Mock());claim=Mock()
    missing=({'family':'corporate_actions','security_key':91,'start':'2025-01-02','end':'2026-01-05'},
        {'family':'float_allocation','security_key':91,'start':'2025-01-02','end':'2026-01-05'})
    with patch('scripts.build_market_day.Client',side_effect=[inspection,client]) as clients,\
         patch('pipelines.certified_research.campaign.load_plan',return_value=SimpleNamespace(config=config)) as load,\
         patch('pipelines.certified_research.capacity_profile.load',return_value={}),\
         patch('pipelines.certified_research.capacity_profile.factory',return_value=Mock()),\
         patch('pipelines.market_sip.flatfiles.download_update_events.load_env_files'),\
         patch('src.trading_runtime.keeper_session.open_workstation_keeper_session',return_value=keeper),\
         patch('pipelines.certified_research.publication.KeeperClaim',return_value=claim),\
         patch('pipelines.certified_research.acquisition.prepare_sources') as acquire,\
         patch('pipelines.certified_research.installation.install') as install,\
         patch('pipelines.certified_research.checkpoint_backfill.run',return_value=dict(status='blocked',errors={},seal=None,
             missing_authorities=missing)) as run:
        code=launcher.main(['backfill-v7','--dataset-id',target,'--source-dataset-id',source,
            '--capacity-profile','pilot.json','--backfill-start-date','2026-01-05','--format',output_format])
    output=capsys.readouterr().out
    if output_format=='json':
        import json
        summary=json.loads(output)
        assert summary['missing_authorities']==list(missing)
        assert summary['missing_authority_counts']=={'corporate_actions':1,'float_allocation':1}
    else:
        assert 'corporate_actions | missing scopes=1' in output
        assert 'float_allocation | missing scopes=1' in output
    assert code==2 and load.call_args.args[2]==source
    assert clients.call_args.args[0].max_threads==3
    assert run.call_args.args[2:4]==(source,target)
    assert run.call_args.kwargs['start']==date(2026,1,5)
    acquire.assert_not_called();install.assert_not_called()
    for owner in (inspection,client,claim,keeper):owner.close.assert_called_once()


@pytest.mark.parametrize('explicit_end',(False,True))
@pytest.mark.parametrize('command,complete',(('launch',False),('launch-collection',False),('launch-collection',True)))
@pytest.mark.parametrize('with_identity',(False,True))
def test_fresh_cli_orders_schema_source_preparation_and_execution_and_closes_owners(explicit_end,command,complete,with_identity):
    inspection=Mock();client=Mock();client.query.return_value=[dict(n=0)];client.clean_error.side_effect=str
    keeper=SimpleNamespace(client=Mock(),close=Mock());source_claim=Mock();claim=Mock();dictionary=Mock();paths=Mock()
    scope_claim=Mock()
    invoke_scope=explicit_end and command=='launch' and complete and not with_identity
    inspection.query.return_value=[dict(last_day='2026-01-02')]
    stages=[]
    def installed(*args):stages.append('schema')
    def identity_prepared(args,owner,clients,dictionaries,stop,progress):
        assert owner is source_claim
        if args.identity_point:stages.append('identity')
    def acquired(*args,**kwargs):
        stages.append('sources');assert kwargs['recover_retained_reporting']
        assert args[2].end==date(2026,1,2)
    def fresh(*args,**kwargs):
        stages.append('fresh')
        assert callable(kwargs['sec_scope_preparer'])
        if command=='launch-collection':assert len(kwargs['sec_scope_preparation_hash'])==64
        if invoke_scope:kwargs['sec_scope_preparer'](client.query,args[1],(),claim,Mock())
        if complete:return dict(status='prepared')
        return dict(status='blocked',errors={'inputs':ValueError('missing authority')},seal=None)
    def executed(*args,**kwargs):
        stages.append('execute');return dict(status='complete',certificate=dict(root_hash='a'*64))
    with patch('scripts.build_market_day.Client',side_effect=[inspection,client]),\
         patch('pipelines.certified_research.capacity_profile.load',return_value={}),\
         patch('pipelines.certified_research.capacity_profile.factory',return_value=Mock()),\
         patch('pipelines.market_sip.flatfiles.download_update_events.load_env_files'),\
         patch('src.trading_runtime.keeper_session.open_workstation_keeper_session',return_value=keeper),\
         patch('pipelines.certified_research.publication.SourceClaim',side_effect=[source_claim,scope_claim]),\
         patch('pipelines.certified_research.publication.KeeperClaim',return_value=claim),\
         patch('pipelines.certified_research.publication.DictionaryClaim',return_value=dictionary),\
         patch('pipelines.certified_research.acquisition.IngestionPaths',return_value=paths),\
         patch('pipelines.certified_research.installation.install',side_effect=installed),\
         patch.object(launcher,'prepare_identity',side_effect=identity_prepared),\
         patch('pipelines.certified_research.acquisition.prepare_sources',side_effect=acquired),\
         patch('pipelines.certified_research.sec_acquisition.prepare',return_value=dict(status='reused')) as sec_prepare,\
         patch('pipelines.certified_research.sec_scope_preparation.prepare',return_value=()) as scope_prepare,\
         patch('pipelines.certified_research.sec_lineage.qualify',return_value=dict(qualified=(),unqualified=())),\
         patch('pipelines.certified_research.fresh_execution.run',side_effect=fresh),\
         patch('pipelines.certified_research.collection_preparation.prepare',side_effect=fresh),\
         patch('pipelines.certified_research.collection_execution.execute',side_effect=executed):
        argv=[command,'--dataset-id','00000000-0000-0000-0000-000000000001',
            '--capacity-profile','pilot.json','--context-start-date','2025-12-01',
            '--fundamental-start-date','2024-01-01','--recover-retained-reporting']
        if explicit_end:argv.extend(('--end-date','2026-01-02'))
        if with_identity:argv.extend(('--identity-point','2026-01-02,TEST,BBG000000001'))
        code=launcher.main(argv)
    assert code==(0 if complete else 2)
    assert stages==['schema']+(['identity'] if with_identity else [])+['sources','fresh']+(['execute'] if complete else [])
    assert sec_prepare.call_args.kwargs['fundamental_start']==date(2024,1,1)
    for owner in (dictionary,source_claim,claim,keeper,client,inspection):owner.close.assert_called_once()
    if invoke_scope:
        scope_claim.close.assert_called_once()
        assert scope_prepare.call_args.args[3] is scope_claim
        assert scope_prepare.call_args.kwargs['public_time_mode']=='acceptance_checked_day'
    else:scope_prepare.assert_not_called()
    paths.validate.assert_called_once()


def test_launch_selections_share_one_configuration_and_cannot_omit_v7_input():
    args=launcher.parse_args(['plan','--end-date','2026-01-05','--volume-windows-seconds','10,60,3600',
        '--short-windows-sessions','5,20','--resolutions-ms','1000,60000'])
    config=launcher.campaign_config(args,args.end_date)
    assert config.volume_windows_seconds==(10,60,3600)
    assert config.short_windows_sessions==(5,20) and config.resolutions_ms==(1000,60000)
    with pytest.raises(SystemExit):launcher.parse_args(['plan','--volume-windows-seconds','60,10'])
    args=launcher.parse_args(['plan','--end-date','2026-01-05','--resolutions-ms','60000'])
    with pytest.raises(ValueError,match='V7 requires'):launcher.campaign_config(args,args.end_date)


def test_independent_market_launch_does_not_wait_for_ingestion_lease_or_sec(capsys):
    from uuid import uuid4
    inspection=Mock();client=Mock();client.query.return_value=[dict(n=0)];client.clean_error.side_effect=str
    keeper=SimpleNamespace(client=Mock(),close=Mock());claim=Mock();dictionary=Mock()
    with patch('scripts.build_market_day.Client',side_effect=[inspection,client]),\
         patch('pipelines.certified_research.capacity_profile.load',return_value={}),\
         patch('pipelines.certified_research.capacity_profile.factory',return_value=Mock()),\
         patch('pipelines.market_sip.flatfiles.download_update_events.load_env_files'),\
         patch('src.trading_runtime.keeper_session.open_workstation_keeper_session',return_value=keeper),\
         patch('pipelines.certified_research.publication.SourceClaim') as source_claim,\
         patch('pipelines.certified_research.publication.KeeperClaim',return_value=claim),\
         patch('pipelines.certified_research.publication.DictionaryClaim',return_value=dictionary),\
         patch('pipelines.certified_research.acquisition.IngestionPaths'),\
         patch('pipelines.certified_research.installation.install'),\
         patch('pipelines.certified_research.source.inventory',return_value={}),\
         patch('pipelines.certified_research.source.reporting_inventory',return_value={}),\
         patch('pipelines.certified_research.source.require_ready',return_value='a'*64),\
         patch('pipelines.certified_research.sec_acquisition.prepare') as sec,\
         patch('pipelines.certified_research.acquisition.prepare_sources') as ingest,\
         patch('pipelines.certified_research.fresh_execution.run',return_value=dict(status='blocked',errors={},seal=None)) as run:
        code=launcher.main(['launch','--dataset-id',str(uuid4()),'--capacity-profile','pilot.json',
            '--product-scope','market_bars','--start-date','2026-01-02','--context-start-date','2026-01-02','--end-date','2026-01-02','--format','json'])
    assert code==2
    assert run.call_args.args[1].product_scope=='market_bars'
    assert run.call_args.kwargs['sec_scope_preparer'] is None
    source_claim.assert_not_called();sec.assert_not_called();ingest.assert_not_called()
    assert '"combined_dataset_complete": false' in capsys.readouterr().out


def test_cohort_plan_cli_is_read_only_and_does_not_claim_execution_ready(capsys):
    from tests.test_certified_cohorts import snapshot
    client=Mock();client.clean_error.side_effect=str
    with patch('scripts.build_market_day.Client',return_value=client),patch.object(launcher.universe,'load',return_value=snapshot()):
        result=launcher.main(['plan-cohorts','--context-start-date','2025-12-31','--end-date','2026-01-05','--format','json'])
    assert result==0
    output=json.loads(capsys.readouterr().out)
    assert output['status']=='planned' and not output['execution_ready'] and output['read_only']
    assert len(output['issuer_keys'])==3
    client.close.assert_called_once()
    client.query.assert_not_called() # Isolated snapshot seam; no writers invoked.


def test_retained_recovery_is_explicit_and_has_bounded_ssd_defaults():
    args=launcher.parse_args(['prepare-sources','--end-date','2026-10-02','--recover-retained-reporting'])
    assert args.recover_retained_reporting
    assert (args.staging_reserve_gib,args.staging_reserve_fraction,args.staging_chunk_gib)==(256,.10,256)
    for command in ('plan','verify','run'):
        with pytest.raises(SystemExit):launcher.parse_args([command,'--recover-retained-reporting'])
    for option,value in (('--staging-reserve-gib','-1'),('--staging-chunk-gib','-1'),
                         ('--staging-reserve-fraction','nan'),('--staging-reserve-fraction','1')):
        with pytest.raises(SystemExit):launcher.parse_args(['prepare-sources','--end-date','2026-10-02',option,value])


def test_source_preparation_routes_recovery_before_freezing_source_epoch():
    from threading import Event
    from pipelines.certified_research import acquisition,source
    from pipelines.market_sip.flatfiles import retained_reporting
    day=date(2026,1,2);config=CampaignConfig(day,day)
    inv=dict(missing=(),incomplete=(),expected=(day,))
    absent=dict(missing=(day,),mutating=False);ready=dict(missing=(),mutating=False)
    def query(sql):
        if 'SELECT count() n FROM system.tables' in sql:return [dict(n=0)]
        if 'system.storage_policies' in sql:
            return [dict(disk_name='sip_raw_ssd' if "policy_name='sip_raw_ssd'" in sql else 'live_market_ssd')]
        return []
    writer=Mock();claim=Mock();paths=Mock();stop=Event()
    with patch.object(source,'inventory',return_value=inv),\
         patch.object(source,'reporting_inventory',side_effect=[absent,ready,ready]),\
         patch.object(source,'require_ready',return_value='a'*64) as freeze,\
         patch.object(acquisition,'resume_native_repairs'),patch.object(retained_reporting,'recover') as recover:
        result=acquisition.prepare_sources(query,paths,config,claim,stop,writer_query=writer,
            recover_retained_reporting=True,staging_reserve_bytes=123,staging_chunk_bytes=456)
        recover.assert_called_once()
        assert recover.call_args.args==(writer,paths,inv,(day,),claim,stop)
        assert recover.call_args.kwargs['reserve_bytes']==123
        assert recover.call_args.kwargs['maximum_chunk_bytes']==456
        freeze.assert_called_once_with(query,inv,ready)
        assert result['source_hash']=='a'*64


def profile():
    config=CampaignConfig(date(2026,1,2),date(2026,1,2))
    rules=(dict(token_id=1,modifier_int=0,update_last=1,update_high_low=1,update_volume=1),)
    definition=recipe.market_recipe(config,rules)
    plans=runtime_plan.units(config,definition['definition'],rules)
    reference_hash=digest(runtime_plan.reference_contract())
    recipe_hash=digest(dict(market_recipe=definition['hash'],reference_contract=reference_hash))
    value=dict(version='qualified-capacity-profile-v1',installation_hash=installation.manifest()['installation_hash'],
        reference_contract_hash=reference_hash,rates=[dict(product='campaign_metadata',recipe_hash=recipe_hash,
        evidence_hash='a'*64,units=2,persisted_bytes=100,staging_peak_bytes=0,merge_peak_bytes=100,
        peak_active_units=1,policy='live_market_ssd')],reserve_bytes={'live_market_ssd':0,'sip_raw_ssd':0},
        volume_groups={'live_market_ssd':'d','sip_raw_ssd':'d'},safety_factor='1.25')
    value['profile_hash']=digest(value)
    return config,plans,value


def test_capacity_profile_is_bounded_fingerprinted_and_bound_to_current_recipes():
    config,plans,value=profile()
    with patch.object(Path,'open',return_value=io.BytesIO(json.dumps(value).encode())):
        loaded=capacity_profile.load('pilot.json')
    guard=capacity_profile.factory(Mock(),loaded)(config,plans)
    assert guard.recipes['campaign_metadata']==value['rates'][0]['recipe_hash']
    incompatible=dict(loaded,reference_contract_hash='f'*64)
    incompatible['profile_hash']=digest({k:v for k,v in incompatible.items() if k not in ('profile_hash','parsed_rates')})
    with pytest.raises(ValueError,match='reference algorithms'):
        capacity_profile.factory(Mock(),incompatible)(config,plans)
    with pytest.raises(ValueError,match='changed after loading'):
        capacity_profile.factory(Mock(),dict(loaded,safety_factor='1.00'))(config,plans)
    value['rates'][0]['persisted_bytes']=1
    with patch.object(Path,'open',return_value=io.BytesIO(json.dumps(value).encode())):
        with pytest.raises(ValueError,match='fingerprint'):capacity_profile.load('pilot.json')
    with patch.object(Path,'open',return_value=io.BytesIO(b'x'*(1024*1024+1))):
        with pytest.raises(ValueError,match='metadata bound'):capacity_profile.load('pilot.json')


@pytest.mark.parametrize('status,code',(('complete',0),('failed',2),('interrupted',130)))
@pytest.mark.parametrize('observe',(False,True))
def test_real_cli_branch_closes_claim_and_client_and_returns_execution_status(status,code,observe):
    client=Mock();client.clean_error.side_effect=str;inspection=Mock()
    keeper=SimpleNamespace(client=Mock(),close=Mock())
    output=io.StringIO();claim=Mock()
    result=dict(status=status,errors={} if status!='failed' else {'bars':ValueError('input changed')},
        seal=dict(root_hash='a'*64,units=2) if status=='complete' else None)
    sample=dict(upper_bound_proven=False,staging_bytes=100,output_inactive_bytes=0,merge_input_bytes=0,merge_written_uncompressed_bytes=0)
    sample['evidence_hash']=digest(sample)
    def executed(*args,**kwargs):
        if observe:kwargs['storage_observer']('aggregate','after_staging',client.query)
        return result
    with patch('scripts.build_market_day.Client',side_effect=[inspection,client]) as builder, \
            patch('pipelines.certified_research.campaign.load_plan',return_value=SimpleNamespace(config=CampaignConfig(date(2026,1,2),date(2026,1,2),query_threads=1),units=())), \
            patch('pipelines.market_sip.flatfiles.download_update_events.load_env_files'), \
            patch('src.trading_runtime.keeper_session.open_workstation_keeper_session',return_value=keeper), \
            patch('pipelines.certified_research.publication.KeeperClaim',return_value=claim), \
            patch.object(capacity_profile,'load',return_value={}),patch.object(capacity_profile,'factory'), \
            patch('pipelines.certified_research.capacity_measurement.require_isolated') as isolate, \
            patch('pipelines.certified_research.capacity_measurement.transient_snapshot',return_value=sample), \
            patch('pipelines.certified_research.saved_execution.run',side_effect=executed) as run,redirect_stdout(output):
        actual=launcher.main(['resume','--dataset-id','00000000-0000-0000-0000-000000000001',
            '--capacity-profile','pilot.json','--format','json']+(['--observe-storage'] if observe else []))
    assert actual==code
    assert json.loads(output.getvalue())['status']==status
    if observe:
        assert json.loads(output.getvalue())['storage_observations']['samples']==1
        assert isolate.call_count==2
    else:isolate.assert_not_called()
    assert run.call_args.kwargs['stop'].is_set() is False
    client.close.assert_called_once();claim.close.assert_called_once();keeper.close.assert_called_once()
    inspection.close.assert_called_once()
    assert builder.call_args_list[1].args[0].max_threads==1
