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
