"""Discovery supplies seed roots and exact replay/context dates to the launcher."""
from datetime import date
from types import SimpleNamespace
from threading import Event
from unittest.mock import Mock,patch

import pytest

from pipelines.certified_research.contracts import CampaignConfig
from pipelines.certified_research.planning import MarketSession
from pipelines.certified_research.seed_preparation import prepare_listing


def inputs():
    prior=date(2026,1,2);target=date(2026,1,5)
    return dict(query=Mock(),config=CampaignConfig(target,target),claim=SimpleNamespace(check=Mock()),
        source_guard=Mock(),admitted_sessions=(MarketSession(prior,11,'TEST'),MarketSession(target,11,'TEST')),
        recipe_definition={},rules=({'token_id':1},),day_sources={prior:'b'*64,target:'c'*64},
        adjustment_hashes={prior:'d'*64})


def found(day):
    technical=dict(session=day,dependency=dict(output_hash='e'*64))
    structural=dict(session=day,dependency=dict(output_hash='f'*64,state_context=dict(ticker='TEST')),
        checkpoint=dict(state_hash='a'*64))
    return technical,structural


def test_preparation_joins_independent_discovery_into_frozen_execution_roots():
    arguments=inputs();technical,structural=found(date(2026,1,2))
    with patch('pipelines.certified_research.seed_preparation.verify_definition',return_value='a'*64),patch(
            'pipelines.certified_research.indicator_state.discover',return_value=technical),patch(
            'pipelines.certified_research.v7.discover',return_value=structural) as discovery:
        result=prepare_listing(**arguments)
    assert result['ready'] and len(result['seed_units'])==2
    assert result['seed_parents']=={11:('seed:11:technical','seed:11:v7')}
    assert result['state_start_dates']=={11:(date(2026,1,5),date(2026,1,5))}
    assert result['required_source_days']==(date(2026,1,2),date(2026,1,5))
    assert all(p.unit.parents==('source:2026-01-02',) for p in result['seed_units'])
    assert discovery.call_args.args[7][date(2026,1,2)]['source_hash']=='b'*64
    arguments['query'].assert_not_called() # Only discovery owns database access.


@pytest.mark.parametrize('failure',('seed','source','membership','rename'))
def test_preparation_reports_missing_dependencies_before_any_execution(failure):
    arguments=inputs();technical,structural=found(date(2026,1,2))
    if failure=='seed':structural=None
    elif failure=='source':arguments['day_sources'].pop(date(2026,1,5))
    elif failure=='membership':arguments['admitted_sessions']=arguments['admitted_sessions'][1:]
    else:structural['dependency']['state_context']['ticker']='OLD'
    with patch('pipelines.certified_research.seed_preparation.verify_definition',return_value='a'*64),patch(
            'pipelines.certified_research.indicator_state.discover',return_value=technical),patch(
            'pipelines.certified_research.v7.discover',return_value=structural):
        result=prepare_listing(**arguments)
    assert not result['ready'] and result['blocked'] and not result['seed_parents']
    assert not result['state_start_dates']


def test_operator_stop_prevents_discovery():
    arguments=inputs();stop=Event();stop.set()
    with pytest.raises(InterruptedError):prepare_listing(**arguments,stop=stop)
    arguments['query'].assert_not_called()


def test_metadata_bound_rejects_before_discovery_without_truncating():
    arguments=inputs()
    with pytest.raises(ValueError,match='metadata bound'):prepare_listing(**arguments,maximum_sessions=1)
    arguments['query'].assert_not_called()


def test_later_target_rename_is_blocked_before_writers():
    from dataclasses import replace
    arguments=inputs();day=date(2026,1,6);technical,structural=found(date(2026,1,2))
    arguments['config']=replace(arguments['config'],end=day)
    arguments['admitted_sessions']+=(MarketSession(day,11,'NEW'),)
    arguments['day_sources'][day]='f'*64
    with patch('pipelines.certified_research.seed_preparation.verify_definition',return_value='a'*64),patch(
            'pipelines.certified_research.indicator_state.discover',return_value=technical),patch(
            'pipelines.certified_research.v7.discover',return_value=structural):
        result=prepare_listing(**arguments)
    assert not result['ready'] and any('rename transition' in reason for reason in result['blocked'])


@pytest.mark.parametrize('incomplete',(False,True))
def test_whole_universe_preparation_uses_private_clients_and_exposes_no_partial_executable_graph(incomplete):
    from threading import Barrier,Lock
    from pipelines.certified_research.seed_preparation import prepare_universe
    from pipelines.certified_research.campaign import PlannedUnit
    from pipelines.certified_research.execution import Unit
    config=CampaignConfig(date(2026,1,5),date(2026,1,5),workers=2)
    sessions=tuple(MarketSession(date(2026,1,5),key,'T'+str(key)) for key in (11,12))
    membership=dict(ready=True,sessions=sessions,membership_hash='a'*64)
    clients=[];lock=Lock();barrier=Barrier(2);events=[]
    def factory():
        client=SimpleNamespace(query=Mock(),close=Mock())
        with lock:clients.append(client)
        return client
    def prepare(query,config,claim,guard,history,*args,**kwargs):
        key=history[0].listing_key;barrier.wait(timeout=5)
        ready=not (incomplete and key==12)
        return dict(ready=ready,blocked=() if ready else ('Original clock evidence is missing',),recipe_hash='b'*64,
            preparation_hash=str(key)*32,seed_units=(PlannedUnit(Unit('seed'+str(key),'v7_seed'),{}),),
            seed_parents={key:('indicator'+str(key),'v7'+str(key))},state_start_dates={key:(config.start,config.start)},
            required_source_days=(config.start,),sessions=history)
    with patch('pipelines.certified_research.seed_preparation.prepare_listing',side_effect=prepare),patch(
            'pipelines.certified_research.source.resolve_spellings',return_value=dict(sessions=sessions,evidence_hash='c'*64,absent=())):
        result=prepare_universe(factory,config,SimpleNamespace(check=Mock()),Mock(),membership,{},(),{}, {},progress=events.append)
    assert result['ready']==(not incomplete)
    assert len(clients)==3 and all(client.close.call_count==1 for client in clients)
    assert all('certified' not in event['counts'] for event in events)
    assert sum(events[-1]['counts'].values())==2
    if incomplete:
        assert result['blocked'].keys()=={12} and not result['seed_parents'] and not result['seed_units'] and not result['sessions']
        assert events[-1]['counts']['blocked']==1 and events[-1]['counts']['prepared']==1
    else:
        assert set(result['seed_parents'])=={11,12} and len(result['sessions'])==2
