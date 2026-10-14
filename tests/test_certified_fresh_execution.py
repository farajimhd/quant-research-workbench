from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research import fresh_execution as fresh,seed_preparation
from pipelines.certified_research.contracts import CampaignConfig
from pipelines.certified_research.campaign import PlannedUnit
from pipelines.certified_research.execution import Unit


@pytest.mark.parametrize('ready',(False,True))
@pytest.mark.parametrize('origin',(None,date(2026,1,2)))
def test_fresh_route_admits_only_a_complete_prepared_graph(ready,origin):
    config=CampaignConfig(date(2026,1,2),date(2026,1,2));query=Mock(return_value=[dict(token_id=1,
        modifier_int=0,update_high_low=1,update_last=1,update_volume=1)])
    assembled=dict(ready=ready,missing_authorities=({'family':'float_allocation'},),plans=())
    with patch.object(fresh.source,'inventory',return_value={}),patch.object(fresh.source,'reporting_inventory',return_value={}),\
         patch.object(fresh.source,'require_ready',return_value='a'*64),\
         patch.object(fresh.initial_preparation,'discover_and_prepare',return_value=assembled) as prepare,\
         patch.object(fresh.initial_plan,'run',return_value=dict(status='complete')) as execute:
        result=fresh.run(query,config,'00000000-0000-0000-0000-000000000001',Mock(),Mock(),Mock(),Mock(),
            context_start=date(2025,12,1),fundamental_start=date(2024,1,1),anchor_date=date(2026,10,13),rebuild_origin=origin)
    assert prepare.call_args.kwargs['rebuild_origin']==origin
    assert execute.call_count==int(ready)
    assert result['status']==('complete' if ready else 'blocked')


def test_adjustment_discovery_recomputes_candidate_context_from_certified_corporate_authority():
    day=date(2025,12,31);config=CampaignConfig(date(2026,1,2),date(2026,1,2))
    query=Mock(return_value=[dict(listing_key=1,session_date=str(day))])
    plan=PlannedUnit(Unit('corporate','corporate_actions'),dict(entity_key=2,start='2025-01-01',end='2026-01-03',
        receipt=dict(parent_hash='a'*64)))
    with patch('pipelines.certified_research.adjustments.resolve',return_value=SimpleNamespace(evidence_hash='b'*64)) as resolve:
        result=seed_preparation.discover_adjustment_contexts(query,config,'00000000-0000-0000-0000-000000000001',Mock(),Mock(),
            dict(ready=True,sessions=(SimpleNamespace(listing_key=1,day=config.start),)),(plan,),{1:'corporate'},(day,))
    assert result=={1:{day:'b'*64}}
    assert resolve.call_args.args[-2:]==(date(2025,12,30),day)


def test_fresh_execution_passes_automatic_sec_scope_provider_only_after_complete_discovery():
    provider=Mock();config=CampaignConfig(date(2026,1,2),date(2026,1,2))
    with patch.object(fresh,'prepare',return_value=dict(ready=True)) as prepare,\
         patch.object(fresh.initial_plan,'run',return_value=dict(status='complete')) as run:
        fresh.run(Mock(),config,'dataset',Mock(),Mock(),Mock(),Mock(),context_start=date(2025,12,1),
            fundamental_start=date(2025,1,1),anchor_date=date(2026,1,2),sec_scope_preparer=provider)
        assert run.call_args.kwargs['sec_scope_preparer'] is provider
        prepare.return_value=dict(ready=False,status='blocked');run.reset_mock()
        fresh.run(Mock(),config,'dataset',Mock(),Mock(),Mock(),Mock(),context_start=date(2025,12,1),
            fundamental_start=date(2025,1,1),anchor_date=date(2026,1,2),sec_scope_preparer=provider)
        run.assert_not_called();provider.assert_not_called()
