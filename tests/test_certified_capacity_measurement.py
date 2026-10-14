from unittest.mock import patch
from uuid import uuid4
import pytest
from pipelines.certified_research.capacity_measurement import measure,transient_snapshot,PhaseObservations,require_isolated


def query_for(dataset,*,mixed=False,changed=False):
    calls=[0]
    def query(sql):
        if 'system.parts' in sql:
            calls[0]+=1
            return [dict(table='research_bars_v1',name='part',rows=1,bytes_on_disk=200+(int(changed and calls[0]>1)),
                         data_compressed_bytes=100,data_uncompressed_bytes=500,disk_name='live_market_ssd')]
        return [dict(n=1,datasets=2 if mixed else 1,dataset=dataset)]
    return query


def test_exact_physical_bytes_without_promoting_unmeasured_capacity():
    dataset=str(uuid4())
    with patch('pipelines.certified_research.capacity_measurement.schema.verify_auxiliary_storage'):
        result=measure(query_for(dataset),'arte',dataset,('research_bars_v1',),lambda:'a'*64)
    assert result['tables'][0]['disk_bytes']==200
    assert result['status']=='measured' and result['projection_ready'] is False


@pytest.mark.parametrize('options',({'mixed':True},{'changed':True}))
def test_mixed_or_changing_physical_evidence_is_rejected(options):
    dataset=str(uuid4())
    with patch('pipelines.certified_research.capacity_measurement.schema.verify_auxiliary_storage'):
        with pytest.raises(ValueError):measure(query_for(dataset,**options),'arte',dataset,('research_bars_v1',),lambda:'a'*64)


def test_unverified_authority_is_rejected():
    dataset=str(uuid4())
    with patch('pipelines.certified_research.capacity_measurement.schema.verify_auxiliary_storage'):
        with pytest.raises(ValueError,match='verified'):measure(query_for(dataset),'arte',dataset,('research_bars_v1',),lambda:None)


def test_transient_snapshot_counts_staging_and_inactive_outputs_without_claiming_peak():
    def query(sql):
        if 'system.merges' in sql:return [dict(table='research_bars_v1',input_bytes=500,written_uncompressed=700)]
        return [dict(table='research_stage_bars_v1',active=1,bytes=100,disk_name='live_market_ssd'),
            dict(table='research_stage_bars_v1',active=0,bytes=30,disk_name='live_market_ssd'),
            dict(table='research_bars_v1',active=0,bytes=40,disk_name='live_market_ssd')]
    with patch('pipelines.certified_research.capacity_measurement.schema.verify_auxiliary_storage'):
        result=transient_snapshot(query,'arte',('research_stage_bars_v1',),('research_bars_v1',))
    assert result['staging_bytes']==130 and result['output_inactive_bytes']==40
    assert result['merge_input_bytes']==500 and result['merge_written_uncompressed_bytes']==700
    assert not result['upper_bound_proven']


def test_transient_snapshot_rejects_unknown_or_duplicate_scope():
    with pytest.raises(ValueError):transient_snapshot(None,'arte',('unknown',),())
    with pytest.raises(ValueError):transient_snapshot(None,'arte',('research_bars_v1',),('research_bars_v1',))


def test_inactive_backup_disk_parts_cannot_be_counted_as_ssd_headroom():
    def query(sql):
        if 'system.merges' in sql:return []
        return [dict(table='research_bars_v1',active=0,bytes=40,disk_name='default')]
    with patch('pipelines.certified_research.capacity_measurement.schema.verify_auxiliary_storage'):
        with pytest.raises(ValueError,match='inactive transient parts'):
            transient_snapshot(query,'arte',(),('research_bars_v1',))


def test_phase_observations_are_bounded_and_do_not_promote_sampled_maxima():
    from pipelines.certified_research.contracts import digest
    body=dict(upper_bound_proven=False,staging_bytes=100,output_inactive_bytes=20,
              merge_input_bytes=30,merge_written_uncompressed_bytes=40)
    sample=dict(body,evidence_hash=digest(body))
    observer=PhaseObservations(lambda query:sample,maximum_samples=2)
    observer.record('aggregate','before_write',None)
    observer.record('aggregate','after_staging',None)
    result=observer.result()
    assert result['observed_maxima']['staging_bytes']==100 and not result['upper_bound_proven']
    with pytest.raises(ValueError,match='no samples dropped'):observer.record('aggregate','after_cleanup',None)
    assert len(observer.result()['samples'])==2
    assert observer.result()['capture_status']=='failed'
    with pytest.raises(ValueError,match='previously failed'):observer.record('aggregate','before_write',None)
    sample['staging_bytes']=999
    result['samples'][0]['snapshot']['staging_bytes']=888
    assert observer.result()['observed_maxima']['staging_bytes']==100


def test_failed_snapshot_remains_explicit_and_empty_reuse_is_not_measurement():
    empty=PhaseObservations(lambda query:None)
    assert empty.result()['capture_status']=='no_samples'
    def failure(query):raise RuntimeError('fixture capture failure')
    observer=PhaseObservations(failure)
    with pytest.raises(RuntimeError,match='capture failure'):observer.record('aggregate','after_staging',None)
    assert observer.result()['capture_status']=='failed' and not observer.result()['samples']


@pytest.mark.parametrize('invalid',(-1,True,1.5,None))
def test_hashed_invalid_counters_do_not_enter_observation_history(invalid):
    from pipelines.certified_research.contracts import digest
    body=dict(upper_bound_proven=False,staging_bytes=invalid,output_inactive_bytes=0,
              merge_input_bytes=0,merge_written_uncompressed_bytes=0)
    observer=PhaseObservations(lambda query:dict(body,evidence_hash=digest(body)))
    with pytest.raises(ValueError,match='nonnegative exact integers'):observer.record('aggregate','after_staging',None)
    assert not observer.result()['samples']


def test_pilot_isolation_rejects_foreign_dataset_before_writes():
    dataset=str(uuid4())
    with pytest.raises(ValueError,match='isolated dataset'):
        require_isolated(lambda sql:[dict(n=1,datasets=1,dataset=str(uuid4()))],'arte',dataset,('research_bars_v1',))
    require_isolated(lambda sql:[dict(n=0,datasets=0,dataset='')],'arte',dataset,('research_bars_v1',))


def test_observation_cli_is_explicitly_stored_pilot_only():
    from scripts.prepare_certified_research import parse_args
    dataset=str(uuid4())
    args=parse_args(['resume','--dataset-id',dataset,'--capacity-profile','pilot.json','--observe-storage'])
    assert args.observe_storage
    with pytest.raises(SystemExit):parse_args(['plan','--observe-storage'])


def test_measurement_launcher_is_read_only_and_closes_claim(capsys):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from scripts import prepare_certified_research as launcher
    dataset=str(uuid4());client=Mock();client.clean_error.side_effect=str
    claim=Mock();keeper=SimpleNamespace(client=Mock(),close=Mock())
    def measured(*args):
        assert args[3]==('research_bars_v1',)
        assert args[4]()=='a'*64
        return dict(status='measured',tables=[dict(disk_bytes=200)],projection_ready=False)
    with patch('scripts.build_market_day.Client',return_value=client),\
         patch('pipelines.market_sip.flatfiles.download_update_events.load_env_files'),\
         patch('src.trading_runtime.keeper_session.open_workstation_keeper_session',return_value=keeper),\
         patch('pipelines.certified_research.publication.KeeperClaim',return_value=claim),\
         patch('pipelines.certified_research.verification.verify',return_value=dict(root_hash='a'*64)),\
         patch('pipelines.certified_research.capacity_measurement.measure',side_effect=measured):
        assert launcher.main(['measure-storage','--dataset-id',dataset,'--measurement-tables','research_bars_v1'])==0
    assert '200 bytes' in capsys.readouterr().out
    for owner in (client,claim,keeper):owner.close.assert_called_once()
    with pytest.raises(SystemExit):launcher.parse_args(['measure-storage','--dataset-id',dataset,'--acquire-missing'])
