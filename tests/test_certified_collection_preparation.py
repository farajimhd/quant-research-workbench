from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock,patch
from uuid import uuid4
from pipelines.certified_research import collection_preparation as module
from pipelines.certified_research.contracts import CampaignConfig,digest


def exercise(ready,partial=False,fundamental_start=date(2024,1,1),collection_id=None):
    config=CampaignConfig(date(2026,1,2),date(2026,1,2))
    partition=dict(groups=((1,),(2,)),issuer_keys=(1,2),full_source_hash='a'*64,config_hash=config.content_hash)
    partition['partition_hash']=digest(partition)
    claims=[];installed=[];child_configs={}
    def child(id_):
        from dataclasses import replace
        child_configs[id_]=replace(config,issuer_keys=(len(claims)+1,))
        claim=Mock();claims.append(claim);return claim
    def saved(query,database,id_):
        return SimpleNamespace(plan_hash='b'*64,config=child_configs[id_])
    launch=SimpleNamespace(admit=Mock(),controller=SimpleNamespace(install=Mock()))
    launch.input_checks=Mock()
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
         patch.object(module,'verify_child_inputs',side_effect=launch.input_checks),\
         patch.object(module.collection_store,'Store',return_value=SimpleNamespace(install=installed.append)):
        result=module.prepare(query,config,collection_id or str(uuid4()),Mock(),child,None,None,None,
            context_start=date(2025,12,1),fundamental_start=fundamental_start,anchor_date=date(2026,1,2))
    return result,claims,installed,launch


def test_complete_preparation_installs_metadata_without_running_market():
    result,claims,installed,launch=exercise(True)
    assert result['status']=='prepared' and result['execution_started'] is False
    assert len(installed)==1 and len(installed[0]['members'])==2
    assert launch.admit.call_count==2 and launch.controller.install.call_count==2
    assert len(claims)==2 and all(c.close.call_count==1 for c in claims)
    assert launch.input_checks.call_count==4


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


def test_fundamental_context_changes_child_identity_before_reuse():
    root=str(uuid4())
    _,_,first,_=exercise(True,fundamental_start=date(2024,1,1),collection_id=root)
    _,_,second,_=exercise(True,fundamental_start=date(2023,1,1),collection_id=root)
    assert first[0]['partition']['preparation_inputs']!=second[0]['partition']['preparation_inputs']
    assert first[0]['members'][0]['dataset_id']!=second[0]['members'][0]['dataset_id']


def test_child_input_verification_rechecks_source_and_expanded_universe():
    from pipelines.certified_research.campaign import PlannedUnit
    from pipelines.certified_research.execution import Unit
    import pytest
    config=CampaignConfig(date(2026,1,2),date(2026,1,2),issuer_keys=(1,))
    args=dict(context_start='2024-01-01',end='2026-01-02',anchor_date='2026-01-02',source_hash='a'*64,issuer_keys=(1,))
    saved=SimpleNamespace(config=config,units=(PlannedUnit(Unit('universe','universe'),args),))
    check=Mock()
    with patch.object(module.verification,'source_guard',return_value=check),\
         patch.object(module.universe,'load',return_value={}) as load,\
         patch.object(module.cohorts,'select',return_value=dict(ready=True,source_hash='a'*64)):
        module.verify_child_inputs(None,saved)
        check.assert_called_once();assert load.call_args.args[1]==date(2024,1,1)
    with patch.object(module.verification,'source_guard',return_value=lambda:None),\
         patch.object(module.universe,'load',return_value={}),\
         patch.object(module.cohorts,'select',return_value=dict(ready=True,source_hash='b'*64)):
        with pytest.raises(ValueError,match='universe input changed'):module.verify_child_inputs(None,saved)
