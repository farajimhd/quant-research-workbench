import json
from datetime import date
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import Mock,patch
import pytest
from pipelines.certified_research import storage_estimate as estimate
from pipelines.certified_research.contracts import CampaignConfig
from scripts import prepare_certified_research as cli

ID='00000000-0000-0000-0000-000000000001'


def saved():return SimpleNamespace(dataset_id=ID,config=CampaignConfig(date(2026,1,2),date(2026,1,2)),
    plan_hash='a'*64,units=())


def test_missing_measurements_produce_no_invented_bytes_or_profile():
    with patch.object(estimate.campaign,'load_plan',return_value=saved()),\
         patch.object(estimate.capacity,'inventory',return_value=dict(evidence_hash='b'*64)),\
         patch.object(estimate.capacity,'plan_demands',return_value=dict(demands={'technical':1},active_units={'technical':1},unknown_products={})):
        result=estimate.estimate(Mock(),'arte',ID)
    assert result['status']=='measurement_required' and result['required_bytes'] is None
    assert result['read_only'] and not result['execution_started']


def test_qualified_estimate_uses_existing_admission_without_execution():
    guard=Mock(return_value=dict(status='admitted',headroom={},certificate_hash='c'*64))
    with patch.object(estimate.campaign,'load_plan',return_value=saved()),\
         patch.object(estimate.capacity_profile,'factory',return_value=Mock(return_value=guard)):
        result=estimate.estimate(Mock(),'arte',ID,profile={})
    guard.assert_called_once_with('a'*64,())
    assert result['status']=='admitted' and not result['execution_started']


def test_plan_drift_invalidates_estimate():
    changed=saved();changed.plan_hash='d'*64
    with patch.object(estimate.campaign,'load_plan',side_effect=[saved(),changed]),\
         patch.object(estimate.capacity,'inventory',return_value={}),\
         patch.object(estimate.capacity,'plan_demands',return_value=dict(demands={},active_units={},unknown_products={})),\
         pytest.raises(ValueError,match='changed'):
        estimate.estimate(Mock(),'arte',ID)


def test_collection_estimate_sums_child_demands_and_includes_collection_metadata():
    child=saved();child.config=replace(child.config,issuer_keys=(1,))
    frozen=dict(collection_hash='e'*64,partition=dict(config_hash=replace(child.config,issuer_keys=()).content_hash),
        members=[dict(dataset_id=ID,plan_hash=child.plan_hash,issuer_keys=(1,))])
    store=Mock();store.load.return_value=frozen
    with patch.object(estimate.collection_store,'Store',return_value=store),\
         patch.object(estimate.campaign,'load_plan',return_value=child),\
         patch.object(estimate.capacity,'inventory',return_value={}),\
         patch.object(estimate.capacity,'plan_demands',return_value=dict(demands={'technical':3},active_units={'technical':2},unknown_products={})):
        result=estimate.estimate(Mock(),'arte',ID,is_collection=True)
    assert result['demands']==dict(technical=3,collection_metadata=3)
    assert result['active_units']==dict(technical=2,collection_metadata=1)
    assert result['required_bytes'] is None and store.load.call_count==2


def test_cli_storage_estimate_is_server_readonly_and_never_runs_producers(capsys):
    client=Mock();client.clean_error.side_effect=str
    with patch('scripts.build_market_day.Client',return_value=client) as factory,\
         patch.object(estimate,'estimate',return_value=dict(status='measurement_required',required_bytes=None)) as report,\
         patch('pipelines.certified_research.saved_execution.run') as execute:
        code=cli.main(['estimate-storage','--dataset-id',ID,'--format','json'])
    assert code==2 and json.loads(capsys.readouterr().out)['required_bytes'] is None
    assert factory.call_args.kwargs['readonly'] is True
    execute.assert_not_called();client.close.assert_called_once()


@pytest.mark.parametrize('flag',['--acquire-missing','--stage-archive','--acquire-sec-missing','--repair-retained-symbols'])
def test_estimation_rejects_acquisition_or_repair(flag):
    with pytest.raises(SystemExit):cli.parse_args(['estimate-storage','--dataset-id',ID,flag])
