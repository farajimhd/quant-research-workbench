from unittest.mock import patch
from uuid import uuid4
import pytest
from pipelines.certified_research.capacity_measurement import measure


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
