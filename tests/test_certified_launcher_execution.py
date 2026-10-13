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


def test_execution_parser_requires_frozen_dataset_and_qualified_profile():
    dataset='00000000-0000-0000-0000-000000000001'
    for command in ('run','resume'):
        with pytest.raises(SystemExit):launcher.parse_args([command,'--dataset-id',dataset])
        args=launcher.parse_args([command,'--dataset-id',dataset,'--capacity-profile','pilot.json'])
        assert args.command==command
        with pytest.raises(SystemExit):launcher.parse_args([command,'--dataset-id',dataset,
            '--capacity-profile','pilot.json','--acquire-missing'])


def test_fresh_launch_requires_explicit_context_and_retains_acquisition_controls():
    common=['launch','--dataset-id','00000000-0000-0000-0000-000000000001','--capacity-profile','pilot.json']
    with pytest.raises(SystemExit):launcher.parse_args(common)
    args=launcher.parse_args([*common,'--end-date','2026-01-02','--context-start-date','2025-12-01',
        '--fundamental-start-date','2024-01-01','--recover-retained-reporting'])
    assert args.command=='launch' and args.recover_retained_reporting


@pytest.mark.parametrize('explicit_end',(False,True))
def test_fresh_cli_orders_schema_source_preparation_and_execution_and_closes_owners(explicit_end):
    inspection=Mock();client=Mock();client.query.return_value=[dict(n=0)];client.clean_error.side_effect=str
    keeper=SimpleNamespace(client=Mock(),close=Mock());source_claim=Mock();claim=Mock();dictionary=Mock();paths=Mock()
    inspection.query.return_value=[dict(last_day='2026-01-02')]
    stages=[]
    def installed(*args):stages.append('schema')
    def acquired(*args,**kwargs):
        stages.append('sources');assert kwargs['recover_retained_reporting']
        assert args[2].end==date(2026,1,2)
    def fresh(*args,**kwargs):stages.append('fresh');return dict(status='blocked',errors={'inputs':ValueError('missing authority')},seal=None)
    with patch('scripts.build_market_day.Client',side_effect=[inspection,client]),\
         patch('pipelines.certified_research.capacity_profile.load',return_value={}),\
         patch('pipelines.certified_research.capacity_profile.factory',return_value=Mock()),\
         patch('pipelines.market_sip.flatfiles.download_update_events.load_env_files'),\
         patch('src.trading_runtime.keeper_session.open_workstation_keeper_session',return_value=keeper),\
         patch('pipelines.certified_research.publication.SourceClaim',return_value=source_claim),\
         patch('pipelines.certified_research.publication.KeeperClaim',return_value=claim),\
         patch('pipelines.certified_research.publication.DictionaryClaim',return_value=dictionary),\
         patch('pipelines.certified_research.acquisition.IngestionPaths',return_value=paths),\
         patch('pipelines.certified_research.installation.install',side_effect=installed),\
         patch('pipelines.certified_research.acquisition.prepare_sources',side_effect=acquired),\
         patch('pipelines.certified_research.fresh_execution.run',side_effect=fresh):
        argv=['launch','--dataset-id','00000000-0000-0000-0000-000000000001',
            '--capacity-profile','pilot.json','--context-start-date','2025-12-01',
            '--fundamental-start-date','2024-01-01','--recover-retained-reporting']
        if explicit_end:argv.extend(('--end-date','2026-01-02'))
        code=launcher.main(argv)
    assert code==2 and stages==['schema','sources','fresh']
    for owner in (dictionary,source_claim,claim,keeper,client,inspection):owner.close.assert_called_once()
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
    inv=dict(missing=(),incomplete=())
    absent=dict(missing=(day,),mutating=False);ready=dict(missing=(),mutating=False)
    def query(sql):
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
def test_real_cli_branch_closes_claim_and_client_and_returns_execution_status(status,code):
    client=Mock();client.clean_error.side_effect=str;inspection=Mock()
    keeper=SimpleNamespace(client=Mock(),close=Mock())
    output=io.StringIO();claim=Mock()
    result=dict(status=status,errors={} if status!='failed' else {'bars':ValueError('input changed')},
        seal=dict(root_hash='a'*64,units=2) if status=='complete' else None)
    with patch('scripts.build_market_day.Client',side_effect=[inspection,client]) as builder, \
            patch('pipelines.certified_research.campaign.load_plan',return_value=SimpleNamespace(config=CampaignConfig(date(2026,1,2),date(2026,1,2),query_threads=1))), \
            patch('pipelines.market_sip.flatfiles.download_update_events.load_env_files'), \
            patch('src.trading_runtime.keeper_session.open_workstation_keeper_session',return_value=keeper), \
            patch('pipelines.certified_research.publication.KeeperClaim',return_value=claim), \
            patch.object(capacity_profile,'load',return_value={}),patch.object(capacity_profile,'factory'), \
            patch('pipelines.certified_research.saved_execution.run',return_value=result) as run,redirect_stdout(output):
        actual=launcher.main(['resume','--dataset-id','00000000-0000-0000-0000-000000000001',
            '--capacity-profile','pilot.json','--format','json'])
    assert actual==code
    assert json.loads(output.getvalue())['status']==status
    assert run.call_args.kwargs['stop'].is_set() is False
    client.close.assert_called_once();claim.close.assert_called_once();keeper.close.assert_called_once()
    inspection.close.assert_called_once()
    assert builder.call_args_list[1].args[0].max_threads==1
