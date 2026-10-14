from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock,patch
import pytest
from scripts import prepare_certified_research as cli
from pipelines.certified_research import historical_identity as identity

POINT='2026-01-02,TEST,BBG000000001'


@pytest.mark.parametrize('extra',[
    ['--acquire-identity-missing'],
    ['--identity-point',POINT,'--identity-point',POINT],
    ['--identity-point','2026-01-02,TEST,bad'],
    ['--identity-point','20260102,TEST,BBG000000001'],
    ['--identity-point','2018-01-02,TEST,BBG000000001'],
])
def test_invalid_point_batches_fail_before_clients(extra):
    with patch('scripts.build_market_day.Client') as client,pytest.raises(SystemExit):
        cli.main(['prepare-sources','--end-date','2026-01-02',*extra])
    client.assert_not_called()


def test_frozen_execution_cannot_prepare_identity():
    with pytest.raises(SystemExit):
        cli.parse_args(['resume','--dataset-id','00000000-0000-0000-0000-000000000001',
            '--capacity-profile','pilot.json','--identity-point',POINT])


@pytest.mark.parametrize('exists',[True,False])
def test_source_cli_reuses_identity_or_blocks_market_stage_without_provider(exists,capsys):
    main=Mock();reader=Mock();main.clean_error.side_effect=str
    keeper=SimpleNamespace(client=Mock(),close=Mock());claim=Mock()
    observation=identity.ListingObservation(date(2026,1,2),'TEST','BBG000000001','','',
        'XNAS','USD','CS',True,date(2020,1,1),None)
    with patch('scripts.build_market_day.Client',side_effect=[main,reader]),\
         patch('pipelines.market_sip.flatfiles.download_update_events.load_env_files'),\
         patch('pipelines.certified_research.acquisition.IngestionPaths') as paths,\
         patch('src.trading_runtime.keeper_session.open_workstation_keeper_session',return_value=keeper),\
         patch('pipelines.certified_research.publication.SourceClaim',return_value=claim),\
         patch.object(identity,'load',return_value=observation if exists else None),\
         patch('services.reference_gateway.providers.MassiveReferenceClient') as provider,\
         patch('pipelines.certified_research.acquisition.prepare_sources',return_value=
            dict(source=dict(expected=[date(2026,1,2)]),source_hash='a'*64)) as market:
        code=cli.main(['prepare-sources','--end-date','2026-01-02','--identity-point',POINT])
    assert code==(0 if exists else 2)
    provider.assert_not_called()
    assert market.call_count==int(exists)
    for resource in (reader,main,claim,keeper):resource.close.assert_called_once()
    if exists:assert 'reused=1' in capsys.readouterr().out


def test_opt_in_forwarded_without_enabling_market_download():
    args=cli.parse_args(['prepare-sources','--end-date','2026-01-02',
        '--identity-point',POINT,'--acquire-identity-missing'])
    assert not args.acquire_missing
    with patch.object(identity,'prepare_observations',return_value=
            dict(status='complete',units={'identity:0':'certified'})) as run:
        cli.prepare_identity(args,Mock(),Mock(),Mock(),Mock())
    assert run.call_args.kwargs['acquire_missing'] is True
