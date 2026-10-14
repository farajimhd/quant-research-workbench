from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock,patch
from uuid import uuid4
from pipelines.certified_research import collection_preparation as module
from pipelines.certified_research.contracts import CampaignConfig,digest


def exercise(ready,partial=False):
    config=CampaignConfig(date(2026,1,2),date(2026,1,2))
    partition=dict(groups=((1,),(2,)),issuer_keys=(1,2),full_source_hash='a'*64,config_hash=config.content_hash)
    partition['partition_hash']=digest(partition)
    claims=[];installed=[]
    def child(id_):
        claim=Mock();claims.append(claim);return claim
    def saved(query,database,id_):
        return SimpleNamespace(plan_hash='b'*64)
    launch=SimpleNamespace(admit=Mock(),controller=SimpleNamespace(install=Mock()))
    def query(sql):
        if 'SELECT definition' in sql:
            import json
            value=dict(unit_count=3)
            return [dict(definition=json.dumps(value),contract_hash=digest(value))]
        if 'uniqExact(unit_id)' in sql:return [dict(n=1,ids=1)]
        return [dict(n=int(partial))]
    with patch.object(module.source,'exchange_sessions',return_value=(date(2026,1,2),)),\
         patch.object(module.context,'preceding_sessions',return_value=(date(2025,12,31),)),\
         patch.object(module.universe,'load',return_value=dict(source_hash='a'*64,ready=True)),\
         patch.object(module.cohorts,'partition_snapshot',return_value=partition),\
         patch.object(module.fresh_execution,'prepare',return_value=dict(ready=ready)),\
         patch.object(module.initial_plan,'build_launch',return_value=launch),\
         patch.object(module.campaign,'load_plan',side_effect=saved),\
         patch.object(module.collection_store,'Store',return_value=SimpleNamespace(install=installed.append)):
        result=module.prepare(query,config,str(uuid4()),Mock(),child,None,None,None,
            context_start=date(2025,12,1),fundamental_start=date(2024,1,1),anchor_date=date(2026,1,2))
    return result,claims,installed,launch


def test_complete_preparation_installs_metadata_without_running_market():
    result,claims,installed,launch=exercise(True)
    assert result['status']=='prepared' and result['execution_started'] is False
    assert len(installed)==1 and len(installed[0]['members'])==2
    assert launch.admit.call_count==2 and launch.controller.install.call_count==2
    assert len(claims)==2 and all(c.close.call_count==1 for c in claims)


def test_missing_child_authority_cannot_install_partial_collection():
    result,claims,installed,launch=exercise(False)
    assert result['status']=='blocked' and result['completed']==0
    assert not installed and not launch.admit.called
    assert claims[0].close.call_count==1


def test_partial_child_publication_is_rebuilt_through_exact_install_guard():
    result,claims,installed,launch=exercise(True,partial=True)
    assert result['status']=='prepared'
    assert launch.controller.install.call_count==2
    assert len(installed)==1 and all(c.close.call_count==1 for c in claims)
